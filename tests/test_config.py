from __future__ import annotations

import pytest
from pydantic import ValidationError

from dssp_mock.domain.config import (
    ArchitectureConfig,
    MockInstanceConfig,
    ParameterConfig,
    ParameterType,
    SingerConfig,
    default_config,
)


def test_architecture_rejects_unknown_parameter_dependency() -> None:
    with pytest.raises(ValidationError, match="unknown dependencies"):
        ArchitectureConfig(
            id="arch",
            name="Architecture",
            parameters=[
                ParameterConfig(
                    name="energy",
                    type=ParameterType.INDIRECT,
                    depends_on=["missing-pitch"],
                )
            ],
        )


def test_architecture_rejects_parameter_dependency_cycle() -> None:
    with pytest.raises(ValidationError, match="directed acyclic graph"):
        ArchitectureConfig(
            id="arch",
            name="Architecture",
            parameters=[
                ParameterConfig(name="a", depends_on=["b"]),
                ParameterConfig(name="b", depends_on=["c"]),
                ParameterConfig(name="c", depends_on=["a"]),
            ],
        )


def test_direct_parameter_cannot_declare_dependencies() -> None:
    with pytest.raises(ValidationError, match="DIRECT parameters cannot declare dependencies"):
        ParameterConfig(
            name="direct-input",
            type=ParameterType.DIRECT,
            depends_on=["pitch"],
        )


def test_indirect_parameter_can_depend_on_direct_parameter() -> None:
    architecture = ArchitectureConfig(
        id="arch",
        name="Architecture",
        parameters=[
            ParameterConfig(name="conditioning", type=ParameterType.DIRECT),
            ParameterConfig(
                name="generated",
                type=ParameterType.INDIRECT,
                depends_on=["conditioning"],
            ),
        ],
    )

    assert architecture.parameters[1].depends_on == ["conditioning"]


def test_instance_uses_required_parameter_rate_and_resource_ttl_defaults() -> None:
    instance = MockInstanceConfig(id="instance", name="Instance")

    assert instance.parameter_sample_rate == pytest.approx(100.0)
    assert instance.resource_ttl_seconds == 300
    assert instance.synthesis_delays_ms.model_dump() == {
        "pronunciation": 0.0,
        "phoneme": 0.0,
        "duration": 0.0,
        "parameter": 0.0,
        "audio": 0.0,
    }


def test_synthesis_delays_reject_negative_values() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        MockInstanceConfig(
            id="instance",
            name="Instance",
            synthesis_delays_ms={"audio": -1},
        )


def test_singer_languages_include_metadata_and_migrate_legacy_lists() -> None:
    singer = SingerConfig(
        id="singer",
        name="Singer",
        mix_group="default",
        languages={"zh": {"name": "中文", "default_lyric": "啦"}},
        default_language="zh",
        mock_key="singer",
    )
    legacy = SingerConfig(
        id="legacy",
        name="Legacy",
        mix_group="default",
        languages=["en"],
        default_language="en",
        mock_key="legacy",
    )

    assert singer.model_dump()["languages"] == {
        "zh": {"name": "中文", "default_lyric": "啦"}
    }
    assert legacy.model_dump()["languages"] == {
        "en": {"name": "en", "default_lyric": "la"}
    }


def test_parameter_ranges_default_and_pitch_is_always_fixed() -> None:
    regular = ParameterConfig(name="energy")
    pitch = ParameterConfig(name="pitch", min_value=-50, max_value=50)

    assert (regular.min_value, regular.max_value) == (-1000.0, 1000.0)
    assert (pitch.min_value, pitch.max_value) == (0.0, 12_800.0)


def test_default_architecture_matches_diffsinger_parameter_contract() -> None:
    architecture = default_config().instances[0].architectures[0]
    parameters = {
        parameter.name: (
            parameter.type,
            parameter.depends_on,
            parameter.min_value,
            parameter.max_value,
        )
        for parameter in architecture.parameters
    }

    assert (architecture.id, architecture.name) == ("diffsinger", "DiffSinger")
    assert parameters == {
        "expressiveness": (ParameterType.DIRECT, [], 0.0, 1_000.0),
        "pitch": (ParameterType.INDIRECT, ["expressiveness"], 0.0, 12_800.0),
        "energy": (ParameterType.INDIRECT, ["pitch"], -96_000.0, 0.0),
        "breathiness": (ParameterType.INDIRECT, ["pitch"], -96_000.0, 0.0),
        "tension": (ParameterType.INDIRECT, ["pitch"], -10_000.0, 10_000.0),
        "voicing": (ParameterType.INDIRECT, ["pitch"], -96_000.0, 0.0),
        "mouth_opening": (ParameterType.INDIRECT, ["pitch"], 0.0, 1_000.0),
        "gender": (ParameterType.DIRECT, [], -1_000.0, 1_000.0),
        "velocity": (ParameterType.DIRECT, [], -1_000.0, 1_000.0),
        "tone_shift": (ParameterType.DIRECT, [], -1_200.0, 1_200.0),
    }
    assert architecture.audio_dependencies == [
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


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(1.0, 1.0), (2.0, 1.0), (float("nan"), 1.0), (0.0, float("inf"))],
)
def test_non_pitch_parameter_range_must_be_finite_and_increasing(
    minimum: float, maximum: float
) -> None:
    with pytest.raises(ValidationError, match="parameter range"):
        ParameterConfig(name="energy", min_value=minimum, max_value=maximum)
