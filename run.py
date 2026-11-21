"""
Entry point to launch the YouTube Video RAG application.
Supports local execution and cloud deployment (Render, Railway, etc.).

Usage:
    python run.py [--host 0.0.0.0] [--port 8000]
"""

import os
import argparse
import uvicorn

if __name__ == "__main__":
    default_port = int(os.environ.get("PORT", 8000))
    default_host = os.environ.get("HOST", "0.0.0.0")

    parser = argparse.ArgumentParser(description="Run YouTube Video RAG server")
    parser.add_argument("--host", default=default_host, help=f"Host address (default: {default_host})")
    parser.add_argument("--port", type=int, default=default_port, help=f"Port (default: {default_port})")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for dev")
    args = parser.parse_args()

    print(f"Starting YouTube Video RAG server at http://{args.host}:{args.port}")
    uvicorn.run("backend.api.main:app", host=args.host, port=args.port, reload=args.reload)
