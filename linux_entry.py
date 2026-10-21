#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from desktop.common import VERSION


SYSTEM_CONFIG = Path("/etc/workbuddy2api/workbuddy2api.env")
SERVICE_UNIT = "workbuddy2api.service"
DATA_DIR = Path("/var/lib/workbuddy2api")
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


def _external_command_env() -> dict[str, str]:
    """Return an environment safe for launching host-system executables."""
    env = os.environ.copy()
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original:
        env["LD_LIBRARY_PATH"] = original
    else:
        env.pop("LD_LIBRARY_PATH", None)
    env.pop("LD_PRELOAD", None)
    return env


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
        return subprocess.run(
            command,
            check=False,
            env=_external_command_env(),
        ).returncode
    except KeyboardInterrupt:
        return 130


def _print_credentials() -> int:
    if not SYSTEM_CONFIG.exists():
        raise SystemExit(
            f"installed configuration was not found: {SYSTEM_CONFIG}\n"
            "Install the Debian package first."
        )
    if not os.access(SYSTEM_CONFIG, os.R_OK):
        raise SystemExit(
            f"configuration is not readable: {SYSTEM_CONFIG}\n"
            "Run: sudo workbuddy2api __credentials"
        )

    values = dotenv_values(SYSTEM_CONFIG)
    username = values.get("ADMIN_USERNAME") or ""
    password = values.get("ADMIN_PASSWORD") or ""
    host = values.get("ADMIN_HOST") or "127.0.0.1"
    port = values.get("ADMIN_PORT") or "8790"
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host

    if not username or not password:
        raise SystemExit(f"ADMIN_USERNAME/ADMIN_PASSWORD are missing from {SYSTEM_CONFIG}")

    print(f"Admin URL: http://{display_host}:{port}/admin")
    print(f"Username: {username}")
    print(f"Password: {password}")
    print(f"Config: {SYSTEM_CONFIG}")
    return 0


def _credentials() -> int:
    if os.access(SYSTEM_CONFIG, os.R_OK):
        return _print_credentials()

    sudo = shutil.which("sudo")
    if not sudo:
        raise SystemExit(
            f"reading {SYSTEM_CONFIG} requires administrator privileges; run: "
            "sudo workbuddy2api credentials"
        )

    return subprocess.run(
        [sudo, sys.executable, "__credentials"],
        check=False,
        env=_external_command_env(),
    ).returncode


def _doctor_line(ok: bool, label: str, detail: str = "") -> None:
    prefix = "[OK]" if ok else "[FAIL]"
    suffix = f" - {detail}" if detail else ""
    print(f"{prefix} {label}{suffix}")


def _doctor_root() -> int:
    failures = 0

    def check(ok: bool, label: str, detail: str = "") -> bool:
        nonlocal failures
        _doctor_line(ok, label, detail)
        if not ok:
            failures += 1
        return ok

    print(f"WorkBuddy2API doctor {VERSION}")
    print()

    binary = Path(sys.executable).resolve()
    check(binary.exists(), "binary", str(binary))

    config_ok = check(SYSTEM_CONFIG.is_file(), "config", str(SYSTEM_CONFIG))
    values: dict[str, str | None] = {}
    if config_ok:
        try:
            mode = stat.S_IMODE(SYSTEM_CONFIG.stat().st_mode)
            st = SYSTEM_CONFIG.stat()
            check(
                st.st_uid == 0 and st.st_gid == 0 and mode == 0o600,
                "config permissions",
                f"uid={st.st_uid} gid={st.st_gid} mode={mode:04o}",
            )
            values = dict(dotenv_values(SYSTEM_CONFIG))
            missing = [name for name in _REQUIRED_RUNTIME_ENV if not values.get(name)]
            check(not missing, "required configuration", "complete" if not missing else f"missing: {', '.join(missing)}")
        except Exception as exc:
            check(False, "read configuration", str(exc))

    try:
        service_user = pwd.getpwnam("workbuddy2api")
        check(True, "service user", f"uid={service_user.pw_uid}")
    except KeyError:
        service_user = None
        check(False, "service user", "workbuddy2api user not found")

    if DATA_DIR.exists():
        data_stat = DATA_DIR.stat()
        data_mode = stat.S_IMODE(data_stat.st_mode)
        owner_ok = service_user is not None and data_stat.st_uid == service_user.pw_uid
        check(owner_ok and data_mode == 0o750, "data directory", f"{DATA_DIR} mode={data_mode:04o}")
    else:
        check(False, "data directory", f"{DATA_DIR} does not exist")

    systemctl = shutil.which("systemctl")
    if not check(bool(systemctl), "systemctl", systemctl or "not found"):
        print()
        print(f"Doctor result: {failures} failure(s)")
        return 1

    env = _external_command_env()

    def systemctl_ok(*args: str) -> tuple[bool, str]:
        proc = subprocess.run(
            [systemctl, *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        return proc.returncode == 0, proc.stdout.strip()

    enabled, enabled_out = systemctl_ok("is-enabled", SERVICE_UNIT)
    check(enabled, "service enabled", enabled_out or ("yes" if enabled else "no"))

    active, active_out = systemctl_ok("is-active", SERVICE_UNIT)
    check(active, "service active", active_out or ("yes" if active else "no"))

    show_proc = subprocess.run(
        [systemctl, "show", SERVICE_UNIT, "--property=ExecStart", "--value"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    exec_text = show_proc.stdout.strip()
    check(
        show_proc.returncode == 0 and "/usr/bin/workbuddy2api __serve" in exec_text,
        "service ExecStart",
        exec_text or "unavailable",
    )

    db_url = (values.get("ADMIN_DATABASE_URL") or "") if values else ""
    if db_url.startswith("sqlite:///"):
        db_path = Path(db_url.removeprefix("sqlite:///"))
        db_exists = db_path.is_file()
        db_detail = str(db_path)
        if db_exists and service_user is not None:
            db_stat = db_path.stat()
            db_detail += f" uid={db_stat.st_uid} size={db_stat.st_size}"
            db_exists = db_stat.st_uid == service_user.pw_uid and db_stat.st_size > 0
        check(db_exists, "SQLite database", db_detail)
    elif db_url:
        check(True, "database", "external database configured")
    elif config_ok:
        check(False, "database", "ADMIN_DATABASE_URL missing")

    host = (values.get("ADMIN_HOST") or "127.0.0.1") if values else "127.0.0.1"
    port = (values.get("ADMIN_PORT") or "8790") if values else "8790"
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else str(host)
    base_url = f"http://{probe_host}:{port}"

    try:
        with urllib.request.urlopen(f"{base_url}/api/app/version", timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8"))
            remote_version = str(payload.get("version") or "")
            check(
                response.status == 200 and remote_version == VERSION,
                "HTTP version endpoint",
                f"{base_url} -> {remote_version or 'unknown'}",
            )
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        check(False, "HTTP version endpoint", f"{base_url} -> {exc}")

    try:
        with urllib.request.urlopen(f"{base_url}/admin", timeout=4) as response:
            check(response.status == 200, "admin page", f"HTTP {response.status}")
    except (urllib.error.URLError, TimeoutError) as exc:
        check(False, "admin page", str(exc))

    print()
    if failures:
        print(f"Doctor result: {failures} failure(s)")
        return 1
    print("Doctor result: all checks passed")
    return 0


def _doctor() -> int:
    if os.geteuid() == 0:
        return _doctor_root()

    sudo = shutil.which("sudo")
    if not sudo:
        raise SystemExit("doctor requires administrator privileges; run: sudo workbuddy2api doctor")

    return subprocess.run(
        [sudo, sys.executable, "__doctor"],
        check=False,
        env=_external_command_env(),
    ).returncode


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

    commands.add_parser(
        "credentials",
        help="show the generated admin URL, username and password",
    )
    commands.add_parser(
        "doctor",
        help="run read-only checks for the packaged Linux service",
    )

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
    if len(sys.argv) >= 2 and sys.argv[1] == "__serve":
        return _run_server()
    if len(sys.argv) >= 2 and sys.argv[1] == "__credentials":
        return _print_credentials()
    if len(sys.argv) >= 2 and sys.argv[1] == "__doctor":
        return _doctor_root()

    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "service":
        return _systemctl(args.service_action)

    if args.command == "credentials":
        return _credentials()

    if args.command == "doctor":
        return _doctor()

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
