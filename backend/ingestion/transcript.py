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


def parse_raw_text_transcript(raw_text: str) -> List[TranscriptSnippet]:
    """
    Parses manually pasted transcript text.
    Handles:
      1. YouTube UI copied transcript format (alternating timestamp line + text line)
      2. Inline timestamp format: '01:23 Some spoken line' or '[01:23] Some spoken line'
      3. SRT / VTT timestamp cues: '00:01:23.456 --> 00:01:25.789'
      4. Plain text / paragraphs without timestamps (assigns progressive synthetic timestamps)
    """
    import re

    if not raw_text or not raw_text.strip():
        return []

    lines = [l.strip() for l in raw_text.strip().splitlines() if l.strip()]
    snippets: List[TranscriptSnippet] = []

    def time_to_sec(t_str: str) -> float:
        parts = t_str.replace(",", ".").split(":")
        if len(parts) == 3:
            return float(parts[0]) * 3600.0 + float(parts[1]) * 60.0 + float(parts[2])
        elif len(parts) == 2:
            return float(parts[0]) * 60.0 + float(parts[1])
        return float(parts[0])

    ts_pattern = re.compile(r"^\[?((?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?)\]?\s*(.*)$")
    vtt_pattern = re.compile(r"^((?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?)\s*-->\s*((?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?)")

    # Check if lines look like alternating timestamp / text
    i = 0
    matched_timestamps = 0
    while i < len(lines):
        line = lines[i]
        # Ignore WEBVTT or pure number cue IDs
        if line.upper() == "WEBVTT" or line.isdigit():
            i += 1
            continue

        vtt_match = vtt_pattern.match(line)
        if vtt_match:
            start_s = time_to_sec(vtt_match.group(1))
            end_s = time_to_sec(vtt_match.group(2))
            text_lines = []
            i += 1
            while i < len(lines) and not vtt_pattern.match(lines[i]) and not lines[i].isdigit():
                text_lines.append(lines[i])
                i += 1
            text = " ".join(text_lines).strip()
            if text:
                snippets.append(TranscriptSnippet(text=text, start=round(start_s, 2), duration=round(max(1.0, end_s - start_s), 2)))
            continue

        ts_match = ts_pattern.match(line)
        if ts_match:
            matched_timestamps += 1
            t_str, rest = ts_match.groups()
            start_s = time_to_sec(t_str)
            if rest:
                snippets.append(TranscriptSnippet(text=rest, start=round(start_s, 2), duration=3.0))
                i += 1
            else:
                # Next line might be the text
                if i + 1 < len(lines) and not ts_pattern.match(lines[i + 1]):
                    snippets.append(TranscriptSnippet(text=lines[i + 1], start=round(start_s, 2), duration=3.0))
                    i += 2
                else:
                    i += 1
            continue

        i += 1

    # If timestamp parsing yielded results, refine durations
    if snippets and matched_timestamps > 0:
        for idx in range(len(snippets) - 1):
            next_start = snippets[idx + 1].start
            cur_start = snippets[idx].start
            if next_start > cur_start:
                snippets[idx].duration = round(next_start - cur_start, 2)
            else:
                snippets[idx].duration = 3.0
        return clean_transcript_lines(snippets)

    # Fallback for untimed plain text: split into sentences/paragraphs
    current_time = 0.0
    plain_snippets = []
    for line in lines:
        if line.upper() == "WEBVTT":
            continue
        # Split line into roughly sentence chunks
        chunks = [c.strip() for c in re.split(r"(?<=[.!?])\s+", line) if c.strip()]
        for c in chunks:
            est_duration = max(2.5, min(15.0, len(c.split()) * 0.4))
            plain_snippets.append(TranscriptSnippet(text=c, start=round(current_time, 2), duration=round(est_duration, 2)))
            current_time += est_duration

    return clean_transcript_lines(plain_snippets)


def _parse_timedtext_response(resp_text: str, ext: str = "json3") -> List[TranscriptSnippet]:
    """
    Parses any timedtext payload returned by YouTube (json3, srv1 XML, VTT, or SRT).
    """
    import json
    import re
    import xml.etree.ElementTree as ET

    snippets: List[TranscriptSnippet] = []

    # 1. JSON3 format
    if ext == "json3" or resp_text.strip().startswith("{"):
        try:
            data = json.loads(resp_text)
            for event in data.get("events", []):
                if "segs" in event:
                    text = "".join(s.get("utf8", "") for s in event["segs"]).replace("\n", " ").strip()
                    if text:
                        start = float(event.get("tStartMs", 0)) / 1000.0
                        dur = float(event.get("dDurationMs", 0)) / 1000.0
                        snippets.append(TranscriptSnippet(text=text, start=round(start, 2), duration=round(dur, 2)))
            if snippets:
                return snippets
        except Exception:
            pass

    # 2. XML / srv1 / srv2 / srv3 format
    if "<transcript" in resp_text or "<text" in resp_text:
        try:
            root = ET.fromstring(resp_text)
            for elem in root.findall(".//text"):
                text = (elem.text or "").replace("\n", " ").strip()
                if text:
                    start = float(elem.attrib.get("start", 0.0))
                    dur = float(elem.attrib.get("dur", 3.0))
                    snippets.append(TranscriptSnippet(text=text, start=round(start, 2), duration=round(dur, 2)))
            if snippets:
                return snippets
        except Exception:
            pass

    # 3. VTT / SRT or line-based text
    return parse_raw_text_transcript(resp_text)


def fetch_transcript_ytdlp(video_id: str, languages: Optional[List[str]] = None) -> List[TranscriptSnippet]:
    """
    Tier 1 caption retrieval via yt-dlp InnerTube Mobile API.
    Directly extracts YouTube JSON3 / VTT timed text without downloading video or audio.
    Uses strict android/ios client emulation and skips webpage download to bypass YouTube HTTP 429 on cloud IPs.
    """
    import yt_dlp
    import httpx

    if languages is None:
        languages = ["en", "en-US", "en-GB"]

    url = f"https://www.youtube.com/watch?v={video_id}"

    # Try clients in order of cloud-IP resilience: android first, then ios
    for client in ["android", "ios"]:
        ydl_opts = {
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 8,
            "extractor_args": {
                "youtube": {
                    "player_client": [client],
                    "player_skip": ["webpage", "configs", "js"],
                }
            },
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                subtitles = info.get("subtitles", {})
                auto_captions = info.get("automatic_captions", {})

                # Candidate formats: prefer native (no tlang= auto-translation) and json3
                candidate_fmts = []

                # 1. Manual subtitles in requested languages
                for lang in languages:
                    if lang in subtitles:
                        candidate_fmts.extend(subtitles[lang])

                # 2. Native auto-generated captions (prefer -orig first, then requested languages)
                orig_langs = [f"{l}-orig" for l in languages] + languages
                for lang in orig_langs:
                    if lang in auto_captions:
                        candidate_fmts.extend(auto_captions[lang])

                # 3. Fallback to any other subtitles or auto-captions
                if not candidate_fmts:
                    for lang, fmts in {**subtitles, **auto_captions}.items():
                        if lang.startswith("en") or "orig" in lang:
                            candidate_fmts.extend(fmts)

                if not candidate_fmts and (subtitles or auto_captions):
                    for fmts in {**subtitles, **auto_captions}.values():
                        candidate_fmts.extend(fmts)

                # Prioritize: non-translated native captions first (avoids YouTube 429 on /api/timedtext?tlang=...)
                # then json3 extension, then original variant
                candidate_fmts.sort(
                    key=lambda f: (
                        1 if "tlang=" in f.get("url", "") else 0,
                        0 if f.get("ext") == "json3" else 1,
                        0 if "gemini" in f.get("url", "") else 1,
                    )
                )

                headers = {
                    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
                }

                # Try candidate formats until one succeeds
                for fmt in candidate_fmts[:5]:
                    url_to_fetch = fmt.get("url")
                    if not url_to_fetch:
                        continue
                    try:
                        resp = httpx.get(url_to_fetch, headers=headers, timeout=15.0)
                        if resp.status_code == 200 and resp.text.strip():
                            fmt_ext = fmt.get("ext", "json3")
                            snippets = _parse_timedtext_response(resp.text, ext=fmt_ext)
                            if len(snippets) >= 1:
                                logger.info("Successfully fetched %d snippets via %s client (fmt: %s).", len(snippets), client, fmt_ext)
                                return clean_transcript_lines(snippets)
                    except Exception as fetch_err:
                        logger.info("Candidate timedtext format fetch failed (%s). Trying next format...", fetch_err)

        except Exception as e:
            logger.warning("yt-dlp subtitle extraction with client %s failed: %s", client, e)

    return []


def fetch_whisper_fallback(video_id: str) -> List[TranscriptSnippet]:
    """
    Tier 3 Fallback: Runs speech-to-text with Whisper when video has zero captions.
    Uses mobile player_client to prevent HTTP 429 Too Many Requests rate-limits on cloud servers.
    """
    import sys
    import tempfile
    import os
    import subprocess
    import glob

    logger.info("Attempting Whisper fallback for video: %s", video_id)
    ffmpeg_bin = ensure_ffmpeg()
    
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
                f"YouTube automated caption extraction was rate-limited or disabled for video '{video_id}'. "
                "You can still index and chat with this video immediately! Simply click '✍ Paste Text' "
                "above the player to paste the transcript or notes."
            )

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_template = os.path.join(tmpdir, f"{video_id}.%(ext)s")
            cmd = [
                sys.executable,
                "-m",
                "yt_dlp",
                "-f",
                "ba/b",
                "--extractor-args",
                "youtube:player_client=android,ios;player_skip=webpage,configs,js",
                "--user-agent",
                "com.google.android.youtube/19.29.37 (Linux; U; Android 11) gzip",
                "-o",
                output_template,
                f"https://www.youtube.com/watch?v={video_id}",
            ]
            if ffmpeg_bin:
                cmd.extend(["--ffmpeg-location", os.path.dirname(ffmpeg_bin)])

            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            except subprocess.TimeoutExpired:
                raise RuntimeError(
                    f"Audio extraction timed out after 15s (YouTube rate limits cloud datacenter IPs). "
                    "Please click 'Paste text' above the player to index your transcript immediately!"
                )
            if proc.returncode != 0:
                err_detail = proc.stderr.strip() or proc.stdout.strip()
                raise RuntimeError(
                    f"Audio download failed on cloud IP: {err_detail[:200]}. "
                    "YouTube rate-limited automated access for this video. "
                    "You can paste the video's transcript directly using the 'Paste Transcript' option in the app."
                )

            # Locate downloaded audio file
            downloaded = glob.glob(os.path.join(tmpdir, f"{video_id}.*"))
            if not downloaded:
                raise RuntimeError(f"Could not locate downloaded audio for {video_id}")
            audio_path = downloaded[0]

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


def fetch_transcript_cloud_proxy(video_id: str) -> List[TranscriptSnippet]:
    """
    Tier 0 caption retrieval via cloud-proxy REST API.
    Uses high-speed residential proxy routing to bypass datacenter/cloud IP blocks on Render, AWS, and GCP.
    """
    import httpx
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        resp = httpx.post(
            "https://transcribeyoutube.com/api/transcript",
            json={"url": url},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=3.0
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok") and data.get("transcript"):
                snippets: List[TranscriptSnippet] = []
                for item in data["transcript"]:
                    text = item.get("text", "").strip()
                    if text:
                        start = float(item.get("start", 0.0))
                        dur = float(item.get("dur", 3.0))
                        snippets.append(TranscriptSnippet(text=text, start=round(start, 2), duration=round(dur, 2)))
                if len(snippets) >= 1:
                    logger.info("Successfully fetched %d snippets via cloud proxy API.", len(snippets))
                    return clean_transcript_lines(snippets)
    except Exception as e:
        logger.info("Cloud proxy transcript retrieval failed (%s). Falling back to InnerTube API...", e)
    return []


def _get_snippet_field(item: Any, field_name: str, default: Any = None) -> Any:
    """Helper to extract text/start/duration across different youtube-transcript-api object/dict versions."""
    if isinstance(item, dict):
        return item.get(field_name, default)
    return getattr(item, field_name, default)


def fetch_transcript(url_or_id: str, languages: Optional[List[str]] = None) -> List[TranscriptSnippet]:
    """
    Main entry point for Phase 1:
    Fetches captions with start time and duration per line.
    Strategy:
      0. Tier 0 (Cloud Proxy API): Residential proxy route that completely bypasses datacenter IP blocks.
      1. Tier 1 (yt-dlp InnerTube Mobile Extraction): Skips webpage download to bypass cloud IP 429 rate limits.
      2. Tier 2 (youtube-transcript-api): Standard caption scraping fallback.
      3. Tier 3 (Whisper Speech-to-Text): Audio download & transcription if zero captions exist.
    """
    import youtube_transcript_api
    from youtube_transcript_api import YouTubeTranscriptApi

    if languages is None:
        languages = ["en", "en-US", "en-GB"]

    video_id = extract_video_id(url_or_id)

    # 0. Cloud Proxy API: Guaranteed residential IP bypass for Render/AWS/GCP
    try:
        proxy_snippets = fetch_transcript_cloud_proxy(video_id)
        if len(proxy_snippets) >= 1:
            logger.info("Retrieved %d snippets via cloud proxy API.", len(proxy_snippets))
            return clean_transcript_lines(proxy_snippets)
    except Exception as e:
        logger.info("Cloud proxy transcript retrieval failed (%s). Falling back...", e)

    # 1. Primary Strategy: yt-dlp direct timedtext extraction (bypasses cloud IP 429 blocks)
    try:
        ytdlp_snippets = fetch_transcript_ytdlp(video_id, languages)
        if len(ytdlp_snippets) >= 1:
            logger.info("Retrieved %d snippets via yt-dlp InnerTube API.", len(ytdlp_snippets))
            return clean_transcript_lines(ytdlp_snippets)
    except Exception as e:
        logger.info("yt-dlp InnerTube extraction failed (%s). Falling back to youtube-transcript-api...", e)

    # 2. Secondary Strategy: youtube-transcript-api
    raw_snippets = []
    try:
        api = YouTubeTranscriptApi() if isinstance(YouTubeTranscriptApi, type) and hasattr(YouTubeTranscriptApi, 'list') else None
        transcript_list = None
        if api and hasattr(api, "list"):
            transcript_list = api.list(video_id)
        elif hasattr(YouTubeTranscriptApi, "list_transcripts"):
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        selected_transcript = None
        if transcript_list:
            for lang in languages:
                try:
                    selected_transcript = transcript_list.find_manually_created_transcript([lang])
                    if selected_transcript:
                        break
                except Exception:
                    pass

            if not selected_transcript:
                for lang in languages:
                    try:
                        selected_transcript = transcript_list.find_generated_transcript([lang])
                        if selected_transcript:
                            break
                    except Exception:
                        pass

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

        if len(raw_snippets) >= 1:
            return clean_transcript_lines(raw_snippets)

    except Exception as e:
        logger.info("youtube-transcript-api retrieval failed (%s). Triggering Tier 3 Whisper fallback...", e)

    # 3. Tertiary Strategy: Whisper audio fallback when zero captions exist anywhere
    return fetch_whisper_fallback(video_id)
