from backend.storage.vector_store import VideoVectorStore, StoredChunk
from backend.retrieval.hybrid import QueryRewriter, HybridRetriever


def test_query_rewriter():
    history = [{"question": "What is FlashAttention and why is it faster?"}]
    rewritten = QueryRewriter.rewrite("How does it work?", history)
    assert "FlashAttention" in rewritten

    # Non-ambiguous query should remain unchanged
    standalone = "Explain multi-head attention in transformer models"
    assert QueryRewriter.rewrite(standalone, history) == standalone


def test_vector_store_and_hybrid_retrieval(tmp_path):
    store = VideoVectorStore("test_vid_123", cache_dir=str(tmp_path))
    chunks = [
        StoredChunk("C1", "test_vid_123", 0.0, 45.0, "Introduction to deep learning and neural network backpropagation.", "00:00", "00:45"),
        StoredChunk("C2", "test_vid_123", 40.0, 85.0, "Convolutional neural networks CNNs for computer vision and image classification.", "00:40", "01:25"),
        StoredChunk("C3", "test_vid_123", 80.0, 125.0, "Transformers and self-attention mechanism with QKV query key value projections.", "01:20", "02:05"),
        StoredChunk("C4", "test_vid_123", 120.0, 165.0, "LoRA Low-Rank Adaptation for parameter efficient fine-tuning PEFT on LLMs.", "02:00", "02:45"),
        StoredChunk("C5", "test_vid_123", 160.0, 205.0, "Summary and conclusions on deploying models to edge devices.", "02:40", "03:25"),
    ]
    store.build_from_chunks(chunks)
    assert store.is_cached()

    # Test Phase 3 done condition: A test query returns sensible chunks
    v_results = store.search_vector("What is LoRA parameter efficient fine-tuning?", top_k=2)
    assert len(v_results) > 0
    assert v_results[0][0].chunk_id == "C4"

    # Test Phase 4 done condition: BM25 + Vector hybrid retrieval returns best chunk at top
    retriever = HybridRetriever(store)
    hybrid_results = retriever.retrieve("Tell me about LoRA PEFT", top_k=3)
    assert len(hybrid_results) > 0
    top_chunk = hybrid_results[0].chunk
    assert top_chunk.chunk_id == "C4"
    assert top_chunk.start_sec == 120.0
