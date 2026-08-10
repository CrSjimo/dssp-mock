from __future__ import annotations

import pytest
from pydantic import ValidationError

from dssp_mock.domain.config import (
    ArchitectureConfig,
    MockInstanceConfig,
    ParameterConfig,
    ParameterType,
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


def test_parameter_ranges_default_and_pitch_is_always_fixed() -> None:
    regular = ParameterConfig(name="energy")
    pitch = ParameterConfig(name="pitch", min_value=-50, max_value=50)

    assert (regular.min_value, regular.max_value) == (-1000.0, 1000.0)
    assert (pitch.min_value, pitch.max_value) == (0.0, 12_800.0)


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(1.0, 1.0), (2.0, 1.0), (float("nan"), 1.0), (0.0, float("inf"))],
)
def test_non_pitch_parameter_range_must_be_finite_and_increasing(
    minimum: float, maximum: float
) -> None:
    with pytest.raises(ValidationError, match="parameter range"):
        ParameterConfig(name="energy", min_value=minimum, max_value=maximum)
