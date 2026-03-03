# =============================================================================
# DCS AI Radio - Phase 2 Pipeline
# Whisper STT → DCS State Context → Ollama LLM → F5-TTS Voice Cloning
#
# Prerequisites:
#   pip install soundfile numpy sounddevice whisper requests f5-tts
#   Ollama running with llama3.1 pulled
#   Export.lua installed in DCS Saved Games\Scripts\
#   A pre-converted 24kHz mono reference WAV for voice cloning
# =============================================================================

# === Monkey-patch torchaudio.load (must be before any f5_tts import) ===
import soundfile as sf
import torch
import numpy as np
import torchaudio

# Path to your pre-converted 24kHz mono reference clip
REFERENCE_WAV = "C:\\Users\\kcook\\OneDrive\\Coding\\dcs-ai-radio\\tests\\MyVoice\\Recording_24k.wav"

def _patched_load(filepath, *args, **kwargs):
    """Bypass broken torchcodec on PyTorch nightly + RTX 5080."""
    if "tmp" in str(filepath).lower() or "temp" in str(filepath).lower():
        filepath = REFERENCE_WAV
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
import numpy as np

# Import the DCS state module
# Adjust this path if dcs_state.py is elsewhere
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from dcs_state import BattlefieldState, DCSFileWatcher


# =============================================================================
# CONFIG
# =============================================================================
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"#"llama3.1"
SAMPLE_RATE = 16000          # Whisper expects 16kHz
RECORD_SECONDS = 5           # How long to record when PTT is pressed
REFERENCE_TEXT = None         # Auto-transcribed on startup


# =============================================================================
# SYSTEM PROMPT - Now built dynamically from DCS state
# =============================================================================
def build_prompt(player_text, battlefield_context):
    """Build the full LLM prompt with role + battlefield state + player input."""

    return f"""You are a military ATC. Respond with ONE short radio transmission.
Maximum 15 words. Use standard aviation phraseology.
Use the player's callsign from the battlefield data.
No narration, no stage directions, no explanations.

{battlefield_context}

Player: "{player_text}"

ATC:"""


# =============================================================================
# INITIALIZATION
# =============================================================================
def init():
    """Load all models, start DCS watcher, and prepare the pipeline."""
    print("=" * 60)
    print("DCS AI Radio - Phase 2 Pipeline (DCS Integration)")
    print("=" * 60)

    # Start DCS state watcher
    print("\n[1/4] Starting DCS state watcher...")
    battlefield = BattlefieldState()
    watcher = DCSFileWatcher(battlefield)
    watcher.start()

    # Load Whisper
    print("\n[2/4] Loading Whisper STT...")
    whisper_model = whisper.load_model("base", device="cpu")
    print("  ✓ Whisper ready")

    # Load F5-TTS
    print("\n[3/4] Loading F5-TTS...")
    tts = F5TTS()
    print("  ✓ F5-TTS ready")

    # Auto-transcribe reference clip
    print("\n[4/4] Transcribing reference voice clip...")
    ref_result = whisper_model.transcribe(REFERENCE_WAV)
    ref_text = ref_result["text"].strip()
    print(f"  ✓ Reference text: {ref_text}")

    # Test Ollama connection
    print("\nTesting Ollama connection...")
    try:
        test = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": "Say 'radio check'.",
            "stream": False
        }, timeout=10)
        test.raise_for_status()
        print("  ✓ Ollama ready")
    except Exception as e:
        print(f"  ✗ Ollama error: {e}")
        print("  Make sure Ollama is running (ollama serve)")
        return None

    return {
        "whisper": whisper_model,
        "tts": tts,
        "ref_text": ref_text,
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


def generate_response(player_text, battlefield_context):
    """Send player text + battlefield context to Ollama."""
    print("\n🤖 Generating response...")
    start = time.time()

    prompt = build_prompt(player_text, battlefield_context)

    response = requests.post(OLLAMA_URL, json={
    "model": OLLAMA_MODEL,
    "system": "You are a military ATC. Respond with ONE short radio transmission. Maximum 15 words. Use standard aviation phraseology. No narration, no explanations.",
    "prompt": f"Battlefield: {battlefield_context}\n\nPlayer: \"{player_text}\"\n\nATC:",
    "stream": False,
    "keep_alive": "30m",
    "options": {
        "num_predict": 40,
        "temperature": 0.7
    }
})

    reply = response.json()["response"].strip()
    elapsed = time.time() - start
    print(f"  ✓ [{elapsed:.1f}s] ATC says: \"{reply}\"")
    return reply


def speak(tts, ref_text, response_text):
    """Convert text to speech using F5-TTS voice cloning, then play it."""
    print("\n🔊 Generating speech...")
    start = time.time()

    output_path = os.path.join(tempfile.gettempdir(), "dcs_radio_output.wav")
    tts.infer(
        ref_file=REFERENCE_WAV,
        ref_text=ref_text,
        gen_text=response_text,
        file_wave=output_path,
        nfe_step=16
    )

    elapsed = time.time() - start
    print(f"  ✓ [{elapsed:.1f}s] Speech generated")

    # Play it back
    print("  ▶ Playing...")
    data, sr = sf.read(output_path, dtype="float32")
    data = data * 5.0  # Volume boost - adjust this number up or down
    data = np.clip(data, -1.0, 1.0)  # Prevent clipping
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
    print("=" * 60)

    while True:
        user_input = input("\n>> Press Enter to transmit (or 'quit'/'status'): ").strip()

        if user_input.lower() == "quit":
            print("Shutting down.")
            models["watcher"].stop()
            break

        if user_input.lower() == "status":
            context = get_battlefield_context(models["battlefield"])
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

        # Step 4: Generate LLM response with battlefield context
        response_text = generate_response(player_text, battlefield_context)

        # Step 5: Speak it
        speak(models["tts"], models["ref_text"], response_text)


if __name__ == "__main__":
    main()