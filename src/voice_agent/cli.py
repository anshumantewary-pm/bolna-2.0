"""Text-mode harness: type as the caller, hear (read) the agent at speaking pace.

Typing a new line while the agent is still "speaking" is a barge-in, exactly like the
caller talking over it. After each turn the per-stage latency trace is printed.
"""

from __future__ import annotations

import asyncio
import sys
import threading

from .agent.claude_agent import ClaudeAgent
from .config import Settings
from .providers.fake import ConsoleAudio, WordTTS
from .session import VoiceSession
from .tools import ToolRegistry, lookup_order

HELP = """\
Voice agent (text mode). Type what the caller says and press Enter.
Type while the agent is talking to interrupt it. Commands: /quit
Try: "Hi, where's my order?" then "A1001"
"""


def _start_stdin_reader(loop: asyncio.AbstractEventLoop, lines: asyncio.Queue[str | None]) -> None:
    # A daemon thread, so a pending input() never blocks shutdown.
    def read() -> None:
        for line in sys.stdin:
            loop.call_soon_threadsafe(lines.put_nowait, line)
        loop.call_soon_threadsafe(lines.put_nowait, None)

    threading.Thread(target=read, daemon=True).start()


async def run() -> None:
    import anthropic

    settings = Settings.from_env()
    client = anthropic.AsyncAnthropic()
    tools = ToolRegistry([lookup_order], timeout_s=settings.tool_timeout_s)
    agent = ClaudeAgent(client, tools, settings)
    audio = ConsoleAudio(words_per_second=3.0)
    session = VoiceSession(agent, WordTTS(), audio, settings)

    print(HELP)
    print(f"model {settings.model} | effort {settings.effort} | fast {settings.fast}\n")

    def report(task: asyncio.Task[None]) -> None:
        audio.end_line()
        if not task.cancelled() and task.exception() is not None:
            print(f"  [error] {task.exception()}")
        if session.traces:
            print(f"  [{session.traces[-1].summary()}]")

    lines: asyncio.Queue[str | None] = asyncio.Queue()
    _start_stdin_reader(asyncio.get_running_loop(), lines)
    while (line := await lines.get()) is not None:
        text = line.strip()
        if not text:
            continue
        if text in {"/quit", "/exit"}:
            break
        task = await session.on_user_turn(text)
        task.add_done_callback(report)
    await session.interrupt()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    print("bye")


if __name__ == "__main__":
    main()
