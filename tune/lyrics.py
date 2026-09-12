"""Fetch synced karaoke lyrics across multiple free, open sources without API keys.

Sources in priority order:
1. Local persistent disk cache (~/.cache/tune/lyrics/) - 0ms instant reload
2. LRCLIB (verified synced LRC) - millisecond-accurate studio synchronization
3. YouTube Subtitles & Auto-Captions (yt-dlp) - audio-aligned voice synchronization
4. Plain authentic lyrics fallback (LRCLIB, Genius, lyrics.ovh) - strictly unsynced, no fake timing

Returns a list of `{"start": float | None, "end": float | None, "text": str, "synced": bool}`.
Empty list = unavailable.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

_CACHE_DIR = Path.home() / ".cache" / "tune" / "lyrics"


def _get_cache_dir() -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR


def _parse_ts(ts: str) -> float:
    """Parse timestamp in 'hh:mm:ss.xxx' or 'mm:ss.xxx' format to seconds."""
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        try:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        except ValueError:
            return 0.0
    elif len(parts) == 2:
        try:
            m, s = parts
            return int(m) * 60 + float(s)
        except ValueError:
            return 0.0
    return 0.0


COMMON_HINDI_WORDS = {
    "हम": "hum", "तुम": "tum", "तू": "tu", "आप": "aap", "मैं": "main", "मुझे": "mujhe",
    "तुझे": "tujhe", "हमें": "hameen", "मेरा": "mera", "मेरी": "meri", "मेरे": "mere",
    "तेरा": "tera", "तेरी": "teri", "तेरे": "tere", "उसका": "uska", "उसकी": "uski",
    "है": "hai", "हैं": "hain", "हूँ": "hoon", "था": "tha", "थी": "thi", "थे": "the",
    "हो": "ho", "हुआ": "hua", "हुई": "hui", "हुए": "hue", "होता": "hota", "होती": "hoti",
    "नहीं": "nahi", "नही": "nahi", "ना": "na", "मत": "mat", "रह": "reh", "यह": "yeh",
    "वह": "woh", "ये": "ye", "वो": "woh", "कि": "ki", "की": "ki", "का": "ka", "के": "ke",
    "को": "ko", "से": "se", "में": "mein", "पे": "pe", "पर": "par", "तक": "tak",
    "और": "aur", "या": "yaa", "लेकिन": "lekin", "मगर": "magar", "बिन": "bin",
    "क्या": "kya", "क्यों": "kyun", "कहाँ": "kahan", "कब": "kab", "कैसे": "kaise",
    "कौन": "kaun", "कोई": "koi", "कुछ": "kuch", "सब": "sab", "सभी": "sabhi",
    "दिल": "dil", "इश्क़": "ishq", "इश्क": "ishq", "प्यार": "pyar", "मोहब्बत": "mohabbat",
    "ज़िंदगी": "zindagi", "जिंदगी": "zindagi", "नसीब": "naseeb", "साँस": "saans",
    "बात": "baat", "रात": "raat", "साथ": "saath", "पास": "paas", "दूर": "door",
    "आँखें": "aankhen", "आँखों": "aankhon", "नज़र": "nazar", "चेहरा": "chehra",
    "खुशी": "khushi", "ग़म": "gham", "गम": "gham", "दर्द": "dard", "याद": "yaad",
    "सकते": "sakte", "सकता": "sakta", "सकती": "sakti", "चैन": "chain", "वजह": "wajah",
    "वजूद": "wajood", "जुदा": "juda", "खुद": "khud", "आशिक़ी": "aashiqui", "आशिकी": "aashiqui"
}

DEVANAGARI_VOWELS = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri",
    "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "अं": "an", "अः": "ah"
}

DEVANAGARI_MATRAS = {
    "ा": "aa", "ि": "i", "ी": "i", "ु": "u", "ू": "oo", "ृ": "ri",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ं": "n", "ँ": "n", "ः": "h",
    "ॉ": "o", "ॅ": "e"
}

DEVANAGARI_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "ny",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    "ड़": "d", "ढ़": "dh", "फ़": "f", "ज़": "z", "ख़": "kh", "ग़": "g", "क़": "q", "ज्ञ": "gya", "त्र": "tra", "क्ष": "ksh"
}

HALANT = "्"


def _transliterate_devanagari_word(word: str) -> str:
    clean_w = re.sub(r"[^\u0900-\u097F]", "", word)
    if clean_w in COMMON_HINDI_WORDS:
        return word.replace(clean_w, COMMON_HINDI_WORDS[clean_w])

    res = []
    i = 0
    n = len(word)
    while i < n:
        ch = word[i]
        if i + 1 < n and word[i:i+2] in DEVANAGARI_CONSONANTS:
            base = DEVANAGARI_CONSONANTS[word[i:i+2]]
            i += 2
            if i < n and word[i] == HALANT:
                res.append(base)
                i += 1
            elif i < n and word[i] in DEVANAGARI_MATRAS:
                res.append(base + DEVANAGARI_MATRAS[word[i]])
                i += 1
            else:
                next_c = word[i] if i < n else ""
                if not next_c or next_c.isspace() or next_c in ".,!?\n":
                    res.append(base)
                else:
                    res.append(base + "a")
            continue

        if ch in DEVANAGARI_CONSONANTS:
            base = DEVANAGARI_CONSONANTS[ch]
            i += 1
            if i < n and word[i] == HALANT:
                res.append(base)
                i += 1
            elif i < n and word[i] in DEVANAGARI_MATRAS:
                res.append(base + DEVANAGARI_MATRAS[word[i]])
                i += 1
            else:
                next_c = word[i] if i < n else ""
                if not next_c or next_c.isspace() or next_c in ".,!?\n":
                    res.append(base)
                else:
                    res.append(base + "a")
            continue

        if ch in DEVANAGARI_VOWELS:
            res.append(DEVANAGARI_VOWELS[ch])
            i += 1
            continue

        if ch in DEVANAGARI_MATRAS:
            res.append(DEVANAGARI_MATRAS[ch])
            i += 1
            continue

        res.append(ch)
        i += 1

    s = "".join(res)
    s = re.sub(r"\b([a-z]+)aa\b", r"\1a", s, flags=re.IGNORECASE)
    return s


GURMUKHI_TO_DEVANAGARI = {
    "ਅ": "अ", "ਆ": "आ", "ਇ": "इ", "ਈ": "ई", "ਉ": "उ", "ਊ": "ऊ", "ਏ": "ए", "ਐ": "ऐ", "ਓ": "ओ", "ਔ": "औ",
    "ਕ": "क", "ਖ": "ख", "ਗ": "ग", "ਘ": "घ", "ਙ": "ङ",
    "ਚ": "च", "ਛ": "छ", "ਜ": "ज", "ਝ": "झ", "ਞ": "ञ",
    "ਟ": "ट", "ਠ": "ठ", "ਡ": "ड", "ਢ": "ढ", "ਣ": "ण",
    "ਤ": "त", "ਥ": "थ", "ਦ": "द", "ਧ": "ध", "ਨ": "न",
    "ਪ": "प", "ਫ": "फ", "ਬ": "ब", "ਭ": "भ", "ਮ": "म",
    "ਯ": "य", "ਰ": "र", "ਲ": "ल", "ਵ": "व", "ੜ": "ड़", "ਸ਼": "श", "ਸ": "स", "ਹ": "ह",
    "ਾ": "ा", "ਿ": "ि", "ੀ": "ी", "ੁ": "ु", "ੂ": "ू", "ੇ": "े", "ੈ": "ै", "ੋ": "ो", "ੌ": "ौ",
    "ਂ": "ं", "ੰ": "ं", "ੱ": "", "੍": "्"
}


def gurmukhi_to_devanagari(text: str) -> str:
    res = [GURMUKHI_TO_DEVANAGARI.get(ch, ch) for ch in text]
    return "".join(res)


def devanagari_to_hinglish(text: str) -> str:
    """Convert Devanagari Hindi text to clean, natural Hinglish (Roman script)."""
    if not text or not any("\u0900" <= c <= "\u097F" for c in text):
        return text
    tokens = text.split(" ")
    out = [_transliterate_devanagari_word(tok) for tok in tokens]
    return " ".join(out)


def indic_to_hinglish(text: str) -> str:
    """Convert Indic scripts (Devanagari, Gurmukhi) to clean Hinglish (Roman script)."""
    if not text:
        return text
    if any("\u0a00" <= c <= "\u0a7f" for c in text):
        text = gurmukhi_to_devanagari(text)
    return devanagari_to_hinglish(text)


def parse_vtt(text: str) -> list[dict]:
    """Parse WebVTT subtitle text into [{start, end, text, synced: True}]."""
    lines: list[dict] = []
    arrow_re = re.compile(
        r"((?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{1,3})\s*-->\s*((?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{1,3})"
    )
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block or block.startswith("WEBVTT") or block.startswith("NOTE"):
            continue
        m = arrow_re.search(block)
        if not m:
            continue
        start, end = _parse_ts(m.group(1)), _parse_ts(m.group(2))
        _, _, rest = block.partition(m.group(0))
        rest = rest.partition("\n")[2] if "\n" in rest else ""  # strip cue settings on same line
        body = re.sub(r"<[^>]+>", "", rest).strip()
        body = (body.replace("&nbsp;", " ").replace("&amp;", "&")
                    .replace("&quot;", '"').replace("&#39;", "'")
                    .replace("&rarr;", "→").strip())
        if body:
            body = indic_to_hinglish(body)
            # Deduplicate identical consecutive auto-caption lines
            if lines and lines[-1]["text"] == body and abs(lines[-1]["end"] - start) < 0.5:
                lines[-1]["end"] = max(lines[-1]["end"], end)
            else:
                lines.append({"start": start, "end": end, "text": body, "synced": True})
    return lines


def parse_lrc(text: str) -> list[dict]:
    """Parse LRC lyrics into [{start, end, text, synced: True, next_start: float}] (seconds)."""
    out: list[dict] = []
    # Tag formats: [mm:ss.xx], [mm:ss:xx], [mm:ss.xxx], [hh:mm:ss.xx]
    tag = re.compile(r"\[(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        hits = tag.findall(raw)
        if not hits:
            continue
        body = tag.sub("", raw).strip()
        if not body:
            continue
        body = indic_to_hinglish(body)
        for h, m, s, ms in hits:
            hours = int(h) if h else 0
            start = hours * 3600 + int(m) * 60 + int(s) + int((ms or "0").ljust(3, "0")[:3]) / 1000.0
            out.append({"start": start, "text": body, "synced": True})
    out.sort(key=lambda x: x["start"])
    for i, ln in enumerate(out):
        next_s = out[i + 1]["start"] if i + 1 < len(out) else ln["start"] + 8.0
        ln["next_start"] = next_s
        gap = max(0.2, next_s - ln["start"])
        words = [w for w in re.sub(r"[^\w\s]", "", ln["text"]).split() if w]
        natural_dur = max(1.2, len(words) * 0.38 + 0.5)
        if gap <= natural_dur + 0.6:
            ln["end"] = max(ln["start"] + 0.5, next_s)
        else:
            ln["end"] = ln["start"] + min(natural_dur, gap - 0.4)
    return out


def format_plain_lyrics(plain_text: str) -> list[dict]:
    """Format authentic plain lyrics without synthesizing fake timing intervals."""
    raw_lines = [ln.strip() for ln in plain_text.splitlines()]
    lines: list[dict] = []
    for ln in raw_lines:
        if not ln:
            continue
        if re.match(r"(?i)^\d+\s+contributors", ln) or re.match(r"(?i)^lyrics\s+by", ln):
            continue
        if re.match(r"(?i)^embed\b", ln) or re.match(r"(?i)^you might also like\b", ln):
            continue
        ln = indic_to_hinglish(ln)
        lines.append({"start": None, "end": None, "text": ln, "synced": False})
    return lines


def synthesize_sync_for_plain(plain_text: str, duration: float | None = None) -> list[dict]:
    """Compatibility alias for plain lyrics. Never manufactures fake timestamps."""
    return format_plain_lyrics(plain_text)


def _clean_title_noise(t: str) -> str:
    """Remove video metadata noise, tags, and trailing descriptors."""
    if not t:
        return ""
    # Bracketed noise: [Official Video], (Lyrics), [HD], etc.
    t = re.sub(r"(?i)\s*[\(\[](?:official\s+)?(?:music\s+)?(?:video|audio|lyrics?|lyric\s+video|visualizer|hd|4k|remastered|performance|live|version|full\s+song|full\s+video|hq|explicit|clean)[^\)\]]*[\)\]]", "", t)
    t = re.sub(r"(?i)\s*[\(\[](?:ft\.?|feat\.?)[^\)\]]+[\)\]]", "", t)
    # Unbracketed "with lyrics" / "with lyric"
    t = re.sub(r"(?i)\s*(?:-\s*|\|\s*)?\bwith\s+lyrics?\b", "", t)
    # Unbracketed video labels at boundaries or end
    t = re.sub(r"(?i)\s*(?:-\s*|\|\s*)?\b(?:song\s+lyrics?|lyrics?\s+song|full\s+video(?:\s+song)?|official\s+(?:music\s+)?video|lyrical\s+video|lyric\s+video|lyrics?\s+video|lyrics?|official\s+audio|audio\s+song|full\s+audio|full\s+song|video\s+song)\b.*$", "", t)
    t = re.sub(r"(?i)\s+(?:song|video|audio)\s*$", "", t)
    return t.strip()


def clean_track_info(title: str, channel: str = "") -> tuple[str, str]:
    """Extract clean (song_title, artist) from YouTube video metadata."""
    t = title or ""
    pipes = [_clean_title_noise(p) for p in t.split("|") if _clean_title_noise(p)]
    first_part = pipes[0] if pipes else _clean_title_noise(t)
    first_clean = _clean_title_noise(first_part)

    song = first_clean
    artist = ""
    for sep in (" - ", " – ", " — "):
        if sep in first_clean:
            parts = first_clean.split(sep, 1)
            p0, p1 = parts[0].strip(), parts[1].strip()
            ch_clean = re.sub(r"(?i)\s*-\s*topic$", "", channel)
            ch_clean = re.sub(r"(?i)vevo$", "", ch_clean).strip().lower()
            if ch_clean and (ch_clean in p1.lower() or p1.lower() in ch_clean) and ch_clean not in p0.lower():
                song = p0
                artist = p1
            else:
                artist = p0
                song = p1
            break

    # If no artist extracted yet, inspect extra pipe segments before falling back to channel
    if not artist and len(pipes) > 1:
        last_seg = pipes[-1].strip()
        if len(last_seg.split()) <= 4 and not any(k in last_seg.lower() for k in ("movie", "film", "teaser", "trailer", "video")):
            artist = last_seg

    if not artist and channel:
        ch = re.sub(r"(?i)\s*-\s*topic$", "", channel)
        ch = re.sub(r"(?i)vevo$", "", ch)
        artist = ch.strip()

    # If title still has quotes: e.g. Artist "Song"
    q_match = re.search(r'["\']([^"\']+)["\']', song)
    if q_match:
        song = q_match.group(1).strip()

    return song.strip(), artist.strip()


def get_candidate_queries(title: str, artist: str = "", channel: str = "", query: str = "") -> list[str]:
    """Generate prioritized search queries from raw song, artist, channel, and query metadata."""
    queries = []

    # 0. User search query has highest intent priority
    if query and not (query.startswith("http://") or query.startswith("https://") or query.startswith("ytdl://")):
        q_clean = _clean_title_noise(query.strip())
        q_base = re.sub(r"\s*[\(\[][^\)\]]+[\)\]]", "", q_clean).strip()
        if q_clean:
            queries.append(q_clean)
        if q_base and q_base != q_clean:
            queries.append(q_base)
        if artist and artist not in queries:
            queries.append(f"{q_clean} {artist}")
            if q_base and q_base != q_clean:
                queries.append(f"{q_base} {artist}")

    t = title or ""
    pipes = [_clean_title_noise(p) for p in t.split("|") if _clean_title_noise(p)]
    first_part = pipes[0] if pipes else _clean_title_noise(t)
    extra_segments = pipes[1:] if len(pipes) > 1 else []
    first_clean = _clean_title_noise(first_part)

    # Base without parenthetical descriptors (e.g. "Tum Hi Ho (Aashiqui 2)" -> "Tum Hi Ho")
    first_base = re.sub(r"\s*[\(\[][^\)\]]+[\)\]]", "", first_clean).strip()

    # Extract parenthetical contents that might contain English title transliteration or song name
    for paren in re.findall(r"[\(\[](.*?)[\)\]]", first_clean):
        pc = _clean_title_noise(paren).strip()
        if pc and len(pc) >= 2 and not any(k in pc.lower() for k in ("video", "audio", "lyrics", "full", "hd", "4k", "remastered")):
            if re.search(r"[a-zA-Z]", pc):
                queries.append(pc)
                if artist:
                    queries.append(f"{pc} {artist}")

    # Extract Latin word clusters from mixed-script titles (e.g. "तुम ही हो Aashiqui 2")
    latin_clusters = [m.strip() for m in re.findall(r"[a-zA-Z0-9'\s]{3,}", first_clean) if len(m.strip().split()) >= 1]
    for lc in latin_clusters:
        lc_clean = _clean_title_noise(lc).strip()
        if lc_clean and lc_clean not in queries and len(lc_clean) >= 3:
            queries.append(lc_clean)
            if artist:
                queries.append(f"{lc_clean} {artist}")

    has_sep = False
    for sep in (" - ", " – ", " — "):
        if sep in first_clean:
            has_sep = True
            p0, p1 = [x.strip() for x in first_clean.split(sep, 1)]
            queries.append(f"{p0} {p1}")
            queries.append(f"{p1} {p0}")
            queries.append(p0)
            queries.append(p1)
            for ex in extra_segments:
                queries.append(f"{p0} {ex}")
                queries.append(f"{p1} {ex}")
            break

    if not has_sep:
        if first_clean:
            queries.append(first_clean)
        if first_base and first_base != first_clean:
            queries.append(first_base)
        for ex in extra_segments:
            queries.append(f"{first_clean} {ex}")
            if first_base and first_base != first_clean:
                queries.append(f"{first_base} {ex}")

    if artist and artist not in queries:
        queries.append(f"{first_clean} {artist}")
        if first_base and first_base != first_clean:
            queries.append(f"{first_base} {artist}")
        queries.append(artist)

    # Sub-artist splitting from commas/collaborations: e.g. "Armaan Malik, Amaal Mallik"
    for seg in extra_segments:
        if "," in seg:
            for sub_a in seg.split(","):
                sub_a_clean = sub_a.strip()
                if sub_a_clean:
                    queries.append(f"{first_clean} {sub_a_clean}")
                    queries.append(sub_a_clean)

    # Extra pipe combination: e.g. "Dil Ibaadat KK"
    if len(pipes) > 1:
        queries.append(f"{first_clean} {pipes[-1]}")

    seen = set()
    unique = []
    for q in queries:
        qn = q.strip().lower()
        if qn and qn not in seen:
            seen.add(qn)
            unique.append(q.strip())
    return unique


def _cache_key(url: str, title: str, artist: str) -> str:
    key_src = url or f"{title}_{artist}"
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", key_src)[:80]
    return safe + ".json"


def _load_cached_lyrics(key: str) -> list[dict] | None:
    try:
        p = _get_cache_dir() / key
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list) and len(data) > 0:
                # Cleanse any legacy fake timestamps and ensure natural vocal cadence
                is_synced = any(ln.get("synced", True) and ln.get("start") is not None for ln in data)
                for i, ln in enumerate(data):
                    if ln.get("text"):
                        ln["text"] = indic_to_hinglish(ln["text"])
                    if ln.get("synced") is False:
                        ln["start"] = None
                        ln["end"] = None
                    elif is_synced and ln.get("start") is not None:
                        next_s = data[i + 1]["start"] if (i + 1 < len(data) and data[i + 1].get("start") is not None) else ln["start"] + 8.0
                        ln["next_start"] = next_s
                        gap = max(0.2, next_s - ln["start"])
                        words = [w for w in re.sub(r"[^\w\s]", "", ln.get("text", "")).split() if w]
                        natural_dur = max(1.2, len(words) * 0.38 + 0.5)
                        if gap <= natural_dur + 0.6:
                            ln["end"] = max(ln["start"] + 0.5, next_s)
                        else:
                            ln["end"] = ln["start"] + min(natural_dur, gap - 0.4)
                return data
    except Exception:
        pass
    return None


def _save_cached_lyrics(key: str, lines: list[dict]) -> None:
    if not lines:
        return
    try:
        p = _get_cache_dir() / key
        p.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _normalize_text(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[\(\[\{\)\}\]\'\"]", " ", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return " ".join(s.split())


def is_matching_song(cand_title: str, cand_artist: str, cand_dur: float | None,
                     target_title: str, target_artist: str = "",
                     target_dur: float | None = None,
                     raw_title: str = "",
                     query: str = "") -> tuple[bool, float]:
    """Strictly verify that a candidate lyric matches the actual song playing.
    
    Rejects:
    - Covers, remixes, parodies, karaoke, acoustic versions when playing originals
    - Mismatched songs with overlapping common words
    - Candidate tracks whose duration differs significantly
    """
    c_title_norm = _normalize_text(cand_title)
    t_title_norm = _normalize_text(target_title)
    c_art_norm = _normalize_text(cand_artist)
    t_art_norm = _normalize_text(target_artist)
    raw_norm = _normalize_text(f"{raw_title} {target_title} {target_artist}")
    q_norm = _normalize_text(query) if (query and not (query.startswith("http://") or query.startswith("https://") or query.startswith("ytdl://"))) else ""

    # 1. Version words isolation (no unwanted covers/remixes)
    version_words = ["cover", "karaoke", "instrumental", "tribute", "parody",
                     "remix", "acoustic", "live", "slowed", "reverb", "nightcore", "sped up"]
    for vw in version_words:
        if vw in c_title_norm or vw in c_art_norm:
            if vw not in t_title_norm and vw not in t_art_norm and vw not in raw_norm and vw not in q_norm:
                return False, 0.0

    # 2. Title token overlap
    stopwords = {"the", "a", "an", "and", "of", "in", "to", "for", "with",
                 "official", "video", "audio", "lyrics", "lyric", "hd", "4k", "hq", "full", "song"}
    c_words = set(c_title_norm.split()) - stopwords
    if not c_words:
        c_words = set(c_title_norm.split())

    targets_to_check: list[str] = []
    if t_title_norm:
        targets_to_check.append(t_title_norm)
    if q_norm and q_norm != t_title_norm:
        targets_to_check.append(q_norm)
    if not targets_to_check and c_title_norm and c_title_norm in raw_norm:
        targets_to_check.append(c_title_norm)

    ratios: list[float] = []
    exact_title = False

    for tgt in targets_to_check:
        if not tgt:
            continue
        tgt_words = set(tgt.split()) - stopwords
        if not tgt_words:
            tgt_words = set(tgt.split())
        overlap = len(tgt_words & c_words)
        req_ratio = 0.75 if len(tgt_words) <= 3 else 0.55
        ratio = overlap / max(1, len(tgt_words))
        is_exact = (c_title_norm == tgt) or (tgt in c_title_norm) or (c_title_norm in tgt)
        if is_exact:
            exact_title = True
        if is_exact or ratio >= req_ratio:
            ratios.append(ratio)

    if not exact_title and not ratios:
        return False, 0.0

    best_ratio = max(ratios) if ratios else 0.0

    # 3. Artist verification
    art_stopwords = {"topic", "vevo", "official", "records", "music", "the", "entertainment", "channel", "company"}
    channel_labels = {"tseries", "t series", "sony", "sonymusic", "zee", "zeemusic", "tips", "yrf", "saregama", "universal", "records", "music"}
    ta_words = set(t_art_norm.split()) - art_stopwords
    ca_words = set(c_art_norm.split()) - art_stopwords
    raw_words = set(raw_norm.split()) - art_stopwords
    if q_norm:
        raw_words.update(set(q_norm.split()) - art_stopwords)

    is_channel_label = any(cl in t_art_norm for cl in channel_labels)
    artist_matches = False
    art_score = 0.0

    if ca_words and (ca_words & raw_words):
        artist_matches = True
        art_score += 40.0
    elif ta_words and ca_words:
        if ta_words & ca_words:
            artist_matches = True
            art_score += 40.0
        elif is_channel_label:
            art_score += 10.0
        else:
            art_score -= 25.0
            if not exact_title:
                return False, 0.0

    # 4. Duration validation
    dur_score = 0.0
    if target_dur and cand_dur and target_dur > 10.0 and cand_dur > 10.0:
        diff = abs(cand_dur - target_dur)
        # If not exact title or artist doesn't match, be stricter with duration
        if not (exact_title and (artist_matches or is_channel_label)):
            if diff > 45.0 or (diff > 25.0 and diff / target_dur > 0.18):
                return False, 0.0
        else:
            # Even for exact title, reject major version cuts (e.g. 2min clip vs 5.5min full song)
            if (cand_dur < 25.0 or cand_dur > target_dur * 2.2 or target_dur > cand_dur * 2.2
                    or diff > 100.0):
                return False, 0.0

        if diff <= 3.0:
            dur_score = 40.0
        elif diff <= 8.0:
            dur_score = 25.0
        elif diff <= 25.0:
            dur_score = 15.0
        elif diff <= 60.0:
            dur_score = 5.0

    score = best_ratio * 50.0 + dur_score + art_score
    if exact_title:
        score += 25.0

    return True, score


# --- Free Source 1: LRCLIB (verified synced lyrics database) ----------------

def fetch_lrclib(title: str, artist: str = "", duration: float | None = None,
                 timeout: int = 3, synced_only: bool = False, query: str = "") -> list[dict]:
    """Look up synced or plain lyrics on LRCLIB with exact + strict fuzzy validation."""
    clean_title, clean_artist = clean_track_info(title, artist)
    queries: list[dict[str, object]] = []

    if clean_title and clean_artist:
        p: dict[str, object] = {"track_name": clean_title, "artist_name": clean_artist}
        if duration:
            p["duration"] = int(duration)
        queries.append(p)

    # If user query provided and clean_track_info differs, add user query params
    if query and not (query.startswith("http://") or query.startswith("https://") or query.startswith("ytdl://")):
        qt, qa = clean_track_info(query, artist)
        if qt and (qt != clean_title or qa != clean_artist):
            qp: dict[str, object] = {"track_name": qt}
            if qa:
                qp["artist_name"] = qa
            elif clean_artist:
                qp["artist_name"] = clean_artist
            if duration:
                qp["duration"] = int(duration)
            queries.append(qp)

    headers = {"User-Agent": "SkyePlayer/1.0 (music player)"}
    # 1. Exact match endpoint (fast timeout: 2s)
    for q_params in queries:
        try:
            url = "https://lrclib.net/api/get?" + urllib.parse.urlencode(q_params)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=min(timeout, 2.0)) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            cand_dur = data.get("duration")
            if duration and cand_dur and duration > 10.0 and cand_dur > 10.0:
                diff = abs(cand_dur - duration)
                if diff > 45.0 and (diff / duration) > 0.2:
                    continue  # Mismatched cut or version; fall through to candidate search
            if data.get("syncedLyrics"):
                return parse_lrc(data["syncedLyrics"])
            elif not synced_only and data.get("plainLyrics"):
                return format_plain_lyrics(data["plainLyrics"])
        except Exception:
            pass

    # 2. Search endpoint with strict candidate matching
    search_queries = get_candidate_queries(title, artist, query=query)
    best_plain = None

    # Search top 2 queries max to guarantee ultra-fast response (< 0.8s)
    for sq in search_queries[:2]:
        try:
            url = "https://lrclib.net/api/search?" + urllib.parse.urlencode({"q": sq})
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                items = json.loads(r.read().decode("utf-8", "replace"))
            if not isinstance(items, list):
                continue

            valid_synced: list[tuple[float, dict]] = []
            valid_plain: list[tuple[float, dict]] = []

            for it in items:
                cand_title = it.get("trackName") or ""
                cand_artist = it.get("artistName") or ""
                cand_dur = it.get("duration")
                ok, score = is_matching_song(
                    cand_title, cand_artist, cand_dur,
                    clean_title or title, clean_artist or artist, duration,
                    raw_title=title, query=query
                )
                if ok:
                    raw_txt = it.get("syncedLyrics") or it.get("plainLyrics") or ""
                    # Prefer Romanized / Hinglish lyrics over non-Latin Indic script
                    if raw_txt and not any("\u0900" <= c <= "\u0d7f" for c in raw_txt):
                        score += 35.0
                    if it.get("syncedLyrics"):
                        valid_synced.append((score, it))
                    elif it.get("plainLyrics"):
                        valid_plain.append((score, it))

            if valid_synced:
                valid_synced.sort(key=lambda x: x[0], reverse=True)
                return parse_lrc(valid_synced[0][1]["syncedLyrics"])

            if not synced_only and valid_plain and best_plain is None:
                valid_plain.sort(key=lambda x: x[0], reverse=True)
                best_plain = valid_plain[0][1]["plainLyrics"]
        except Exception:
            pass

    if not synced_only and best_plain:
        return format_plain_lyrics(best_plain)

    return []


# --- Free Source 2: YouTube subtitles & auto-captions via yt-dlp ------------

def fetch_ytdlp(url: str, timeout: int = 15) -> list[dict]:
    """Download video subtitles or auto-captions via yt-dlp."""
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return []
    ytdl = shutil.which("yt-dlp") or str(Path.home() / ".local/bin/yt-dlp")
    if not os.path.exists(ytdl) and not shutil.which("yt-dlp"):
        return []
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "lyr")
        try:
            subprocess.run(
                [ytdl, "--skip-download", "--write-subs", "--write-auto-subs",
                 "--sub-langs", "en.*,en,all,-live_chat",
                 "--sub-format", "vtt", "-o", out, url],
                capture_output=True, text=True, timeout=timeout)
        except (subprocess.TimeoutExpired, Exception):
            return []
        vtts = sorted(Path(d).glob("*.vtt"))
        if not vtts:
            return []
        pref = None
        for v in vtts:
            name = v.name.lower()
            if any(k in name for k in (".en.", "-en.", ".hi.", "-hi.", "orig", "english", "hindi")):
                pref = v
                break
        target_vtt = pref
        if not target_vtt:
            return []
        try:
            return parse_vtt(target_vtt.read_text(errors="replace"))
        except Exception:
            return []


# --- Free Source 3: Genius (public search & lyrics container) ---------------

def fetch_genius(title: str, artist: str = "", duration: float | None = None,
                 timeout: int = 2, query: str = "") -> list[dict]:
    """Search Genius and extract authentic plain lyrics (no fake timestamps)."""
    candidates = get_candidate_queries(title, artist, query=query)
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    for q in candidates[:3]:
        search_url = "https://genius.com/api/search/multi?q=" + urllib.parse.quote(q)
        try:
            req = urllib.request.Request(search_url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            sections = data.get("response", {}).get("sections", [])
            path = None
            for sec in sections:
                if sec.get("type") == "song":
                    hits = sec.get("hits", [])
                    if hits:
                        hit_res = hits[0].get("result", {})
                        h_title = hit_res.get("title") or ""
                        h_artist = (hit_res.get("primary_artist") or {}).get("name") or ""
                        ok, _ = is_matching_song(h_title, h_artist, None, title, artist, duration, raw_title=title, query=query)
                        if ok:
                            path = hit_res.get("path")
                            break
            if not path:
                continue
            page_url = "https://genius.com" + path
            req2 = urllib.request.Request(page_url, headers=headers)
            with urllib.request.urlopen(req2, timeout=timeout) as r2:
                html_text = r2.read().decode("utf-8", "ignore")
            containers = re.findall(r'<div[^>]*data-lyrics-container="true"[^>]*>(.*?)</div>', html_text, re.DOTALL)
            if not containers:
                containers = re.findall(r'<div[^>]*class="[^"]*Lyrics__Container[^"]*"[^>]*>(.*?)</div>', html_text, re.DOTALL)
            if containers:
                raw = "\n".join(containers)
                raw = re.sub(r"<br\s*/?>", "\n", raw)
                raw = re.sub(r"<[^>]+>", "", raw)
                clean_text = html.unescape(raw).strip()
                lines = format_plain_lyrics(clean_text)
                if lines:
                    return lines
        except Exception:
            pass
    return []


# --- Free Source 4: lyrics.ovh (open REST API) ------------------------------

def fetch_lyricsovh(title: str, artist: str = "", duration: float | None = None,
                    timeout: int = 2, query: str = "") -> list[dict]:
    """Query lyrics.ovh REST API for authentic plain lyrics."""
    clean_t, clean_a = clean_track_info(title, artist)
    if not clean_t or not clean_a:
        if query:
            clean_t, clean_a = clean_track_info(query, artist)
    if not clean_t or not clean_a:
        return []
    headers = {"User-Agent": "SkyePlayer/1.0"}
    for a, t in ((clean_a, clean_t), (clean_t, clean_a)):
        try:
            url = f"https://api.lyrics.ovh/v1/{urllib.parse.quote(a)}/{urllib.parse.quote(t)}"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            lyr = data.get("lyrics")
            if lyr and lyr.strip():
                return format_plain_lyrics(lyr)
        except Exception:
            pass
    return []


# --- Unified Multi-Source Fetcher -------------------------------------------

def fetch(url: str = "", timeout: int = 15, title: str = "", artist: str = "",
          duration: float | None = None, query: str = "") -> list[dict]:
    """Unified lyrics fetcher using all free sources in priority order:
    1. Disk cache (0ms instant reload, authentic non-empty lines only)
    2. LRCLIB (verified synced lyrics + plain fallback in ~0.5s)
    3. YouTube Subtitles & Auto-Captions via yt-dlp (fast timeout: 3s)
    4. Genius / lyrics.ovh plain fallback (fast timeout: 2s)
    """
    ck = _cache_key(url, title, artist)
    cached = _load_cached_lyrics(ck)
    if cached:
        return cached

    # 1. LRCLIB (synced lyrics preferred, plain fallback in one round)
    lines = fetch_lrclib(title, artist, duration=duration, timeout=3, synced_only=False, query=query)
    if lines:
        _save_cached_lyrics(ck, lines)
        return lines

    # 2. YouTube subtitles & auto-captions (strict 3.0s timeout)
    if url and ("youtube.com" in url or "youtu.be" in url):
        lines = fetch_ytdlp(url, timeout=3)
        if lines:
            _save_cached_lyrics(ck, lines)
            return lines

    # 3. Genius plain lyrics (fast 2s timeout)
    lines = fetch_genius(title, artist, duration=duration, timeout=2, query=query)
    if lines:
        _save_cached_lyrics(ck, lines)
        return lines

    # 4. lyrics.ovh plain lyrics (fast 2s timeout)
    lines = fetch_lyricsovh(title, artist, duration=duration, timeout=2, query=query)
    if lines:
        _save_cached_lyrics(ck, lines)
        return lines

    return []



