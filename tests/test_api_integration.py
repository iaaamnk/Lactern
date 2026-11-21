import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import patch
import backend.ingestion.transcript as trans_mod
import backend.api.main as main_mod
from backend.ingestion.transcript import TranscriptSnippet

MOCK_SNIPPETS = [
    TranscriptSnippet("Welcome to this tutorial on building RAG applications.", 0.0, 15.0),
    TranscriptSnippet("In this video we focus on YouTube transcript indexing and vector search.", 15.0, 20.0),
    TranscriptSnippet("We use BM25 keyword search alongside dense vectors to avoid blurring acronyms.", 35.0, 25.0),
    TranscriptSnippet("When an answer is generated, the LLM cites chunk IDs, never raw timestamps.", 60.0, 25.0),
    TranscriptSnippet("The backend validates every cited chunk ID and adds a 2.5 second seek lead-in.", 85.0, 30.0),
]


def test_full_rag_api_lifecycle(tmp_path):
    video_id = "test_lifecycle_vid"
    url = f"https://www.youtube.com/watch?v={video_id}"

    # Clear previous cache if any
    cache_file = Path("./data/cache") / f"{video_id}.json"
    if cache_file.exists():
        cache_file.unlink()
    main_mod.active_stores.pop(video_id, None)

    with patch.object(trans_mod, "fetch_transcript", return_value=MOCK_SNIPPETS) as mock_fetch:
        # 1. Ingest Video (First request)
        ingest_req = main_mod.IngestRequest(url=url)
        resp1 = main_mod.ingest_video(ingest_req)
        assert resp1.video_id == video_id
        assert resp1.cached is False
        assert resp1.chunk_count > 0
        assert mock_fetch.call_count == 1

        # 2. Phase 8 Done Condition: "A second request for the same video skips ingestion"
        resp2 = main_mod.ingest_video(ingest_req)
        assert resp2.video_id == video_id
        assert resp2.cached is True  # Cached!
        assert mock_fetch.call_count == 1  # Ingestion was skipped!

        # 3. GET chunks for transcript viewer
        chunks = main_mod.get_video_chunks(video_id)
        assert len(chunks) == resp1.chunk_count
        assert chunks[0]["chunk_id"] == "C1"
        assert chunks[0]["start_sec"] == 0.0

        # 4. POST /api/ask - Question covered in transcript
        ask_req = main_mod.AskRequest(
            video_id=video_id,
            question="Why do we use BM25 keyword search?",
            history=[],
        )
        ask_res = main_mod.ask_question(ask_req)
        assert "BM25" in ask_res.answer
        assert len(ask_res.citations) > 0

        first_citation = ask_res.citations[0]
        assert first_citation.chunk_id.startswith("C")
        assert first_citation.start_sec >= 0.0
        assert first_citation.seek_sec <= first_citation.start_sec
        assert first_citation.label.startswith("▶")

        # 5. POST /api/ask - Follow-up query rewriting test
        followup_req = main_mod.AskRequest(
            video_id=video_id,
            question="What about acronyms?",
            history=[{"question": "Why do we use BM25 keyword search?", "answer": ask_res.answer}],
        )
        followup_res = main_mod.ask_question(followup_req)
        assert len(followup_res.citations) > 0

        # 6. POST /api/ask - Question not covered in transcript
        uncovered_req = main_mod.AskRequest(
            video_id=video_id,
            question="How do you make strawberry ice cream from scratch?",
            history=[],
        )
        uncovered_res = main_mod.ask_question(uncovered_req)
        assert "not covered in the video" in uncovered_res.answer.lower()
        assert len(uncovered_res.citations) == 0
