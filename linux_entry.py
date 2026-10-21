#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from desktop.common import VERSION


SYSTEM_CONFIG = Path("/etc/workbuddy2api/workbuddy2api.env")
_REQUIRED_RUNTIME_ENV = (
    "ADMIN_DATABASE_URL",
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "ADMIN_JWT_SECRET",
)


def _default_port() -> int:
    raw = os.getenv("ADMIN_PORT", "8790")
    try:
        port = int(raw)
    except ValueError as exc:
        raise SystemExit(f"ADMIN_PORT must be an integer, got {raw!r}") from exc
    if not 1 <= port <= 65535:
        raise SystemExit(f"ADMIN_PORT must be between 1 and 65535, got {port}")
    return port


def _load_config(path: str | None) -> None:
    configured = path or os.getenv("WORKBUDDY_CONFIG_FILE")
    config_path = Path(configured).expanduser() if configured else SYSTEM_CONFIG

    if not config_path.exists():
        if configured:
            raise SystemExit(f"configuration file does not exist: {config_path}")
        return

    if os.access(config_path, os.R_OK):
        load_dotenv(config_path, override=False)
        return

    # systemd reads EnvironmentFile as root before switching to the service user,
    # so an unreadable file is fine when the required variables are already
    # present. A direct shell launch, however, must not silently fall back to the
    # development MySQL/JWT defaults just because the package config is private.
    if all(os.getenv(name) for name in _REQUIRED_RUNTIME_ENV):
        return

    raise SystemExit(
        f"installed configuration exists but is not readable by the current user: {config_path}\n"
        "Start the packaged service with sudo systemctl start workbuddy2api, "
        "or pass a readable config with --config PATH."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="WorkBuddy2API Linux service entrypoint",
    )
    parser.add_argument("--config", default=None, help="env file; defaults to /etc/workbuddy2api/workbuddy2api.env when present")
    parser.add_argument("--host", default=None, help="listen host; defaults to ADMIN_HOST or 0.0.0.0")
    parser.add_argument("--port", type=int, default=None, help="listen port; defaults to ADMIN_PORT or 8790")
    parser.add_argument("--log-level", default=None, help="uvicorn log level; defaults to ADMIN_LOG_LEVEL or info")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    args = parser.parse_args()

    if args.version:
        print(VERSION)
        return

    _load_config(args.config)

    import uvicorn

    host = args.host or os.getenv("ADMIN_HOST", "0.0.0.0")
    port = args.port if args.port is not None else _default_port()
    log_level = args.log_level or os.getenv("ADMIN_LOG_LEVEL", "info")
    if not 1 <= port <= 65535:
        raise SystemExit(f"--port must be between 1 and 65535, got {port}")

    uvicorn.run(
        "admin.server:app",
        host=host,
        port=port,
        log_level=log_level,
        workers=1,
    )


if __name__ == "__main__":
    main()
