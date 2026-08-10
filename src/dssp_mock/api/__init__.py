from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from dssp_mock.api.control import create_admin_app, create_control_app


def create_mock_app(*args: Any, **kwargs: Any) -> FastAPI:
    """Lazily import the mock API factory to keep package imports acyclic."""

    from dssp_mock.api.mock import create_mock_app as factory

    return factory(*args, **kwargs)


__all__ = ["create_admin_app", "create_control_app", "create_mock_app"]
