"""Cut streaming LLM text into speakable chunks so TTS can start before the reply is finished."""

from __future__ import annotations

_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "st", "vs", "etc", "e.g", "i.e", "no", "jr", "sr"}


class SentenceChunker:
    """Feed text deltas in; get back complete sentences (or long clauses) to send to TTS.

    The first chunk of a reply may be cut at a comma once it has ``first_clause_chars``
    characters, so the caller hears audio sooner; later chunks need ``min_clause_chars``.
    """

    def __init__(self, first_clause_chars: int = 20, min_clause_chars: int = 60) -> None:
        self.first_clause_chars = first_clause_chars
        self.min_clause_chars = min_clause_chars
        self._buf = ""
        self._emitted = False

    def feed(self, text: str) -> list[str]:
        self._buf += text
        out: list[str] = []
        while (cut := self._find_cut()) is not None:
            piece = self._buf[:cut].strip()
            self._buf = self._buf[cut:].lstrip()
            if piece:
                out.append(piece)
                self._emitted = True
        return out

    def flush(self) -> str | None:
        piece = self._buf.strip()
        self._buf = ""
        if piece:
            self._emitted = True
            return piece
        return None

    def _find_cut(self) -> int | None:
        buf = self._buf
        clause_min = self.min_clause_chars if self._emitted else self.first_clause_chars
        for i, ch in enumerate(buf):
            if ch == "\n":
                return i + 1
            if ch not in ".!?,;:":
                continue
            if i + 1 >= len(buf):
                return None  # can't tell yet: "12." might become "12.50"
            if not buf[i + 1].isspace():
                continue  # "12.50", "e.g", "3:30"
            if ch in ".!?":
                if ch == "." and self._is_abbreviation(buf[:i]):
                    continue
                return i + 1
            if i >= clause_min:
                return i + 1
        return None

    @staticmethod
    def _is_abbreviation(before: str) -> bool:
        words = before.split()
        if not words:
            return False
        word = words[-1].lower()
        return word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha())
