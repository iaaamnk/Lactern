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


try:
    import faiss
    HAS_FAISS = True
except ImportError:
    HAS_FAISS = False


class FaissVectorIndex:
    """
    Dedicated FAISS Vector Indexing & Retrieval module.
    Uses native C++ faiss.IndexFlatIP (Cosine / Inner Product) when available.
    Provides identical FAISS interface (add, search, L2 normalization) everywhere.
    """

    def __init__(self, dimension: int = 384):
        self.dimension = dimension
        if HAS_FAISS:
            self._index = faiss.IndexFlatIP(dimension)
        else:
            self._index = None
            self._vectors: Optional[np.ndarray] = None

    def add(self, embeddings: np.ndarray):
        """Add L2-normalized vectors to the FAISS index."""
        x = np.ascontiguousarray(embeddings, dtype=np.float32)
        if x.ndim == 1:
            x = np.expand_dims(x, 0)
        # Cosine normalization
        norms = np.linalg.norm(x, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        x = x / norms

        if HAS_FAISS and self._index is not None:
            self._index.add(x)
        else:
            self._vectors = x if self._vectors is None else np.vstack([self._vectors, x])

    def search(self, query_vector: np.ndarray, top_k: int) -> Tuple[np.ndarray, np.ndarray]:
        """Query FAISS index for top_k nearest neighbors by cosine similarity."""
        q = np.ascontiguousarray(query_vector, dtype=np.float32)
        if q.ndim == 1:
            q = np.expand_dims(q, 0)
        norm = np.linalg.norm(q, axis=1, keepdims=True)
        norm[norm == 0.0] = 1.0
        q = q / norm

        if HAS_FAISS and self._index is not None:
            scores, indices = self._index.search(q, top_k)
            return scores[0], indices[0]
        else:
            if self._vectors is None or len(self._vectors) == 0:
                return np.array([], dtype=np.float32), np.array([], dtype=np.int64)
            scores = np.dot(self._vectors, q.T).flatten()
            k = min(top_k, len(scores))
            ranked_indices = np.argsort(scores)[::-1][:k]
            ranked_scores = scores[ranked_indices]
            return ranked_scores, ranked_indices


_GLOBAL_ST_MODEL = None


def get_shared_sentence_transformer(model_name: str = "all-MiniLM-L6-v2"):
    """Singleton getter for SentenceTransformer to avoid re-downloading/re-instantiating on every store."""
    global _GLOBAL_ST_MODEL
    if _GLOBAL_ST_MODEL is not None:
        return _GLOBAL_ST_MODEL
    try:
        from sentence_transformers import SentenceTransformer
        _GLOBAL_ST_MODEL = SentenceTransformer(model_name)
        return _GLOBAL_ST_MODEL
    except Exception:
        return None


class EmbeddingProvider:
    """
    Semantic & Dense Embedding provider for both chunks and queries.
    Uses sentence-transformers 'all-MiniLM-L6-v2' (384-dim semantic embeddings) by default,
    with a graceful TF-IDF fallback for test isolation or environments without PyTorch.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._st_model = get_shared_sentence_transformer(model_name)
        if self._st_model is not None:
            self._tfidf = None
            self.dimension = 384
        else:
            self._tfidf = PureTfidfVectorizer(min_df=1)
            self.dimension = 0

    def fit_and_embed_corpus(self, texts: List[str]) -> np.ndarray:
        if self._st_model is not None:
            embeddings = self._st_model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
            self.dimension = embeddings.shape[1]
            return np.ascontiguousarray(embeddings, dtype=np.float32)
        
        self._tfidf = PureTfidfVectorizer(min_df=1)
        matrix = self._tfidf.fit_transform(texts)
        self.dimension = matrix.shape[1] if matrix.size > 0 else 0
        return matrix

    def embed_query(self, query: str) -> np.ndarray:
        if self._st_model is not None:
            embedding = self._st_model.encode([query], normalize_embeddings=True, show_progress_bar=False)[0]
            return np.ascontiguousarray(embedding, dtype=np.float32)

        if self._tfidf is not None:
            return self._tfidf.transform([query])[0]

        raise RuntimeError("Embedding model has not been initialized.")


class VideoVectorStore:
    """
    In-memory and cached FAISS vector store for a specific YouTube video.
    """

    def __init__(self, video_id: str, cache_dir: str = "./data/cache", model_name: str = "all-MiniLM-L6-v2"):
        self.video_id = video_id
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.chunks: List[StoredChunk] = []
        self.embeddings: Optional[np.ndarray] = None
        self.embedder = EmbeddingProvider(model_name=model_name)
        self.faiss_index: Optional[FaissVectorIndex] = None

    @property
    def cache_path(self) -> Path:
        return self.cache_dir / f"{self.video_id}.json"

    def is_cached(self) -> bool:
        return self.cache_path.exists()

    def build_from_chunks(self, chunks: List[Any]):
        """Ingest chunk objects, compute all-MiniLM-L6-v2 embeddings, and build FAISS index."""
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
            self.faiss_index = FaissVectorIndex(dimension=self.embedder.dimension)
            self.faiss_index.add(self.embeddings)
        self.save_to_cache()

    def search_vector(self, query: str, top_k: int = 10) -> List[Tuple[StoredChunk, float]]:
        """Dense / vector search by cosine similarity using FAISS Index."""
        if not self.chunks:
            return []

        q_vec = self.embedder.embed_query(query)

        # Use FAISS index if built, otherwise build or direct cosine
        if self.faiss_index is None and self.embeddings is not None:
            self.faiss_index = FaissVectorIndex(dimension=self.embeddings.shape[1])
            self.faiss_index.add(self.embeddings)

        if self.faiss_index is not None:
            scores, indices = self.faiss_index.search(q_vec, top_k)
            results = []
            for score, idx in zip(scores, indices):
                if 0 <= idx < len(self.chunks):
                    results.append((self.chunks[idx], float(score)))
            return results

        return []

    def save_to_cache(self):
        """Serialize chunks and embeddings to disk cache for fast retrieval & skipping ingestion."""
        payload = {
            "video_id": self.video_id,
            "model_name": self.embedder.model_name,
            "dimension": self.embedder.dimension,
            "chunks": [c.to_dict() for c in self.chunks],
            "embeddings": self.embeddings.tolist() if self.embeddings is not None else [],
        }
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def load_from_cache(self) -> bool:
        """Load from disk cache if present and reconstruct FAISS index."""
        if not self.cache_path.exists():
            return False

        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.video_id = data["video_id"]
            self.chunks = [StoredChunk.from_dict(d) for d in data.get("chunks", [])]
            raw_embs = data.get("embeddings", [])
            
            if raw_embs:
                self.embeddings = np.array(raw_embs, dtype=np.float32)
                dim = self.embeddings.shape[1] if self.embeddings.ndim > 1 else len(self.embeddings)
                self.faiss_index = FaissVectorIndex(dimension=dim)
                self.faiss_index.add(self.embeddings)
            else:
                texts = [c.text for c in self.chunks]
                if texts:
                    self.embeddings = self.embedder.fit_and_embed_corpus(texts)
                    self.faiss_index = FaissVectorIndex(dimension=self.embedder.dimension)
                    self.faiss_index.add(self.embeddings)

            return True
        except Exception:
            return False
