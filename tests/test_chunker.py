from voice_agent.chunker import SentenceChunker


def test_emits_complete_sentences_and_waits_for_the_rest():
    c = SentenceChunker()
    assert c.feed("Hello there. How are") == ["Hello there."]
    assert c.feed(" you?") == []  # "?" at the end could still be followed by more text
    assert c.flush() == "How are you?"
    assert c.flush() is None


def test_does_not_split_decimals_or_abbreviations():
    c = SentenceChunker()
    assert c.feed("It costs $12.50 today. Dr. Smith said so. And") == [
        "It costs $12.50 today.",
        "Dr. Smith said so.",
    ]


def test_first_chunk_can_end_at_a_comma_for_faster_audio():
    c = SentenceChunker(first_clause_chars=20, min_clause_chars=60)
    assert c.feed("Your order A1001 has shipped, and it should arrive Monday. Next") == [
        "Your order A1001 has shipped,",
        "and it should arrive Monday.",
    ]


def test_short_lead_in_is_not_cut_at_comma():
    c = SentenceChunker(first_clause_chars=20)
    assert c.feed("Sure, I can help with that. ") == ["Sure, I can help with that."]


def test_text_arriving_in_tiny_deltas():
    c = SentenceChunker()
    out = []
    for ch in "Got it. Checking now. ":
        out += c.feed(ch)
    assert out == ["Got it.", "Checking now."]
