# =============================================================================
# DCS AI Radio - Battlefield State Listener (File-Based)
# Reads live mission data from a JSON file written by DCS Export.lua
#
# The Lua script writes to:
#   %USERPROFILE%\Saved Games\DCS\dcs-ai-radio\state.json
#
# Run standalone to test: python dcs_state.py
# =============================================================================

import json
import threading
import time
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# =============================================================================
# CONFIG
# =============================================================================
# Auto-detect DCS saved games path
DCS_SAVED_GAMES = Path(os.environ["USERPROFILE"]) / "Saved Games" / "DCS"
STATE_FILE = DCS_SAVED_GAMES / "dcs-ai-radio" / "state.json"
POLL_INTERVAL = 0.25  # How often to check the file (seconds)


# =============================================================================
# STATE CLASSES
# =============================================================================
@dataclass
class Position:
    lat: float = 0.0
    lon: float = 0.0
    alt_m: float = 0.0

@dataclass
class PlayerState:
    callsign: str = "Unknown"
    aircraft_type: str = "Unknown"
    coalition: str = "Unknown"
    country: str = "Unknown"
    position: Position = field(default_factory=Position)
    heading: float = 0.0
    ias_mps: float = 0.0
    altitude_asl: float = 0.0
    altitude_agl: float = 0.0
    mach: float = 0.0
    fuel_internal: float = 0.0

    @property
    def ias_knots(self):
        return self.ias_mps * 1.944

    @property
    def altitude_feet(self):
        return self.altitude_asl * 3.281

@dataclass
class Unit:
    id: str = ""
    name: str = "Unknown"
    unit_type: str = "Unknown"
    coalition: str = "Unknown"
    country: str = "Unknown"
    position: Position = field(default_factory=Position)
    heading: float = 0.0
    speed: float = 0.0
    is_human: bool = False
    is_ai: bool = False
    is_alive: bool = True

@dataclass
class Threat:
    threat_type: str = "Unknown"
    azimuth: float = 0.0
    priority: float = 0.0
    signal_type: str = "Unknown"


# =============================================================================
# BATTLEFIELD STATE
# =============================================================================
class BattlefieldState:
    """Maintains a live, thread-safe snapshot of the DCS mission state."""

    def __init__(self):
        self._lock = threading.Lock()
        self.connected = False
        self.mission_time = 0.0
        self.player = PlayerState()
        self.units: list[Unit] = []
        self.threats: list[Threat] = []
        self.last_update = 0.0

    def update(self, data: dict):
        """Update state from parsed JSON."""
        with self._lock:
            event = data.get("event", "")

            if event == "stop":
                self.connected = False
                return

            if event == "start":
                self.connected = True
                self.last_update = time.time()
                return

            self.mission_time = data.get("timestamp", 0.0)
            self.last_update = time.time()
            self.connected = True

            # Player data
            p = data.get("player", {})
            pos = p.get("position", {})
            self.player = PlayerState(
                callsign=p.get("callsign", "Unknown"),
                aircraft_type=p.get("type", "Unknown"),
                coalition=p.get("coalition", "Unknown"),
                country=p.get("country", "Unknown"),
                position=Position(
                    lat=pos.get("lat", 0),
                    lon=pos.get("lon", 0),
                    alt_m=pos.get("alt_m", 0)
                ),
                heading=p.get("heading", 0),
                ias_mps=p.get("ias_mps", 0),
                altitude_asl=p.get("altitude_asl", 0),
                altitude_agl=p.get("altitude_agl", 0),
                mach=p.get("mach", 0),
                fuel_internal=p.get("fuel_internal", 0)
            )

            # World units
            self.units = []
            for u in data.get("units", []):
                upos = u.get("position", {})
                flags = u.get("flags", {})
                self.units.append(Unit(
                    id=str(u.get("id", "")),
                    name=u.get("name", "Unknown"),
                    unit_type=u.get("type", "Unknown"),
                    coalition=u.get("coalition", "Unknown"),
                    country=u.get("country", "Unknown"),
                    position=Position(
                        lat=upos.get("lat", 0),
                        lon=upos.get("lon", 0),
                        alt_m=upos.get("alt_m", 0)
                    ),
                    heading=u.get("heading", 0),
                    speed=u.get("speed", 0),
                    is_human=flags.get("human", False),
                    is_ai=flags.get("ai", False),
                    is_alive=flags.get("born", True)
                ))

            # Threats
            self.threats = []
            for t in data.get("threats", []):
                self.threats.append(Threat(
                    threat_type=t.get("type", "Unknown"),
                    azimuth=t.get("azimuth", 0),
                    priority=t.get("priority", 0),
                    signal_type=t.get("signal_type", "Unknown")
                ))

    def get_snapshot(self) -> dict:
        """Get a thread-safe copy of current state for prompt building."""
        with self._lock:
            return {
                "connected": self.connected,
                "mission_time": self.mission_time,
                "player": {
                    "callsign": self.player.callsign,
                    "aircraft": self.player.aircraft_type,
                    "coalition": self.player.coalition,
                    "altitude_ft": round(self.player.altitude_feet),
                    "speed_kts": round(self.player.ias_knots),
                    "heading": round(self.player.heading),
                    "fuel": round(self.player.fuel_internal, 2),
                    "position": {
                        "lat": self.player.position.lat,
                        "lon": self.player.position.lon
                    }
                },
                "friendly_units": [
                    {"name": u.name, "type": u.unit_type, "alive": u.is_alive}
                    for u in self.units if u.coalition == self.player.coalition
                ],
                "hostile_units": [
                    {"name": u.name, "type": u.unit_type, "alive": u.is_alive}
                    for u in self.units
                    if u.coalition != self.player.coalition and u.coalition != "neutral"
                ],
                "threats": [
                    {"type": t.threat_type, "bearing": round(t.azimuth), "priority": t.priority}
                    for t in self.threats
                ],
                "staleness": round(time.time() - self.last_update, 1) if self.last_update > 0 else None
            }

    def build_context_string(self) -> str:
        """Build a concise battlefield summary for LLM context injection."""
        snap = self.get_snapshot()

        if not snap["connected"]:
            return "[No DCS connection]"

        p = snap["player"]
        lines = [
            f"Player: {p['callsign']} | Aircraft: {p['aircraft']} | Coalition: {p['coalition']}",
            f"Alt: {p['altitude_ft']}ft | Speed: {p['speed_kts']}kts | Hdg: {p['heading']}deg | Fuel: {p['fuel']}",
            f"Friendlies nearby: {len(snap['friendly_units'])} | Hostiles nearby: {len(snap['hostile_units'])}"
        ]

        if snap["threats"]:
            for t in snap["threats"][:3]:
                lines.append(f"THREAT: {t['type']} bearing {t['bearing']}deg")

        return "\n".join(lines)


# =============================================================================
# FILE WATCHER THREAD
# =============================================================================
class DCSFileWatcher:
    """Polls the state.json file written by DCS Export.lua."""

    def __init__(self, state: BattlefieldState, state_file: Path = STATE_FILE):
        self.state = state
        self.state_file = state_file
        self._thread = None
        self._running = False
        self._last_modified = 0.0

    def start(self):
        """Start watching in a background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        print(f"  ✓ File watcher started")
        print(f"    Watching: {self.state_file}")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _watch(self):
        """Main watcher loop."""
        while self._running:
            try:
                if self.state_file.exists():
                    mod_time = self.state_file.stat().st_mtime
                    if mod_time > self._last_modified:
                        self._last_modified = mod_time
                        content = self.state_file.read_text(encoding="utf-8")
                        if content.strip():
                            data = json.loads(content)
                            event = data.get("event", "")
                            if event == "start":
                                print("  📡 DCS mission started")
                            elif event == "stop":
                                print("  📡 DCS mission ended")
                            self.state.update(data)
                else:
                    if self.state.connected:
                        self.state.connected = False
            except json.JSONDecodeError:
                pass  # File was mid-write, skip this cycle
            except Exception as e:
                if self._running:
                    print(f"  ⚠ Watcher error: {e}")

            time.sleep(POLL_INTERVAL)


# =============================================================================
# STANDALONE TEST
# =============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("DCS AI Radio - State Watcher (Standalone Test)")
    print(f"Watching: {STATE_FILE}")
    print("Start a DCS mission to see data flow")
    print("Press Ctrl+C to stop")
    print("=" * 60)

    state = BattlefieldState()
    watcher = DCSFileWatcher(state)
    watcher.start()

    try:
        while True:
            time.sleep(2)
            if state.connected:
                print("\n" + state.build_context_string())
            else:
                print("  Waiting for DCS connection...")
    except KeyboardInterrupt:
        print("\nShutting down...")
        watcher.stop()