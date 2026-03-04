# =============================================================================
# DCS AI Radio - Phase 2 Pipeline v3
# Whisper STT → DCS State Context → Ollama LLM → F5-TTS (Multi-Voice)
#
# Prerequisites:
#   pip install soundfile numpy sounddevice whisper requests f5-tts
#   Ollama running with llama3.1 and llama3.2:3b pulled
#   Export.lua installed in DCS Saved Games\Scripts\
#   Voice clips in voices\clips\ as 24kHz mono WAVs
# =============================================================================

# === Monkey-patch torchaudio.load (must be before any f5_tts import) ===
import soundfile as sf
import torch
import numpy as np
import torchaudio
from command_bridge import DCSCommandBridge
from command_router import CommandRouter
from autopatcher import patch_dcs_mission_scripting

bridge = DCSCommandBridge()  # Ensure we have the shared directory path

# Fallback reference for the monkey-patch redirect
FALLBACK_WAV = None  # Set dynamically based on current voice

def _patched_load(filepath, *args, **kwargs):
    """Bypass broken torchcodec on PyTorch nightly + RTX 5080."""
    global FALLBACK_WAV
    if "tmp" in str(filepath).lower() or "temp" in str(filepath).lower():
        if FALLBACK_WAV:
            filepath = FALLBACK_WAV
    data, samplerate = sf.read(filepath, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = data[np.newaxis, :]
    tensor = torch.from_numpy(data)
    if samplerate != 24000:
        resampler = torchaudio.transforms.Resample(orig_freq=samplerate, new_freq=24000)
        tensor = resampler(tensor)
        samplerate = 24000
    return tensor, samplerate

torchaudio.load = _patched_load
# === End patch ===

import concurrent.futures
import json
import math
import queue
import threading
import sounddevice as sd
from faster_whisper import WhisperModel
import requests
import tempfile
import os
import sys
import time
from f5_tts.api import F5TTS

# Import project modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from dcs_state import BattlefieldState, DCSFileWatcher
from voice_manager import VoiceManager
from proactive_radio import ProactiveRadio
from radio_settings import get_volume_gain, get_voice_speed, get_ptt_key, get_wingman_group, ensure_defaults
from audio_utils import clean_text_for_tts, apply_audio_envelope, apply_speed


# =============================================================================
# CONFIG
# =============================================================================
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL_FAST = "llama3.2:3b"
OLLAMA_MODEL_RICH = "llama3.1"
SAMPLE_RATE = 16000

# VAD recording config
VAD_THRESHOLD   = 0.015   # RMS level to consider speech (tune if too sensitive)
VAD_SILENCE_SEC = 1.2     # seconds of silence before cutting off
VAD_MAX_SEC     = 8.0     # hard cap on recording length
VAD_CHUNK_MS    = 80      # ms per detection chunk


# =============================================================================
# JTAC — BRA COMPUTATION (runs in Python from state.json data)
# =============================================================================
def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles between two lat/lon points."""
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a)) / 1852


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """True bearing in degrees from point 1 to point 2."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(rlat2)
    y = math.cos(rlat1) * math.sin(rlat2) - math.sin(rlat1) * math.cos(rlat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def fetch_jtac_context(battlefield) -> str:
    """Find nearest hostile and return BRA string for LLM prompt injection."""
    if battlefield is None:
        return "No battlefield data available."
    hostiles, (plat, plon) = battlefield.get_hostile_units_with_positions()
    if not hostiles or (plat == 0 and plon == 0):
        return "No hostile contacts in range."
    nearest = min(hostiles, key=lambda h: _haversine_nm(plat, plon, h["lat"], h["lon"]))
    rng_nm  = _haversine_nm(plat, plon, nearest["lat"], nearest["lon"])
    brng    = _bearing_deg(plat, plon, nearest["lat"], nearest["lon"])
    alt_ft  = nearest["alt_m"] * 3.281
    return (f"Nearest hostile: {nearest['type']}, BRA {brng:.0f}/{rng_nm:.1f}nm, "
            f"angels {alt_ft / 1000:.1f}. Total hostiles: {len(hostiles)}.")


# =============================================================================
# VOICE REFERENCE CACHE
# Stores Whisper transcriptions of each voice clip so we don't
# re-transcribe every time a unit speaks
# =============================================================================
voice_ref_texts = {}  # voice_file_path -> transcribed text


def transcribe_voice_library(whisper_model, voice_manager):
    """Pre-transcribe all voice reference clips on startup."""
    print("\n  Transcribing voice library...")
    for voice_path in voice_manager.voices:
        path_str = str(voice_path)
        segments, _ = whisper_model.transcribe(path_str, language="en", beam_size=1)
        voice_ref_texts[path_str] = "".join(s.text for s in segments).strip()
        print(f"    ✓ {voice_path.name}: \"{voice_ref_texts[path_str][:60]}...\"")
    print(f"  ✓ {len(voice_ref_texts)} voice references transcribed")


# =============================================================================
# PROMPT BUILDING
# =============================================================================
def build_prompt(player_text, battlefield_context):
    """Build the full LLM prompt with role + battlefield state + player input."""
    return f"""You are a military air traffic controller working approach and tower frequencies.
You have full awareness of the current battlefield situation shown below.
You speak in brief, professional radio calls using standard aviation phraseology.
Keep responses to one or two radio transmissions.
Use the player's actual callsign from the battlefield data.
Be direct and concise like a real controller.
Add small touches of personality - acknowledge good flying, warn about hazards, 
show awareness of the tactical situation.
Do not add any narration, stage directions, or explanations.
Only output the radio call itself.

{battlefield_context}

Player radio transmission: "{player_text}"

Your response as ATC:"""


# =============================================================================
# INITIALIZATION
# =============================================================================
def init():
    """Load all models, start DCS watcher, and prepare the pipeline."""
    print("=" * 60)
    print("DCS AI Radio - Phase 2 Pipeline v3 (Multi-Voice)")
    print("=" * 60)

    ensure_defaults()

    print("\n[0/7] Patching DCS MissionScripting.lua...")
    patch_dcs_mission_scripting()
    print("  ✓ DCS MissionScripting.lua patched")

    print("\n[1/7] Starting DCS state watcher...")
    bridge = DCSCommandBridge()  # Ensure we have the shared directory path
    print("  ✓ Command bridge ready")

    # Start DCS state watcher
    print("\n[2/7] Starting DCS state watcher...")
    battlefield = BattlefieldState()
    watcher = DCSFileWatcher(battlefield)
    watcher.start()

    print("\n[3/7] Initializing Command Router...")
    router = CommandRouter()

    # Load voice manager
    print("\n[4/7] Loading voice library...")
    voice_mgr = VoiceManager()

    # Load Whisper
    print("\n[5/7] Loading Whisper STT (faster-whisper)...")
    whisper_model = WhisperModel("base", device="cuda", compute_type="float16")
    print("  ✓ faster-whisper ready")

    # Pre-transcribe voice library
    transcribe_voice_library(whisper_model, voice_mgr)

    # Load F5-TTS
    print("\n[6/7] Loading F5-TTS...")
    tts = F5TTS()
    print("  ✓ F5-TTS ready")

    # Test Ollama connection
    print("\n[7/7] Testing Ollama connection...")
    try:
        for model in [OLLAMA_MODEL_FAST, OLLAMA_MODEL_RICH]:
            test = requests.post(OLLAMA_URL, json={
                "model": model,
                "prompt": "Say 'radio check'.",
                "stream": False,
                "keep_alive": "30m"
            }, timeout=30)
            test.raise_for_status()
            print(f"  ✓ {model} ready")
    except Exception as e:
        print(f"  ✗ Ollama error: {e}")
        print("  Make sure Ollama is running (ollama serve)")
        return None

    speaking_lock = threading.Lock()

    print("\n[+] Starting proactive AWACS radio...")
    proactive = ProactiveRadio(
        bridge=bridge,
        battlefield=battlefield,
        tts=tts,
        voice_mgr=voice_mgr,
        voice_ref_texts=voice_ref_texts,
        speaking_lock=speaking_lock,
        ollama_url=OLLAMA_URL,
    )
    proactive.start()

    return {
        "whisper": whisper_model,
        "tts": tts,
        "voice_mgr": voice_mgr,
        "battlefield": battlefield,
        "watcher": watcher,
        "router": router,
        "proactive": proactive,
        "speaking_lock": speaking_lock,
    }


# =============================================================================
# PIPELINE STEPS
# =============================================================================
def record_audio():
    """Record with energy-based VAD: starts on speech, stops after silence."""
    chunk_samples       = int(SAMPLE_RATE * VAD_CHUNK_MS / 1000)
    silence_chunks_need = int(VAD_SILENCE_SEC * 1000 / VAD_CHUNK_MS)
    max_chunks          = int(VAD_MAX_SEC    * 1000 / VAD_CHUNK_MS)

    print("\n🎤 Listening... (speak now)")
    chunks         = []
    speech_started = False
    silence_count  = 0

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
        for _ in range(max_chunks):
            chunk, _ = stream.read(chunk_samples)
            chunk     = chunk.flatten()
            rms       = float(np.sqrt(np.mean(chunk ** 2)))

            if rms > VAD_THRESHOLD:
                if not speech_started:
                    print("  🔴 Recording...")
                    speech_started = True
                silence_count = 0
                chunks.append(chunk)
            elif speech_started:
                silence_count += 1
                chunks.append(chunk)          # keep silence tail for naturalness
                if silence_count >= silence_chunks_need:
                    break

    if not chunks:
        return None

    audio = np.concatenate(chunks)
    print(f"  ✓ {len(audio) / SAMPLE_RATE:.1f}s captured")
    return audio


def transcribe(whisper_model, audio):
    """Convert speech to text using faster-whisper."""
    print("\n📝 Transcribing...")
    start = time.time()
    segments, _ = whisper_model.transcribe(audio, language="en", beam_size=1)
    text = "".join(s.text for s in segments).strip()
    elapsed = time.time() - start
    print(f"  ✓ [{elapsed:.1f}s] Player said: \"{text}\"")
    return text


def get_battlefield_context(battlefield):
    """Get current DCS state as a context string for the LLM."""
    context = battlefield.build_context_string()
    print(f"\n📡 Battlefield context:")
    for line in context.split("\n"):
        print(f"    {line}")
    return context


def fetch_dcs_atc_context():
    """Fetch closest airbase + weather from DCS. Runs in parallel with routing."""
    result = bridge.send_command("atc", "get_closest_airbase")
    closest_ab = result.get("result", "")
    if not closest_ab or "error" in closest_ab:
        if result.get("error") == "timeout":
            print(f"  ⚠ ATC: command.json timeout — mission script not reading commands")
        else:
            print(f"  ⚠ ATC: DCS returned: {closest_ab or result}")
        return None, None
    weather_result = bridge.send_command("atc", "get_airbase_info", airbase=closest_ab)
    return closest_ab, weather_result.get("result", "")


def generate_response(player_text, battlefield_context, intent_data, model=None,
                      closest_ab=None, dcs_weather=None):
    """Send player text + battlefield context + real-time ATC data to Ollama."""
    if model is None:
        model = OLLAMA_MODEL_FAST

    print(f"\n🤖 Generating response ({model})...")
    start = time.time()

    atc_context = ""
    category = intent_data.get("category", "none")

    if category == "atc":
        if closest_ab and "error" not in closest_ab:
            atc_context = f"\nYou are the ATC controller at {closest_ab} Airbase. Local Weather: {dcs_weather}."
            print(f"  ✅ Context anchored to: {closest_ab}")
        else:
            print("  ⚠ No DCS connection, using generic context")

    prompt = f"""
    SYSTEM: You are a professional DCS World Radio Operator.
    CURRENT BATTLEFIELD: {battlefield_context}
    {atc_context}

    PLAYER MESSAGE: "{player_text}"

    RESPONSE (Keep it under 20 words, use standard brevity):
    """

    response = requests.post(OLLAMA_URL, json={
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "num_predict": 40,
            "temperature": 0.7
        }
    })

    reply = response.json()["response"].strip()
    reply = reply.strip('"').strip("'")
    elapsed = time.time() - start

    speaker = closest_ab if category == "atc" and closest_ab else "AI"
    print(f"   ✓ [{elapsed:.1f}s] {speaker} says: \"{reply}\"")

    return reply


def speak(tts, voice_mgr, role, response_text):
    """Convert text to speech using F5-TTS with the assigned voice for this role."""
    global FALLBACK_WAV

    print("\n🔊 Generating speech...")
    start = time.time()

    # Get the voice for this role
    voice_file = voice_mgr.get_voice_by_role(role)
    ref_text = voice_ref_texts.get(voice_file, "")

    if not ref_text:
        print(f"  ⚠ No reference text for voice {voice_file}, skipping TTS")
        return

    # Set the fallback for the monkey-patch
    FALLBACK_WAV = voice_file

    output_path = os.path.join(tempfile.gettempdir(), "dcs_radio_output.wav")
    tts.infer(
        ref_file=voice_file,
        ref_text=ref_text,
        gen_text=response_text,
        file_wave=output_path,
        nfe_step=8,
    )

    elapsed = time.time() - start
    print(f"  ✓ [{elapsed:.1f}s] Speech generated (voice: {os.path.basename(voice_file)})")

    # Play it back with volume boost; adjust sample rate to control speed
    print("  ▶ Playing...")
    data, sr = sf.read(output_path, dtype="float32")
    data = apply_speed(apply_audio_envelope(data * get_volume_gain(role), sr), get_voice_speed(role))
    sd.play(np.clip(data, -1.0, 1.0), sr)
    sd.wait()
    print("  ✓ Done")


def generate_and_speak(player_text, battlefield_context, intent_data,
                        tts, voice_mgr, model=None,
                        closest_ab=None, dcs_weather=None,
                        cmd_result=None, battlefield=None):
    """Buffer full LLM response then single TTS call for natural inflection.

    LLM tokens are streamed and printed live (so the log feels responsive),
    but TTS fires once on the complete text — no mid-sentence inflection breaks.
    """
    global FALLBACK_WAV

    if model is None:
        model = OLLAMA_MODEL_FAST

    category = intent_data.get("category", "none")
    role     = intent_data.get("role", "atc")

    # Build role-specific context block
    context_addition = ""
    if category == "atc":
        if closest_ab and "error" not in closest_ab:
            context_addition = f"\nYou are the ATC controller at {closest_ab} Airbase. Local Weather: {dcs_weather}."
            print(f"  ✅ Context anchored to: {closest_ab}")
        else:
            print("  ⚠ No ATC context, using generic response")
    elif category == "jtac":
        target_info = fetch_jtac_context(battlefield)
        context_addition = (f"\nYou are a JTAC on the ground providing close air support. "
                            f"Current intel: {target_info} Use brevity codes.")
        print(f"  ✅ JTAC context: {target_info}")
    elif category == "wingman":
        context_addition = ("\nYou are the player's AI wingman, callsign Two. "
                            "Respond with brief military radio acknowledgements.")
        if cmd_result:
            ok = str(cmd_result.get("result", "")).startswith("ok:")
            context_addition += f" Order {'confirmed' if ok else 'could not be executed'}."
    elif category == "ground_crew":
        fuel_pct = int((battlefield.player.fuel_internal if battlefield else 0) * 100)
        context_addition = (f"\nYou are the ground crew chief. Player fuel at {fuel_pct}%. "
                            "Respond in crew chief style, brief and practical.")

    prompt = f"""
    SYSTEM: You are a professional DCS World Radio Operator.
    CURRENT BATTLEFIELD: {battlefield_context}
    {context_addition}

    PLAYER MESSAGE: "{player_text}"

    OUTPUT RULES: One radio call. Use commas between callsign, information, and instructions like a professional military radio operator. 10-20 words. No quotes, no labels, no stage directions.
    """

    voice_file = voice_mgr.get_voice_by_role(role)
    ref_text   = voice_ref_texts.get(voice_file, "")
    if not ref_text:
        print(f"  ⚠ No ref text for voice {voice_file}, skipping TTS")
        return
    FALLBACK_WAV = voice_file

    print(f"\n🤖 Generating ({model})... ", end="", flush=True)
    start     = time.time()
    full_text = ""

    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": model,
            "prompt": prompt,
            "stream": True,
            "keep_alive": "30m",
            "options": {"num_predict": 35, "temperature": 0.7, "stop": [".", "!", "?"]},
        }, stream=True, timeout=15)

        for line in resp.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            token = chunk.get("response", "")
            full_text += token
            print(token, end="", flush=True)
            if chunk.get("done"):
                break

    except Exception as e:
        print(f"\n  ✗ LLM error: {e}")
        return

    elapsed_llm = time.time() - start
    full_text   = full_text.strip().strip('"\'')
    speaker     = closest_ab if category == "atc" and closest_ab else "AI"
    print(f"\n  ✓ [{elapsed_llm:.1f}s] {speaker}: \"{full_text}\"")

    cleaned = clean_text_for_tts(full_text)
    if not cleaned:
        return

    path = os.path.join(tempfile.gettempdir(), "dcs_radio_out.wav")
    tts.infer(ref_file=voice_file, ref_text=ref_text,
              gen_text=cleaned, file_wave=path, nfe_step=16)

    data, sr = sf.read(path, dtype="float32")
    data = apply_speed(apply_audio_envelope(data * get_volume_gain(role), sr), get_voice_speed(role))
    print("  ▶ Playing...")
    sd.play(np.clip(data, -1.0, 1.0), sr)
    sd.wait()
    print(f"  ✓ Done [{time.time() - start:.1f}s total]")
    return full_text


# =============================================================================
# INPUT LISTENERS  (feed into a shared queue so the main loop is event-driven)
# =============================================================================
_tx_queue: queue.Queue = queue.Queue()


def _stdin_reader():
    """Read lines from stdin. Dashboard sends text or '__MIC__' trigger."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line == "__MIC__":
            _tx_queue.put(("mic", None))
        elif line.lower() == "quit":
            _tx_queue.put(("quit", None))
        elif line.lower() == "status":
            _tx_queue.put(("status", None))
        elif line.lower() == "voices":
            _tx_queue.put(("voices", None))
        else:
            _tx_queue.put(("text", line))


def _ptt_listener():
    """Global hotkey listener — works even while DCS has focus."""
    from pynput import keyboard as _kb

    key_name = get_ptt_key()          # e.g. "scroll_lock", "f9"
    try:
        target_key = getattr(_kb.Key, key_name)
        match = lambda k: k == target_key
    except AttributeError:
        match = lambda k: getattr(k, "char", None) == key_name

    def on_press(key):
        try:
            if match(key):
                _tx_queue.put(("mic", None))
        except Exception:
            pass

    with _kb.Listener(on_press=on_press, suppress=False) as listener:
        listener.join()


def _process_transmission(player_text, models):
    """Route + context fetch + LLM + TTS for one player transmission."""
    battlefield_context = get_battlefield_context(models["battlefield"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        route_future = executor.submit(models["router"].route, player_text)
        dcs_future   = executor.submit(fetch_dcs_atc_context)
        intent_data  = route_future.result()
        closest_ab, dcs_weather = dcs_future.result()

    # Execute DCS command if applicable
    cmd_result = None
    category = intent_data.get("category", "none")
    action   = intent_data.get("action", "none")

    if category == "wingman" and action not in ("none",):
        group = get_wingman_group()
        if group:
            cmd_result = bridge.wingman_command(action, group)
            print(f"  ⚡ Wingman [{action}] → {cmd_result.get('result', cmd_result.get('error'))}")
        else:
            print("  ⚠ Wingman command skipped: wingman_group not set in settings.json")

    with models["speaking_lock"]:
        generate_and_speak(
            player_text, battlefield_context, intent_data,
            models["tts"], models["voice_mgr"],
            model=intent_data.get("model", OLLAMA_MODEL_FAST),
            closest_ab=closest_ab, dcs_weather=dcs_weather,
            cmd_result=cmd_result,
            battlefield=models["battlefield"],
        )


# =============================================================================
# MAIN LOOP
# =============================================================================
def main():
    models = init()
    if models is None:
        return

    ptt = get_ptt_key()
    print("\n" + "=" * 60)
    print("Pipeline ready!")
    print(f"  PTT key : [{ptt}]  (change in Settings → ptt_key)")
    print(f"  In-game : press [{ptt}] to transmit (works while DCS has focus)")
    print(f"  Dashboard: click 'Use Mic' or type in the transmit bar")
    print(f"  Commands : status | voices | quit  (via dashboard text bar)")
    print("=" * 60)

    threading.Thread(target=_stdin_reader,  daemon=True).start()
    threading.Thread(target=_ptt_listener,  daemon=True).start()

    while True:
        try:
            kind, data = _tx_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        if kind == "quit":
            print("Shutting down.")
            models["watcher"].stop()
            models["proactive"].stop()
            break

        if kind == "status":
            get_battlefield_context(models["battlefield"])
            continue

        if kind == "voices":
            print(models["voice_mgr"].get_assignments_summary())
            continue

        if kind == "text":
            print(f"\n📝 Text input: \"{data}\"")
            _process_transmission(data, models)

        elif kind == "mic":
            audio = record_audio()
            if audio is None:
                print("  (No speech detected)")
                continue
            player_text = transcribe(models["whisper"], audio)
            if player_text:
                _process_transmission(player_text, models)


if __name__ == "__main__":
    main()