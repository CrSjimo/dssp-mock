from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import uvicorn
from fastapi import FastAPI

from dssp_mock.domain.config import MediaMode, MockInstanceConfig
from dssp_mock.repositories.config_repository import ConfigRepository
from dssp_mock.services.errors import ProblemError, not_found
from dssp_mock.services.request_log import RequestLogStore
from dssp_mock.services.resource_store import ResourceStore, resource_app

MockAppFactory = Callable[..., FastAPI]


@dataclass(slots=True)
class _ServerHandle:
    name: str
    host: str
    port: int
    server: uvicorn.Server
    thread: threading.Thread
    resource_base_url: str | None = None
    started_at: str | None = None
    error: str | None = None
    finished: threading.Event = field(default_factory=threading.Event)

    @property
    def running(self) -> bool:
        return self.thread.is_alive() and self.server.started and not self.server.should_exit


class InstanceManager:
    """Own the shared resource listener and all configured mock listeners."""

    def __init__(
        self,
        repository: ConfigRepository,
        resource_store: ResourceStore,
        log_store: RequestLogStore,
        mock_app_factory: MockAppFactory | None = None,
        *,
        log_level: str = "warning",
        startup_timeout: float = 8.0,
        shutdown_timeout: float = 8.0,
    ) -> None:
        self.repository = repository
        self.resource_store = resource_store
        self.log_store = log_store
        self._mock_app_factory = mock_app_factory
        self.log_level = log_level
        self.startup_timeout = startup_timeout
        self.shutdown_timeout = shutdown_timeout
        self._lock = threading.RLock()
        self._resource: _ServerHandle | None = None
        self._instances: dict[str, _ServerHandle] = {}
        self._instance_errors: dict[str, str] = {}
        self._resource_error: str | None = None
        self._explicitly_stopped: set[str] = set()

    @property
    def resource_base_url(self) -> str:
        config = self.repository.get()
        if config.resource_public_base_url:
            return config.resource_public_base_url
        return _http_url(config.resource_host, config.resource_port)

    def get_resource_base_url(self) -> str:
        """Compatibility-friendly method form of :attr:`resource_base_url`."""

        return self.resource_base_url

    def start_resource(self) -> dict[str, Any]:
        with self._lock:
            config = self.repository.get()
            current = self._resource
            if current is not None and current.running:
                if (current.host, current.port) == (
                    config.resource_host,
                    config.resource_port,
                ):
                    return self._resource_status(config.resource_host, config.resource_port)
                self._stop_handle(current)
                self._resource = None

            app = resource_app(self.resource_store)
            try:
                self._resource = self._spawn(
                    "resources",
                    app,
                    config.resource_host,
                    config.resource_port,
                )
            except Exception as exc:
                detail = _exception_detail(exc)
                self._resource = None
                self._resource_error = detail
                raise ProblemError(
                    409,
                    "Resource service failed to start",
                    detail,
                    problem_type="https://dssp-mock.local/problems/listener-start-failed",
                ) from exc
            self._resource_error = None
            return self._resource_status(config.resource_host, config.resource_port)

    def stop_resource(self) -> dict[str, Any]:
        with self._lock:
            config = self.repository.get()
            if self._resource is not None:
                self._stop_handle(self._resource)
                self._resource = None
            self._resource_error = None
            return self._resource_status(config.resource_host, config.resource_port)

    def start(self, instance_id: str) -> dict[str, Any]:
        with self._lock:
            instance = self._require_instance(instance_id)
            if instance.media_mode == MediaMode.HTTP:
                self.start_resource()
            base_url = self.resource_base_url
            current = self._instances.get(instance_id)
            if current is not None and current.running:
                unchanged = (current.host, current.port) == (
                    instance.host,
                    instance.port,
                ) and current.resource_base_url == base_url
                if unchanged:
                    return self._instance_status(instance, current)
                self._stop_instance_unchecked(instance_id)

            try:
                app = self._create_mock_app(instance_id, base_url)
                handle = self._spawn(
                    f"mock-{instance_id}",
                    app,
                    instance.host,
                    instance.port,
                    resource_base_url=base_url,
                )
            except Exception as exc:
                detail = _exception_detail(exc)
                self._instances.pop(instance_id, None)
                self._instance_errors[instance_id] = detail
                raise ProblemError(
                    409,
                    "Mock instance failed to start",
                    detail,
                    problem_type="https://dssp-mock.local/problems/listener-start-failed",
                    extensions={"instance_id": instance_id},
                ) from exc

            self._instances[instance_id] = handle
            self._instance_errors.pop(instance_id, None)
            self._explicitly_stopped.discard(instance_id)
            return self._instance_status(instance, handle)

    def stop(self, instance_id: str) -> dict[str, Any]:
        with self._lock:
            instance = self._require_instance(instance_id)
            self._stop_instance_unchecked(instance_id)
            self._instance_errors.pop(instance_id, None)
            self._explicitly_stopped.add(instance_id)
            return self._instance_status(instance, None)

    def restart(self, instance_id: str) -> dict[str, Any]:
        with self._lock:
            self._require_instance(instance_id)
            self._stop_instance_unchecked(instance_id)
            return self.start(instance_id)

    def reconcile(self) -> dict[str, Any]:
        """Make running listeners match the persisted autostart configuration.

        Individual mock listener failures are retained in status output so that a
        single occupied port does not make the administration service unavailable.
        The shared resource listener is attempted first for the same reason.
        """

        with self._lock:
            config = self.repository.get()
            resource_required = any(
                instance.media_mode == MediaMode.HTTP for instance in config.instances
            )
            if resource_required:
                try:
                    self.start_resource()
                except ProblemError:
                    # Keep the administration UI available so the binding can be fixed.
                    pass
            elif self._resource is not None:
                self.stop_resource()

            configured_ids = {instance.id for instance in config.instances}
            for instance_id in list(self._instances):
                if instance_id not in configured_ids:
                    self._stop_instance_unchecked(instance_id)
                    self._instance_errors.pop(instance_id, None)
                    self._explicitly_stopped.discard(instance_id)

            for instance in config.instances:
                current = self._instances.get(instance.id)
                should_start = (current is not None and current.running) or (
                    instance.autostart and instance.id not in self._explicitly_stopped
                )
                if should_start:
                    try:
                        self.start(instance.id)
                    except ProblemError:
                        # Keep reconciling other independent instances.
                        continue
            return self.statuses()

    def status(self, instance_id: str) -> dict[str, Any]:
        with self._lock:
            instance = self._require_instance(instance_id)
            return self._instance_status(instance, self._instances.get(instance_id))

    def statuses(self) -> dict[str, Any]:
        with self._lock:
            config = self.repository.get()
            return {
                "resource": self._resource_status(
                    config.resource_host,
                    config.resource_port,
                ),
                "instances": [
                    self._instance_status(instance, self._instances.get(instance.id))
                    for instance in config.instances
                ],
            }

    def shutdown(self) -> None:
        with self._lock:
            for instance_id in list(self._instances):
                try:
                    self._stop_instance_unchecked(instance_id)
                except Exception:
                    # Shutdown remains best-effort so every listener gets a chance to exit.
                    continue
            if self._resource is not None:
                try:
                    self._stop_handle(self._resource)
                except Exception:
                    pass
                finally:
                    self._resource = None
            self.resource_store.clear()

    def _require_instance(self, instance_id: str) -> MockInstanceConfig:
        instance = self.repository.get_instance(instance_id)
        if instance is None:
            raise not_found("Mock instance", instance_id)
        return instance

    def _create_mock_app(self, instance_id: str, resource_base_url: str) -> FastAPI:
        factory = self._mock_app_factory
        if factory is None:
            from dssp_mock.api.mock import create_mock_app

            factory = create_mock_app
        return factory(
            instance_id=instance_id,
            repository=self.repository,
            resource_store=self.resource_store,
            log_store=self.log_store,
            resource_base_url=resource_base_url,
        )

    def _spawn(
        self,
        name: str,
        app: FastAPI,
        host: str,
        port: int,
        *,
        resource_base_url: str | None = None,
    ) -> _ServerHandle:
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level=self.log_level,
                access_log=False,
            )
        )
        placeholder = threading.Thread()
        handle = _ServerHandle(
            name=name,
            host=host,
            port=port,
            server=server,
            thread=placeholder,
            resource_base_url=resource_base_url,
        )

        def run() -> None:
            try:
                server.run()
            except BaseException as exc:  # Uvicorn uses SystemExit for bind failures.
                handle.error = _server_exception_detail(exc, host, port)
            finally:
                handle.finished.set()

        handle.thread = threading.Thread(
            target=run,
            name=f"dssp-{name}-{host}-{port}",
            daemon=True,
        )
        handle.thread.start()
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if server.started and handle.thread.is_alive():
                handle.started_at = datetime.now(UTC).isoformat()
                return handle
            if handle.finished.wait(0.02):
                break

        if handle.thread.is_alive():
            server.should_exit = True
            handle.thread.join(timeout=min(self.shutdown_timeout, 2.0))
        if handle.error:
            raise RuntimeError(handle.error)
        if handle.thread.is_alive():
            raise RuntimeError(f"Timed out while starting listener on {host}:{port}.")
        raise RuntimeError(
            f"Listener on {host}:{port} exited before it reported readiness; "
            "the address may already be in use."
        )

    def _stop_instance_unchecked(self, instance_id: str) -> None:
        handle = self._instances.get(instance_id)
        if handle is not None:
            try:
                self._stop_handle(handle)
            except Exception as exc:
                self._instance_errors[instance_id] = _exception_detail(exc)
                raise
            else:
                self._instances.pop(instance_id, None)

    def _stop_handle(self, handle: _ServerHandle) -> None:
        if not handle.thread.is_alive():
            return
        handle.server.should_exit = True
        handle.thread.join(timeout=self.shutdown_timeout)
        if handle.thread.is_alive():
            handle.server.force_exit = True
            handle.thread.join(timeout=1.0)
        if handle.thread.is_alive():
            raise RuntimeError(
                f"Listener {handle.name!r} on {handle.host}:{handle.port} did not stop."
            )

    def _resource_status(self, host: str, port: int) -> dict[str, Any]:
        handle = self._resource
        running = handle.running if handle is not None else False
        error = self._resource_error
        if handle is not None and not handle.thread.is_alive() and handle.error:
            error = handle.error
        return {
            "status": _status_name(handle, error),
            "running": running,
            "host": host,
            "port": port,
            "url": self.resource_base_url,
            "started_at": handle.started_at if handle is not None else None,
            "error": error,
        }

    def _instance_status(
        self,
        instance: MockInstanceConfig,
        handle: _ServerHandle | None,
    ) -> dict[str, Any]:
        error = self._instance_errors.get(instance.id)
        if handle is not None and not handle.thread.is_alive() and handle.error:
            error = handle.error
        return {
            "id": instance.id,
            "name": instance.name,
            "status": _status_name(handle, error),
            "running": handle.running if handle is not None else False,
            "autostart": instance.autostart,
            "host": instance.host,
            "port": instance.port,
            "url": _http_url(instance.host, instance.port),
            "started_at": handle.started_at if handle is not None else None,
            "error": error,
        }


def _status_name(handle: _ServerHandle | None, error: str | None) -> str:
    if handle is not None and handle.running:
        return "running"
    if error:
        return "error"
    return "stopped"


def _public_host(host: str) -> str:
    unwrapped = host.strip().strip("[]")
    if unwrapped in {"0.0.0.0", "::", ""}:
        return "127.0.0.1"
    return unwrapped


def _http_url(host: str, port: int) -> str:
    display_host = _public_host(host)
    if ":" in display_host:
        display_host = f"[{display_host}]"
    return f"http://{display_host}:{port}"


def _server_exception_detail(exc: BaseException, host: str, port: int) -> str:
    if isinstance(exc, SystemExit):
        return (
            f"Uvicorn exited with code {exc.code!r} while starting {host}:{port}; "
            "the address may already be in use or unavailable."
        )
    return f"Listener {host}:{port} failed: {_exception_detail(exc)}"


def _exception_detail(exc: BaseException) -> str:
    detail = str(exc).strip()
    return detail or exc.__class__.__name__
