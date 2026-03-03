# =============================================================================
# DCS AI Radio - Voice Manager
# Assigns voice reference clips to units and maintains consistency
#
# Each unit gets a voice assigned at first encounter based on a hash of
# their unit ID. Same unit = same voice across the entire session.
# =============================================================================

import os
import hashlib
from pathlib import Path


# =============================================================================
# CONFIG
# =============================================================================
VOICE_DIR = Path("C:\\Users\\kcook\\OneDrive\\Coding\\dcs-ai-radio\\voices\\clips")


# =============================================================================
# VOICE MANAGER
# =============================================================================
class VoiceManager:
    """Assigns and tracks voice clips for each unit in the mission."""

    def __init__(self, voice_dir: Path = VOICE_DIR):
        self.voice_dir = voice_dir
        self.voices = []
        self.assignments = {}  # unit_id -> voice file path

        # Load available voice files
        self._load_voices()

    def _load_voices(self):
        """Scan the voice directory for WAV files."""
        if not self.voice_dir.exists():
            print(f"  ⚠ Voice directory not found: {self.voice_dir}")
            return

        self.voices = sorted([
            f for f in self.voice_dir.iterdir()
            if f.suffix.lower() == ".wav"
        ])

        print(f"  ✓ Loaded {len(self.voices)} voices from {self.voice_dir}")
        for v in self.voices:
            print(f"    - {v.name}")

    def get_voice(self, unit_id: str) -> str:
        """Get the voice file path for a unit. Assigns one if not yet assigned."""
        if not self.voices:
            return None

        # Return existing assignment
        if unit_id in self.assignments:
            return str(self.assignments[unit_id])

        # Assign a voice based on hash of unit ID (consistent across calls)
        hash_val = int(hashlib.md5(unit_id.encode()).hexdigest(), 16)
        index = hash_val % len(self.voices)
        self.assignments[unit_id] = self.voices[index]

        print(f"  🎙 Assigned voice '{self.voices[index].name}' to unit '{unit_id}'")
        return str(self.assignments[unit_id])

    def get_voice_by_role(self, role: str) -> str:
        """Get a consistent voice for a role like 'atc' or 'jtac'."""
        return self.get_voice(f"role_{role}")

    def reset(self):
        """Clear all assignments (e.g., on new mission)."""
        self.assignments.clear()
        print("  ✓ Voice assignments cleared")

    def get_assignments_summary(self) -> str:
        """Return a readable summary of current voice assignments."""
        if not self.assignments:
            return "No voice assignments yet."

        lines = ["Current voice assignments:"]
        for unit_id, voice in self.assignments.items():
            lines.append(f"  {unit_id} → {voice.name}")
        return "\n".join(lines)


# =============================================================================
# STANDALONE TEST
# =============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("DCS AI Radio - Voice Manager Test")
    print("=" * 60)

    vm = VoiceManager()

    # Simulate unit assignments
    test_units = ["unit_001", "unit_002", "unit_003", "unit_001", "unit_004", "unit_005", "unit_006"]

    print("\nAssigning voices to units:")
    for unit in test_units:
        voice = vm.get_voice(unit)
        print(f"  {unit} → {os.path.basename(voice)}")

    # Role-based assignments
    print("\nRole-based assignments:")
    for role in ["atc", "jtac", "wingman"]:
        voice = vm.get_voice_by_role(role)
        print(f"  {role} → {os.path.basename(voice)}")

    print(f"\n{vm.get_assignments_summary()}")