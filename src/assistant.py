import base64
import io
import numpy as np
import soundfile as sf
from openai import OpenAI
import dotenv
dotenv.load_dotenv('.env.local')
import os
from silero_vad import load_silero_vad, get_speech_timestamps
import torch

vad_model = load_silero_vad()

client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

def has_speech(
    pcm_bytes: bytes,
    sample_rate: int = 16000,
) -> bool:
    """
    Returns True if speech is detected in the PCM buffer.
    Expects PCM16 LE mono audio.
    """

    if not pcm_bytes:
        return False

    audio = (
        torch.frombuffer(
            bytearray(pcm_bytes),  # make writable
            dtype=torch.int16,
        )
        .float()
        / 32768.0
    )

    if audio.numel() < sample_rate * 0.2:
        return False

    speech_timestamps = get_speech_timestamps(
        audio,
        vad_model,
        sampling_rate=sample_rate,
        threshold=0.5
    )

    return len(speech_timestamps) > 0



def pcm_base64_to_wav_bytes(
    pcm_bytes: bytes,
    sample_rate: int,
) -> io.BytesIO:
    """
    Convert base64 PCM S16LE mono audio to an in-memory WAV file.
    """
    

    # IMPORTANT: int16 little-endian
    audio = np.frombuffer(pcm_bytes, dtype=np.int16)

    # Convert to float32 for audio libs
    audio = audio.astype(np.float32) / 32768.0

    wav_io = io.BytesIO()
    sf.write(
        wav_io,
        audio,
        samplerate=sample_rate,
        format="WAV",
        subtype="PCM_16",
    )
    wav_io.seek(0)
    return wav_io

def transcribe_base64_pcm(
    b64_audio: str,
    sample_rate: int = 16000,
) -> str:
    wav_io = pcm_base64_to_wav_bytes(b64_audio, sample_rate)

    response = client.audio.transcriptions.create(
        file=("audio.wav", wav_io, "audio/wav"),
        model="gpt-4o-transcribe",
    )

    return response.text


