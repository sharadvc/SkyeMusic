"""10-Band Equalizer presets and MPV audio filter parameters."""

from __future__ import annotations

# MPV 10-band equalizer frequency bands:
# g1 (31.25Hz), g2 (62.5Hz), g3 (125Hz), g4 (250Hz), g5 (500Hz),
# g6 (1kHz), g7 (2kHz), g8 (4kHz), g9 (8kHz), g10 (16kHz)
# Gain values in dB (-12.0 to +12.0)

EQ_PRESETS: dict[str, dict[str, float]] = {
    "flat": {"g1": 0, "g2": 0, "g3": 0, "g4": 0, "g5": 0, "g6": 0, "g7": 0, "g8": 0, "g9": 0, "g10": 0},
    "bass": {"g1": 7, "g2": 6, "g3": 4, "g4": 2, "g5": 0, "g6": 0, "g7": 0, "g8": 1, "g9": 2, "g10": 3},
    "bass_extreme": {"g1": 11, "g2": 9, "g3": 6, "g4": 3, "g5": 1, "g6": 0, "g7": 0, "g8": 2, "g9": 4, "g10": 5},
    "vocal": {"g1": -3, "g2": -2, "g3": 0, "g4": 3, "g5": 5, "g6": 6, "g7": 5, "g8": 3, "g9": 1, "g10": -1},
    "acoustic": {"g1": 3, "g2": 2, "g3": 1, "g4": 1, "g5": 2, "g6": 3, "g7": 4, "g8": 3, "g9": 3, "g10": 2},
    "cyberpunk": {"g1": 8, "g2": 7, "g3": 4, "g4": 0, "g5": -2, "g6": 2, "g7": 5, "g8": 6, "g9": 7, "g10": 8},
    "rock": {"g1": 5, "g2": 4, "g3": 3, "g4": 1, "g5": -1, "g6": 1, "g7": 3, "g8": 4, "g9": 4, "g10": 4},
    "pop": {"g1": -1, "g2": 2, "g3": 4, "g4": 5, "g5": 4, "g6": 2, "g7": 0, "g8": 2, "g9": 4, "g10": 3},
}

EQ_ORDER = ["flat", "bass", "bass_extreme", "vocal", "acoustic", "cyberpunk", "rock", "pop"]


def format_mpv_eq(preset_name: str) -> str:
    """Format MPV audio filter string for a given equalizer preset."""
    name = preset_name.lower().strip()
    gains = EQ_PRESETS.get(name, EQ_PRESETS["flat"])
    freqs = [31.25, 62.5, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
    keys = [f"g{i}" for i in range(1, 11)]

    filters = []
    for k, freq in zip(keys, freqs):
        gain = gains.get(k, 0)
        if gain != 0:
            filters.append(f"equalizer=f={freq}:width_type=o:width=1:g={gain}")

    return ",".join(filters)


def next_eq_preset(current: str) -> str:
    cur = current.lower().strip()
    if cur not in EQ_ORDER:
        return "bass"
    idx = (EQ_ORDER.index(cur) + 1) % len(EQ_ORDER)
    return EQ_ORDER[idx]
