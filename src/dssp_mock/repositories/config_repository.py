from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from dssp_mock.domain.config import AppConfig, MockInstanceConfig, default_config


class ConfigRepository:
    """Thread-safe JSON configuration repository with atomic replacement."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._lock = threading.RLock()
        self._config = self._load_or_create()

    def _load_or_create(self) -> AppConfig:
        if not self.path.exists():
            config = default_config()
            self._write(config)
            return config
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return AppConfig.model_validate(raw)

    def _write(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        payload = config.model_dump_json(indent=2)
        temporary.write_text(payload + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def get(self) -> AppConfig:
        with self._lock:
            return self._config.model_copy(deep=True)

    def replace(self, config: AppConfig) -> AppConfig:
        with self._lock:
            # Re-validate even an already constructed model so callers cannot bypass
            # invariants by mutating the detached snapshot returned by get().
            snapshot = AppConfig.model_validate(config.model_dump(mode="json"))
            self._write(snapshot)
            self._config = snapshot
            return snapshot.model_copy(deep=True)

    def get_instance(self, instance_id: str) -> MockInstanceConfig | None:
        with self._lock:
            for instance in self._config.instances:
                if instance.id == instance_id:
                    return instance.model_copy(deep=True)
        return None
