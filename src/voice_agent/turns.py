"""Turn detection from STT events (PRD section 7.2): when the caller is done, and barge-in."""

from __future__ import annotations

from dataclasses import dataclass

from .providers.base import SpeechStarted, SttEvent, Transcript, UtteranceEnd

# Short acknowledgements that shouldn't interrupt the agent ("mm-hm" while it talks).
BACKCHANNELS = {"mm", "mhm", "mm-hm", "uh-huh", "yeah", "yes", "ok", "okay", "right", "sure", "hmm"}


@dataclass(frozen=True)
class BargeIn:
    """The caller started talking over the agent."""

    text: str


@dataclass(frozen=True)
class EndOfTurn:
    text: str


TurnSignal = BargeIn | EndOfTurn


class TurnDetector:
    """Assemble final transcript segments into one caller turn.

    End of turn = the STT says speech ended (``speech_final``) or an ``UtteranceEnd``
    arrives, and we have words. Barge-in = an interim transcript with real words, at most
    once per utterance; single backchannel words are ignored.
    """

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._barged = False

    def on_event(self, event: SttEvent) -> TurnSignal | None:
        if isinstance(event, SpeechStarted):
            return None  # VAD alone can be a cough or background noise; wait for words
        if isinstance(event, UtteranceEnd):
            return self._end()
        if not isinstance(event, Transcript):
            return None

        text = event.text.strip()
        signal: TurnSignal | None = None
        if text and not self._barged and not _is_backchannel(" ".join([*self._parts, text])):
            self._barged = True
            signal = BargeIn(text)
        if event.is_final and text:
            self._parts.append(text)
        if event.speech_final:
            return self._end() or signal
        return signal

    def _end(self) -> EndOfTurn | None:
        self._barged = False
        if not self._parts:
            return None
        text = " ".join(self._parts)
        self._parts = []
        return EndOfTurn(text)


def _is_backchannel(text: str) -> bool:
    words = [w.strip(".,!?").lower() for w in text.split()]
    return 0 < len(words) <= 2 and all(w in BACKCHANNELS for w in words)
