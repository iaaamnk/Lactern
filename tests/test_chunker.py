from backend.ingestion.transcript import TranscriptSnippet
from backend.ingestion.chunker import chunk_transcript, format_timestamp


def test_format_timestamp():
    assert format_timestamp(0) == "00:00"
    assert format_timestamp(45) == "00:45"
    assert format_timestamp(275) == "04:35"
    assert format_timestamp(3665) == "01:01:05"


def test_chunk_transcript_timestamp_safeguard():
    # Crucial rule: Chunk start time MUST be tied to the first line's start time, never average
    snippets = [
        TranscriptSnippet("Welcome to the video course.", 12.5, 4.0),
        TranscriptSnippet("In this lesson we cover attention mechanisms.", 16.5, 5.0),
        TranscriptSnippet("Transformers were introduced in 2017.", 21.5, 6.0),
        TranscriptSnippet("They revolutionized natural language processing.", 27.5, 5.0),
        TranscriptSnippet("Let's look at the query, key, value matrices.", 32.5, 8.0),
        TranscriptSnippet("Each token projects into these representations.", 40.5, 6.0),
        TranscriptSnippet("Next we will explore multi-head attention.", 46.5, 5.0),
        TranscriptSnippet("Multi-head attention enables parallel representations.", 51.5, 6.0),
        TranscriptSnippet("Let us look at an example code snippet in PyTorch.", 57.5, 5.0),
    ]

    chunks = chunk_transcript("demo_vid", snippets, target_duration=40.0, overlap_duration=10.0)
    assert len(chunks) >= 2

    # Check Chunk 1 start time is exactly snippet[0].start
    first_chunk = chunks[0]
    assert first_chunk.start_sec == 12.5
    assert first_chunk.chunk_id == "C1"
    assert "Welcome to the video course." in first_chunk.text
    assert first_chunk.labeled_header.startswith("[C1] (00:12-")

    # Check Chunk 2 has an overlap and correct start time from its first included snippet
    second_chunk = chunks[1]
    assert second_chunk.chunk_id == "C2"
    assert second_chunk.start_sec > first_chunk.start_sec
    assert second_chunk.start_sec < first_chunk.end_sec  # Overlap exists!
