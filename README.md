# YouTube Video RAG with Clickable Timestamps

A full-stack Retrieval-Augmented Generation (RAG) system that indexes YouTube video transcripts into time-aware chunks, answers questions strictly grounded in the video's content, and displays clickable timestamp chips that immediately jump the video player to the exact relevant moment.

---

## 3 Core Design Rules

1. **Timestamps are Metadata, Never Generated**: The LLM cites only retrieved chunk IDs (e.g. `[C12]`), never raw timestamps or hallucinated numbers.
2. **Strict Grounding**: The LLM answers strictly from retrieved chunks and defaults to *"This is not covered in the video."* when uncertain.
3. **Server-Side Validation**: The backend validates every cited chunk ID against the retrieved set before sending it to the client, dropping any hallucinated IDs.

---

## Architecture & Build Phases

```
Ingestion (once per video):
  YouTube URL ──► Transcript with Timestamps ──► 30–60s Overlapping Chunks ──► Embeddings ──► Vector Cache (./data/cache)

Question Answering (runtime):
  User Question ──► Query Rewriter ──► Hybrid Retrieval (Vector + BM25) ──► LLM [Answer + Chunk IDs]
                          │
                          ▼
            Backend Validates IDs & Maps to start_sec (with 2.5s lead-in)
                          │
                          ▼
      Frontend: YouTube IFrame Player + Clickable Timestamp Chips (▶ MM:SS)
```

1. **Phase 1: Transcript Ingestion** (`backend/ingestion/transcript.py`): `youtube-transcript-api` (preferring manual captions, falling back to auto-captions) with Whisper fallback and auto-caption deduplication.
2. **Phase 2: Time-Aware Chunking** (`backend/ingestion/chunker.py`): 30–60s chunks with 10–20% overlap, strictly anchoring chunk `start_sec` to the first caption line.
3. **Phase 3: Embedding and Storage** (`backend/storage/vector_store.py`): Standardized vector space model with sublinear TF and L2 normalization. Cached under `./data/cache`.
4. **Phase 4: Hybrid Retrieval & Reranking** (`backend/retrieval/hybrid.py`): Query rewriter for follow-ups + BM25 keyword search + Dense vector search combined with Reciprocal Rank Fusion (RRF).
5. **Phase 5: Answer Generation** (`backend/generation/generator.py`): Labeled chunks context `[C12] (04:35-05:20) text` with refusal behavior.
6. **Phase 6: Citation Validation** (`backend/generation/validator.py`): Drops hallucinated IDs, attaches `seek_sec` with a 2–3s lead-in.
7. **Phase 7: Frontend Player** (`frontend/`): YouTube IFrame Player API, clickable chips (`▶ MM:SS`), auto-scrolling synchronized transcript panel, and fallback links for non-embeddable videos.
8. **Phase 8: API & Caching** (`backend/api/main.py`): FastAPI endpoints with repeat ingestion skipping.

---

## Local Development

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run Tests
```bash
pytest tests/
```

### 3. Run Locally
```bash
python run.py
```
Open [http://localhost:8000](http://localhost:8000) in your browser.

---

## Pushing to GitHub

1. Create a new repository on [GitHub](https://github.com/new) (e.g. `youtube-video-rag`).
2. Run the following commands in your terminal:
```bash
# Add your GitHub remote (replace with your repo URL)
git remote add origin https://github.com/<YOUR-USERNAME>/<YOUR-REPO-NAME>.git

# Ensure branch is main
git branch -M main

# Push code to GitHub
git push -u origin main
```

---

## Deploying to Render

### Option 1: Automatic Blueprint (Recommended)
This repository includes a `render.yaml` specification.
1. Log in to [Render](https://dashboard.render.com/).
2. Click **New +** > **Blueprint**.
3. Connect your GitHub repository.
4. Render will read `render.yaml` and configure the service automatically.
5. Click **Apply**.

### Option 2: Manual Web Service Setup
1. On [Render Dashboard](https://dashboard.render.com/), click **New +** > **Web Service**.
2. Connect your GitHub repository.
3. Configure the following settings:
   - **Name**: `youtube-video-rag`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python run.py`
   - **Instance Type**: `Free`
4. In **Environment Variables** (optional):
   - `GEMINI_API_KEY`: *(Optional)* If you want real-time Gemini generation instead of the offline grounded rule engine.
5. Click **Deploy Web Service**.
