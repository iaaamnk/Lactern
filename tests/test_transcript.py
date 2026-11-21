import pytest
from backend.ingestion.transcript import extract_video_id, clean_transcript_lines, TranscriptSnippet


def test_extract_video_id_standard():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert extract_video_id(url) == "dQw4w9WgXcQ"


def test_extract_video_id_short():
    url = "https://youtu.be/dQw4w9WgXcQ"
    assert extract_video_id(url) == "dQw4w9WgXcQ"


def test_extract_video_id_embed():
    url = "https://www.youtube.com/embed/dQw4w9WgXcQ"
    assert extract_video_id(url) == "dQw4w9WgXcQ"


def test_extract_video_id_shorts():
    url = "https://www.youtube.com/shorts/dQw4w9WgXcQ"
    assert extract_video_id(url) == "dQw4w9WgXcQ"


def test_extract_video_id_raw():
    assert extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_video_id_invalid():
    with pytest.raises(ValueError):
        extract_video_id("https://google.com/search?q=youtube")


def test_clean_transcript_lines_deduplication():
    snippets = [
        TranscriptSnippet("hello world", 0.0, 2.0),
        TranscriptSnippet("hello world and welcome", 1.5, 2.5),  # Prefix match
        TranscriptSnippet("to the deep dive", 3.0, 2.0),
        TranscriptSnippet("[Music]", 5.0, 3.0),  # Filter out
        TranscriptSnippet("into machine learning", 8.0, 2.0),
    ]
    cleaned = clean_transcript_lines(snippets)
    texts = [s.text for s in cleaned]
    assert "[Music]" not in texts
    assert "hello world and welcome" in texts
    assert "hello world" not in texts  # Prefix replaced by fuller version
