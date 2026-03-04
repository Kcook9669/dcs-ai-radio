import winreg
import os
from pathlib import Path

def patch_dcs_mission_scripting():
    """Finds DCS install dir via Registry and patches MissionScripting.lua to load the mod and desanitize os, io, and lfs."""
    try:
        # 1. Find DCS installation path via Windows Registry
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Eagle Dynamics\DCS World")
        dcs_path, _ = winreg.QueryValueEx(key, "Path")
        winreg.CloseKey(key)
        
        scripting_file = Path(dcs_path) / "Scripts" / "MissionScripting.lua"
        
        if not scripting_file.exists():
            print(f"Error: Could not find MissionScripting.lua at {scripting_file}")
            return False

        with open(scripting_file, "r") as f:
            lines = f.readlines()

        needs_patching = False
        new_lines = []
        
        # 2. Check and fix sanitization for io, lfs, AND os
        for line in lines:
            if any(mod in line for mod in ["sanitizeModule('io')", "sanitizeModule('lfs')", "sanitizeModule('os')"]) and not line.strip().startswith("--"):
                new_lines.append(f"--{line}")
                needs_patching = True
            else:
                new_lines.append(line)
                
        # 3. Check if our auto-loader is already at the bottom
        already_has_loader = any("dcs-ai-radio-mission.lua" in line for line in lines)
        
        if not already_has_loader:
            new_lines.append("\n-- DCS AI Radio Auto-Loader\n")
            new_lines.append("local aiRadioScript = lfs.writedir() .. 'Scripts\\\\dcs-ai-radio-mission.lua'\n")
            new_lines.append("local f = io.open(aiRadioScript, 'r')\n")
            new_lines.append("if f then f:close(); dofile(aiRadioScript) end\n")
            needs_patching = True

        if not needs_patching:
            print("DCS MissionScripting.lua is already fully patched.")
            return True

        print("Patching DCS MissionScripting.lua to unlock 'os' and enable AI Radio...")
        with open(scripting_file, "w") as f:
            f.writelines(new_lines)

        print("Patch successful!")
        return True

    except Exception as e:
        print(f"Failed to auto-patch DCS (You may need to run Python as Administrator once). Error: {e}")
        return False

# Run the patcher
patch_dcs_mission_scripting()