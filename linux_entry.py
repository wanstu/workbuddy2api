#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os

from desktop.common import VERSION


def _default_port() -> int:
    raw = os.getenv("ADMIN_PORT", "8790")
    try:
        port = int(raw)
    except ValueError as exc:
        raise SystemExit(f"ADMIN_PORT must be an integer, got {raw!r}") from exc
    if not 1 <= port <= 65535:
        raise SystemExit(f"ADMIN_PORT must be between 1 and 65535, got {port}")
    return port


def main() -> None:
    parser = argparse.ArgumentParser(
        description="WorkBuddy2API Linux service entrypoint",
    )
    parser.add_argument("--host", default=None, help="listen host; defaults to ADMIN_HOST or 0.0.0.0")
    parser.add_argument("--port", type=int, default=None, help="listen port; defaults to ADMIN_PORT or 8790")
    parser.add_argument("--log-level", default=os.getenv("ADMIN_LOG_LEVEL", "info"))
    parser.add_argument("--version", action="store_true", help="print version and exit")
    args = parser.parse_args()

    if args.version:
        print(VERSION)
        return

    import uvicorn

    host = args.host or os.getenv("ADMIN_HOST", "0.0.0.0")
    port = args.port if args.port is not None else _default_port()
    if not 1 <= port <= 65535:
        raise SystemExit(f"--port must be between 1 and 65535, got {port}")

    uvicorn.run(
        "admin.server:app",
        host=host,
        port=port,
        log_level=args.log_level,
        workers=1,
    )


if __name__ == "__main__":
    main()
