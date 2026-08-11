"""Local-first music intelligence: moods, radio, similarity, discovery.

Everything here is derived from yt-dlp metadata + the user's own history and
favorites. No accounts, no cloud, no hardcoded playlists. Search/related/resolve
are injected (the daemon passes its cached, semaphore-limited versions) so this
module stays pure and testable.
"""

from __future__ import annotations

import random
import re

from . import resolver
from .queue import Track

# A mood maps to several *seed search angles*. The engine searches a few of
# them, then ranks/dedupes with the user's listening signals. Not a fixed list.
MOODS: dict[str, dict] = {
    "focus":     {"seeds": ["lofi focus", "ambient study", "deep focus", "piano concentration", "instrumental focus"]},
    "energetic": {"seeds": ["high energy", "upbeat workout", "electronic dance", "party banger"]},
    "calm":      {"seeds": ["calm acoustic", "soft ambient", "relaxing piano", "mellow"]},
    "chill":     {"seeds": ["chill rnb", "chill hip hop", "lo-fi", "chillout", "mellow beats"]},
    "nostalgic": {"seeds": ["2000s hits", "90s classics", "80s synthpop", "old school hip hop"]},
    "sad":       {"seeds": ["sad acoustic", "heartbreak songs", "emotional ballads", "sad piano"]},
    "happy":     {"seeds": ["happy pop", "feel good", "summer hits", "uplifting pop"]},
    "party":     {"seeds": ["party hits", "club mix", "dance party", "hip hop party"]},
    "workout":   {"seeds": ["workout mix", "gym music", "high intensity training", "cardio"]},
    "sleep":     {"seeds": ["sleep music", "rain sounds", "deep sleep", "ambient night"]},
    "romantic":  {"seeds": ["romantic songs", "love songs", "r&b love", "ballads"]},
    "morning":   {"seeds": ["morning music", "sunny pop", "coffee shop", "acoustic morning"]},
    "night":     {"seeds": ["late night", "night drive", "midnight rnb", "dark ambient"]},
    "roadtrip":  {"seeds": ["road trip", "driving songs", "windows down", "alt rock"]},
}

MOOD_ALIASES: dict[str, str] = {
    "study": "focus", "studying": "focus", "work": "focus", "concentrate": "focus",
    "deep work": "focus", "homework": "focus",
    "gym": "workout", "training": "workout", "exercise": "workout", "running": "workout",
    "relax": "calm", "relaxing": "calm", "chillout": "chill", "mellow": "chill",
    "sad songs": "sad", "sad music": "sad", "heartbreak": "sad",
    "happy songs": "happy", "feel good": "happy", "feel-good": "happy",
    "party songs": "party", "club": "party",
    "sleeping": "sleep", "nap": "sleep",
    "date night": "romantic", "love songs": "romantic",
    "good morning": "morning",
    "late night": "night", "night drive": "night",
    "road trip": "roadtrip", "driving": "roadtrip",
    "nostalgia": "nostalgic", "old songs": "nostalgic",
}

# Language aliases -> canonical qualifier (appended to mood seed searches).
LANGUAGES: dict[str, list[str]] = {
    "hindi": ["hindi", "hindustani"],
    "english": ["english", "eng"],
    "punjabi": ["punjabi"],
    "tamil": ["tamil", "tam"],
    "telugu": ["telugu"],
    "kannada": ["kannada"],
    "malayalam": ["malayalam"],
    "bengali": ["bengali", "bangla"],
    "marathi": ["marathi"],
    "gujarati": ["gujarati"],
    "urdu": ["urdu"],
    "odia": ["odia", "oriya"],
    "assamese": ["assamese"],
    "nepali": ["nepali"],
    "spanish": ["spanish"],
    "french": ["french"],
    "korean": ["korean"],
    "japanese": ["japanese"],
    "chinese": ["chinese", "mandarin"],
    "arabic": ["arabic"],
    "portuguese": ["portuguese"],
    "russian": ["russian"],
    "turkish": ["turkish"],
    "german": ["german"],
    "italian": ["italian"],
    "latin": ["latin"],
    "indonesian": ["indonesian"],
    "thai": ["thai"],
}

_LANG_ALIAS: dict[str, str] = {
    alias: lang for lang, aliases in LANGUAGES.items() for alias in aliases
}


def _lang_for(word: str) -> str | None:
    return _LANG_ALIAS.get((word or "").strip().lower())


def parse_mood_arg(arg: str) -> tuple[str | None, str | None, str | None]:
    """`tune mood <mood> [language] [artist alias]` -> (mood, lang, artist)."""
    tokens = (arg or "").split()
    if not tokens:
        return None, None, None
    mood = _mood_for(tokens[0])
    if mood is None:
        return None, None, None
    rest = list(tokens[1:])
    lang = None
    for i, t in enumerate(rest):
        lg = _lang_for(t)
        if lg:
            lang = lg
            rest.pop(i)
            break
    artist = " ".join(rest).strip() or None
    return mood, lang, artist

_DISCOVERY_SEEDS = [
    "new music this week", "underrated songs", "indie discovery",
    "fresh tracks", "hidden gems", "up and coming artist", "album cuts",
]

# "chill rnb", "sad hip hop", "energetic pop" -> first token is the mood
_GENRE_TOKENS = {
    "rnb", "hip", "hop", "pop", "rock", "acoustic", "piano", "jazz", "electronic",
    "edm", "rap", "indie", "folk", "metal", "classical", "techno", "house",
    "soul", "blues", "country", "reggae", "ambient", "lo-fi", "lofi", "beats",
    "music", "dance",
}


def _mood_for(word: str) -> str | None:
    w = (word or "").strip().lower()
    if w in MOODS:
        return w
    return MOOD_ALIASES.get(w)


def _first_token_mood(low: str) -> str | None:
    """'chill rnb' / 'sad hip hop' / 'sad hindi' -> the leading mood word when it's
    followed by a genre or language token. Avoids hijacking queries like
    'happy birthday'."""
    tokens = re.split(r"[\s,]+", low)
    if len(tokens) >= 2 and tokens[0] in MOODS:
        if any(t in _GENRE_TOKENS or _lang_for(t) for t in tokens[1:3]):
            return tokens[0]
    return None


def detect_intent(query: str) -> tuple[str, str]:
    """Classify a free-text request -> (kind, payload).

    kind is one of: exact | similar | radio | mood | search.
    """
    q = (query or "").strip()
    low = q.lower()
    if not q:
        return ("search", q)
    if resolver._is_url_or_id(q):
        return ("exact", q)
    # "similar to X"
    mt = re.search(r"similar\s+to\s+(.+)$", low)
    if mt:
        return ("similar", mt.group(1).strip())
    # "something/songs/music/tracks like X"
    mt = re.search(r"(?:something|songs|music|tracks|song)\s+like\s+(.+)$", low)
    if mt:
        return ("radio", mt.group(1).strip())
    # single-token mood ("focus", "chill", "studying")
    tokens = [t for t in re.split(r"[\s,]+", low) if t]
    if len(tokens) == 1:
        mm = _mood_for(tokens[0])
        if mm:
            return ("mood", mm)
    # "some X" / "play some X"
    mt = re.search(r"^(?:play\s+)?some\s+(.+)$", low)
    if mt:
        mm = _mood_for(mt.group(1).strip())
        if mm:
            return ("mood", mm)
    # "<mood> [lang] songs / music / playlist" e.g. "sad hindi songs"
    mt = re.search(r"(?:^|\s)([a-z][a-z-]*)\s+([a-z][a-z-]*)?\s*(?:songs|music|playlist)$", low)
    if mt:
        mm = _mood_for(mt.group(1))
        if mm:
            payload = mt.group(1)
            if mt.group(2) and _lang_for(mt.group(2)):
                payload += " " + _lang_for(mt.group(2))
            return ("mood", payload)
    # "for studying / work / the gym"
    mt = re.search(r"for\s+(?:the\s+)?([a-z ]+?)(?:\s+music|\s+songs)?$", low)
    if mt:
        mm = _mood_for(mt.group(1).strip())
        if mm:
            return ("mood", mm)
    # multi-word alias phrases ("late night", "feel good", "road trip")
    for phrase, mood in MOOD_ALIASES.items():
        if " " in phrase and phrase in low:
            return ("mood", mood)
    # leading mood word + genre token ("chill rnb", "sad hip hop")
    mm = _first_token_mood(low)
    if mm:
        return ("mood", mm)
    return ("search", q)


# --- session builders --------------------------------------------------------

def _search_many(seeds: list[str], search_fn, per_seed: int = 6) -> list[Track]:
    out: list[Track] = []
    for s in seeds:
        try:
            out.extend(search_fn(s, limit=per_seed))
        except Exception:
            continue
    return out


def build_mood_session(mood: str, search_fn, limit: int = 16,
                       avoid=None, prefs=None, lang: str | None = None,
                       artist: str | None = None) -> list[Track]:
    cfg = MOODS.get(mood) or MOODS["chill"]
    seeds = random.sample(cfg["seeds"], k=min(3, len(cfg["seeds"])))
    if lang:
        seeds = [f"{s} {lang}" for s in seeds]
    if artist:
        seeds = [f"{s} {artist}" for s in seeds]
    tracks = _search_many(seeds, search_fn, per_seed=6)
    if artist:
        prefs = dict(prefs or {})
        prefs["artist"] = artist
    return rank_tracks(tracks, limit=limit, avoid=avoid, prefs=prefs)


def _resolve_one(seed: str, resolve_fn, search_fn) -> Track | None:
    """Resolve a seed (artist/song/URL) to a single Track, if possible."""
    try:
        base = resolve_fn(seed)
        if isinstance(base, list):
            base = base[0] if base else None
        if base:
            return base
    except Exception:
        pass
    try:
        hits = search_fn(seed, limit=1)
        return hits[0] if hits else None
    except Exception:
        return None


def build_radio_session(seed: str, resolve_fn, radio_fn, search_fn,
                        limit: int = 16, avoid=None, prefs=None) -> list[Track]:
    tracks: list[Track] = []
    base = _resolve_one(seed, resolve_fn, search_fn)
    if base:
        tracks = list(radio_fn(base.url))
        tracks.insert(0, base)
    tracks += _search_many([seed], search_fn, per_seed=6)
    return rank_tracks(tracks, limit=limit, avoid=avoid, prefs=prefs)


def build_similar_session(seed: str, resolve_fn, radio_fn, search_fn,
                          limit: int = 16, avoid=None, prefs=None) -> list[Track]:
    tracks: list[Track] = []
    base = _resolve_one(seed, resolve_fn, search_fn)
    if base:
        tracks = list(radio_fn(base.url))
        tracks.insert(0, base)
    if not tracks:
        tracks = _search_many([seed], search_fn, per_seed=8)
    return rank_tracks(tracks, limit=limit, avoid=avoid, prefs=prefs)


def build_discovery_session(search_fn, limit: int = 16, seen=None,
                            avoid=None, prefs=None) -> list[Track]:
    seeds = random.sample(_DISCOVERY_SEEDS, k=min(3, len(_DISCOVERY_SEEDS)))
    tracks = _search_many(seeds, search_fn, per_seed=6)
    if seen:
        tracks = [t for t in tracks if t.url not in seen]
    return rank_tracks(tracks, limit=limit, avoid=avoid, prefs=prefs)


def rank_tracks(tracks: list[Track], limit: int = 16,
                avoid=None, prefs=None) -> list[Track]:
    """Dedupe by url, drop anything in `avoid` (queued/recently played), then
    boost the user's preferred artists and reorder."""
    avoid = avoid or set()
    prefs = prefs or {}
    fav = {a.lower() for a in prefs.get("fav_artists", set())}
    top = prefs.get("top_artists", {})  # channel -> weight
    want_artist = (prefs.get("artist") or "").lower()

    seen: set[str] = set()
    out: list[Track] = []
    for t in tracks:
        if not t.url or t.url in seen or t.url in avoid:
            continue
        seen.add(t.url)
        out.append(t)

    def score(t: Track) -> float:
        s = random.random()
        ch = (t.channel or "").lower()
        if ch in fav:
            s += 2.0
        s += top.get(ch, 0.0)
        if want_artist and (want_artist in ch
                            or want_artist in (t.title or "").lower()):
            s += 3.0
        return s

    out.sort(key=score, reverse=True)
    return out[:limit]
