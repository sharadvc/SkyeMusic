"""Smart Radio & Mood Generator for tune."""

from __future__ import annotations

MOOD_PRESETS: dict[str, str] = {
    "lofi": "lofi hip hop radio beats to relax study to",
    "chill": "chill vibes ambient lofi synthwave lounge",
    "workout": "workout beast mode motivation high bpm gym phonk",
    "phonk": "drift phonk aggression bass boosted workout",
    "bollywood": "best bollywood romantic hits arijit singh pritam",
    "retro": "80s synthpop classic rock retro pop hits",
    "cyberpunk": "cyberpunk synthwave darksynth electronic pulse",
    "sad": "sad emotional acoustic heartbreak late night vibes",
    "party": "party dance bangers club edm house hits",
    "gaming": "gaming music hype edm ncs dubstep electronic",
}


def resolve_radio_query(seed: str) -> str:
    """Resolve seed keyword or mood into an optimized radio search query."""
    s = (seed or "").lower().strip()
    if s in MOOD_PRESETS:
        return MOOD_PRESETS[s]
    return f"{seed} music playlist mix"
