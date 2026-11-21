"""
Transcript ingestion module (Phase 1).
Fetches captions with start time and duration per line; handles deduplication and provides Whisper fallback.
"""

from dataclasses import dataclass
import re
from pathlib import Path
from typing import List, Optional, Any
from urllib.parse import urlparse, parse_qs
import logging

logger = logging.getLogger(__name__)


@dataclass
class TranscriptSnippet:
    text: str
    start: float  # in seconds
    duration: float  # in seconds

    @property
    def end(self) -> float:
        return round(self.start + self.duration, 2)


def extract_video_id(url_or_id: str) -> str:
    """Extract 11-character YouTube video ID from various URL formats or raw ID."""
    url_or_id = url_or_id.strip()
    if re.match(r"^[a-zA-Z0-9_-]{11}$", url_or_id):
        return url_or_id

    parsed = urlparse(url_or_id)
    if parsed.netloc in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            qs = parse_qs(parsed.query)
            if "v" in qs and qs["v"]:
                return qs["v"][0]
        elif parsed.path.startswith("/embed/"):
            return parsed.path.split("/")[2]
        elif parsed.path.startswith("/v/"):
            return parsed.path.split("/")[2]
        elif parsed.path.startswith("/shorts/"):
            return parsed.path.split("/")[2]
    elif parsed.netloc in ("youtu.be", "www.youtu.be"):
        path_parts = parsed.path.lstrip("/").split("/")
        if path_parts and path_parts[0]:
            return path_parts[0]

    # Regex search as fallback
    match = re.search(r"(?:v=|\/)([a-zA-Z0-9_-]{11})(?:[&?]|$)", url_or_id)
    if match:
        return match.group(1)

    raise ValueError(f"Could not extract a valid YouTube video ID from '{url_or_id}'.")


def clean_transcript_lines(snippets: List[TranscriptSnippet]) -> List[TranscriptSnippet]:
    """
    Safeguard for auto-captions:
    1. Deduplicate consecutive repeated text or overlapping prefixes.
    2. Remove empty snippets and strip unneeded formatting artifacts.
    """
    cleaned: List[TranscriptSnippet] = []
    
    for snip in snippets:
        text = snip.text.replace("\n", " ").strip()
        # Remove music tags like [Music] or [Applause] if they're the entire content
        if not text or text.lower() in {"[music]", "[applause]", "(music)", "(applause)"}:
            continue
            
        if not cleaned:
            cleaned.append(TranscriptSnippet(text=text, start=round(snip.start, 2), duration=round(snip.duration, 2)))
            continue

        prev = cleaned[-1]
        
        # Check if current line is identical to previous
        if text.lower() == prev.text.lower():
            # Extend duration of previous
            prev.duration = round(max(prev.duration, (snip.start + snip.duration) - prev.start), 2)
            continue

        # Check for overlapping prefix/suffix in auto-captions
        # e.g., Prev: "in this tutorial we will" Next: "we will learn python"
        prev_words = prev.text.split()
        curr_words = text.split()
        
        # Check if next line is already fully contained in prev line
        if text.lower() in prev.text.lower():
            continue
            
        # Check if prev line is a prefix of curr line
        if text.lower().startswith(prev.text.lower()):
            prev.text = text
            prev.duration = round(max(prev.duration, (snip.start + snip.duration) - prev.start), 2)
            continue

        # Check for partial word overlap at boundary (up to 5 words)
        max_overlap_words = min(len(prev_words), len(curr_words), 6)
        overlap_found = False
        for k in range(max_overlap_words, 1, -1):
            if [w.lower() for w in prev_words[-k:]] == [w.lower() for w in curr_words[:k]]:
                # Merge current snippet without repeating overlapping words
                new_text = " ".join(curr_words[k:])
                if new_text:
                    cleaned.append(TranscriptSnippet(text=new_text, start=round(snip.start, 2), duration=round(snip.duration, 2)))
                overlap_found = True
                break

        if not overlap_found:
            cleaned.append(TranscriptSnippet(text=text, start=round(snip.start, 2), duration=round(snip.duration, 2)))

    return cleaned


def ensure_ffmpeg() -> Optional[str]:
    """Ensure ffmpeg is discoverable on PATH for yt-dlp and whisper across Windows & Linux."""
    import os
    import shutil
    import sys

    # Check if already on PATH (e.g. system ffmpeg installed on Render/Linux)
    existing = shutil.which("ffmpeg")
    if existing:
        return existing

    exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    workspace_bin = Path(__file__).resolve().parent.parent.parent / "bin"
    local_ffmpeg = workspace_bin / exe_name
    if local_ffmpeg.exists():
        os.environ["PATH"] = str(workspace_bin) + os.pathsep + os.environ.get("PATH", "")
        return str(local_ffmpeg)

    # Check imageio_ffmpeg fallback (works on both Windows and Linux)
    try:
        import imageio_ffmpeg
        ffmpeg_src = imageio_ffmpeg.get_ffmpeg_exe()
        workspace_bin.mkdir(parents=True, exist_ok=True)
        if not local_ffmpeg.exists():
            shutil.copy2(ffmpeg_src, str(local_ffmpeg))
            if sys.platform != "win32":
                os.chmod(str(local_ffmpeg), 0o755)
        os.environ["PATH"] = str(workspace_bin) + os.pathsep + os.environ.get("PATH", "")
        return str(local_ffmpeg)
    except Exception:
        pass

    return None


def fetch_whisper_fallback(video_id: str) -> List[TranscriptSnippet]:
    """
    Fallback when captions are missing, disabled, or too short.
    Uses yt-dlp to download audio and runs speech-to-text with Whisper.
    """
    logger.info("Attempting Whisper fallback for video: %s", video_id)
    ensure_ffmpeg()
    
    # Check if whisper or faster-whisper is installed
    whisper_module = None
    try:
        import whisper
        whisper_module = "openai-whisper"
    except ImportError:
        try:
            import faster_whisper
            whisper_module = "faster-whisper"
        except ImportError:
            raise RuntimeError(
                f"YouTube captions are unavailable or disabled for video '{video_id}'. "
                "To enable speech-to-text audio fallback, install 'openai-whisper' "
                "(pip install openai-whisper) and ensure ffmpeg is available."
            )

    try:
        import tempfile
        import os
        import subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, f"{video_id}.mp3")
            cmd = [
                "yt-dlp",
                "-x",
                "--audio-format", "mp3",
                "-o", audio_path,
                f"https://www.youtube.com/watch?v={video_id}"
            ]
            subprocess.run(cmd, check=True, capture_output=True)

            snippets: List[TranscriptSnippet] = []
            if whisper_module == "openai-whisper":
                import whisper
                model = whisper.load_model("base")
                result = model.transcribe(audio_path)
                for seg in result.get("segments", []):
                    snippets.append(TranscriptSnippet(
                        text=seg["text"].strip(),
                        start=round(float(seg["start"]), 2),
                        duration=round(float(seg["end"]) - float(seg["start"]), 2)
                    ))
            elif whisper_module == "faster-whisper":
                from faster_whisper import WhisperModel
                model = WhisperModel("base", compute_type="int8")
                segments, _ = model.transcribe(audio_path)
                for seg in segments:
                    snippets.append(TranscriptSnippet(
                        text=seg.text.strip(),
                        start=round(float(seg.start), 2),
                        duration=round(float(seg.end) - float(seg.start), 2)
                    ))

            return clean_transcript_lines(snippets)
    except Exception as e:
        logger.warning("Whisper fallback failed: %s", e)
        raise RuntimeError(f"Whisper speech-to-text failed for video '{video_id}': {e}")


def _get_snippet_field(item: Any, field_name: str, default: Any = None) -> Any:
    """Helper to extract text/start/duration across different youtube-transcript-api object/dict versions."""
    if isinstance(item, dict):
        return item.get(field_name, default)
    return getattr(item, field_name, default)


def fetch_transcript(url_or_id: str, languages: Optional[List[str]] = None) -> List[TranscriptSnippet]:
    """
    Main entry point for Phase 1:
    Fetches captions with start time and duration per line.
    Supports both modern youtube-transcript-api (>=1.0.0) and legacy API styles.
    Prefers manual captions, falls back to auto-generated captions, then Whisper.
    """
    import youtube_transcript_api
    from youtube_transcript_api import YouTubeTranscriptApi

    if languages is None:
        languages = ["en", "en-US", "en-GB"]

    video_id = extract_video_id(url_or_id)
    raw_snippets = []

    try:
        # Check if modern instantiated API (>=1.0.0) or legacy static API (<1.0.0)
        api = YouTubeTranscriptApi() if isinstance(YouTubeTranscriptApi, type) and hasattr(YouTubeTranscriptApi, 'list') else None

        transcript_list = None
        if api and hasattr(api, "list"):
            transcript_list = api.list(video_id)
        elif hasattr(YouTubeTranscriptApi, "list_transcripts"):
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        selected_transcript = None
        if transcript_list:
            # 1. Prefer manual caption in requested languages
            for lang in languages:
                try:
                    selected_transcript = transcript_list.find_manually_created_transcript([lang])
                    if selected_transcript:
                        break
                except Exception:
                    pass

            # 2. Fall back to auto-generated caption in requested languages
            if not selected_transcript:
                for lang in languages:
                    try:
                        selected_transcript = transcript_list.find_generated_transcript([lang])
                        if selected_transcript:
                            break
                    except Exception:
                        pass

            # 3. Fall back to any available transcript and translate to English
            if not selected_transcript:
                try:
                    for t in transcript_list:
                        if hasattr(t, "is_translatable") and t.is_translatable:
                            selected_transcript = t.translate("en")
                            break
                        else:
                            selected_transcript = t
                            break
                except Exception:
                    pass

        # If transcript found, fetch snippets
        if selected_transcript:
            raw_data = selected_transcript.fetch()
            raw_snippets = [
                TranscriptSnippet(
                    text=_get_snippet_field(item, "text", ""),
                    start=float(_get_snippet_field(item, "start", 0.0)),
                    duration=float(_get_snippet_field(item, "duration", 0.0))
                )
                for item in raw_data
            ]
        elif api and hasattr(api, "fetch"):
            # Direct fetch fallback
            raw_data = api.fetch(video_id, languages=languages)
            raw_snippets = [
                TranscriptSnippet(
                    text=_get_snippet_field(item, "text", ""),
                    start=float(_get_snippet_field(item, "start", 0.0)),
                    duration=float(_get_snippet_field(item, "duration", 0.0))
                )
                for item in raw_data
            ]
        elif hasattr(YouTubeTranscriptApi, "get_transcript"):
            raw_data = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
            raw_snippets = [
                TranscriptSnippet(
                    text=_get_snippet_field(item, "text", ""),
                    start=float(_get_snippet_field(item, "start", 0.0)),
                    duration=float(_get_snippet_field(item, "duration", 0.0))
                )
                for item in raw_data
            ]

    except Exception as e:
        logger.info("Direct transcript retrieval failed (%s). Triggering fallback...", e)
        return fetch_whisper_fallback(video_id)

    # Detect empty or very short transcript (< 3 snippets) and fallback
    if len(raw_snippets) < 3:
        try:
            return fetch_whisper_fallback(video_id)
        except Exception:
            pass

    return clean_transcript_lines(raw_snippets)
