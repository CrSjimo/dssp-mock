from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dssp_mock.api.mock import create_mock_app
from dssp_mock.domain.config import (
    ArchitectureConfig,
    MediaMode,
    MockInstanceConfig,
    SingerConfig,
    default_config,
)
from dssp_mock.repositories.config_repository import ConfigRepository
from dssp_mock.services import resource_store as resource_store_module
from dssp_mock.services.errors import ProblemError
from dssp_mock.services.request_log import RequestLogStore
from dssp_mock.services.resource_store import ResourceStore


def test_resource_store_expires_entries_without_persisting(monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(resource_store_module.time, "monotonic", lambda: clock[0])
    store = ResourceStore()

    token = store.put(b"temporary", "text/plain", "sample.txt", ttl_seconds=5)
    assert store.get(token) is not None

    clock[0] = 105.01
    assert store.get(token) is None


def test_resource_store_deduplicates_and_enforces_capacity() -> None:
    store = ResourceStore(max_entries=1, max_total_bytes=6, max_item_bytes=5)

    first = store.put(b"same", "text/plain", "first.txt", ttl_seconds=5)
    duplicate = store.put(b"same", "text/plain", "second.txt", ttl_seconds=10)

    assert duplicate == first
    with pytest.raises(ProblemError) as item_error:
        store.put(b"123456", "text/plain", "large.txt", ttl_seconds=5)
    assert item_error.value.status == 413
    with pytest.raises(ProblemError) as capacity_error:
        store.put(b"new", "text/plain", "new.txt", ttl_seconds=5)
    assert capacity_error.value.status == 507


def test_mock_apps_read_only_their_own_instance_configuration(tmp_path) -> None:
    config = default_config()
    config.instances.append(
        MockInstanceConfig(
            id="isolated",
            name="Isolated mock",
            port=13712,
            architectures=[
                ArchitectureConfig(
                    id="other-arch",
                    name="Other Architecture",
                    singers=[
                        SingerConfig(
                            id="other-singer",
                            name="Other Singer",
                            mix_group="other",
                            languages={
                                "en": {"name": "English", "default_lyric": "la"}
                            },
                            default_language="en",
                            mock_key="demo-singer",
                        )
                    ],
                )
            ],
        )
    )
    repository = ConfigRepository(tmp_path / "config.json")
    repository.replace(config)
    resources = ResourceStore()
    logs = RequestLogStore()
    first = create_mock_app("default", repository, resources, logs, "http://resources.test")
    second = create_mock_app("isolated", repository, resources, logs, "http://resources.test")

    with TestClient(first) as first_client, TestClient(second) as second_client:
        assert [item["id"] for item in first_client.get("/v1/arch").json()] == ["diffsinger"]
        assert [item["id"] for item in second_client.get("/v1/arch").json()] == ["other-arch"]
        assert first_client.get("/v1/arch/other-arch").status_code == 404
        assert second_client.get("/v1/arch/diffsinger").status_code == 404


def test_http_media_mode_returns_shared_ephemeral_resource_url(tmp_path) -> None:
    config = default_config()
    config.instances[0].media_mode = MediaMode.HTTP
    config.instances[0].resource_ttl_seconds = 17
    repository = ConfigRepository(tmp_path / "config.json")
    repository.replace(config)
    resources = ResourceStore()
    app = create_mock_app(
        "default",
        repository,
        resources,
        RequestLogStore(),
        "http://127.0.0.1:9010",
    )

    with TestClient(app) as client:
        response = client.get("/v1/arch/diffsinger/singer/demo-singer/avatar")

    assert response.status_code == 200
    url = response.json()["avatar_url"]
    assert url.startswith("http://127.0.0.1:9010/resources/")
    token = url.rsplit("/", 1)[-1]
    entry = resources.get(token)
    assert entry is not None
    assert entry.media_type == "image/png"
    assert entry.data.startswith(b"\x89PNG\r\n\x1a\n")
