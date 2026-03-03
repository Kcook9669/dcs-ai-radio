# === Monkey-patch (must be before f5_tts import) ===
import soundfile as sf
import torch
import numpy as np
import torchaudio
converted_wav = "C:\\Users\\kcook\\OneDrive\\Coding\\dcs-ai-radio\\tests\\MyVoice\\Recording_24k.wav"
def _patched_load(filepath, converted_wav = converted_wav,  *args, **kwargs):
    print(f"DEBUG patch loading: {filepath}")
    # If F5-TTS is loading a temp file, load our pre-converted file instead
    if "tmp" in str(filepath).lower() or "temp" in str(filepath).lower():
        filepath = converted_wav
        print(f"DEBUG: redirected to {filepath}")
    data, samplerate = sf.read(filepath, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = data[np.newaxis, :]
    tensor = torch.from_numpy(data)
    if samplerate != 24000:
        resampler = torchaudio.transforms.Resample(orig_freq=samplerate, new_freq=24000)
        tensor = resampler(tensor)
        samplerate = 24000
    print(f"DEBUG patch: shape={tensor.shape}, sr={samplerate}, duration={tensor.shape[1]/samplerate:.1f}s")
    return tensor, samplerate

torchaudio.load = _patched_load
# === End patch ===

from f5_tts.api import F5TTS
from importlib.resources import files

# Check GPU
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Using device: {device}")

# Initialize F5-TTS
tts = F5TTS()

# === Test 1: F5-TTS built-in example ===
print("\n--- TEST 1: Built-in example ---")
example_path = str(files("f5_tts").joinpath("infer/examples/basic/basic_ref_en.wav"))
print(f"Example file: {example_path}")

tts.infer(
    ref_file=example_path,
    ref_text="Some have accepted this as a miracle without any physical explanation.",
    gen_text="Enfield 1-1, Batumi Approach. Winds 270 at 15, runway 13 in use. Cleared to land.",
    file_wave="test_example_output.wav"
)
print("Done - play test_example_output.wav")

# === Test 2: Your voice ===
print("\n--- TEST 2: Your voice ---")

# Pre-convert to mono 24kHz so F5-TTS doesn't mangle it
raw_wav = "C:\\Users\\kcook\\OneDrive\\Coding\\dcs-ai-radio\\tests\\MyVoice\\Recording.wav"
converted_wav = "C:\\Users\\kcook\\OneDrive\\Coding\\dcs-ai-radio\\tests\\MyVoice\\Recording_24k.wav"

data, sr = sf.read(raw_wav, dtype="float32")
if data.ndim > 1:
    data = data.mean(axis=1)  # stereo to mono
if sr != 24000:
    resampler = torchaudio.transforms.Resample(orig_freq=sr, new_freq=24000)
    data = resampler(torch.from_numpy(data).unsqueeze(0)).squeeze(0).numpy()
    sr = 24000
sf.write(converted_wav, data, sr, subtype='PCM_16')
print(f"Converted: {sr}Hz, {len(data)/sr:.1f}s")

reference_wav = converted_wav
import whisper
whisper_model = whisper.load_model("base")
result = whisper_model.transcribe(reference_wav)
reference_text = result["text"]
print(f"Whisper transcription: {reference_text}")

tts.infer(
    ref_file=reference_wav,
    ref_text=reference_text,
    gen_text="Enfield 1-1, Batumi Approach. Winds 270 at 15, runway 13 in use. Cleared to land.",
    file_wave="test_voice_output.wav"
)
print("Done - play test_voice_output.wav")