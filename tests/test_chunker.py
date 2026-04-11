import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.voiceover.chunker import chunk_script


def test_basic_sentence_splitting():
    chunks = chunk_script("Hello world. Another sentence. Final thought!", max_chars=18)

    assert [chunk["text"] for chunk in chunks] == [
        "Hello world.",
        "Another sentence.",
        "Final thought!",
    ]
    assert all(chunk["is_pause"] is False for chunk in chunks)


def test_paragraph_pause_insertion():
    chunks = chunk_script("Paragraph one.\n\nParagraph two.", max_chars=80)

    assert chunks == [
        {"text": "Paragraph one.", "pause_ms": 0, "is_pause": False, "soft_split": False},
        {"text": "", "pause_ms": 600, "is_pause": True},
        {"text": "Paragraph two.", "pause_ms": 0, "is_pause": False, "soft_split": False},
    ]


def test_long_sentence_soft_split_uses_comma_or_whitespace():
    text = "This sentence keeps going, adding more words, until it absolutely must split."

    chunks = chunk_script(text, max_chars=30)

    assert all(chunk["is_pause"] is False for chunk in chunks)
    assert len(chunks) > 1
    assert all(chunk["soft_split"] is True for chunk in chunks)
    assert chunks[0]["text"].endswith(",")


def test_empty_string_returns_no_chunks():
    assert chunk_script("", max_chars=50) == []
    assert chunk_script("   \n\n  ", max_chars=50) == []


def test_single_sentence_under_limit_stays_as_one_chunk():
    chunks = chunk_script("Short sentence.", max_chars=50)

    assert chunks == [{"text": "Short sentence.", "pause_ms": 0, "is_pause": False, "soft_split": False}]


def test_mixed_punctuation_respects_sentence_boundaries():
    chunks = chunk_script("One? Two! Three.", max_chars=6)

    assert [chunk["text"] for chunk in chunks] == ["One?", "Two!", "Three."]


def test_multiple_short_sentences_can_share_a_chunk():
    chunks = chunk_script("One. Two. Three.", max_chars=11)

    assert [chunk["text"] for chunk in chunks] == ["One. Two.", "Three."]
