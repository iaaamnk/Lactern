"""
Retrieval module (Phase 4).
Performs query rewriting for follow-ups, vector search, BM25 keyword search,
and Reciprocal Rank Fusion (RRF) reranking to return the top 4-6 most relevant chunks.
"""

from dataclasses import dataclass
import re
from typing import List, Dict, Any, Optional, Tuple
from rank_bm25 import BM25Okapi
from backend.storage.vector_store import VideoVectorStore, StoredChunk


@dataclass
class ScoredChunk:
    chunk: StoredChunk
    score: float
    vector_rank: Optional[int] = None
    bm25_rank: Optional[int] = None


class QueryRewriter:
    """
    Safeguard for follow-up questions:
    Rewrites ambiguous follow-up questions like 'what about the second one?'
    using prior conversation turns into standalone search queries.
    """

    AMBIGUOUS_PATTERNS = [
        r"\b(it|this|that|these|those|they|them|the second one|the first one|the last one)\b",
        r"\b(what about|how about|tell me more|why is that|can you explain)\b",
    ]

    @classmethod
    def is_ambiguous(cls, question: str) -> bool:
        q_lower = question.lower()
        if len(q_lower.split()) <= 4:
            return True
        for pattern in cls.AMBIGUOUS_PATTERNS:
            if re.search(pattern, q_lower):
                return True
        return False

    @classmethod
    def rewrite(cls, question: str, history: Optional[List[Dict[str, str]]] = None) -> str:
        if not history or not cls.is_ambiguous(question):
            return question.strip()

        # Extract last user question and last assistant response key subjects
        last_turn = history[-1]
        context_hint = ""
        if "question" in last_turn:
            context_hint = last_turn["question"]
        elif "content" in last_turn and last_turn.get("role") == "user":
            context_hint = last_turn["content"]

        # Merge context hint with follow-up
        if context_hint:
            # Strip question marks and combine
            clean_hint = re.sub(r"[?!.]", "", context_hint).strip()
            return f"{clean_hint} {question.strip()}"
        return question.strip()


class BM25Retriever:
    """
    Keyword search retriever using BM25.
    Protects against embeddings blurring specific names, acronyms, and technical jargon.
    """

    def __init__(self, chunks: List[StoredChunk]):
        self.chunks = chunks
        self.corpus = [self._tokenize(c.text) for c in chunks]
        self.bm25 = BM25Okapi(self.corpus) if self.corpus else None

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        # Lowercase and split on words / alphanumeric tokens
        return re.findall(r"\w+", text.lower())

    def search(self, query: str, top_k: int = 15) -> List[Tuple[StoredChunk, float]]:
        if not self.bm25 or not self.chunks:
            return []

        tokens = self._tokenize(query)
        if not tokens:
            return []

        scores = self.bm25.get_scores(tokens)
        ranked_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

        results = []
        for idx in ranked_indices[:top_k]:
            if scores[idx] > 0.0:  # Only include matching chunks
                results.append((self.chunks[idx], float(scores[idx])))
        return results


class HybridRetriever:
    """
    Combines Vector Search + BM25 keyword search using Reciprocal Rank Fusion (RRF),
    then reranks to select the optimal top 4-6 chunks.
    """

    def __init__(self, vector_store: VideoVectorStore, rrf_k: int = 60):
        self.vector_store = vector_store
        self.bm25 = BM25Retriever(vector_store.chunks)
        self.rrf_k = rrf_k

    def retrieve(
        self,
        question: str,
        history: Optional[List[Dict[str, str]]] = None,
        top_k: int = 5,
        retrieve_pool_size: int = 15,
    ) -> List[ScoredChunk]:
        """
        1. Rewrite follow-up if ambiguous.
        2. Retrieve candidate pool from Vector search.
        3. Retrieve candidate pool from BM25 search.
        4. Fuse ranks using RRF.
        5. Return the top 4-6 best chunks.
        """
        search_query = QueryRewriter.rewrite(question, history)

        vector_results = self.vector_store.search_vector(search_query, top_k=retrieve_pool_size)
        bm25_results = self.bm25.search(search_query, top_k=retrieve_pool_size)

        # Build rank lookups
        vector_rank_map = {chunk.chunk_id: (rank + 1, chunk) for rank, (chunk, _) in enumerate(vector_results)}
        bm25_rank_map = {chunk.chunk_id: (rank + 1, chunk) for rank, (chunk, _) in enumerate(bm25_results)}

        all_chunk_ids = set(vector_rank_map.keys()) | set(bm25_rank_map.keys())
        scored: List[ScoredChunk] = []

        for cid in all_chunk_ids:
            chunk = None
            v_rank = None
            b_rank = None
            rrf_score = 0.0

            if cid in vector_rank_map:
                v_rank, chunk = vector_rank_map[cid]
                rrf_score += 1.0 / (self.rrf_k + v_rank)

            if cid in bm25_rank_map:
                b_rank, chunk = bm25_rank_map[cid]
                rrf_score += 1.0 / (self.rrf_k + b_rank)

            if chunk:
                scored.append(
                    ScoredChunk(
                        chunk=chunk,
                        score=round(rrf_score, 6),
                        vector_rank=v_rank,
                        bm25_rank=b_rank,
                    )
                )

        # Sort by fused score descending
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]
