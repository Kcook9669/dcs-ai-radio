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

import sounddevice as sd
import whisper
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


# =============================================================================
# CONFIG
# =============================================================================
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL_FAST = "llama3.2:3b"
OLLAMA_MODEL_RICH = "llama3.1"
SAMPLE_RATE = 16000
RECORD_SECONDS = 5


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
        result = whisper_model.transcribe(path_str)
        voice_ref_texts[path_str] = result["text"].strip()
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

    # Start DCS state watcher
    print("\n[1/5] Starting DCS state watcher...")
    battlefield = BattlefieldState()
    watcher = DCSFileWatcher(battlefield)
    watcher.start()

    # Load voice manager
    print("\n[2/5] Loading voice library...")
    voice_mgr = VoiceManager()

    # Load Whisper
    print("\n[3/5] Loading Whisper STT...")
    whisper_model = whisper.load_model("base")
    print("  ✓ Whisper ready")

    # Pre-transcribe voice library
    transcribe_voice_library(whisper_model, voice_mgr)

    # Load F5-TTS
    print("\n[4/5] Loading F5-TTS...")
    tts = F5TTS()
    print("  ✓ F5-TTS ready")

    # Test Ollama connection
    print("\n[5/5] Testing Ollama connection...")
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

    return {
        "whisper": whisper_model,
        "tts": tts,
        "voice_mgr": voice_mgr,
        "battlefield": battlefield,
        "watcher": watcher
    }


# =============================================================================
# PIPELINE STEPS
# =============================================================================
def record_audio():
    """Record audio from microphone."""
    print(f"\n🎤 Recording for {RECORD_SECONDS} seconds...")
    audio = sd.rec(
        int(RECORD_SECONDS * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32"
    )
    sd.wait()
    print("  ✓ Recording complete")
    return audio.flatten()


def transcribe(whisper_model, audio):
    """Convert speech to text using Whisper."""
    print("\n📝 Transcribing...")
    start = time.time()
    result = whisper_model.transcribe(audio, fp16=torch.cuda.is_available())
    text = result["text"].strip()
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


def generate_response(player_text, battlefield_context, model=None):
    """Send player text + battlefield context to Ollama."""
    if model is None:
        model = OLLAMA_MODEL_FAST

    print(f"\n🤖 Generating response ({model})...")
    start = time.time()

    prompt = build_prompt(player_text, battlefield_context)

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
    # Clean up any stray quotes the LLM might add
    reply = reply.strip('"').strip("'")
    elapsed = time.time() - start
    print(f"  ✓ [{elapsed:.1f}s] ATC says: \"{reply}\"")
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
        nfe_step=16
    )

    elapsed = time.time() - start
    print(f"  ✓ [{elapsed:.1f}s] Speech generated (voice: {os.path.basename(voice_file)})")

    # Play it back with volume boost
    print("  ▶ Playing...")
    data, sr = sf.read(output_path, dtype="float32")
    data = data * 7.0
    data = np.clip(data, -1.0, 1.0)
    sd.play(data, sr)
    sd.wait()
    print("  ✓ Done")


# =============================================================================
# MAIN LOOP
# =============================================================================
def main():
    models = init()
    if models is None:
        return

    print("\n" + "=" * 60)
    print("Pipeline ready!")
    print("  - Start a DCS mission (or have one running)")
    print("  - Press Enter to talk, type 'quit' to exit")
    print("  - Type 'status' to see current battlefield state")
    print("  - Type 'voices' to see voice assignments")
    print("=" * 60)

    while True:
        user_input = input("\n>> Press Enter to transmit (or 'quit'/'status'/'voices'): ").strip()

        if user_input.lower() == "quit":
            print("Shutting down.")
            models["watcher"].stop()
            break

        if user_input.lower() == "status":
            get_battlefield_context(models["battlefield"])
            continue

        if user_input.lower() == "voices":
            print(models["voice_mgr"].get_assignments_summary())
            continue

        # Step 1: Get current battlefield state
        battlefield_context = get_battlefield_context(models["battlefield"])

        # Step 2: Record
        audio = record_audio()

        # Step 3: Transcribe
        player_text = transcribe(models["whisper"], audio)
        if not player_text:
            print("  (Nothing detected, try again)")
            continue

        # Step 4: Generate LLM response
        # For now, everything goes through ATC with the fast model
        # Role detection will route to different NPCs and models later
        response_text = generate_response(
            player_text,
            battlefield_context,
            model=OLLAMA_MODEL_FAST
        )

        # Step 5: Speak it with ATC voice
        speak(models["tts"], models["voice_mgr"], "atc", response_text)


if __name__ == "__main__":
    main()