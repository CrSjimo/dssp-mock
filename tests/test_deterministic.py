from __future__ import annotations

import numpy as np

from dssp_mock.services.deterministic import stable_hex
from dssp_mock.services.media import timbre_patch


def test_stable_hex_has_a_fixed_canonical_result() -> None:
    expected = "f345ca4e7bd8dbe6a81648bdaad3d36844fb2fb86db2d063d67103cd3c448392"

    assert stable_hex({"b": 2, "a": 1}) == expected
    assert stable_hex({"a": 1, "b": 2}) == expected


def test_stable_hex_separates_namespaces_and_part_boundaries() -> None:
    assert stable_hex("value", namespace="first") != stable_hex("value", namespace="second")
    assert stable_hex("ab", "c") != stable_hex("a", "bc")


def test_timbre_patch_is_stable_for_a_key_and_distinct_between_keys() -> None:
    first = timbre_patch("singer-alpha")
    repeated = timbre_patch("singer-alpha")
    different = timbre_patch("singer-beta")

    np.testing.assert_array_equal(first.harmonic_amplitudes, repeated.harmonic_amplitudes)
    assert first.attack == repeated.attack
    assert first.release == repeated.release
    assert first.vibrato_rate == repeated.vibrato_rate
    assert first.vibrato_depth == repeated.vibrato_depth

    assert not np.array_equal(first.harmonic_amplitudes, different.harmonic_amplitudes)
