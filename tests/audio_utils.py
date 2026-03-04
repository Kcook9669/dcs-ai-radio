# =============================================================================
# DCS AI Radio - Shared Audio Utilities
# Used by both pipelinev3.py and proactive_radio.py
# =============================================================================

import re
import numpy as np


def _n2w(n: int) -> str:
    """Convert integer 1-99 to spoken English words (for aircraft designators)."""
    ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight",
            "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
            "sixteen", "seventeen", "eighteen", "nineteen"]
    tens = ["", "", "twenty", "thirty", "forty", "fifty",
            "sixty", "seventy", "eighty", "ninety"]
    if n < 20:
        return ones[n]
    return tens[n // 10] + ("" if n % 10 == 0 else " " + ones[n % 10])


def clean_text_for_tts(text: str) -> str:
    """Sanitize text for F5-TTS: normalize brevity codes, numbers, punctuation."""
    text = text.lower()

    # Strip DCS internal variant suffixes (A-10C_2 → A-10C, F-16C_50 → F-16C)
    text = re.sub(r'_\w+', ' ', text)

    # Aircraft designator numbers: A-10 → a ten, F-16C → f sixteen c
    # Must run before digit-to-word expansion
    def _expand_designator(m):
        prefix = m.group(1)
        num    = _n2w(int(m.group(2)))
        suffix = (" " + m.group(3)) if m.group(3) else ""
        return f"{prefix} {num}{suffix}"
    text = re.sub(r'\b([a-z]{1,3})-(\d{1,3})([a-z]?)\b', _expand_designator, text)

    # Fix BRA+angels separator: 307/59nm/angels → "307/59nm, angels"
    text = text.replace('/angels', ', angels')

    # Phonetic substitutions for words F5-TTS consistently mispronounces
    jargon = {
        "bogey":  "bogie",
        "bogeys": "bogies",
        "awacs":  "ay wax",   # spoken as a word
        "jtac":   "jay tack", # spoken as a word
        "atc":    "a t c",    # spoken as letters
        "rtb":    "r t b",    # spoken as letters
        "bra":    "b r a",    # spoken as letters
    }
    for word, replacement in jargon.items():
        text = re.sub(r'\b' + word + r'\b', replacement, text)

    # Expand runway designators (27l → 27 left)
    text = re.sub(r'(\d+)l\b', r'\1 left', text)
    text = re.sub(r'(\d+)r\b', r'\1 right', text)
    text = re.sub(r'(\d+)c\b', r'\1 center', text)

    # BRA notation: 307/59nm → "307 59 nautical miles"
    text = re.sub(r'(\d+)/(\d+)nm', r'\1 \2 nautical miles', text)

    # Angels (altitude in thousands): angels 2.8 → "angels 2 point 8"
    text = re.sub(r'angels\s+(\d+)\.(\d+)', r'angels \1 point \2', text)
    text = re.sub(r'angels\s+(\d+)\b', r'angels \1', text)

    # Expand digits to spoken words
    num_map = {
        "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
        "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"
    }
    for num, word in num_map.items():
        text = text.replace(num, f" {word} ")

    # Strip everything except letters, spaces, and commas (commas = natural TTS pauses)
    text = re.sub(r'[^a-z\s,]', '', text)
    return " ".join(text.split())


def apply_audio_envelope(data: np.ndarray, sr: int,
                          fade_in_ms: int = 25, fade_out_ms: int = 30,
                          tail_ms: int = 850) -> np.ndarray:
    """Fade-in, fade-out, and a silent tail to simulate a held radio key."""
    data = data.copy()
    fade_in  = min(int(sr * fade_in_ms  / 1000), len(data) // 4)
    fade_out = min(int(sr * fade_out_ms / 1000), len(data) // 4)
    if fade_in  > 0: data[:fade_in]  *= np.linspace(0.0, 1.0, fade_in)
    if fade_out > 0: data[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)
    if tail_ms  > 0:
        data = np.concatenate([data, np.zeros(int(sr * tail_ms / 1000), dtype=data.dtype)])
    return data


def apply_speed(data: np.ndarray, speed: float) -> np.ndarray:
    """Pitch-preserving time stretch via librosa. Falls back to no-op at 1.0."""
    if abs(speed - 1.0) < 0.01:
        return data
    try:
        import librosa
        return librosa.effects.time_stretch(data, rate=speed)
    except Exception:
        return data
