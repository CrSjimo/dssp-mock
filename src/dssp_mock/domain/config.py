from __future__ import annotations

import math
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator


class PronunciationMode(StrEnum):
    FULL = "FULL"
    SKIP = "SKIP"


class PhonemeMode(StrEnum):
    FULL = "FULL"
    TOKEN_ONLY = "TOKEN_ONLY"
    SKIP = "SKIP"


class ParameterType(StrEnum):
    DIRECT = "DIRECT"
    INDIRECT = "INDIRECT"


class MediaMode(StrEnum):
    DATA_URL = "data_url"
    HTTP = "http"


class SynthesisDelaysConfig(BaseModel):
    pronunciation: float = Field(default=0.0, ge=0, le=3_600_000)
    phoneme: float = Field(default=0.0, ge=0, le=3_600_000)
    duration: float = Field(default=0.0, ge=0, le=3_600_000)
    parameter: float = Field(default=0.0, ge=0, le=3_600_000)
    audio: float = Field(default=0.0, ge=0, le=3_600_000)


def _validate_identifier(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("identifier must not be empty")
    if "/" in value or "\\" in value:
        raise ValueError("identifier must not contain a slash")
    return value


class ParameterConfig(BaseModel):
    name: str
    type: ParameterType = ParameterType.INDIRECT
    depends_on: list[str] = Field(default_factory=list)
    min_value: float = -1000.0
    max_value: float = 1000.0

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("depends_on")
    @classmethod
    def unique_dependencies(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("depends_on must not contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_parameter(self) -> Self:
        if self.name == "pitch":
            # Pitch uses the DSSP cent domain and is deliberately not configurable.
            self.min_value = 0.0
            self.max_value = 12_800.0
        elif not (
            math.isfinite(self.min_value)
            and math.isfinite(self.max_value)
            and math.isfinite(self.max_value - self.min_value)
            and self.min_value < self.max_value
        ):
            raise ValueError("parameter range must be finite and min_value < max_value")
        if self.type == ParameterType.DIRECT and self.depends_on:
            raise ValueError("DIRECT parameters cannot declare dependencies")
        if self.name in self.depends_on:
            raise ValueError("a parameter cannot depend on itself")
        return self


class DemoAudioConfig(BaseModel):
    name: str = ""


class SingerLanguageConfig(BaseModel):
    name: str
    default_lyric: str


class SingerConfig(BaseModel):
    id: str
    name: str
    mix_group: str
    languages: dict[str, SingerLanguageConfig] = Field(min_length=1)
    default_language: str
    mock_key: str
    demo_audios: list[DemoAudioConfig] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("name", "mix_group", "mock_key")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value.strip()

    @field_validator("languages", mode="before")
    @classmethod
    def migrate_legacy_languages(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        if not all(isinstance(language, str) for language in value):
            return value
        if len(value) != len(set(value)):
            raise ValueError("languages must not contain duplicates")
        return {
            language: {"name": language, "default_lyric": "la"}
            for language in value
        }

    @field_validator("languages")
    @classmethod
    def valid_languages(
        cls, value: dict[str, SingerLanguageConfig]
    ) -> dict[str, SingerLanguageConfig]:
        cleaned = {code.strip(): info for code, info in value.items()}
        if any(not code for code in cleaned):
            raise ValueError("language codes must not be blank")
        if len(cleaned) != len(value):
            raise ValueError("languages must not contain duplicates")
        return cleaned

    @model_validator(mode="after")
    def default_language_is_supported(self) -> Self:
        if self.default_language not in self.languages:
            raise ValueError("default_language must be included in languages")
        return self


class ArchitectureConfig(BaseModel):
    id: str
    name: str
    pronunciation_mode: PronunciationMode = PronunciationMode.FULL
    phoneme_mode: PhonemeMode = PhonemeMode.FULL
    parameters: list[ParameterConfig] = Field(default_factory=list)
    audio_dependencies: list[str] = Field(default_factory=list)
    singers: list[SingerConfig] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("architecture name must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def validate_graph_and_children(self) -> Self:
        parameter_names = [parameter.name for parameter in self.parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("parameter names must be unique within an architecture")
        name_set = set(parameter_names)
        for parameter in self.parameters:
            missing = set(parameter.depends_on) - name_set
            if missing:
                raise ValueError(
                    f"parameter {parameter.name!r} has unknown dependencies: {sorted(missing)}"
                )
        missing_audio = set(self.audio_dependencies) - name_set
        if missing_audio:
            raise ValueError(f"unknown audio dependencies: {sorted(missing_audio)}")
        if len(self.audio_dependencies) != len(set(self.audio_dependencies)):
            raise ValueError("audio_dependencies must not contain duplicates")

        visiting: set[str] = set()
        visited: set[str] = set()
        graph = {parameter.name: parameter.depends_on for parameter in self.parameters}

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("parameter dependencies must form a directed acyclic graph")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for name in graph:
            visit(name)

        singer_ids = [singer.id for singer in self.singers]
        if len(singer_ids) != len(set(singer_ids)):
            raise ValueError("singer ids must be unique within an architecture")
        return self


class MockInstanceConfig(BaseModel):
    id: str
    name: str
    host: str = "127.0.0.1"
    port: int = Field(default=13711, ge=1, le=65535)
    autostart: bool = True
    parameter_sample_rate: float = Field(default=100.0, gt=0, le=10_000)
    synthesis_delays_ms: SynthesisDelaysConfig = Field(default_factory=SynthesisDelaysConfig)
    media_mode: MediaMode = MediaMode.DATA_URL
    resource_ttl_seconds: int = Field(default=300, ge=1, le=86_400)
    architectures: list[ArchitectureConfig] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("name", "host")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def unique_architectures(self) -> Self:
        ids = [arch.id for arch in self.architectures]
        if len(ids) != len(set(ids)):
            raise ValueError("architecture ids must be unique within an instance")
        mock_keys = [singer.mock_key for arch in self.architectures for singer in arch.singers]
        if len(mock_keys) != len(set(mock_keys)):
            raise ValueError("singer mock_key values must be unique within an instance")
        return self


class AppConfig(BaseModel):
    resource_host: str = "127.0.0.1"
    resource_port: int = Field(default=7861, ge=1, le=65535)
    resource_public_base_url: str | None = None
    instances: list[MockInstanceConfig] = Field(default_factory=list)

    @field_validator("resource_host")
    @classmethod
    def resource_host_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("resource_host must not be blank")
        return value.strip()

    @field_validator("resource_public_base_url")
    @classmethod
    def normalize_base_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if not value.startswith(("http://", "https://")):
            raise ValueError("resource_public_base_url must be an HTTP(S) URL")
        return value.rstrip("/")

    @model_validator(mode="after")
    def unique_instances_and_bindings(self) -> Self:
        ids = [instance.id for instance in self.instances]
        if len(ids) != len(set(ids)):
            raise ValueError("instance ids must be unique")
        bindings = [(instance.host, instance.port) for instance in self.instances]
        if len(bindings) != len(set(bindings)):
            raise ValueError("mock instances must use different host/port bindings")
        if (self.resource_host, self.resource_port) in bindings:
            raise ValueError("resource service must not share a mock instance binding")
        return self


def default_config() -> AppConfig:
    return AppConfig(
        instances=[
            MockInstanceConfig(
                id="default",
                name="Default mock",
                architectures=[
                    ArchitectureConfig(
                        id="diffsinger",
                        name="DiffSinger",
                        parameters=[
                            ParameterConfig(
                                name="expressiveness",
                                type=ParameterType.DIRECT,
                                min_value=0.0,
                                max_value=1_000.0,
                            ),
                            ParameterConfig(
                                name="pitch",
                                type=ParameterType.INDIRECT,
                                depends_on=["expressiveness"],
                            ),
                            ParameterConfig(
                                name="energy",
                                type=ParameterType.INDIRECT,
                                depends_on=["pitch"],
                                min_value=-96_000.0,
                                max_value=0.0,
                            ),
                            ParameterConfig(
                                name="breathiness",
                                type=ParameterType.INDIRECT,
                                depends_on=["pitch"],
                                min_value=-96_000.0,
                                max_value=0.0,
                            ),
                            ParameterConfig(
                                name="tension",
                                type=ParameterType.INDIRECT,
                                depends_on=["pitch"],
                                min_value=-10_000.0,
                                max_value=10_000.0,
                            ),
                            ParameterConfig(
                                name="voicing",
                                type=ParameterType.INDIRECT,
                                depends_on=["pitch"],
                                min_value=-96_000.0,
                                max_value=0.0,
                            ),
                            ParameterConfig(
                                name="mouth_opening",
                                type=ParameterType.INDIRECT,
                                depends_on=["pitch"],
                                min_value=0.0,
                                max_value=1_000.0,
                            ),
                            ParameterConfig(
                                name="gender",
                                type=ParameterType.DIRECT,
                                min_value=-1_000.0,
                                max_value=1_000.0,
                            ),
                            ParameterConfig(
                                name="velocity",
                                type=ParameterType.DIRECT,
                                min_value=-1_000.0,
                                max_value=1_000.0,
                            ),
                            ParameterConfig(
                                name="tone_shift",
                                type=ParameterType.DIRECT,
                                min_value=-1_200.0,
                                max_value=1_200.0,
                            ),
                        ],
                        audio_dependencies=[
                            "pitch",
                            "breathiness",
                            "tension",
                            "voicing",
                            "energy",
                            "mouth_opening",
                            "gender",
                            "velocity",
                            "tone_shift",
                        ],
                        singers=[
                            SingerConfig(
                                id="demo-singer",
                                name="Demo Singer",
                                mix_group="default",
                                languages={
                                    "zh": SingerLanguageConfig(
                                        name="中文",
                                        default_lyric="啦",
                                    ),
                                    "en": SingerLanguageConfig(
                                        name="English",
                                        default_lyric="la",
                                    ),
                                    "ja": SingerLanguageConfig(
                                        name="日本語",
                                        default_lyric="ラ",
                                    ),
                                },
                                default_language="zh",
                                mock_key="demo-singer",
                                demo_audios=[
                                    DemoAudioConfig(name="Demo 1"),
                                    DemoAudioConfig(name=""),
                                ],
                            )
                        ],
                    )
                ],
            )
        ]
    )
