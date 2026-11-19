from voice_agent.providers.base import SpeechStarted, Transcript, UtteranceEnd
from voice_agent.turns import BargeIn, EndOfTurn, TurnDetector


def test_final_segments_join_into_one_turn_on_speech_final():
    d = TurnDetector()
    assert d.on_event(SpeechStarted()) is None
    assert d.on_event(Transcript("where is", is_final=False)) == BargeIn("where is")
    assert d.on_event(Transcript("where is my", is_final=False)) is None  # barge-in only once
    assert d.on_event(Transcript("Where is my order?", is_final=True)) is None
    assert d.on_event(Transcript("It's A1001.", is_final=True, speech_final=True)) == EndOfTurn(
        "Where is my order? It's A1001."
    )


def test_utterance_end_closes_the_turn_when_endpointing_did_not():
    d = TurnDetector()
    d.on_event(Transcript("Order A1002", is_final=True))
    assert d.on_event(UtteranceEnd()) == EndOfTurn("Order A1002")
    assert d.on_event(UtteranceEnd()) is None  # nothing pending


def test_backchannel_does_not_barge_in_but_still_counts_as_an_answer():
    d = TurnDetector()
    assert d.on_event(Transcript("mm-hm", is_final=False)) is None
    assert d.on_event(Transcript("Yeah.", is_final=True, speech_final=True)) == EndOfTurn("Yeah.")


def test_speech_final_without_words_is_ignored():
    d = TurnDetector()
    assert d.on_event(Transcript("", is_final=True, speech_final=True)) is None


def test_next_utterance_can_barge_in_again():
    d = TurnDetector()
    d.on_event(Transcript("hello there", is_final=True, speech_final=True))
    assert d.on_event(Transcript("wait", is_final=False)) == BargeIn("wait")
