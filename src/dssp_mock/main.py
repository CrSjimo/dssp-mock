from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from dssp_mock.api.control import create_control_app
from dssp_mock.repositories.config_repository import ConfigRepository
from dssp_mock.runtime.instance_manager import InstanceManager
from dssp_mock.services.request_log import RequestLogStore
from dssp_mock.services.resource_store import ResourceStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dssp-mock",
        description="Run the DSSP mock administration UI and configured mock listeners.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.local.json"),
        help="Persistent JSON configuration path (default: config.local.json).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Administration UI bind host (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7860,
        help="Administration UI bind port (default: 7860).",
    )
    parser.add_argument(
        "--log-level",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        default="info",
        help="Uvicorn log level (default: info).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65_535:
        parser.error("--port must be between 1 and 65535")

    repository = ConfigRepository(args.config)
    resource_store = ResourceStore()
    log_store = RequestLogStore()

    # Delayed import avoids loading the comparatively large synthesis layer for
    # commands that only inspect CLI help.
    from dssp_mock.api.mock import create_mock_app

    manager = InstanceManager(
        repository,
        resource_store,
        log_store,
        create_mock_app,
        log_level=args.log_level,
    )
    app = create_control_app(repository, manager, log_store)
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=True,
    )


if __name__ == "__main__":
    main()
