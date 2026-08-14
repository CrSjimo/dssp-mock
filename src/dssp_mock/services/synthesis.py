from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np

from dssp_mock.domain.api import (
    AudioInput,
    DurationInput,
    MultiSingerContext,
    ParameterInput,
    ParameterNote,
    SingleSingerContext,
)
from dssp_mock.domain.config import (
    ArchitectureConfig,
    MockInstanceConfig,
    ParameterConfig,
    ParameterType,
    PhonemeMode,
    PronunciationMode,
    SingerConfig,
)
from dssp_mock.services.deterministic import stable_hex, stable_random, stable_rng, stable_word
from dssp_mock.services.errors import invalid_request, not_found, unsupported
from dssp_mock.services.media import (
    AUDIO_SAMPLE_RATE,
    media_url,
    normalize_audio,
    render_voice,
    wav_bytes,
)
from dssp_mock.services.resource_store import ResourceStore

MAX_PIECE_DURATION_SECONDS = 120.0


def architecture_metadata(arch: ArchitectureConfig) -> dict:
    parameters: dict[str, dict] = {}
    for parameter in arch.parameters:
        item: dict = {"type": parameter.type.value}
        if parameter.type == ParameterType.INDIRECT:
            item["depends_on"] = list(parameter.depends_on)
        parameters[parameter.name] = item
    return {
        "id": arch.id,
        "name": arch.name,
        "pronunciation_mode": arch.pronunciation_mode.value,
        "phoneme_mode": arch.phoneme_mode.value,
        "parameters": parameters,
        "audio_dependencies": list(arch.audio_dependencies),
    }


def singer_metadata(arch: ArchitectureConfig, singer: SingerConfig) -> dict:
    return {
        "id": singer.id,
        "name": singer.name,
        "arch": arch.id,
        "mix_group": singer.mix_group,
        "languages": {
            code: info.model_dump(mode="json") for code, info in singer.languages.items()
        },
        "default_language": singer.default_language,
        "arch_specific_info": None,
        "default_extra": None,
    }


class SynthesisService:
    def __init__(
        self,
        instance: MockInstanceConfig,
        resource_store: ResourceStore,
        resource_base_url: str,
    ) -> None:
        self.instance = instance
        self.resource_store = resource_store
        self.resource_base_url = resource_base_url

    def get_arch(self, arch_id: str) -> ArchitectureConfig:
        for arch in self.instance.architectures:
            if arch.id == arch_id:
                return arch
        raise not_found("Architecture", arch_id)

    def get_singer(self, arch: ArchitectureConfig, singer_id: str) -> SingerConfig:
        for singer in arch.singers:
            if singer.id == singer_id:
                return singer
        raise not_found("Singer", singer_id)

    def single_context(
        self, context: SingleSingerContext
    ) -> tuple[ArchitectureConfig, SingerConfig]:
        arch = self.get_arch(context.arch)
        return arch, self.get_singer(arch, context.singer.id)

    def multi_context(
        self, context: MultiSingerContext
    ) -> tuple[ArchitectureConfig, list[SingerConfig]]:
        arch = self.get_arch(context.arch)
        singer_ids = [singer.id for singer in context.singers]
        if len(singer_ids) != len(set(singer_ids)):
            raise invalid_request("A synthesis context cannot contain the same singer twice.")
        singers = [self.get_singer(arch, singer_id) for singer_id in singer_ids]
        if len({singer.mix_group for singer in singers}) > 1:
            raise invalid_request("All singers in a synthesis context must share a mix_group.")
        return arch, singers

    def env_tag(self, context: MultiSingerContext) -> dict:
        _arch, singers = self.multi_context(context)
        mock_keys = sorted(singer.mock_key for singer in singers)
        return {"env_tag": stable_hex(mock_keys, namespace="environment-tag-v1")}

    def pronunciation(self, context: SingleSingerContext, notes: list) -> dict:
        arch, singer = self.single_context(context)
        if arch.pronunciation_mode == PronunciationMode.SKIP:
            raise unsupported(f"Architecture {arch.id!r} has pronunciation_mode=SKIP.")
        output = []
        for note in notes:
            self._validate_language(note.language, [singer])
            if note.lyric in {"AP", "SP", "-"}:
                pronunciation = note.lyric
                candidates: list[str] = []
            else:
                pronunciation = stable_word(
                    note.lyric,
                    note.language,
                    namespace="pronunciation-v1",
                )
                candidates = []
                if note.lyric.startswith("*"):
                    rng = stable_random(
                        note.lyric,
                        note.language,
                        namespace="pronunciation-candidate-count-v1",
                    )
                    count = rng.randint(1, 3)
                    for candidate_index in range(count):
                        candidate = stable_word(
                            note.lyric,
                            note.language,
                            candidate_index,
                            namespace="pronunciation-candidate-v1",
                        )
                        if candidate == pronunciation:
                            candidate = stable_word(
                                note.lyric,
                                note.language,
                                candidate_index,
                                "alternate",
                                namespace="pronunciation-candidate-v1",
                            )
                        candidates.append(candidate)
            output.append({"pronunciation": pronunciation, "candidates": candidates})
        return {"state": "COMPLETE", "output": {"notes": output}}

    def phoneme(self, context: SingleSingerContext, notes: list) -> dict:
        arch, singer = self.single_context(context)
        if arch.phoneme_mode == PhonemeMode.SKIP:
            raise unsupported(f"Architecture {arch.id!r} has phoneme_mode=SKIP.")
        output = []
        for note_index, note in enumerate(notes):
            self._validate_language(note.language, [singer])
            tokens = list(note.pronunciation)
            if not tokens:
                raise invalid_request(f"input.notes[{note_index}].pronunciation must not be empty.")
            phonemes = []
            for token_index, token in enumerate(tokens):
                rng = stable_random(
                    note.pronunciation,
                    note.language,
                    token_index,
                    token,
                    namespace="phoneme-onset-v1",
                )
                phonemes.append({"token": token, "onset": rng.random() < 0.32})
            if not any(item["onset"] for item in phonemes):
                rng = stable_random(
                    note.pronunciation,
                    note.language,
                    namespace="phoneme-required-onset-v1",
                )
                phonemes[rng.randrange(len(phonemes))]["onset"] = True
            output.append({"phonemes": phonemes})
        return {"state": "COMPLETE", "output": {"notes": output}}

    def duration(self, context: MultiSingerContext, input_data: DurationInput) -> dict:
        arch, singers = self.multi_context(context)
        if arch.phoneme_mode in {PhonemeMode.TOKEN_ONLY, PhonemeMode.SKIP}:
            raise unsupported(
                f"Architecture {arch.id!r} has phoneme_mode={arch.phoneme_mode.value}."
            )
        self._validate_common_input(
            input_data.piece_duration,
            input_data.notes,
            input_data.mix,
            input_data.mix_sample_rate,
            singers,
        )
        notes_output = []
        for note_index, note in enumerate(input_data.notes):
            if not note.phonemes:
                raise invalid_request(f"input.notes[{note_index}].phonemes must not be empty.")
            onset_indices = [i for i, phoneme in enumerate(note.phonemes) if phoneme.onset]
            if not onset_indices:
                raise invalid_request(
                    f"input.notes[{note_index}].phonemes must contain at least one onset."
                )
            first_onset = onset_indices[0]
            starts = np.zeros(len(note.phonemes), dtype=np.float64)
            if first_onset:
                gaps = []
                for index in range(first_onset):
                    phoneme = note.phonemes[index]
                    rng = stable_random(
                        note.position.model_dump(),
                        note.cent,
                        phoneme.token,
                        phoneme.language,
                        index,
                        namespace="duration-pre-onset-v1",
                    )
                    gaps.append(rng.uniform(0.008, 0.045))
                for index in range(first_onset):
                    starts[index] = -sum(gaps[index:])
            starts[first_onset] = 0.0
            current = 0.0
            for index in range(first_onset + 1, len(note.phonemes)):
                phoneme = note.phonemes[index]
                rng = stable_random(
                    note.position.model_dump(),
                    note.cent,
                    phoneme.token,
                    phoneme.language,
                    index,
                    namespace="duration-post-onset-v1",
                )
                current += rng.uniform(0.018, 0.095)
                starts[index] = current
            notes_output.append(
                {"phonemes": [{"start": round(float(start), 6)} for start in starts]}
            )
        return {"state": "COMPLETE", "output": {"notes": notes_output}}

    def parameter(self, context: MultiSingerContext, input_data: ParameterInput) -> dict:
        arch, singers = self.multi_context(context)
        self._validate_common_input(
            input_data.piece_duration,
            input_data.notes,
            input_data.mix,
            input_data.mix_sample_rate,
            singers,
        )
        configured = {parameter.name: parameter for parameter in arch.parameters}
        unknown = set(input_data.parameters) - set(configured)
        if unknown:
            raise invalid_request(f"Unknown input parameters: {sorted(unknown)}.")
        self._validate_parameter_values(input_data)

        for name, value in input_data.parameters.items():
            metadata = configured[name]
            if value.retake is None:
                continue
            if metadata.type == ParameterType.DIRECT:
                raise invalid_request(f"DIRECT parameter {name!r} cannot be retaken.")
            missing = set(metadata.depends_on) - set(input_data.parameters)
            if missing:
                raise invalid_request(
                    f"Parameter {name!r} requires input dependencies: {sorted(missing)}."
                )
            if value.retake.position + value.retake.length > len(value.values):
                raise invalid_request(
                    f"Retake range for parameter {name!r} exceeds its values array."
                )

        output_rate = self.instance.parameter_sample_rate
        target_times = _time_axis(input_data.piece_duration, output_rate)
        working = {
            name: _resample(parameter.values, parameter.sample_rate, target_times)
            for name, parameter in input_data.parameters.items()
        }
        order = _topological_order(arch)
        requested: dict[str, dict] = {}
        score_seed = {
            "notes": [note.model_dump(mode="json") for note in input_data.notes],
            "mix": input_data.mix,
            "mix_sample_rate": input_data.mix_sample_rate,
            "singers": sorted(singer.mock_key for singer in singers),
        }
        for name in order:
            if name not in input_data.parameters:
                continue
            source = input_data.parameters[name]
            metadata = configured[name]
            if source.retake is None:
                working[name] = _clip_parameter(metadata, working[name])
                continue
            dependency_seed = {
                dependency: np.round(working[dependency], 6).tolist()
                for dependency in sorted(metadata.depends_on)
            }
            generated = _generated_parameter_curve(
                metadata,
                target_times,
                input_data.notes,
                score_seed,
                dependency_seed,
            )
            start_time = source.retake.position / source.sample_rate
            end_time = (source.retake.position + source.retake.length) / source.sample_rate
            mask = (target_times >= start_time) & (target_times < end_time)
            combined = working[name].copy()
            combined[mask] = generated[mask]
            combined = _clip_parameter(metadata, combined)
            working[name] = combined
            requested[name] = {
                "values": np.round(combined, 6).tolist(),
                "sample_rate": output_rate,
            }
        return {"state": "COMPLETE", "output": {"parameters": requested}}

    def audio(self, context: MultiSingerContext, input_data: AudioInput) -> dict:
        arch, singers = self.multi_context(context)
        self._validate_common_input(
            input_data.piece_duration,
            input_data.notes,
            input_data.mix,
            input_data.mix_sample_rate,
            singers,
        )
        configured_names = {parameter.name for parameter in arch.parameters}
        unknown = set(input_data.parameters) - configured_names
        if unknown:
            raise invalid_request(f"Unknown audio parameters: {sorted(unknown)}.")
        missing = set(arch.audio_dependencies) - set(input_data.parameters)
        if missing:
            raise invalid_request(f"Missing audio dependencies: {sorted(missing)}.")
        for name, parameter in input_data.parameters.items():
            if not parameter.values:
                raise invalid_request(f"Audio parameter {name!r} must contain values.")
            if not _all_finite(parameter.values) or not math.isfinite(parameter.sample_rate):
                raise invalid_request(f"Audio parameter {name!r} contains non-finite values.")

        sample_count = int(math.floor(input_data.piece_duration * AUDIO_SAMPLE_RATE + 0.5))
        audio_times = np.arange(sample_count, dtype=np.float64) / AUDIO_SAMPLE_RATE
        note_cents, gate = _note_baseline(input_data.notes, audio_times, gate_notes=True)
        if "pitch" in input_data.parameters:
            pitch = input_data.parameters["pitch"]
            cents = _resample(pitch.values, pitch.sample_rate, audio_times)
        else:
            cents = note_cents
        cents = np.clip(cents, 0.0, 12_800.0)
        weights = _mix_weights(
            input_data.mix,
            input_data.mix_sample_rate,
            len(singers),
            audio_times,
        )
        audio = np.zeros(sample_count, dtype=np.float64)
        for singer_index, singer in enumerate(singers):
            voice = render_voice(cents, gate, singer.mock_key)
            audio += voice * weights[:, singer_index]
        audio = normalize_audio(audio)
        data = wav_bytes(audio)
        url = media_url(
            data,
            "audio/wav",
            "synthesis.wav",
            self.instance,
            self.resource_store,
            self.resource_base_url,
        )
        return {"state": "COMPLETE", "output": {"audio_url": url}}

    def _validate_common_input(
        self,
        piece_duration: float,
        notes: Iterable,
        mix: list[list[float]],
        mix_sample_rate: float,
        singers: list[SingerConfig],
    ) -> None:
        if not math.isfinite(piece_duration) or piece_duration > MAX_PIECE_DURATION_SECONDS:
            raise invalid_request(
                f"piece_duration must be finite and no greater than {MAX_PIECE_DURATION_SECONDS}."
            )
        if not math.isfinite(mix_sample_rate):
            raise invalid_request("mix_sample_rate must be finite.")
        cursor = 0.0
        for index, note in enumerate(notes):
            if not all(
                math.isfinite(value) for value in (note.position.gap, note.position.duration)
            ):
                raise invalid_request(f"input.notes[{index}].position contains non-finite values.")
            cursor += note.position.gap
            cursor += note.position.duration
            self._validate_language(note.language, singers)
            for phoneme in note.phonemes:
                self._validate_language(phoneme.language, singers)
        if cursor > piece_duration + 1e-6:
            raise invalid_request("The note timeline exceeds piece_duration.")
        expected_width = len(singers) - 1
        if len(singers) > 1 and not mix:
            raise invalid_request("mix must contain frames when more than one singer is used.")
        for row_index, row in enumerate(mix):
            if len(row) != expected_width:
                raise invalid_request(
                    f"mix[{row_index}] must contain exactly {expected_width} values."
                )
            if not _all_finite(row) or any(value < 0 or value > 1 for value in row):
                raise invalid_request(f"mix[{row_index}] values must be finite and in [0, 1].")
            if sum(row) > 1 + 1e-9:
                raise invalid_request(f"mix[{row_index}] values must sum to at most 1.")

    @staticmethod
    def _validate_language(language: str, singers: list[SingerConfig]) -> None:
        supported = {item for singer in singers for item in singer.languages}
        if language not in supported:
            raise invalid_request(
                f"Language {language!r} is not supported by the selected singer context."
            )

    @staticmethod
    def _validate_parameter_values(input_data: ParameterInput) -> None:
        for name, parameter in input_data.parameters.items():
            if not math.isfinite(parameter.sample_rate) or not _all_finite(parameter.values):
                raise invalid_request(f"Parameter {name!r} contains non-finite values.")


def _all_finite(values: Iterable[float]) -> bool:
    return all(math.isfinite(value) for value in values)


def _time_axis(duration: float, sample_rate: float) -> np.ndarray:
    count = int(math.ceil(duration * sample_rate - 1e-12))
    return np.arange(max(0, count), dtype=np.float64) / sample_rate


def _resample(values: list[float], sample_rate: float, target_times: np.ndarray) -> np.ndarray:
    if target_times.size == 0:
        return np.empty(0, dtype=np.float64)
    if not values:
        return np.zeros_like(target_times)
    if len(values) == 1:
        return np.full_like(target_times, float(values[0]))
    source_times = np.arange(len(values), dtype=np.float64) / sample_rate
    return np.interp(target_times, source_times, np.asarray(values, dtype=np.float64))


def _topological_order(arch: ArchitectureConfig) -> list[str]:
    graph = {parameter.name: parameter.depends_on for parameter in arch.parameters}
    result: list[str] = []
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        for dependency in graph[name]:
            visit(dependency)
        visited.add(name)
        result.append(name)

    for parameter in arch.parameters:
        visit(parameter.name)
    return result


def _generated_parameter_curve(
    parameter: ParameterConfig,
    times: np.ndarray,
    notes: list[ParameterNote],
    score_seed: dict,
    dependency_seed: dict,
) -> np.ndarray:
    if times.size == 0:
        return np.empty(0, dtype=np.float64)
    rng = stable_rng(
        parameter.name,
        score_seed,
        dependency_seed,
        namespace="parameter-curve-v1",
    )
    duration = float(times[-1]) if len(times) else 0.0
    anchor_rate = float(rng.uniform(2.0, 4.5))
    anchor_count = max(3, int(math.ceil(duration * anchor_rate)) + 2)
    anchors = rng.uniform(-1.0, 1.0, anchor_count)
    positions = times * anchor_rate
    left = np.floor(positions).astype(int)
    left = np.clip(left, 0, anchor_count - 2)
    fraction = np.clip(positions - left, 0.0, 1.0)
    smooth = fraction * fraction * (3 - 2 * fraction)
    noise = anchors[left] * (1 - smooth) + anchors[left + 1] * smooth
    if parameter.name == "pitch":
        baseline, _gate = _note_baseline(notes, times, gate_notes=False)
        amplitude = float(rng.uniform(18.0, 42.0))
        return baseline + noise * amplitude
    value_range = parameter.max_value - parameter.min_value
    midpoint = parameter.min_value + value_range / 2.0
    center = midpoint + value_range * float(rng.uniform(-0.08, 0.08))
    amplitude = value_range * float(rng.uniform(0.28, 0.48))
    return center + noise * amplitude


def _note_baseline(
    notes: Iterable,
    times: np.ndarray,
    *,
    gate_notes: bool,
) -> tuple[np.ndarray, np.ndarray]:
    cents = np.zeros_like(times)
    gate = np.zeros_like(times)
    cursor = 0.0
    last_cent = 6900.0
    for note in notes:
        start = cursor + note.position.gap
        end = start + note.position.duration
        if not gate_notes and start > cursor:
            gap_mask = (times >= cursor) & (times < start)
            cents[gap_mask] = last_cent
        note_mask = (times >= start) & (times < end)
        cents[note_mask] = note.cent
        if gate_notes:
            indices = np.flatnonzero(note_mask)
            if indices.size:
                envelope = np.ones(indices.size, dtype=np.float64)
                fade = min(indices.size // 2, int(0.008 * AUDIO_SAMPLE_RATE))
                if fade:
                    envelope[:fade] = np.linspace(0, 1, fade, endpoint=False)
                    envelope[-fade:] = np.linspace(1, 0, fade, endpoint=False)
                gate[indices] = envelope
        last_cent = float(note.cent)
        cursor = end
    if not gate_notes:
        cents[times >= cursor] = last_cent
    return cents, gate


def _clip_parameter(parameter: ParameterConfig, values: np.ndarray) -> np.ndarray:
    return np.clip(values, parameter.min_value, parameter.max_value)


def _mix_weights(
    mix: list[list[float]],
    mix_sample_rate: float,
    singer_count: int,
    target_times: np.ndarray,
) -> np.ndarray:
    if singer_count == 1:
        return np.ones((len(target_times), 1), dtype=np.float64)
    source = np.asarray(mix, dtype=np.float64)
    source = np.column_stack((source, 1.0 - np.sum(source, axis=1)))
    source_times = np.arange(len(source), dtype=np.float64) / mix_sample_rate
    weights = np.empty((len(target_times), singer_count), dtype=np.float64)
    for index in range(singer_count):
        weights[:, index] = np.interp(target_times, source_times, source[:, index])
    return weights
