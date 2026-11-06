"""
Speech in and out for voice mode: the "ears" and "mouth" around the unchanged question pipeline (the Meghalaya voice
agent's gateway contract, as integrated into the KPCL voice instance).

  transcribe(audio)     qwen3-asr, OpenAI-compatible POST {ASR_BASE_URL}/audio/transcriptions (multipart, 16 kHz WAV)
  speak(text)           Qwen3-TTS on vLLM-Omni, POST {TTS_BASE_URL}/audio/speech -> whole WAV clip
  stream_speech(text)   the same request with stream=true -> raw s16le mono PCM chunks as they are decoded
                        (first audio in ~1 s instead of 4-5 s for a long sentence)

The voice is the officer reference clip (voices/officer_serena.wav, a synthetic Qwen3-TTS preset voice, no real
person's voice) cloned with TASK_TYPE=Base and styled by the Cescom persona line (dcb_voice_instructions). Every failure
is a VoiceUnavailable (the page then falls back to the browser's own voice) or VoiceBusy.
"""
from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.config import AI_DIR, settings


class VoiceUnavailable(RuntimeError):
    """Speech service off, unreachable, or it returned no audio."""


class VoiceBusy(RuntimeError):
    """Too many speech calls in flight; the caller answers 503 with Retry-After."""


_client: httpx.AsyncClient | None = None
_slots: asyncio.Semaphore | None = None
_SLOT_WAIT_SECONDS = 15.0
_ref_audio_uri: str | None = None


def init_voice_client() -> httpx.AsyncClient | None:
    global _client, _slots
    _slots = asyncio.Semaphore(settings.voice_max_concurrent_calls)
    if not (settings.asr_configured or settings.tts_configured):
        return None
    _client = httpx.AsyncClient(verify=settings.ai_model_ca_bundle_path or False,
                                timeout=max(settings.asr_timeout_seconds, settings.tts_timeout_seconds))
    return _client


async def shutdown_voice_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


@asynccontextmanager
async def _slot():
    sem = _slots or asyncio.Semaphore(settings.voice_max_concurrent_calls)
    try:
        await asyncio.wait_for(sem.acquire(), timeout=_SLOT_WAIT_SECONDS)
    except asyncio.TimeoutError:
        raise VoiceBusy("speech service busy") from None
    try:
        yield
    finally:
        sem.release()


# ---------------------------------------------------------------------------------------------- speech to text
async def transcribe(audio: bytes, filename: str = "audio.wav", language: str | None = "en",
                     prompt: str | None = None) -> str:
    if _client is None or not settings.asr_configured:
        raise VoiceUnavailable("speech to text is not configured")
    form = {"model": settings.asr_model}
    if language:
        form["language"] = language          # without it the multilingual model transcribes noise as Chinese
    if prompt:
        form["prompt"] = prompt
    try:
        async with _slot():
            r = await _client.post(f"{settings.asr_base_url.rstrip('/')}/audio/transcriptions",
                                   headers={"Authorization": f"Bearer {settings.asr_api_key}"}, data=form,
                                   files={"file": (filename, audio, "application/octet-stream")},
                                   timeout=settings.asr_timeout_seconds)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise VoiceUnavailable(f"speech to text failed: {e}") from e
    data = r.json()
    return str(data.get("text", "") if isinstance(data, dict) else data).strip()


# ---------------------------------------------------------------------------------------------- text to speech
def _ref_audio() -> str:
    """TTS_REF_AUDIO_PATH as a data: URI, read once. Relative paths resolve against ai_app/."""
    global _ref_audio_uri
    if _ref_audio_uri is None:
        path = settings.tts_ref_audio_path
        if not path:
            raise VoiceUnavailable("TTS_TASK_TYPE=Base needs TTS_REF_AUDIO_PATH")
        full = path if os.path.isabs(path) else str(AI_DIR / path)
        try:
            raw = open(full, "rb").read()
        except OSError as e:
            raise VoiceUnavailable(f"voice reference clip unreadable: {e}") from e
        mime = {".mp3": "audio/mpeg", ".flac": "audio/flac", ".ogg": "audio/ogg"}.get(os.path.splitext(full)[1].lower(), "audio/wav")
        _ref_audio_uri = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    return _ref_audio_uri


def tts_payload(text: str, response_format: str, speed: float | None = None) -> dict:
    payload = {"model": settings.tts_model, "input": text, "response_format": response_format,
               "task_type": settings.tts_task_type, "language": settings.tts_language,
               "max_new_tokens": settings.tts_max_new_tokens}
    if settings.tts_task_type == "Base":
        payload["ref_audio"] = _ref_audio()           # voice cloning: the reference clip is the voice
        payload["x_vector_only_mode"] = settings.tts_x_vector_only_mode
    elif settings.tts_voice:
        payload["voice"] = settings.tts_voice
    if settings.dcb_voice_instructions:
        payload["instructions"] = settings.dcb_voice_instructions
    if speed is not None and settings.tts_speed_enabled and abs(speed - 1.0) > 1e-6:
        payload["speed"] = speed
    return payload


def _tts_ready() -> None:
    if _client is None or not settings.tts_configured:
        raise VoiceUnavailable("text to speech is not enabled")


async def speak(text: str, speed: float | None = None) -> bytes:
    _tts_ready()
    payload = tts_payload(text, "wav", speed)
    try:
        async with _slot():
            r = await _client.post(f"{settings.tts_base_url.rstrip('/')}/audio/speech",
                                   headers={"Authorization": f"Bearer {settings.tts_api_key}"}, json=payload,
                                   timeout=settings.tts_timeout_seconds)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise VoiceUnavailable(f"text to speech failed: {e}") from e
    ctype = r.headers.get("content-type", "")
    if ctype.startswith(("application/json", "text/")) or not r.content:   # an error body served with a 200
        raise VoiceUnavailable(f"text to speech returned no audio (content-type={ctype!r})")
    return r.content


async def stream_speech(text: str, speed: float | None = None) -> AsyncIterator[bytes]:
    """Raw s16le mono PCM at TTS_STREAM_SAMPLE_RATE, yielded as decoded. Errors before the first chunk raise
    VoiceUnavailable / VoiceBusy; after it, the stream just ends early (the listener has heard part of it)."""
    _tts_ready()
    payload = tts_payload(text, "pcm", speed)
    payload["stream"] = True
    payload["stream_format"] = "audio"
    async with _slot():
        try:
            async with _client.stream("POST", f"{settings.tts_base_url.rstrip('/')}/audio/speech",
                                      headers={"Authorization": f"Bearer {settings.tts_api_key}"}, json=payload,
                                      timeout=settings.tts_timeout_seconds) as r:
                if r.status_code >= 400:
                    body = (await r.aread())[:300]
                    raise VoiceUnavailable(f"text to speech returned {r.status_code}: {body!r}")
                if r.headers.get("content-type", "").startswith(("application/json", "text/")):
                    raise VoiceUnavailable("text to speech returned no audio")
                async for chunk in r.aiter_bytes():
                    if chunk:
                        yield chunk
        except httpx.HTTPError as e:
            raise VoiceUnavailable(f"text to speech stream failed: {e}") from e
