#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from desktop.common import VERSION


SYSTEM_CONFIG = Path("/etc/workbuddy2api/workbuddy2api.env")
SERVICE_UNIT = "workbuddy2api.service"
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

    # systemd loads EnvironmentFile before switching to the service user.
    # In that case the private root-owned config does not need to be readable
    # by the service process because the required values are already injected.
    if all(os.getenv(name) for name in _REQUIRED_RUNTIME_ENV):
        return

    raise SystemExit(
        f"installed configuration exists but is not readable by the current user: {config_path}\n"
        "Use workbuddy2api service start/restart/status/stop to manage the installed service, "
        "or use workbuddy2api run --config PATH with a readable config file."
    )


def _run_server(
    *,
    config: str | None = None,
    host: str | None = None,
    port: int | None = None,
    log_level: str | None = None,
) -> int:
    _load_config(config)

    import uvicorn

    listen_host = host or os.getenv("ADMIN_HOST", "0.0.0.0")
    listen_port = port if port is not None else _default_port()
    listen_log_level = log_level or os.getenv("ADMIN_LOG_LEVEL", "info")
    if not 1 <= listen_port <= 65535:
        raise SystemExit(f"--port must be between 1 and 65535, got {listen_port}")

    uvicorn.run(
        "admin.server:app",
        host=listen_host,
        port=listen_port,
        log_level=listen_log_level,
        workers=1,
    )
    return 0


def _systemctl(action: str) -> int:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        raise SystemExit("systemctl was not found; the packaged service requires systemd")

    command = [systemctl, action, SERVICE_UNIT]
    if action == "status":
        command.append("--no-pager")
    elif os.geteuid() != 0:
        sudo = shutil.which("sudo")
        if not sudo:
            raise SystemExit(
                f"{action} requires administrator privileges; run: "
                f"sudo workbuddy2api service {action}"
            )
        command.insert(0, sudo)

    try:
        return subprocess.run(command, check=False).returncode
    except KeyboardInterrupt:
        return 130


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbuddy2api",
        description="WorkBuddy2API Linux command line interface",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=VERSION,
    )

    commands = parser.add_subparsers(dest="command")

    service = commands.add_parser("service", help="manage the installed systemd service")
    service_actions = service.add_subparsers(dest="service_action", required=True)
    for action, help_text in (
        ("start", "start the WorkBuddy2API service"),
        ("restart", "restart the WorkBuddy2API service"),
        ("status", "show service status"),
        ("stop", "stop the WorkBuddy2API service"),
    ):
        service_actions.add_parser(action, help=help_text)

    run = commands.add_parser("run", help="run in the foreground (advanced/debugging)")
    run.add_argument(
        "--config",
        default=None,
        help="env file; defaults to /etc/workbuddy2api/workbuddy2api.env when readable",
    )
    run.add_argument("--host", default=None, help="listen host; defaults to ADMIN_HOST or 0.0.0.0")
    run.add_argument("--port", type=int, default=None, help="listen port; defaults to ADMIN_PORT or 8790")
    run.add_argument("--log-level", default=None, help="uvicorn log level; defaults to ADMIN_LOG_LEVEL or info")

    return parser


def main() -> int:
    # Internal entry used only by the packaged systemd unit. Keep it out of -h
    # so users manage the daemon through the stable "service" interface.
    if len(sys.argv) >= 2 and sys.argv[1] == "__serve":
        return _run_server()

    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "service":
        return _systemctl(args.service_action)

    if args.command == "run":
        return _run_server(
            config=args.config,
            host=args.host,
            port=args.port,
            log_level=args.log_level,
        )

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
