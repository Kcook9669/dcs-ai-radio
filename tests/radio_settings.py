# =============================================================================
# DCS AI Radio - Settings
#
# Reads/writes settings.json from the tests/ directory.
# The dashboard writes it; the pipeline reads it before each audio playback
# so volume and enable changes take effect without restarting.
# =============================================================================

import json
from pathlib import Path

SETTINGS_FILE = Path(__file__).parent / "settings.json"

_BASE_GAIN = 7.0   # gain that corresponds to volume_pct = 100

DEFAULTS: dict = {
    "roles": {
        "atc":         {"enabled": True, "volume": 100, "speed": 1.0},
        "jtac":        {"enabled": True, "volume": 100, "speed": 1.0},
        "wingman":     {"enabled": True, "volume": 100, "speed": 1.0},
        "ground_crew": {"enabled": True, "volume": 100, "speed": 1.0},
        "awacs":       {"enabled": True, "volume": 100, "speed": 1.0},
    },
    "awacs_range_nm": 80,
    "awacs_debug": False,
    "ptt_key": "scroll_lock",
    "wingman_group": "",
}


def get_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULTS


def save_settings(data: dict):
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def ensure_defaults():
    """Create settings.json with defaults if it doesn't exist."""
    if not SETTINGS_FILE.exists():
        save_settings(DEFAULTS)


def get_volume_gain(role: str) -> float:
    """Return audio gain multiplier for a role. volume_pct 100 → gain 7.0."""
    pct = get_settings().get("roles", {}).get(role, {}).get("volume", 100)
    return (pct / 100.0) * _BASE_GAIN


def is_role_enabled(role: str) -> bool:
    return get_settings().get("roles", {}).get(role, {}).get("enabled", True)


def get_voice_speed(role: str) -> float:
    """Return TTS speed multiplier for a role. 1.0 = normal, 0.8 = 20% slower."""
    return float(get_settings().get("roles", {}).get(role, {}).get("speed", 1.0))


def get_awacs_range_nm() -> float:
    return float(get_settings().get("awacs_range_nm", 80))


def get_awacs_debug() -> bool:
    return bool(get_settings().get("awacs_debug", False))


def get_ptt_key() -> str:
    return get_settings().get("ptt_key", "scroll_lock")


def get_wingman_group() -> str:
    """DCS group name of the AI wingman. Must match mission exactly."""
    return get_settings().get("wingman_group", "")
