from backend.storage.vector_store import StoredChunk
from backend.generation.validator import validate_and_enrich_citations
from backend.generation.generator import AnswerGenerator, build_context_block


def test_build_context_block():
    chunks = [
        StoredChunk("C1", "vid1", 10.0, 45.0, "Introduction to system architecture.", "00:10", "00:45"),
        StoredChunk("C2", "vid1", 42.0, 75.0, "Detailed look at database indexing.", "00:42", "01:15"),
    ]
    block = build_context_block(chunks)
    assert "[C1] (00:10-00:45) Introduction to system architecture." in block
    assert "[C2] (00:42-01:15) Detailed look at database indexing." in block


def test_citation_validation_safeguard():
    # Phase 6 Done when: A made-up ID is dropped, never shown
    retrieved = [
        StoredChunk("C1", "vid1", 30.0, 60.0, "First chunk text", "00:30", "01:00"),
        StoredChunk("C2", "vid1", 55.0, 90.0, "Second chunk text", "00:55", "01:30"),
    ]

    # Model cited real C1, real C2, and hallucinated C99 and C_HALLUCINATED
    raw_citations = ["C1", "C99", "[C2]", "C_HALLUCINATED"]
    validated = validate_and_enrich_citations(raw_citations, retrieved, lead_in_seconds=2.5)

    assert len(validated) == 2
    cited_ids = [v.chunk_id for v in validated]
    assert "C1" in cited_ids
    assert "C2" in cited_ids
    assert "C99" not in cited_ids
    assert "C_HALLUCINATED" not in cited_ids

    # Check lead-in seek time
    c1_val = next(v for v in validated if v.chunk_id == "C1")
    assert c1_val.start_sec == 30.0
    assert c1_val.seek_sec == 27.5  # 30.0 - 2.5s lead-in
    assert c1_val.label == "▶ 00:30"


def test_generator_refusal_when_uncovered():
    chunks = [
        StoredChunk("C1", "vid1", 10.0, 45.0, "This video explains cooking traditional Italian pizza.", "00:10", "00:45"),
    ]
    generator = AnswerGenerator()
    # Question about quantum computing is definitely not covered
    result = generator.generate("What is quantum entanglement?", chunks)
    assert "not covered in the video" in result.answer.lower()
    assert result.raw_citations == []
