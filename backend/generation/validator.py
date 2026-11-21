"""
Citation Validation module (Phase 6).
Validates every cited chunk ID against the retrieved chunk set,
drops hallucinated IDs, and maps valid IDs to start_sec with a 2-3 second lead-in.
"""

from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
from backend.storage.vector_store import StoredChunk


@dataclass
class ValidatedCitation:
    chunk_id: str
    start_sec: float
    end_sec: float
    formatted_start: str
    formatted_end: str
    seek_sec: float  # With 2-3 second lead-in safeguard
    label: str       # e.g., "▶ 04:35"
    snippet_preview: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def validate_and_enrich_citations(
    cited_ids: List[str],
    retrieved_chunks: List[StoredChunk],
    lead_in_seconds: float = 2.5,
) -> List[ValidatedCitation]:
    """
    Safeguards:
    1. A made-up or hallucinated ID is dropped, never shown.
    2. Maps valid chunk ID to exact start_sec.
    3. Computes seek_sec with 2-3s lead-in so user doesn't land mid-sentence.
    """
    retrieved_map = {c.chunk_id: c for c in retrieved_chunks}
    validated: List[ValidatedCitation] = []
    seen = set()

    for raw_id in cited_ids:
        # Clean ID string (e.g., "[C1]" -> "C1", "c1" -> "C1")
        clean_id = raw_id.strip(" [](),").upper()
        if not clean_id.startswith("C"):
            clean_id = f"C{clean_id}"

        if clean_id in seen:
            continue

        if clean_id in retrieved_map:
            chunk = retrieved_map[clean_id]
            seek_sec = max(0.0, round(chunk.start_sec - lead_in_seconds, 1))
            preview = chunk.text[:120] + "..." if len(chunk.text) > 120 else chunk.text
            validated.append(
                ValidatedCitation(
                    chunk_id=chunk.chunk_id,
                    start_sec=chunk.start_sec,
                    end_sec=chunk.end_sec,
                    formatted_start=chunk.formatted_start,
                    formatted_end=chunk.formatted_end,
                    seek_sec=seek_sec,
                    label=f"▶ {chunk.formatted_start}",
                    snippet_preview=preview,
                )
            )
            seen.add(clean_id)
        else:
            # Dropped made-up ID per design rule 3 and Phase 6 Done condition
            pass

    return validated
