"""
Storage and Embedding module (Phase 3).
Embeds chunks and stores vectors + metadata with persistent caching per video_id.
Uses clean, dependency-free normalized TF-IDF vector space with sublinear term frequency
and bi-grams for pure reproducibility, speed, and safety.
"""

from dataclasses import dataclass, asdict
import json
import math
import os
from pathlib import Path
import re
from typing import List, Dict, Any, Tuple, Optional
import numpy as np


@dataclass
class StoredChunk:
    chunk_id: str
    video_id: str
    start_sec: float
    end_sec: float
    text: str
    formatted_start: str
    formatted_end: str

    @property
    def labeled_header(self) -> str:
        return f"[{self.chunk_id}] ({self.formatted_start}-{self.formatted_end})"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StoredChunk":
        return cls(
            chunk_id=data["chunk_id"],
            video_id=data["video_id"],
            start_sec=data["start_sec"],
            end_sec=data["end_sec"],
            text=data["text"],
            formatted_start=data.get("formatted_start", ""),
            formatted_end=data.get("formatted_end", ""),
        )


class PureTfidfVectorizer:
    """
    Self-contained pure Python/numpy TF-IDF vectorizer with (1, 2) n-grams,
    sublinear TF, smoothed IDF, and L2 normalization.
    Completely eliminates C++ OpenMP / vcomp140.dll platform loading issues.
    """

    def __init__(self, min_df: int = 1):
        self.min_df = min_df
        self.vocabulary_: Dict[str, int] = {}
        self.idf_: np.ndarray = np.array([])
        self.n_features_: int = 0

    def _extract_ngrams(self, text: str) -> List[str]:
        words = re.findall(r"\b\w+\b", text.lower())
        unigrams = [w for w in words if len(w) > 1]
        bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)]
        return unigrams + bigrams

    def fit_transform(self, raw_documents: List[str]) -> np.ndarray:
        doc_tokens = [self._extract_ngrams(doc) for doc in raw_documents]
        n_samples = len(doc_tokens)

        # Build vocabulary & document frequencies
        df_counts: Dict[str, int] = {}
        for tokens in doc_tokens:
            for term in set(tokens):
                df_counts[term] = df_counts.get(term, 0) + 1

        # Filter by min_df
        terms = sorted([term for term, count in df_counts.items() if count >= self.min_df])
        self.vocabulary_ = {term: idx for idx, term in enumerate(terms)}
        self.n_features_ = len(terms)

        if self.n_features_ == 0:
            return np.zeros((n_samples, 1), dtype=np.float32)

        # Smoothed IDF: ln((1 + N) / (1 + df)) + 1
        self.idf_ = np.zeros(self.n_features_, dtype=np.float32)
        for term, idx in self.vocabulary_.items():
            df = df_counts[term]
            self.idf_[idx] = math.log((1.0 + n_samples) / (1.0 + df)) + 1.0

        # Compute TF-IDF matrix
        matrix = np.zeros((n_samples, self.n_features_), dtype=np.float32)
        for doc_idx, tokens in enumerate(doc_tokens):
            term_counts: Dict[str, int] = {}
            for t in tokens:
                if t in self.vocabulary_:
                    term_counts[t] = term_counts.get(t, 0) + 1

            for term, count in term_counts.items():
                col_idx = self.vocabulary_[term]
                # Sublinear TF: 1 + log(tf)
                tf = 1.0 + math.log(count)
                matrix[doc_idx, col_idx] = tf * self.idf_[col_idx]

        # L2 normalize rows
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        return matrix / norms

    def transform(self, raw_documents: List[str]) -> np.ndarray:
        if self.n_features_ == 0:
            return np.zeros((len(raw_documents), 1), dtype=np.float32)

        n_samples = len(raw_documents)
        matrix = np.zeros((n_samples, self.n_features_), dtype=np.float32)

        for doc_idx, doc in enumerate(raw_documents):
            tokens = self._extract_ngrams(doc)
            term_counts: Dict[str, int] = {}
            for t in tokens:
                if t in self.vocabulary_:
                    term_counts[t] = term_counts.get(t, 0) + 1

            for term, count in term_counts.items():
                col_idx = self.vocabulary_[term]
                tf = 1.0 + math.log(count)
                matrix[doc_idx, col_idx] = tf * self.idf_[col_idx]

        # L2 normalize
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        return matrix / norms


class EmbeddingProvider:
    """
    Embedding provider for both chunks and queries.
    Uses pure normalized TF-IDF vector space by default,
    enforcing the core safeguard: ONE model for both chunks and queries.
    """

    def __init__(self, model_name: str = "pure-tfidf-ngram-v1"):
        self.model_name = model_name
        self.vectorizer: Optional[PureTfidfVectorizer] = None

    def fit_and_embed_corpus(self, texts: List[str]) -> np.ndarray:
        self.vectorizer = PureTfidfVectorizer(min_df=1)
        return self.vectorizer.fit_transform(texts)

    def embed_query(self, query: str) -> np.ndarray:
        if self.vectorizer is None:
            raise RuntimeError("Embedding model has not been fitted on any corpus yet.")
        return self.vectorizer.transform([query])[0]


class VideoVectorStore:
    """
    In-memory and cached vector store for a specific YouTube video.
    """

    def __init__(self, video_id: str, cache_dir: str = "./data/cache"):
        self.video_id = video_id
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.chunks: List[StoredChunk] = []
        self.embeddings: Optional[np.ndarray] = None
        self.embedder = EmbeddingProvider()

    @property
    def cache_path(self) -> Path:
        return self.cache_dir / f"{self.video_id}.json"

    def is_cached(self) -> bool:
        return self.cache_path.exists()

    def build_from_chunks(self, chunks: List[Any]):
        """Ingest chunk objects and build embedding space."""
        self.chunks = []
        texts = []
        for c in chunks:
            stored = StoredChunk(
                chunk_id=c.chunk_id,
                video_id=self.video_id,
                start_sec=c.start_sec,
                end_sec=c.end_sec,
                text=c.text,
                formatted_start=c.formatted_start,
                formatted_end=c.formatted_end,
            )
            self.chunks.append(stored)
            texts.append(c.text)

        if texts:
            self.embeddings = self.embedder.fit_and_embed_corpus(texts)
        self.save_to_cache()

    def search_vector(self, query: str, top_k: int = 10) -> List[Tuple[StoredChunk, float]]:
        """Dense / vector search by cosine similarity."""
        if not self.chunks or self.embeddings is None:
            return []

        q_vec = self.embedder.embed_query(query)
        # Cosine similarity (both rows and query are L2 normalized)
        scores = np.dot(self.embeddings, q_vec)
        
        # Rank by score descending
        ranked_indices = np.argsort(scores)[::-1]
        results = []
        for idx in ranked_indices[:top_k]:
            score = float(scores[idx])
            results.append((self.chunks[idx], score))
        return results

    def save_to_cache(self):
        """Serialize chunks to disk cache for fast retrieval & skipping ingestion."""
        payload = {
            "video_id": self.video_id,
            "model_name": self.embedder.model_name,
            "chunks": [c.to_dict() for c in self.chunks],
        }
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def load_from_cache(self) -> bool:
        """Load from disk cache if present."""
        if not self.cache_path.exists():
            return False

        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.video_id = data["video_id"]
            self.chunks = [StoredChunk.from_dict(d) for d in data.get("chunks", [])]
            texts = [c.text for c in self.chunks]
            if texts:
                self.embeddings = self.embedder.fit_and_embed_corpus(texts)
            return True
        except Exception:
            return False
