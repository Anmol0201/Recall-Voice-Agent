import base64
import io
import numpy as np
import soundfile as sf
from openai import OpenAI
import dotenv
dotenv.load_dotenv('.env.local')
import os

client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))


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