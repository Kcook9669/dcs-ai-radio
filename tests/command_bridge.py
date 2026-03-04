# =============================================================================
# DCS AI Radio - Command Bridge
# Sends commands to DCS via file-based communication with the hook script.
# Reads results back from DCS mission environment.
# =============================================================================

import json
import time
import os
from pathlib import Path
from autopatcher import patch_dcs_mission_scripting

# =============================================================================
# CONFIG
# =============================================================================
DCS_SAVED_GAMES = Path(os.environ["USERPROFILE"]) / "Saved Games" / "DCS"
SHARED_DIR = DCS_SAVED_GAMES / "dcs-ai-radio"
COMMAND_FILE = SHARED_DIR / "command.json"
RESULT_FILE = SHARED_DIR / "result.json"
HOOK_STATUS_FILE = SHARED_DIR / "hook_status.json"

# =============================================================================
# COMMAND BRIDGE.
# =============================================================================
class DCSCommandBridge:
    """Sends commands to DCS and reads results via file-based IPC."""

    def __init__(self, shared_dir=None):
        self.shared_dir = shared_dir or SHARED_DIR
        self.command_file = self.shared_dir / "command.json"
        self.result_file = self.shared_dir / "result.json"
        self.hook_status_file = self.shared_dir / "hook_status.json"

    def is_hook_ready(self):
        """Check if the DCS hook script is loaded and ready."""
        try:
            if self.hook_status_file.exists():
                data = json.loads(self.hook_status_file.read_text())
                return data.get("status") == "ready"
        except (json.JSONDecodeError, OSError):
            pass
        return False

    def send_command(self, category, action, timeout=1.0, **kwargs):
        """Send a command to DCS and wait for the result."""
        command = {
            "category": category,
            "action": action,
            "timestamp": time.time()
        }
        command.update(kwargs)

        # Clear old result
        if self.result_file.exists():
            try:
                self.result_file.unlink()
            except OSError:
                pass

        # Atomic write
        temp_path = self.command_file.with_suffix(".tmp")
        try:
            temp_path.write_text(json.dumps(command))
            if self.command_file.exists():
                self.command_file.unlink()
            temp_path.rename(self.command_file)
        except OSError as e:
            return {"error": f"Failed to write command: {e}"}

        # Wait for result
        start = time.time()
        while time.time() - start < timeout:
            if self.result_file.exists():
                try:
                    content = self.result_file.read_text()
                    if content.strip():
                        result = json.loads(content)
                        self.result_file.unlink()
                        return result
                except (json.JSONDecodeError, OSError):
                    pass
            time.sleep(0.05)

        return {"error": "timeout", "message": "No response from DCS within timeout"}

    # =========================================================================
    # CONVENIENCE METHODS
    # =========================================================================
    def get_airbases(self):
        """Get list of all airbases in the current mission."""
        result = self.send_command("atc", "get_airbases")
        if "error" not in result:
            names = result.get("result", "")
            return [n.strip() for n in names.split(",") if n.strip()]
        return []

    def get_airbase_weather(self, airbase_name):
        """Get wind/weather info for an airbase."""
        result = self.send_command("atc", "get_airbase_info", airbase=airbase_name)
        if "error" not in result:
            raw = result.get("result", "")
            weather = {}
            for pair in raw.split(","):
                if ":" in pair:
                    k, v = pair.split(":", 1)
                    try:
                        weather[k] = float(v)
                    except ValueError:
                        weather[k] = v
            return weather
        return {}

    def wingman_command(self, action, group_name):
        """Send a command to a wingman group."""
        return self.send_command("wingman", action, group_name=group_name)

    def display_message(self, text, duration=10):
        """Display a text message in DCS."""
        return self.send_command("message", "display", text=text, duration=duration)

    def jtac_command(self, action, **kwargs):
        """Send a command to the JTAC handler."""
        return self.send_command("jtac", action, **kwargs)

    def ground_crew_command(self, action, **kwargs):
        """Send a command to the ground crew handler."""
        return self.send_command("ground_crew", action, **kwargs)


# =============================================================================
# STANDALONE TEST
# =============================================================================
if __name__ == "__main__":
    patch_dcs_mission_scripting()
    print("=" * 60)
    print("DCS AI Radio - Command Bridge Test")
    print("=" * 60)

    bridge = DCSCommandBridge()

    if bridge.is_hook_ready():
        print("  Hook is ready")
    else:
        print("  Hook not ready - make sure DCS is running with a mission loaded")
        print(f"    Looking for: {bridge.hook_status_file}")

    print("\nTesting commands (DCS must be running):")

    print("\n[Test 1] Displaying message in DCS...")
    result = bridge.display_message("DCS AI Radio connected!", 5)
    print(f"  Result: {result}")

    print("\n[Test 2] Getting airbases...")
    airbases = bridge.get_airbases()
    if airbases:
        print(f"  Found {len(airbases)} airbases:")
        for ab in airbases[:10]:
            print(f"    - {ab}")
    else:
        print("  No airbases returned")

    if airbases:
        print(f"\n[Test 3] Getting weather at {airbases[0]}...")
        weather = bridge.get_airbase_weather(airbases[0])
        if weather:
            print(f"  Weather: {weather}")
        else:
            print("  No weather data returned")