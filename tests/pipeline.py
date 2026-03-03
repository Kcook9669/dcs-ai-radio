# =============================================================================
# DCS AI Radio - Phase 1 Pipeline Proof of Concept
# Whisper STT → Ollama LLM → F5-TTS Voice Cloning
#
# Prerequisites:
#   pip install soundfile numpy sounddevice whisper requests f5-tts
#   Ollama running with llama3.1 pulled
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
import time
from f5_tts.api import F5TTS


# =============================================================================
# CONFIG
# =============================================================================
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.1"
SAMPLE_RATE = 16000          # Whisper expects 16kHz
RECORD_SECONDS = 5           # How long to record when PTT is pressed
REFERENCE_TEXT = None         # Auto-transcribed on startup


# =============================================================================
# SYSTEM PROMPT - Basic ATC for now, will be replaced with context injection
# =============================================================================
SYSTEM_PROMPT = """You are a military air traffic controller at Batumi airbase in the Republic of Georgia.
You speak in brief, professional radio calls using standard aviation phraseology.
Keep responses SHORT - one or two radio transmissions max.
Use proper callsigns. Be direct and concise like a real controller.
Do not add any narration, stage directions, or explanations.
Only output the radio call itself."""


# =============================================================================
# INITIALIZATION
# =============================================================================
def init():
    """Load all models and prepare the pipeline."""
    print("=" * 60)
    print("DCS AI Radio - Phase 1 Pipeline")
    print("=" * 60)

    # Load Whisper
    print("\n[1/3] Loading Whisper STT...")
    whisper_model = whisper.load_model("base")
    print("  ✓ Whisper ready")

    # Load F5-TTS
    print("\n[2/3] Loading F5-TTS...")
    tts = F5TTS()
    print("  ✓ F5-TTS ready")

    # Auto-transcribe reference clip
    print("\n[3/3] Transcribing reference voice clip...")
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
        "ref_text": ref_text
    }


# =============================================================================
# PIPELINE STEPS
# =============================================================================
def record_audio():
    """Record audio from microphone. Press Enter to start, waits RECORD_SECONDS."""
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


def generate_response(player_text):
    """Send player text to Ollama and get ATC response."""
    print("\n🤖 Generating response...")
    start = time.time()

    prompt = f"""{SYSTEM_PROMPT}

Player radio call: "{player_text}"

Your response as Batumi ATC:"""

    response = requests.post(OLLAMA_URL, json={
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False
    })

    reply = response.json()["response"].strip()
    elapsed = time.time() - start
    print(f"  ✓ [{elapsed:.1f}s] ATC says: \"{reply}\"")
    return reply


def speak(tts, ref_text, response_text):
    """Convert text to speech using F5-TTS voice cloning, then play it."""
    print("\n🔊 Generating speech...")
    start = time.time()

    # Generate to temp file
    output_path = os.path.join(tempfile.gettempdir(), "dcs_radio_output.wav")
    tts.infer(
        ref_file=REFERENCE_WAV,
        ref_text=ref_text,
        gen_text=response_text,
        file_wave=output_path
    )

    elapsed = time.time() - start
    print(f"  ✓ [{elapsed:.1f}s] Speech generated")

    # Play it back
    print("  ▶ Playing...")
    data, sr = sf.read(output_path, dtype="float32")
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
    print("Pipeline ready! Press Enter to talk, type 'quit' to exit.")
    print("=" * 60)

    while True:
        user_input = input("\n>> Press Enter to transmit (or 'quit'): ")
        if user_input.lower() == "quit":
            print("Shutting down.")
            break

        # Step 1: Record
        audio = record_audio()

        # Step 2: Transcribe
        player_text = transcribe(models["whisper"], audio)
        if not player_text:
            print("  (Nothing detected, try again)")
            continue

        # Step 3: Generate LLM response
        response_text = generate_response(player_text)

        # Step 4: Speak it
        speak(models["tts"], models["ref_text"], response_text)


if __name__ == "__main__":
    main()