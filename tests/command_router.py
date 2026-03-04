# =============================================================================
# DCS AI Radio - Radio Command Router
# Maps player voice intents to DCS radio menu key sequences
#
# Architecture:
#   1. Player speaks a radio command
#   2. LLM classifies the intent into a structured command
#   3. This module maps the command to the correct DCS key sequence
#   4. Key injector sends the keystrokes to DCS
#
# Menu structures vary by aircraft. This module defines them per airframe.
# =============================================================================

import json
import os
import re
import time
import requests
from pathlib import Path

# =============================================================================
# DCS RADIO MENU TREES
#
# DCS radio menus are navigated via:
#   F10 (Other) / F11 (ATC) / F12 (Ground Crew) etc.
#   Then number keys 1-0 to select options
#
# The menu structure depends on:
#   - Aircraft type
#   - Current state (airborne vs ground)
#   - What's available in the mission
#
# Format: Each command maps to a key sequence list
#   e.g., ["F11", "1", "3"] means press F11, then 1, then 3
# =============================================================================

# Common ATC commands (most aircraft share these)
ATC_COMMANDS = {
    "request_taxi": {
        "description": "Request taxi to runway",
        "keys": ["\\", "F1", "F1"],  # Radio menu → ATC → Request Taxi
        "context": "ground",
        "keywords": ["taxi", "taxiway", "taxi to runway"]
    },
    "request_takeoff": {
        "description": "Request takeoff clearance",
        "keys": ["\\", "F1", "F3"],
        "context": "ground",
        "keywords": ["takeoff", "take off", "departure", "ready for departure"]
    },
    "request_landing": {
        "description": "Request landing clearance",
        "keys": ["\\", "F1", "F2"],
        "context": "airborne",
        "keywords": ["landing", "inbound", "request landing", "approach"]
    },
    "declare_emergency": {
        "description": "Declare emergency",
        "keys": ["\\", "F1", "F5"],
        "context": "any",
        "keywords": ["emergency", "mayday", "declaring emergency"]
    },
}

# JTAC commands
JTAC_COMMANDS = {
    "request_target": {
        "description": "Request target information / 9-line",
        "keys": ["\\", "F2", "F1"],
        "context": "airborne",
        "keywords": ["9 line", "nine line", "target", "targets in area", "request target"]
    },
    "check_in": {
        "description": "Check in with JTAC",
        "keys": ["\\", "F2", "F2"],
        "context": "airborne",
        "keywords": ["check in", "checking in", "on station"]
    },
    "cleared_hot": {
        "description": "Request clearance to engage",
        "keys": ["\\", "F2", "F3"],
        "context": "airborne",
        "keywords": ["cleared hot", "request clearance", "weapons hot", "engage"]
    },
    "abort": {
        "description": "Abort attack run",
        "keys": ["\\", "F2", "F4"],
        "context": "airborne",
        "keywords": ["abort", "aborting", "wave off"]
    },
}

# Wingman commands
WINGMAN_COMMANDS = {
    "rejoin": {
        "description": "Order wingman to rejoin formation",
        "keys": ["\\", "F3", "F1"],
        "context": "any",
        "keywords": ["rejoin", "form up", "formation"]
    },
    "engage_target": {
        "description": "Order wingman to engage target",
        "keys": ["\\", "F3", "F4"],
        "context": "any",
        "keywords": ["engage", "attack", "weapons free"]
    },
    "cover_me": {
        "description": "Order wingman to cover",
        "keys": ["\\", "F3", "F3"],
        "context": "any",
        "keywords": ["cover me", "cover", "overwatch"]
    },
    "go_trail": {
        "description": "Order wingman to trail formation",
        "keys": ["\\", "F3", "F2"],
        "context": "any",
        "keywords": ["trail", "go trail", "fall back"]
    },
}

# Ground crew commands
GROUND_CREW_COMMANDS = {
    "rearm_refuel": {
        "description": "Request rearm and refuel",
        "keys": ["\\", "F8", "F1"],
        "context": "ground",
        "keywords": ["rearm", "refuel", "rearm and refuel", "hot pit"]
    },
    "ground_power": {
        "description": "Request ground power",
        "keys": ["\\", "F8", "F2"],
        "context": "ground",
        "keywords": ["ground power", "power", "gpu"]
    },
}

# =============================================================================
# AIRCRAFT-SPECIFIC OVERRIDES
# Some aircraft have different menu structures
# =============================================================================
AIRCRAFT_OVERRIDES = {
    "A-10C_2": {
        # A-10C II has JTAC on a different path
        # Add overrides here as we discover them
    },
    "F/A-18C": {
        # Hornet specifics
    },
    "F-16C_50": {
        # Viper specifics
    },
}

# =============================================================================
# ALL COMMANDS COMBINED
# =============================================================================
ALL_COMMANDS = {
    "atc": ATC_COMMANDS,
    "jtac": JTAC_COMMANDS,
    "wingman": WINGMAN_COMMANDS,
    "ground_crew": GROUND_CREW_COMMANDS,
}


# =============================================================================
# TIER 1 — STATIC ADDRESSEE MAP
# First 1-2 meaningful words → immediate routing (no LLM call).
# Keys are lowercase; values must match ALL_COMMANDS categories.
# =============================================================================
FILLER_WORDS = {"uh", "um", "hey", "yeah", "yes", "okay", "ok", "so", "like", "well", "er"}

ADDRESSEE_MAP = {
    # ATC addressees
    "tower":      {"category": "atc",        "action": "none"},
    "approach":   {"category": "atc",        "action": "none"},
    "departure":  {"category": "atc",        "action": "none"},
    "control":    {"category": "atc",        "action": "none"},
    "ground":     {"category": "atc",        "action": "none"},
    "atis":       {"category": "atc",        "action": "none"},
    "radar":      {"category": "atc",        "action": "none"},
    "mayday":     {"category": "atc",        "action": "declare_emergency"},
    "pan":        {"category": "atc",        "action": "declare_emergency"},
    # JTAC addressees
    "jtac":       {"category": "jtac",       "action": "none"},
    "overlord":   {"category": "jtac",       "action": "none"},
    "darkstar":   {"category": "jtac",       "action": "none"},
    "axeman":     {"category": "jtac",       "action": "none"},
    # Wingman callsigns
    "two":        {"category": "wingman",    "action": "none"},
    "three":      {"category": "wingman",    "action": "none"},
    "four":       {"category": "wingman",    "action": "none"},
    "wingman":    {"category": "wingman",    "action": "none"},
    "dash":       {"category": "wingman",    "action": "none"},
    # Ground crew
    "crew":       {"category": "ground_crew","action": "none"},
    "armorers":   {"category": "ground_crew","action": "none"},
    "maintenance":{"category": "ground_crew","action": "none"},
}


def _normalize_key(text: str, max_words: int = 3) -> str:
    """Strip filler words, lowercase, take first max_words meaningful words."""
    words = re.sub(r"[^\w\s]", "", text.lower()).split()
    meaningful = [w for w in words if w not in FILLER_WORDS]
    return " ".join(meaningful[:max_words])


# =============================================================================
# TIER 2 — LEARNED ROUTING CACHE
# Persists high-confidence LLM classifications across sessions.
# Grows automatically; delete routing_cache.json to reset.
# =============================================================================
CACHE_FILE = Path(__file__).parent / "routing_cache.json"
LEARN_THRESHOLD = 0.85   # Minimum LLM confidence to cache a result


class RoutingCache:
    def __init__(self, path: Path = CACHE_FILE):
        self.path = path
        self._cache: dict = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self._cache = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                self._cache = {}

    def _save(self):
        try:
            self.path.write_text(json.dumps(self._cache, indent=2))
        except OSError:
            pass

    def lookup(self, text: str) -> dict | None:
        key = _normalize_key(text)
        entry = self._cache.get(key)
        if entry:
            entry["hits"] = entry.get("hits", 0) + 1
            self._save()
            return {k: v for k, v in entry.items() if k != "hits"}
        return None

    def learn(self, text: str, result: dict, confidence: float):
        if confidence < LEARN_THRESHOLD:
            return
        if result.get("category", "none") == "none":
            return
        key = _normalize_key(text)
        existing = self._cache.get(key, {})
        self._cache[key] = {
            "category": result["category"],
            "action":   result["action"],
            "role":     result.get("role", result["category"]),
            "model":    result.get("model", "llama3.2:3b"),
            "hits":     existing.get("hits", 0),
        }
        self._save()


# =============================================================================
# INTENT CLASSIFIER
# Uses the LLM to classify what the player wants
# =============================================================================
CLASSIFY_PROMPT = """You are a radio command classifier for a military flight simulator.
Given a player's radio transmission, classify it into a structured command.

Available command categories and actions:

ATC (Air Traffic Control):
  request_taxi - Player wants to taxi to runway
  request_takeoff - Player wants takeoff clearance
  request_landing - Player wants landing clearance
  declare_emergency - Player is declaring an emergency

JTAC (Joint Terminal Attack Controller):
  request_target - Player wants target info or 9-line brief
  check_in - Player is checking in on station
  cleared_hot - Player wants weapons clearance
  abort - Player is aborting attack

WINGMAN:
  rejoin - Player wants wingman to rejoin formation
  engage_target - Player wants wingman to attack
  cover_me - Player wants wingman to provide cover
  go_trail - Player wants wingman in trail formation

GROUND_CREW:
  rearm_refuel - Player wants rearm/refuel
  ground_power - Player wants ground power connected

Respond with ONLY a JSON object, no other text:
{{"category": "atc|jtac|wingman|ground_crew", "action": "action_name", "confidence": 0.0-1.0}}

If the transmission doesn't match any command, respond:
{{"category": "none", "action": "none", "confidence": 0.0}}

Player transmission: "{player_text}"
"""


class CommandRouter:
    """Routes player voice commands to DCS key sequences.

    Three-tier routing:
      1. Static addressee map   (instant, no LLM)
      2. Learned phrase cache   (instant, grows from LLM hits)
      3. LLM classification     (slow fallback; high-confidence results → cache)
    """

    def __init__(self, ollama_url="http://localhost:11434/api/generate",
                 classify_model="llama3.2:3b"):
        self.ollama_url = ollama_url
        self.classify_model = classify_model
        self.cache = RoutingCache()

    def classify_intent(self, player_text: str) -> dict:
        """Use LLM to classify the player's radio transmission."""
        prompt = CLASSIFY_PROMPT.format(player_text=player_text)

        try:
            response = requests.post(self.ollama_url, json={
                "model": self.classify_model,
                "prompt": prompt,
                "stream": False,
                "keep_alive": "30m",
                "options": {
                    "num_predict": 50,
                    "temperature": 0.1  # Low temp for consistent classification
                }
            })

            raw = response.json()["response"].strip()
            # Clean up any markdown formatting
            raw = raw.replace("```json", "").replace("```", "").strip()
            result = json.loads(raw)
            return result

        except (json.JSONDecodeError, KeyError) as e:
            print(f"  ⚠ Classification failed: {e}")
            return {"category": "none", "action": "none", "confidence": 0.0}

    def get_key_sequence(self, category: str, action: str, aircraft: str = None) -> list:
        """Get the DCS key sequence for a classified command."""
        # Check aircraft-specific overrides first
        if aircraft and aircraft in AIRCRAFT_OVERRIDES:
            overrides = AIRCRAFT_OVERRIDES[aircraft]
            if action in overrides:
                return overrides[action].get("keys", [])

        # Fall back to default commands
        commands = ALL_COMMANDS.get(category, {})
        command = commands.get(action, {})
        return command.get("keys", [])

    def get_role_for_category(self, category: str) -> str:
        """Map command category to NPC role (for voice selection)."""
        role_map = {
            "atc": "atc",
            "jtac": "jtac",
            "wingman": "wingman",
            "ground_crew": "ground_crew"
        }
        return role_map.get(category, "atc")

    def get_model_for_category(self, category: str) -> str:
        """Select fast or rich LLM based on command category."""
        rich_categories = ["jtac", "wingman"]
        if category in rich_categories:
            return "llama3.1"
        return "llama3.2:3b"

    def route(self, player_text: str, aircraft: str = None) -> dict:
        """Full routing pipeline: tier 1 → tier 2 → tier 3 (LLM)."""
        print(f"\n🔀 Routing...")
        start = time.time()

        category = action = "none"
        confidence = 0.0
        tier_label = "llm"

        # --- Tier 1: static addressee keyword ---
        first_words = _normalize_key(player_text, max_words=2).split()
        for word in first_words:
            if word in ADDRESSEE_MAP:
                hit = ADDRESSEE_MAP[word]
                category = hit["category"]
                action = hit["action"]
                confidence = 1.0
                tier_label = "keyword"
                break

        # --- Tier 2: learned phrase cache ---
        if category == "none":
            cached = self.cache.lookup(player_text)
            if cached:
                category = cached["category"]
                action = cached["action"]
                confidence = 1.0
                tier_label = "cache"

        # --- Tier 3: LLM classification ---
        if category == "none":
            intent = self.classify_intent(player_text)
            category = intent.get("category", "none").lower()
            action = intent.get("action", "none").lower()
            confidence = intent.get("confidence", 0.0)
            tier_label = "llm"

        elapsed = time.time() - start
        print(f"  ✓ [{elapsed:.2f}s] [{tier_label}] {category}/{action} (conf: {confidence:.2f})")

        role = self.get_role_for_category(category)
        model = self.get_model_for_category(category)
        keys = self.get_key_sequence(category, action, aircraft)

        result = {
            "category": category,
            "action": action,
            "confidence": confidence,
            "keys": keys,
            "role": role,
            "model": model,
            "tier": tier_label,
        }

        # Teach tier 2 from LLM results
        if tier_label == "llm":
            self.cache.learn(player_text, result, confidence)

        if keys:
            print(f"  ✓ Keys: {' → '.join(keys)}")
        print(f"  ✓ Role: {role} | Model: {model}")

        return result


# =============================================================================
# STANDALONE TEST
# =============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("DCS AI Radio - Command Router Test")
    print("=" * 60)

    router = CommandRouter()

    test_phrases = [
        "Tower, request taxi to runway",
        "Requesting landing clearance",
        "JTAC, Enfield 1-1 checking in, ready for tasking",
        "Give me a 9 line",
        "Two, rejoin formation",
        "Ground crew, request rearm and refuel",
        "Mayday mayday mayday, declaring emergency",
        "Hey what's the weather like",
    ]

    for phrase in test_phrases:
        print(f"\n{'='*40}")
        print(f"Player: \"{phrase}\"")
        result = router.route(phrase)
        print(f"Result: {json.dumps(result, indent=2)}")