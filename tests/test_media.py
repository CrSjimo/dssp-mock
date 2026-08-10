from __future__ import annotations

import io
import math
import wave

import numpy as np
import pytest
from PIL import Image

from dssp_mock.services.media import AUDIO_SAMPLE_RATE, avatar_png, background_png, demo_audio


def test_avatar_and_background_are_stable_but_use_distinct_renderings() -> None:
    avatar = avatar_png("singer-key")
    background = background_png("singer-key")

    assert avatar_png("singer-key") == avatar
    assert background_png("singer-key") == background
    assert avatar != background

    with Image.open(io.BytesIO(avatar)) as avatar_image:
        assert avatar_image.format == "PNG"
        assert avatar_image.size == (256, 256)
    with Image.open(io.BytesIO(background)) as background_image:
        assert background_image.format == "PNG"
        assert background_image.size == (1024, 512)


def test_demo_audio_is_stable_44100_hz_mono_wav() -> None:
    audio = demo_audio("singer-key", 0)

    assert demo_audio("singer-key", 0) == audio
    with wave.open(io.BytesIO(audio), "rb") as wav_file:
        assert wav_file.getframerate() == AUDIO_SAMPLE_RATE == 44_100
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getnframes() > 0


def test_demo_audio_rms_is_close_to_minus_24_dbfs() -> None:
    audio = demo_audio("rms-test-singer", 1)

    with wave.open(io.BytesIO(audio), "rb") as wav_file:
        frames = wav_file.readframes(wav_file.getnframes())
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0
    rms = float(np.sqrt(np.mean(np.square(samples))))
    dbfs = 20.0 * math.log10(rms)

    assert dbfs == pytest.approx(-24.0, abs=0.15)
