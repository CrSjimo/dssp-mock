from __future__ import annotations

import base64
import io
import math
import wave
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw

from dssp_mock.domain.config import MediaMode, MockInstanceConfig
from dssp_mock.services.deterministic import digest_bytes, stable_random, stable_rng
from dssp_mock.services.resource_store import ResourceStore

AUDIO_SAMPLE_RATE = 44_100
TARGET_RMS = 10 ** (-24 / 20)


@dataclass(frozen=True, slots=True)
class TimbrePatch:
    harmonic_amplitudes: np.ndarray
    attack: float
    release: float
    vibrato_rate: float
    vibrato_depth: float


@lru_cache(maxsize=512)
def timbre_patch(mock_key: str, harmonics: int = 12) -> TimbrePatch:
    rng = stable_rng(mock_key, namespace="timbre-patch-v1")
    decay = rng.uniform(0.7, 1.7)
    odd_gain = rng.uniform(0.65, 1.45)
    even_gain = rng.uniform(0.45, 1.3)
    formant_center = rng.uniform(2.0, 8.5)
    formant_width = rng.uniform(1.1, 3.2)
    indices = np.arange(1, harmonics + 1, dtype=np.float64)
    amplitudes = 1.0 / np.power(indices, decay)
    amplitudes *= np.where((indices.astype(int) % 2) == 1, odd_gain, even_gain)
    amplitudes *= 1.0 + rng.uniform(0.3, 1.2) * np.exp(
        -0.5 * ((indices - formant_center) / formant_width) ** 2
    )
    amplitudes *= rng.uniform(0.88, 1.12, size=harmonics)
    amplitudes /= np.sum(amplitudes)
    return TimbrePatch(
        harmonic_amplitudes=amplitudes,
        attack=float(rng.uniform(0.012, 0.07)),
        release=float(rng.uniform(0.05, 0.2)),
        vibrato_rate=float(rng.uniform(4.1, 6.4)),
        vibrato_depth=float(rng.uniform(0.0008, 0.004)),
    )


def render_voice(
    cents: np.ndarray,
    voiced: np.ndarray,
    mock_key: str,
    sample_rate: int = AUDIO_SAMPLE_RATE,
) -> np.ndarray:
    patch = timbre_patch(mock_key)
    safe_cents = np.clip(cents, 0.0, 12_800.0)
    frequency = 440.0 * np.power(2.0, (safe_cents - 6900.0) / 1200.0)
    time_axis = np.arange(len(cents), dtype=np.float64) / sample_rate
    vibrato = 1.0 + patch.vibrato_depth * np.sin(2 * np.pi * patch.vibrato_rate * time_axis)
    phase = np.cumsum(2 * np.pi * frequency * vibrato / sample_rate)
    signal = np.zeros_like(phase)
    for harmonic, amplitude in enumerate(patch.harmonic_amplitudes, start=1):
        signal += amplitude * np.sin(harmonic * phase)
    return signal * _identity_envelope(voiced, patch, sample_rate)


def _identity_envelope(voiced: np.ndarray, patch: TimbrePatch, sample_rate: int) -> np.ndarray:
    """Apply the singer-specific attack/release to every contiguous voiced region."""
    if voiced.size == 0:
        return voiced
    envelope = np.zeros_like(voiced, dtype=np.float64)
    active = voiced > 0
    padded = np.concatenate(([False], active, [False]))
    changes = np.flatnonzero(padded[1:] != padded[:-1])
    attack_limit = max(1, round(patch.attack * sample_rate))
    release_limit = max(1, round(patch.release * sample_rate))
    for start, end in changes.reshape(-1, 2):
        length = end - start
        segment = np.ones(length, dtype=np.float64)
        attack = min(length // 2, attack_limit)
        release = min(length // 2, release_limit)
        if attack:
            segment[:attack] = np.linspace(0.0, 1.0, attack, endpoint=False)
        if release:
            segment[-release:] = np.minimum(
                segment[-release:], np.linspace(1.0, 0.0, release, endpoint=False)
            )
        envelope[start:end] = segment
    return envelope


def normalize_audio(signal: np.ndarray, target_rms: float = TARGET_RMS) -> np.ndarray:
    if signal.size == 0:
        return signal.astype(np.float64)
    signal = np.nan_to_num(signal.astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    fade_samples = min(len(signal) // 2, int(AUDIO_SAMPLE_RATE * 0.01))
    if fade_samples:
        fade = np.linspace(0.0, 1.0, fade_samples, endpoint=False)
        signal[:fade_samples] *= fade
        signal[-fade_samples:] *= fade[::-1]
    rms = float(np.sqrt(np.mean(signal**2)))
    if rms > 1e-12:
        signal *= target_rms / rms
    peak = float(np.max(np.abs(signal), initial=0.0))
    if peak > 0.98:
        signal *= 0.98 / peak
    return signal


def wav_bytes(signal: np.ndarray, sample_rate: int = AUDIO_SAMPLE_RATE) -> bytes:
    pcm = np.round(np.clip(signal, -1.0, 1.0) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())
    return buffer.getvalue()


@lru_cache(maxsize=512)
def avatar_png(mock_key: str, size: int = 256) -> bytes:
    raw = digest_bytes(mock_key, namespace="avatar-identicon-v1")
    hue = int.from_bytes(raw[:2], "big") / 65535
    foreground = tuple(int(channel * 255) for channel in _hsv_to_rgb(hue, 0.62, 0.82))
    accent = tuple(int(channel * 255) for channel in _hsv_to_rgb((hue + 0.12) % 1, 0.5, 0.95))
    background = tuple(int(channel * 255) for channel in _hsv_to_rgb(hue, 0.12, 0.97))
    image = Image.new("RGB", (size, size), background)
    draw = ImageDraw.Draw(image)
    margin = size // 12
    cell = (size - margin * 2) / 5
    bits = int.from_bytes(raw[2:10], "big")
    for row in range(5):
        for col in range(3):
            if (bits >> (row * 3 + col)) & 1:
                color = accent if ((bits >> (32 + row * 3 + col)) & 1) else foreground
                for mirrored in {col, 4 - col}:
                    x0 = round(margin + mirrored * cell)
                    y0 = round(margin + row * cell)
                    x1 = round(margin + (mirrored + 1) * cell)
                    y1 = round(margin + (row + 1) * cell)
                    radius = max(2, size // 80)
                    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=color)
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()


@lru_cache(maxsize=256)
def background_png(mock_key: str, width: int = 1024, height: int = 512) -> bytes:
    rng = stable_random(mock_key, namespace="background-identicon-v1")
    hue = rng.random()
    color_a = np.array(_hsv_to_rgb(hue, 0.48, 0.38)) * 255
    color_b = np.array(_hsv_to_rgb((hue + 0.24) % 1, 0.66, 0.82)) * 255
    gradient = np.linspace(0.0, 1.0, width, dtype=np.float64)[None, :, None]
    pixels = color_a[None, None, :] * (1 - gradient) + color_b[None, None, :] * gradient
    pixels = np.repeat(pixels, height, axis=0).astype(np.uint8)
    image = Image.fromarray(pixels, "RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    for _index in range(22):
        radius = rng.randint(24, 145)
        x = rng.randint(-radius, width + radius)
        y = rng.randint(-radius, height + radius)
        hue_offset = (hue + rng.uniform(-0.18, 0.18)) % 1
        rgb = tuple(int(channel * 255) for channel in _hsv_to_rgb(hue_offset, 0.45, 0.95))
        alpha = rng.randint(18, 65)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*rgb, alpha))
    phase = rng.random() * math.tau
    points = []
    for x in range(-20, width + 21, 12):
        y = height * 0.58 + math.sin(x / 75 + phase) * height * 0.12
        points.append((x, y))
    draw.line(points, fill=(255, 255, 255, 68), width=8)
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()


@lru_cache(maxsize=512)
def demo_audio(mock_key: str, index: int, duration: float = 3.2) -> bytes:
    rng = stable_random(mock_key, index, namespace="demo-melody-v1")
    sample_count = int(duration * AUDIO_SAMPLE_RATE)
    cents = np.full(sample_count, 6900.0, dtype=np.float64)
    voiced = np.zeros(sample_count, dtype=np.float64)
    roots = [4800, 5000, 5200, 5500, 5700, 6000]
    scales = ([0, 200, 400, 700, 900], [0, 200, 300, 700, 900], [0, 300, 500, 700, 1000])
    root = rng.choice(roots)
    scale = rng.choice(scales)
    patterns = ([0.4, 0.4, 0.8, 0.4, 0.8], [0.6, 0.3, 0.3, 0.6, 0.6], [0.35] * 8)
    cursor = 0.12
    pattern = rng.choice(patterns)
    for note_index, length in enumerate(pattern):
        degree = rng.randrange(len(scale)) + (1 if note_index == len(pattern) - 1 else 0)
        octave, degree_index = divmod(degree, len(scale))
        note_cent = root + scale[degree_index] + octave * 1200
        start = int(cursor * AUDIO_SAMPLE_RATE)
        end = min(sample_count, int((cursor + length * 0.82) * AUDIO_SAMPLE_RATE))
        cents[start:end] = note_cent
        note_length = max(0, end - start)
        if note_length:
            attack = min(note_length // 3, int(0.035 * AUDIO_SAMPLE_RATE))
            release = min(note_length // 3, int(0.08 * AUDIO_SAMPLE_RATE))
            envelope = np.ones(note_length)
            if attack:
                envelope[:attack] = np.linspace(0, 1, attack, endpoint=False)
            if release:
                envelope[-release:] = np.linspace(1, 0, release, endpoint=False)
            voiced[start:end] = envelope
        cursor += length
        if cursor >= duration:
            break
    return wav_bytes(normalize_audio(render_voice(cents, voiced, mock_key)))


def media_url(
    data: bytes,
    media_type: str,
    filename: str,
    instance: MockInstanceConfig,
    store: ResourceStore,
    resource_base_url: str,
) -> str:
    if instance.media_mode == MediaMode.DATA_URL:
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{media_type};base64,{encoded}"
    token = store.put(data, media_type, filename, instance.resource_ttl_seconds)
    return f"{resource_base_url.rstrip('/')}/resources/{token}"


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[float, float, float]:
    sector = int(h * 6)
    fraction = h * 6 - sector
    p = v * (1 - s)
    q = v * (1 - fraction * s)
    t = v * (1 - (1 - fraction) * s)
    return ((v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q))[sector % 6]
