"""One call's session actor (PRD section 7): turn-taking, streaming speech, barge-in.

Each caller turn runs as one asyncio task with two halves running at the same time:
the agent stream (text -> sentences -> queue) and the speaker (queue -> TTS -> audio).
A new caller utterance cancels the task, which stops both halves and records what the
caller actually heard, so the agent can be told on the next turn.
"""

from __future__ import annotations

import asyncio
import enum
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .agent import ConversationAgent, TextDelta, ToolFinished, ToolStarted, TurnEnded
from .agent.prompts import FILLER_LINE, REFUSAL_LINE
from .chunker import SentenceChunker
from .config import Settings
from .providers.base import AudioOutput, TextToSpeech
from .tracing import TurnTrace


class State(enum.Enum):
    LISTENING = "listening"
    THINKING = "thinking"
    TOOL_RUNNING = "tool_running"
    SPEAKING = "speaking"


@dataclass(frozen=True)
class _Utterance:
    text: str
    # False for filler lines: they are not part of the agent's reply.
    from_agent: bool = True


class VoiceSession:
    def __init__(
        self,
        agent: ConversationAgent,
        tts: TextToSpeech,
        audio: AudioOutput,
        settings: Settings | None = None,
        listener: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.agent = agent
        self.tts = tts
        self.audio = audio
        self.settings = settings or Settings()
        # Optional observer for UIs and logs: listener(event_name, data).
        self.listener = listener
        self._state = State.LISTENING
        self.traces: list[TurnTrace] = []
        self._task: asyncio.Task[None] | None = None
        self._note: str | None = None
        self._turns = 0

    @property
    def state(self) -> State:
        return self._state

    @state.setter
    def state(self, value: State) -> None:
        if value is not self._state:
            self._state = value
            self._emit("state", {"state": value.value})

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        if self.listener is not None:
            self.listener(event, data)

    async def on_user_turn(self, text: str) -> asyncio.Task[None]:
        """Caller finished an utterance: barge in on any reply in progress, then answer."""
        await self.interrupt()
        self._turns += 1
        trace = TurnTrace(turn=self._turns)
        self._task = asyncio.create_task(self._run_turn(text, trace))
        self._task.add_done_callback(self._report_failure)
        return self._task

    def _report_failure(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (exc := task.exception()) is not None:
            self._emit("error", {"message": f"{type(exc).__name__}: {exc}"})

    async def interrupt(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await self.audio.stop()
        self.state = State.LISTENING

    async def _run_turn(self, user_text: str, trace: TurnTrace) -> None:
        note, self._note = self._note, None
        queue: asyncio.Queue[_Utterance | None] = asyncio.Queue()
        intended: list[str] = []  # sentences of the reply, as generated
        heard: list[str] = []  # words of the reply the caller actually heard
        chunker = SentenceChunker()
        speaker = asyncio.create_task(self._speak(queue, heard, trace))
        filler: asyncio.Task[None] | None = None
        events = self.agent.run_turn(user_text, trace, note)
        self.state = State.THINKING

        def say(text: str) -> None:
            trace.mark("first_sentence")
            intended.append(text)
            queue.put_nowait(_Utterance(text))
            self._emit("agent_text", {"text": text, "turn": trace.turn})

        try:
            async for event in events:
                if isinstance(event, TextDelta):
                    for sentence in chunker.feed(event.text):
                        say(sentence)
                elif isinstance(event, ToolStarted):
                    self.state = State.TOOL_RUNNING
                    if (pending := chunker.flush()) is not None:
                        say(pending)  # e.g. "Let me check that." before the tool runs
                    if filler is None and not intended:
                        filler = asyncio.create_task(self._filler_after(queue))
                elif isinstance(event, ToolFinished):
                    if filler is not None and not filler.done():
                        filler.cancel()
                    self.state = State.THINKING
                elif isinstance(event, TurnEnded) and event.stop_reason == "refusal":
                    chunker.flush()
                    say(REFUSAL_LINE)
            if (tail := chunker.flush()) is not None:
                say(tail)
            queue.put_nowait(None)
            await speaker
            trace.mark("done")
        except asyncio.CancelledError:
            trace.interrupted = True
            self._note = _interruption_note(intended, heard)
            raise
        finally:
            if filler is not None:
                filler.cancel()
            speaker.cancel()
            await events.aclose()  # lets the agent repair its history if cut off mid-stream
            self.traces.append(trace)
            self.state = State.LISTENING
            self._emit(
                "turn_done",
                {
                    "turn": trace.turn,
                    "marks_ms": {k: round(v) for k, v in trace.marks.items()},
                    "tools": [[n, round(ms)] for n, ms in trace.tool_ms],
                    "usage": trace.usage,
                    "interrupted": trace.interrupted,
                    "summary": trace.summary(),
                },
            )

    async def _speak(
        self, queue: asyncio.Queue[_Utterance | None], heard: list[str], trace: TurnTrace
    ) -> None:
        while (utterance := await queue.get()) is not None:
            self.state = State.SPEAKING
            async for chunk in self.tts.synthesize(utterance.text):
                trace.mark("first_audio")
                await self.audio.play(chunk)
                if utterance.from_agent:
                    heard.append(chunk.text)

    async def _filler_after(self, queue: asyncio.Queue[_Utterance | None]) -> None:
        await asyncio.sleep(self.settings.filler_after_s)
        queue.put_nowait(_Utterance(FILLER_LINE, from_agent=False))


def _interruption_note(intended: list[str], heard: list[str]) -> str | None:
    said = " ".join(intended).split()
    if not said or len(heard) >= len(said):
        return None  # nothing was cut off
    if not heard:
        return (
            "The caller interrupted before hearing any of your previous reply. "
            "Assume they heard none of it."
        )
    return (
        "The caller interrupted your previous reply. They heard only: "
        f"\"{' '.join(heard)}\". Assume they did not hear the rest."
    )
