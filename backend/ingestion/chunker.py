"""
Time-Aware Chunking module (Phase 2).
Merges transcript snippets into 30-60 second chunks (about 150-300 tokens)
with 10-20% overlap, preserving exact start timestamps.
"""

from dataclasses import dataclass
from typing import List
from .transcript import TranscriptSnippet


@dataclass
class VideoChunk:
    video_id: str
    chunk_id: str  # e.g., "C1", "C2"
    start_sec: float  # Start time of the FIRST caption line
    end_sec: float    # End time of the LAST caption line
    text: str

    @property
    def duration_sec(self) -> float:
        return round(self.end_sec - self.start_sec, 2)

    @property
    def formatted_start(self) -> str:
        return format_timestamp(self.start_sec)

    @property
    def formatted_end(self) -> str:
        return format_timestamp(self.end_sec)

    @property
    def labeled_header(self) -> str:
        """Standard format expected by LLM in Phase 5: [C12] (04:35-05:20)"""
        return f"[{self.chunk_id}] ({self.formatted_start}-{self.formatted_end})"


def format_timestamp(seconds: float) -> str:
    """Format seconds into MM:SS or HH:MM:SS string."""
    seconds = max(0, int(round(seconds)))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def chunk_transcript(
    video_id: str,
    snippets: List[TranscriptSnippet],
    target_duration: float = 45.0,  # Target between 30 and 60 seconds
    min_duration: float = 30.0,
    max_duration: float = 65.0,
    overlap_duration: float = 8.0,  # 10-20% overlap
    max_tokens: int = 300,
) -> List[VideoChunk]:
    """
    Merge lines into 30-60 second chunks with 10-20% overlap.
    Preserves the start_sec of the first snippet in each chunk.
    """
    if not snippets:
        return []

    chunks: List[VideoChunk] = []
    chunk_index = 1
    start_idx = 0
    n = len(snippets)

    while start_idx < n:
        curr_snippets: List[TranscriptSnippet] = []
        chunk_start_sec = snippets[start_idx].start
        current_words = 0
        end_idx = start_idx

        while end_idx < n:
            snip = snippets[end_idx]
            duration = (snip.start + snip.duration) - chunk_start_sec
            snip_word_count = len(snip.text.split())

            # Stop if adding this snippet exceeds max duration or max token limit
            # (unless it's the first snippet in the chunk)
            if curr_snippets and (duration > max_duration or (current_words + snip_word_count) > max_tokens):
                break

            curr_snippets.append(snip)
            current_words += snip_word_count
            end_idx += 1

            # If we've reached target duration or min duration with enough text, break
            if duration >= target_duration:
                break

        if not curr_snippets:
            curr_snippets = [snippets[start_idx]]
            end_idx = start_idx + 1

        last_snip = curr_snippets[-1]
        chunk_end_sec = round(last_snip.start + last_snip.duration, 2)
        chunk_text = " ".join(s.text.strip() for s in curr_snippets if s.text.strip())

        chunks.append(
            VideoChunk(
                video_id=video_id,
                chunk_id=f"C{chunk_index}",
                start_sec=round(chunk_start_sec, 2),
                end_sec=chunk_end_sec,
                text=chunk_text,
            )
        )
        chunk_index += 1

        if end_idx >= n:
            break

        # Calculate next start_idx with overlap_duration
        # Walk backwards from end_idx to find the snippet that starts around (chunk_end_sec - overlap_duration)
        overlap_target_sec = chunk_end_sec - overlap_duration
        next_start_idx = end_idx
        for idx in range(end_idx - 1, start_idx, -1):
            if snippets[idx].start >= overlap_target_sec:
                next_start_idx = idx
            else:
                break

        # Ensure forward progress
        if next_start_idx <= start_idx:
            next_start_idx = start_idx + 1

        start_idx = next_start_idx

    return chunks
