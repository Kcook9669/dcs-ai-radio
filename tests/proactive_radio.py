# =============================================================================
# DCS AI Radio - Proactive AWACS/GCI Radio
#
# Runs as a background thread alongside the main player-input pipeline.
# When a friendly AWACS unit is alive on the map (silenced via mission script),
# this module watches BattlefieldState for new hostile contacts and generates
# NATO brevity bogey calls via LLM + TTS.
#
# AWACS detection:  mission script "get_awacs_status" command
# Threat detection: BattlefieldState.get_hostile_units_with_positions()
# Speaking:         shared speaking_lock prevents interrupting main pipeline
# =============================================================================

import math
import os
import tempfile
import threading
import time

import numpy as np
import requests
import sounddevice as sd
import soundfile as sf
from radio_settings import get_volume_gain, get_voice_speed, is_role_enabled, get_awacs_range_nm, get_awacs_debug
from audio_utils import clean_text_for_tts, apply_audio_envelope, apply_speed

# =============================================================================
# CONFIG
# =============================================================================
CONTACT_COOLDOWN  = 90.0    # seconds before re-reporting the same unit
CHECK_INTERVAL    = 8.0     # how often to scan for new contacts (seconds)
MAX_RANGE_NM      = 80.0    # only report contacts within this range
AWACS_CHECK_EVERY = 30.0    # how often to re-query mission script for AWACS status


# =============================================================================
# GEO HELPERS
# =============================================================================
def _haversine_nm(lat1, lon1, lat2, lon2):
    R = 3440.065
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(min(a, 1.0)))


def _bearing_deg(lat1, lon1, lat2, lon2):
    lat1r = math.radians(lat1)
    lat2r = math.radians(lat2)
    dlon  = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _track_word(heading_deg):
    h = heading_deg % 360
    dirs = ["north", "northeast", "east", "southeast",
            "south", "southwest", "west", "northwest"]
    return dirs[round(h / 45) % 8]


# =============================================================================
# PROACTIVE RADIO
# =============================================================================
class ProactiveRadio:
    """Background AWACS/GCI replacement.

    Usage:
        radio = ProactiveRadio(bridge, battlefield, tts, voice_mgr,
                               voice_ref_texts, speaking_lock)
        radio.start()
        ...
        radio.stop()
    """

    def __init__(self, bridge, battlefield, tts, voice_mgr, voice_ref_texts,
                 speaking_lock, ollama_url="http://localhost:11434/api/generate"):
        self.bridge          = bridge
        self.battlefield     = battlefield
        self.tts             = tts
        self.voice_mgr       = voice_mgr
        self.voice_ref_texts = voice_ref_texts
        self.speaking_lock   = speaking_lock
        self.ollama_url      = ollama_url

        self._known: dict[str, float] = {}   # unit_id → last-reported timestamp
        self._awacs_alive    = False
        self._awacs_callsign = "Overlord"
        self._last_awacs_check = 0.0

        self._stop   = threading.Event()
        self._thread: threading.Thread | None = None

    # -------------------------------------------------------------------------
    # AWACS status
    # -------------------------------------------------------------------------
    def _refresh_awacs(self):
        if get_awacs_debug():
            self._awacs_alive    = True
            self._awacs_callsign = "Overlord"
            return
        result = self.bridge.send_command("atc", "get_awacs_status", timeout=1.5)
        raw = result.get("result", "none")
        if not raw or raw == "none" or "error" in raw:
            self._awacs_alive = False
            return
        for entry in raw.split(";"):
            if ":" in entry:
                name, coa = entry.split(":", 1)
                if coa.strip() == "blue":
                    self._awacs_alive    = True
                    self._awacs_callsign = name.strip()
                    return
        self._awacs_alive = False

    # -------------------------------------------------------------------------
    # Contact scanning
    # -------------------------------------------------------------------------
    def _new_contacts(self) -> list[dict]:
        hostiles, (plat, plon) = self.battlefield.get_hostile_units_with_positions()
        if plat == 0 and plon == 0:
            return []

        now     = time.time()
        results = []
        for u in hostiles:
            uid = u["id"]
            if now - self._known.get(uid, 0) < CONTACT_COOLDOWN:
                continue
            dist_nm = _haversine_nm(plat, plon, u["lat"], u["lon"])
            if dist_nm > get_awacs_range_nm():
                continue
            bearing = _bearing_deg(plat, plon, u["lat"], u["lon"])
            angels  = round(u["alt_m"] * 3.281 / 1000, 1)
            results.append({
                "id":      uid,
                "type":    u["type"],
                "dist_nm": round(dist_nm),
                "bearing": round(bearing),
                "angels":  angels,
                "track":   _track_word(u["heading"]),
            })
            self._known[uid] = now

        results.sort(key=lambda x: x["dist_nm"])
        return results

    # -------------------------------------------------------------------------
    # LLM call generation
    # -------------------------------------------------------------------------
    def _generate_call(self, c: dict) -> str | None:
        prompt = (
            f'You are AWACS callsign "{self._awacs_callsign}". '
            f'Generate one crisp NATO brevity bogey call (12-18 words):\n'
            f'Contact: {c["type"]}, BRA {c["bearing"]:03d}/{c["dist_nm"]}nm/'
            f'angels {c["angels"]}, tracking {c["track"]}.\n'
            f'Format: "[callsign], BOGEY, BRA [bearing]/[range]/angels [alt], '
            f'tracking [direction]." No quotes, no extra text, one sentence only.'
        )
        try:
            resp = requests.post(self.ollama_url, json={
                "model":   "llama3.2:3b",
                "prompt":  prompt,
                "stream":  False,
                "keep_alive": "30m",
                "options": {"num_predict": 40, "temperature": 0.3},
            }, timeout=10)
            return resp.json()["response"].strip().strip('"\'')
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # TTS + playback
    # -------------------------------------------------------------------------
    def _speak(self, text: str):
        role       = "jtac"
        voice_file = self.voice_mgr.get_voice_by_role(role)
        ref_text   = self.voice_ref_texts.get(voice_file, "")
        if not ref_text:
            return

        path = os.path.join(tempfile.gettempdir(), "awacs_call.wav")
        try:
            self.tts.infer(
                ref_file=voice_file,
                ref_text=ref_text,
                gen_text=clean_text_for_tts(text),
                file_wave=path,
                nfe_step=16,
            )
        except Exception as e:
            print(f"  [AWACS] TTS error: {e}")
            return

        acquired = self.speaking_lock.acquire(timeout=30)
        if not acquired:
            return
        try:
            data, sr = sf.read(path, dtype="float32")
            data = apply_speed(apply_audio_envelope(data * get_volume_gain("awacs"), sr), get_voice_speed("awacs"))
            sd.play(np.clip(data, -1.0, 1.0), sr)
            sd.wait()
        finally:
            self.speaking_lock.release()

    # -------------------------------------------------------------------------
    # Background loop
    # -------------------------------------------------------------------------
    def _loop(self):
        print("[AWACS] Proactive radio started")
        while not self._stop.is_set():
            try:
                now = time.time()
                if now - self._last_awacs_check >= AWACS_CHECK_EVERY:
                    self._refresh_awacs()
                    self._last_awacs_check = now
                    status = f"alive ({self._awacs_callsign})" if self._awacs_alive else "none on map"
                    print(f"[AWACS] Status: {status}")

                if self._awacs_alive and is_role_enabled("awacs"):
                    contacts = self._new_contacts()
                    if contacts:
                        call = self._generate_call(contacts[0])
                        if call:
                            print(f"\n[AWACS] {self._awacs_callsign}: \"{call}\"")
                            self._speak(call)

            except Exception as e:
                print(f"[AWACS] Loop error: {e}")

            self._stop.wait(CHECK_INTERVAL)

    def start(self):
        self._refresh_awacs()
        self._last_awacs_check = time.time()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ProactiveRadio")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
