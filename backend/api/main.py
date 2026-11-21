"""
FastAPI Backend API (Phase 8).
Provides endpoints for ingestion, caching, status checks, and question answering with clickable timestamps.
Fully synchronous endpoints to ensure maximum compatibility, reliability, and speed in all environments.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
import os
from pathlib import Path
import logging

from backend.ingestion import transcript
from backend.ingestion.chunker import chunk_transcript
from backend.storage.vector_store import VideoVectorStore
from backend.retrieval.hybrid import HybridRetriever
from backend.generation.generator import AnswerGenerator
from backend.generation.validator import validate_and_enrich_citations

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="YouTube Video RAG with Clickable Timestamps", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CACHE_DIR = os.getenv("CACHE_DIR", "./data/cache")
Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)

# In-memory store cache registry
active_stores: Dict[str, VideoVectorStore] = {}
ingestion_status: Dict[str, Dict[str, Any]] = {}


def get_or_load_store(video_id: str) -> Optional[VideoVectorStore]:
    if video_id in active_stores:
        return active_stores[video_id]

    store = VideoVectorStore(video_id, cache_dir=CACHE_DIR)
    if store.load_from_cache():
        active_stores[video_id] = store
        return store
    return None


@app.on_event("startup")
def preload_demo_stores():
    """Pre-warm sentence-transformers model and demo stores into memory on boot."""
    demo_ids = ["LxCjscf2PjA", "0wp5Z3z4bjM", "GvrIe41E7xA", "jNQXAC9IVRw"]
    logger.info("Pre-warming active video stores and embedding model...")
    for vid in demo_ids:
        try:
            get_or_load_store(vid)
        except Exception as e:
            logger.warning(f"Could not preload store {vid}: {e}")



class IngestRequest(BaseModel):
    url: str = Field(..., description="YouTube URL or 11-char Video ID")
    transcript_text: Optional[str] = Field(default=None, description="Optional manual transcript paste fallback")


class IngestResponse(BaseModel):
    video_id: str
    status: str
    cached: bool
    chunk_count: int
    message: str


class AskRequest(BaseModel):
    video_id: str
    question: str
    history: Optional[List[Dict[str, str]]] = Field(default_factory=list)


class CitationResponse(BaseModel):
    chunk_id: str
    start_sec: float
    end_sec: float
    formatted_start: str
    formatted_end: str
    seek_sec: float
    label: str
    snippet_preview: str


class AskResponse(BaseModel):
    answer: str
    citations: List[CitationResponse]
    retrieved_count: int
    model_name: str


def run_ingestion_job(video_id: str, url: str, transcript_text: Optional[str] = None):
    """Ingestion pipeline: transcript -> chunking -> vector store caching."""
    try:
        if transcript_text and transcript_text.strip():
            ingestion_status[video_id] = {"status": "processing", "message": "Parsing manual transcript text..."}
            snippets = transcript.parse_raw_text_transcript(transcript_text.strip())
            if not snippets:
                raise ValueError("Could not extract any transcript snippets from the provided text.")
        else:
            ingestion_status[video_id] = {"status": "processing", "message": "Fetching transcript captions..."}
            snippets = transcript.fetch_transcript(url)
        
        ingestion_status[video_id] = {"status": "processing", "message": "Chunking transcript with time-preservation..."}
        chunks = chunk_transcript(video_id, snippets)

        ingestion_status[video_id] = {"status": "processing", "message": "Building vector store embeddings..."}
        store = VideoVectorStore(video_id, cache_dir=CACHE_DIR)
        store.build_from_chunks(chunks)
        active_stores[video_id] = store

        ingestion_status[video_id] = {
            "status": "ready",
            "chunk_count": len(chunks),
            "message": "Ingestion complete and cached.",
        }
    except Exception as e:
        logger.exception("Ingestion failed for %s", video_id)
        ingestion_status[video_id] = {
            "status": "failed",
            "message": f"Ingestion error: {str(e)}",
        }


@app.post("/api/ingest", response_model=IngestResponse)
def ingest_video(req: IngestRequest):
    """
    POST /ingest {url, transcript_text?}
    Cached per video_id: A second request for the same video skips ingestion unless new transcript text is provided.
    """
    try:
        video_id = transcript.extract_video_id(req.url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Check cache first (skip only if no manual text override is passed)
    if not req.transcript_text:
        existing_store = get_or_load_store(video_id)
        if existing_store:
            ingestion_status[video_id] = {
                "status": "ready",
                "chunk_count": len(existing_store.chunks),
                "message": "Loaded from cache.",
            }
            return IngestResponse(
                video_id=video_id,
                status="ready",
                cached=True,
                chunk_count=len(existing_store.chunks),
                message="Video already indexed in cache.",
            )

    # Check if currently processing
    if video_id in ingestion_status and ingestion_status[video_id].get("status") == "processing":
        return IngestResponse(
            video_id=video_id,
            status="processing",
            cached=False,
            chunk_count=0,
            message="Ingestion currently in progress.",
        )

    # Run ingestion job
    run_ingestion_job(video_id, req.url, req.transcript_text)
    status_info = ingestion_status.get(video_id, {})
    if status_info.get("status") == "failed":
        raise HTTPException(status_code=500, detail=status_info.get("message"))

    return IngestResponse(
        video_id=video_id,
        status="ready",
        cached=False,
        chunk_count=status_info.get("chunk_count", 0),
        message="Video ingested and indexed successfully.",
    )


@app.get("/api/status/{video_id}")
def get_video_status(video_id: str):
    """Check ingestion status of a video."""
    store = get_or_load_store(video_id)
    if store:
        return {
            "video_id": video_id,
            "status": "ready",
            "cached": True,
            "chunk_count": len(store.chunks),
        }

    status_info = ingestion_status.get(video_id)
    if status_info:
        return {"video_id": video_id, **status_info}

    return {"video_id": video_id, "status": "not_found", "message": "Video has not been ingested yet."}


@app.get("/api/chunks/{video_id}")
def get_video_chunks(video_id: str):
    """Retrieve full transcript chunk list for synchronized transcript side panel."""
    store = get_or_load_store(video_id)
    if not store:
        raise HTTPException(status_code=404, detail="Video not ingested or not found.")
    return [c.to_dict() for c in store.chunks]


@app.post("/api/ask", response_model=AskResponse)
def ask_question(req: AskRequest):
    """
    POST /ask {video_id, question, history}
    1. Query rewriter handles follow-up context.
    2. Hybrid retriever (Vector + BM25) fetches and reranks top 5 chunks.
    3. LLM answers strictly from labeled chunks, citing chunk IDs.
    4. Citation validator checks IDs against retrieved set, drops fakes, attaches lead-in seek time.
    """
    store = get_or_load_store(req.video_id)
    if not store:
        raise HTTPException(status_code=404, detail=f"Video '{req.video_id}' has not been ingested yet.")

    retriever = HybridRetriever(store)
    scored_results = retriever.retrieve(req.question, history=req.history, top_k=5)
    retrieved_chunks = [sr.chunk for sr in scored_results]

    generator = AnswerGenerator()
    gen_result = generator.generate(req.question, retrieved_chunks)

    # Validate citations and attach lead-in seek times
    validated = validate_and_enrich_citations(
        cited_ids=gen_result.raw_citations,
        retrieved_chunks=retrieved_chunks,
        lead_in_seconds=2.5,
    )

    return AskResponse(
        answer=gen_result.answer,
        citations=[CitationResponse(**v.to_dict()) for v in validated],
        retrieved_count=len(retrieved_chunks),
        model_name=gen_result.model_name,
    )


# Serve frontend static assets
frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
