from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MAX_SINGERS = 16
MAX_NOTES = 10_000
MAX_PHONEMES_PER_NOTE = 256
MAX_MIX_FRAMES = 1_000_000
MAX_PARAMETER_POINTS = 2_000_000
MAX_PARAMETERS = 256
MAX_TEXT_LENGTH = 4096


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Singer(ApiModel):
    id: str = Field(max_length=MAX_TEXT_LENGTH)
    extra: Any


class SingleSingerContext(ApiModel):
    arch: str = Field(max_length=MAX_TEXT_LENGTH)
    arch_extra: Any
    singer: Singer


class MultiSingerContext(ApiModel):
    arch: str = Field(max_length=MAX_TEXT_LENGTH)
    arch_extra: Any
    singers: list[Singer] = Field(min_length=1, max_length=MAX_SINGERS)


class Lyric(ApiModel):
    lyric: str = Field(max_length=MAX_TEXT_LENGTH)
    language: str = Field(max_length=MAX_TEXT_LENGTH)


class PronunciationInput(ApiModel):
    notes: list[Lyric] = Field(max_length=MAX_NOTES)


class PronunciationRequest(ApiModel):
    context: SingleSingerContext
    input: PronunciationInput
    stream: bool = False


class PronunciationNote(ApiModel):
    pronunciation: str = Field(max_length=MAX_TEXT_LENGTH)
    language: str = Field(max_length=MAX_TEXT_LENGTH)


class PhonemeInput(ApiModel):
    notes: list[PronunciationNote] = Field(max_length=MAX_NOTES)


class PhonemeRequest(ApiModel):
    context: SingleSingerContext
    input: PhonemeInput
    stream: bool = False


class NotePosition(ApiModel):
    gap: float = Field(ge=0)
    duration: float = Field(ge=0)


class DurationInputPhoneme(ApiModel):
    token: str = Field(max_length=MAX_TEXT_LENGTH)
    onset: bool
    language: str = Field(max_length=MAX_TEXT_LENGTH)


class DurationNote(ApiModel):
    position: NotePosition
    cent: int = Field(ge=0, le=12_800)
    pronunciation: str = Field(max_length=MAX_TEXT_LENGTH)
    language: str = Field(max_length=MAX_TEXT_LENGTH)
    phonemes: list[DurationInputPhoneme] = Field(max_length=MAX_PHONEMES_PER_NOTE)


class DurationInput(ApiModel):
    piece_duration: float = Field(ge=0)
    notes: list[DurationNote] = Field(max_length=MAX_NOTES)
    mix: list[list[float]] = Field(max_length=MAX_MIX_FRAMES)
    mix_sample_rate: float = Field(gt=0)


class DurationRequest(ApiModel):
    context: MultiSingerContext
    input: DurationInput
    stream: bool = False


class ParameterInputPhoneme(DurationInputPhoneme):
    start: float


class ParameterNote(ApiModel):
    position: NotePosition
    cent: int = Field(ge=0, le=12_800)
    pronunciation: str = Field(max_length=MAX_TEXT_LENGTH)
    language: str = Field(max_length=MAX_TEXT_LENGTH)
    phonemes: list[ParameterInputPhoneme] = Field(max_length=MAX_PHONEMES_PER_NOTE)


class ParameterRetake(ApiModel):
    position: int = Field(ge=0)
    length: int = Field(ge=0)


class Parameter(ApiModel):
    values: list[float] = Field(max_length=MAX_PARAMETER_POINTS)
    sample_rate: float = Field(gt=0)
    retake: ParameterRetake | None = None


class ParameterInput(ApiModel):
    piece_duration: float = Field(ge=0)
    notes: list[ParameterNote] = Field(max_length=MAX_NOTES)
    mix: list[list[float]] = Field(max_length=MAX_MIX_FRAMES)
    mix_sample_rate: float = Field(gt=0)
    parameters: dict[str, Parameter] = Field(max_length=MAX_PARAMETERS)


class ParameterRequest(ApiModel):
    context: MultiSingerContext
    input: ParameterInput
    stream: bool = False


class AudioParameter(ApiModel):
    values: list[float] = Field(default_factory=list, max_length=MAX_PARAMETER_POINTS)
    sample_rate: float = Field(gt=0)


class AudioInput(ApiModel):
    piece_duration: float = Field(ge=0)
    notes: list[ParameterNote] = Field(max_length=MAX_NOTES)
    mix: list[list[float]] = Field(max_length=MAX_MIX_FRAMES)
    mix_sample_rate: float = Field(gt=0)
    parameters: dict[str, AudioParameter] = Field(max_length=MAX_PARAMETERS)


class AudioRequest(ApiModel):
    context: MultiSingerContext
    input: AudioInput
    stream: bool = False


class EnvTagRequest(ApiModel):
    context: MultiSingerContext
