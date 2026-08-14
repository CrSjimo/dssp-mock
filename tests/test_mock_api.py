from __future__ import annotations

import base64
import io
import math
import re
import wave
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from dssp_mock.api.mock import create_mock_app
from dssp_mock.domain.config import SingerConfig
from dssp_mock.repositories.config_repository import ConfigRepository
from dssp_mock.services.request_log import RequestLogStore
from dssp_mock.services.resource_store import ResourceStore, resource_app

ARCH_ID = "diffsinger"
PRIMARY_SINGER_ID = "demo-singer"
SECONDARY_SINGER_ID = "second-singer"


@dataclass(slots=True)
class ApiHarness:
    client: TestClient
    repository: ConfigRepository
    resources: ResourceStore
    logs: RequestLogStore


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ApiHarness]:
    config_path = Path(tmp_path_factory.mktemp("mock-api")) / "config.json"
    repository = ConfigRepository(config_path)
    config = repository.get()
    config.instances[0].architectures[0].singers.append(
        SingerConfig(
            id=SECONDARY_SINGER_ID,
            name="Second Singer",
            mix_group="default",
            languages={
                "zh": {"name": "中文", "default_lyric": "啦"},
                "en": {"name": "English", "default_lyric": "la"},
            },
            default_language="zh",
            mock_key="second-singer-key",
        )
    )
    repository.replace(config)

    resources = ResourceStore()
    logs = RequestLogStore()
    app = create_mock_app(
        "default",
        repository,
        resources,
        logs,
        "http://resources.test",
    )
    with TestClient(app) as client:
        yield ApiHarness(client, repository, resources, logs)


def _singer(singer_id: str) -> dict:
    return {"id": singer_id, "extra": None}


def _single_context(singer_id: str = PRIMARY_SINGER_ID) -> dict:
    return {"arch": ARCH_ID, "arch_extra": None, "singer": _singer(singer_id)}


def _multi_context(singer_ids: tuple[str, ...] = (PRIMARY_SINGER_ID,)) -> dict:
    return {
        "arch": ARCH_ID,
        "arch_extra": None,
        "singers": [_singer(singer_id) for singer_id in singer_ids],
    }


def _parameter_note(duration: float = 0.3) -> dict:
    return {
        "position": {"gap": 0.0, "duration": duration},
        "cent": 6900,
        "pronunciation": "a",
        "language": "zh",
        "phonemes": [{"token": "a", "onset": True, "language": "zh", "start": 0.0}],
    }


def _parameter_payload(parameters: dict, duration: float = 0.3) -> dict:
    return {
        "context": _multi_context(),
        "input": {
            "piece_duration": duration,
            "notes": [_parameter_note(duration)],
            "mix": [[]],
            "mix_sample_rate": 10.0,
            "parameters": parameters,
        },
    }


def _assert_problem(response, expected_status: int) -> dict:
    assert response.status_code == expected_status
    assert response.headers["content-type"].split(";", 1)[0] == "application/problem+json"
    problem = response.json()
    assert {"type", "title", "status", "detail", "instance"} <= problem.keys()
    assert problem["status"] == expected_status
    return problem


def _decode_data_url(url: str, media_type: str) -> bytes:
    prefix = f"data:{media_type};base64,"
    assert url.startswith(prefix)
    return base64.b64decode(url.removeprefix(prefix), validate=True)


def test_all_metadata_and_media_endpoints(api: ApiHarness) -> None:
    info = api.client.get("/v1/info")
    assert info.status_code == 200
    assert info.json() == {"dssp": {"api_version": 1}}

    architectures = api.client.get("/v1/arch", params={"display_language": "zh"})
    assert architectures.status_code == 200
    assert [item["id"] for item in architectures.json()] == [ARCH_ID]
    assert architectures.json()[0]["parameters"]["pitch"] == {
        "type": "INDIRECT",
        "depends_on": ["expressiveness"],
    }

    architecture = api.client.get(f"/v1/arch/{ARCH_ID}")
    assert architecture.status_code == 200
    assert architecture.json()["audio_dependencies"] == [
        "pitch",
        "breathiness",
        "tension",
        "voicing",
        "energy",
        "mouth_opening",
        "gender",
        "velocity",
        "tone_shift",
    ]

    all_singers = api.client.get("/v1/singer")
    assert all_singers.status_code == 200
    assert {item["id"] for item in all_singers.json()} == {
        PRIMARY_SINGER_ID,
        SECONDARY_SINGER_ID,
    }

    arch_singers = api.client.get(f"/v1/arch/{ARCH_ID}/singer")
    assert arch_singers.status_code == 200
    assert len(arch_singers.json()) == 2

    singer = api.client.get(f"/v1/arch/{ARCH_ID}/singer/{PRIMARY_SINGER_ID}")
    assert singer.status_code == 200
    assert singer.json()["languages"] == {
        "zh": {"name": "中文", "default_lyric": "啦"},
        "en": {"name": "English", "default_lyric": "la"},
        "ja": {"name": "日本語", "default_lyric": "ラ"},
    }
    assert singer.json()["default_language"] == "zh"
    assert singer.json()["arch_specific_info"] is None
    assert singer.json()["default_extra"] is None

    avatar = api.client.get(f"/v1/arch/{ARCH_ID}/singer/{PRIMARY_SINGER_ID}/avatar")
    assert avatar.status_code == 200
    assert avatar.json()["avatar_url"].startswith("data:image/png;base64,")

    background = api.client.get(f"/v1/arch/{ARCH_ID}/singer/{PRIMARY_SINGER_ID}/background")
    assert background.status_code == 200
    assert background.json()["background_url"].startswith("data:image/png;base64,")

    demos = api.client.get(f"/v1/arch/{ARCH_ID}/singer/{PRIMARY_SINGER_ID}/demo_audio")
    assert demos.status_code == 200
    assert [item["name"] for item in demos.json()] == ["Demo 1", ""]
    assert all(item["audio_url"].startswith("data:audio/wav;base64,") for item in demos.json())


def test_env_tag_is_independent_of_singer_order(api: ApiHarness) -> None:
    forward = api.client.post(
        "/v1/env_tag",
        json={"context": _multi_context((PRIMARY_SINGER_ID, SECONDARY_SINGER_ID))},
    )
    reverse = api.client.post(
        "/v1/env_tag",
        json={"context": _multi_context((SECONDARY_SINGER_ID, PRIMARY_SINGER_ID))},
    )

    assert forward.status_code == reverse.status_code == 200
    assert forward.json() == reverse.json()
    assert re.fullmatch(r"[0-9a-f]{64}", forward.json()["env_tag"])


def test_pronunciation_special_tokens_candidates_and_stream_are_deterministic(
    api: ApiHarness,
) -> None:
    payload = {
        "context": _single_context(),
        "input": {
            "notes": [
                {"lyric": "AP", "language": "zh"},
                {"lyric": "SP", "language": "zh"},
                {"lyric": "-", "language": "zh"},
                {"lyric": "普通", "language": "zh"},
                {"lyric": "*多音", "language": "zh"},
            ]
        },
        "stream": True,
    }

    first = api.client.post("/v1/synth/pronunciation", json=payload)
    second = api.client.post("/v1/synth/pronunciation", json=payload)

    assert first.status_code == second.status_code == 200
    assert first.headers["content-type"].split(";", 1)[0] == "application/json"
    assert "ndjson" not in first.headers["content-type"]
    assert first.json() == second.json()
    assert first.json()["state"] == "COMPLETE"
    notes = first.json()["output"]["notes"]
    assert [note["pronunciation"] for note in notes[:3]] == ["AP", "SP", "-"]
    assert all(note["candidates"] == [] for note in notes[:3])
    assert re.fullmatch(r"[a-z]{2,6}", notes[3]["pronunciation"])
    assert re.fullmatch(r"[a-z]{2,6}", notes[4]["pronunciation"])
    assert 1 <= len(notes[4]["candidates"]) <= 3
    assert all(re.fullmatch(r"[a-z]{2,6}", item) for item in notes[4]["candidates"])


def test_phoneme_splits_characters_and_always_has_an_onset(api: ApiHarness) -> None:
    payload = {
        "context": _single_context(),
        "input": {"notes": [{"pronunciation": "abcd", "language": "en"}]},
    }

    response = api.client.post("/v1/synth/phoneme", json=payload)

    assert response.status_code == 200
    assert response.json()["state"] == "COMPLETE"
    phonemes = response.json()["output"]["notes"][0]["phonemes"]
    assert [item["token"] for item in phonemes] == list("abcd")
    assert any(item["onset"] for item in phonemes)


def test_duration_starts_are_deterministic_increasing_and_onset_anchored(
    api: ApiHarness,
) -> None:
    payload = {
        "context": _multi_context(),
        "input": {
            "piece_duration": 0.4,
            "notes": [
                {
                    "position": {"gap": 0.02, "duration": 0.3},
                    "cent": 6900,
                    "pronunciation": "abcd",
                    "language": "zh",
                    "phonemes": [
                        {"token": "a", "onset": False, "language": "zh"},
                        {"token": "b", "onset": False, "language": "zh"},
                        {"token": "c", "onset": True, "language": "zh"},
                        {"token": "d", "onset": False, "language": "zh"},
                    ],
                }
            ],
            "mix": [[]],
            "mix_sample_rate": 10.0,
        },
    }

    first = api.client.post("/v1/synth/duration", json=payload)
    second = api.client.post("/v1/synth/duration", json=payload)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    starts = [item["start"] for item in first.json()["output"]["notes"][0]["phonemes"]]
    assert starts[2] == 0
    assert np.all(np.diff(starts) > 0)
    assert starts[0] >= -(2 * 0.05)


def test_parameter_retake_resamples_and_clips_the_complete_curve(api: ApiHarness) -> None:
    payload = _parameter_payload(
        {
            "expressiveness": {"values": [1000.0], "sample_rate": 10.0},
            "pitch": {
                "values": [20_000.0, 6900.0, -500.0],
                "sample_rate": 10.0,
                "retake": {"position": 1, "length": 1},
            },
        }
    )

    response = api.client.post("/v1/synth/parameter", json=payload)

    assert response.status_code == 200
    output = response.json()["output"]["parameters"]["pitch"]
    assert output["sample_rate"] == pytest.approx(100.0)
    assert len(output["values"]) == 30
    assert min(output["values"]) >= 0.0
    assert max(output["values"]) <= 12_800.0
    assert output["values"][0] == 12_800.0
    assert output["values"][-1] == 0.0
    assert 6000.0 < output["values"][15] < 8000.0


def test_parameter_uses_its_configured_response_range(api: ApiHarness) -> None:
    original = api.repository.get()
    changed = original.model_copy(deep=True)
    energy = next(
        parameter
        for parameter in changed.instances[0].architectures[0].parameters
        if parameter.name == "energy"
    )
    energy.min_value = 10.0
    energy.max_value = 20.0
    api.repository.replace(changed)
    try:
        payload = _parameter_payload(
            {
                "pitch": {"values": [6900.0, 6900.0, 6900.0], "sample_rate": 10.0},
                "energy": {
                    "values": [-9999.0, 9999.0, -9999.0],
                    "sample_rate": 10.0,
                    "retake": {"position": 0, "length": 3},
                },
            }
        )

        response = api.client.post("/v1/synth/parameter", json=payload)

        assert response.status_code == 200
        values = response.json()["output"]["parameters"]["energy"]["values"]
        assert values
        assert min(values) >= 10.0
        assert max(values) <= 20.0
        assert max(values) - min(values) > 0.1
    finally:
        api.repository.replace(original)


@pytest.mark.parametrize(
    ("parameters", "detail_fragment"),
    [
        (
            {
                "expressiveness": {
                    "values": [0.0],
                    "sample_rate": 10.0,
                    "retake": {"position": 0, "length": 1},
                }
            },
            "DIRECT parameter",
        ),
        (
            {
                "energy": {
                    "values": [0.0],
                    "sample_rate": 10.0,
                    "retake": {"position": 0, "length": 1},
                }
            },
            "requires input dependencies",
        ),
    ],
)
def test_parameter_rejects_direct_retake_and_missing_dependencies(
    api: ApiHarness,
    parameters: dict,
    detail_fragment: str,
) -> None:
    problem = _assert_problem(
        api.client.post("/v1/synth/parameter", json=_parameter_payload(parameters)),
        422,
    )
    assert detail_fragment in problem["detail"]


def test_audio_returns_44100_hz_mono_wav_near_minus_24_dbfs(api: ApiHarness) -> None:
    duration = 0.12
    payload = {
        "context": _multi_context(),
        "input": {
            "piece_duration": duration,
            "notes": [_parameter_note(0.1)],
            "mix": [[]],
            "mix_sample_rate": 10.0,
            "parameters": {
                "pitch": {"values": [6900.0, 7000.0], "sample_rate": 10.0},
                "breathiness": {"values": [-12_000.0], "sample_rate": 10.0},
                "tension": {"values": [0.0], "sample_rate": 10.0},
                "voicing": {"values": [-12_000.0], "sample_rate": 10.0},
                "energy": {"values": [-12_000.0], "sample_rate": 10.0},
                "mouth_opening": {"values": [500.0], "sample_rate": 10.0},
                "gender": {"values": [0.0], "sample_rate": 10.0},
                "velocity": {"values": [0.0], "sample_rate": 10.0},
                "tone_shift": {"values": [0.0], "sample_rate": 10.0},
            },
        },
    }

    response = api.client.post("/v1/synth/audio", json=payload)

    assert response.status_code == 200
    assert response.json()["state"] == "COMPLETE"
    wav_data = _decode_data_url(response.json()["output"]["audio_url"], "audio/wav")
    with wave.open(io.BytesIO(wav_data), "rb") as wav_file:
        assert wav_file.getframerate() == 44_100
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getnframes() == round(duration * 44_100)
        frames = wav_file.readframes(wav_file.getnframes())
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0
    rms = float(np.sqrt(np.mean(np.square(samples))))
    assert 20.0 * math.log10(rms) == pytest.approx(-24.0, abs=0.2)


def test_each_synthesis_step_uses_its_configured_delay(
    api: ApiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = api.repository.get()
    changed = original.model_copy(deep=True)
    delays = changed.instances[0].synthesis_delays_ms
    delays.pronunciation = 11
    delays.phoneme = 22
    delays.duration = 33
    delays.parameter = 44
    delays.audio = 55
    api.repository.replace(changed)
    sleep_seconds: list[float] = []
    monkeypatch.setattr("dssp_mock.api.mock.time.sleep", sleep_seconds.append)

    try:
        responses = [
            api.client.post(
                "/v1/synth/pronunciation",
                json={
                    "context": _single_context(),
                    "input": {"notes": [{"lyric": "la", "language": "zh"}]},
                },
            ),
            api.client.post(
                "/v1/synth/phoneme",
                json={
                    "context": _single_context(),
                    "input": {"notes": [{"pronunciation": "la", "language": "zh"}]},
                },
            ),
            api.client.post(
                "/v1/synth/duration",
                json={
                    "context": _multi_context(),
                    "input": {
                        "piece_duration": 0.3,
                        "notes": [_parameter_note()],
                        "mix": [[]],
                        "mix_sample_rate": 10.0,
                    },
                },
            ),
            api.client.post("/v1/synth/parameter", json=_parameter_payload({})),
            api.client.post(
                "/v1/synth/audio",
                json={
                    "context": _multi_context(),
                    "input": {
                        "piece_duration": 0.12,
                        "notes": [_parameter_note(0.1)],
                        "mix": [[]],
                        "mix_sample_rate": 10.0,
                        "parameters": {
                            "pitch": {"values": [6900.0], "sample_rate": 10.0},
                            "breathiness": {
                                "values": [-12_000.0],
                                "sample_rate": 10.0,
                            },
                            "tension": {"values": [0.0], "sample_rate": 10.0},
                            "voicing": {
                                "values": [-12_000.0],
                                "sample_rate": 10.0,
                            },
                            "energy": {
                                "values": [-12_000.0],
                                "sample_rate": 10.0,
                            },
                            "mouth_opening": {
                                "values": [500.0],
                                "sample_rate": 10.0,
                            },
                            "gender": {"values": [0.0], "sample_rate": 10.0},
                            "velocity": {"values": [0.0], "sample_rate": 10.0},
                            "tone_shift": {
                                "values": [0.0],
                                "sample_rate": 10.0,
                            },
                        },
                    },
                },
            ),
        ]
    finally:
        api.repository.replace(original)

    assert all(response.status_code == 200 for response in responses)
    assert sleep_seconds == pytest.approx([0.011, 0.022, 0.033, 0.044, 0.055])


def test_synthesis_requests_have_no_concurrency_limit(api: ApiHarness) -> None:
    original = api.repository.get()
    changed = original.model_copy(deep=True)
    changed.instances[0].synthesis_delays_ms.pronunciation = 100
    api.repository.replace(changed)
    payload = {
        "context": _single_context(),
        "input": {"notes": [{"lyric": "la", "language": "zh"}]},
    }

    try:
        with ThreadPoolExecutor(max_workers=6) as executor:
            responses = list(
                executor.map(
                    lambda _index: api.client.post("/v1/synth/pronunciation", json=payload),
                    range(6),
                )
            )
    finally:
        api.repository.replace(original)

    assert [response.status_code for response in responses] == [200] * 6


def test_missing_api_resource_and_invalid_schema_are_problem_json(api: ApiHarness) -> None:
    _assert_problem(api.client.get("/v1/arch/does-not-exist"), 404)
    _assert_problem(api.client.post("/v1/env_tag", json={}), 422)

    with TestClient(resource_app(api.resources)) as resources:
        _assert_problem(resources.get("/resources/does-not-exist"), 404)


def test_every_request_produces_a_log_entry(api: ApiHarness) -> None:
    api.logs.clear("default")

    response = api.client.get("/v1/info")

    assert response.status_code == 200
    entries = api.logs.list("default")
    assert len(entries) == 1
    assert entries[0]["method"] == "GET"
    assert entries[0]["path"] == "/v1/info"
    assert entries[0]["status_code"] == 200
    assert '"api_version": 1' in entries[0]["response"]
