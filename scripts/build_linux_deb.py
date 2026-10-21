"""Build a self-contained Debian amd64 package on Linux.

The package contains a PyInstaller-frozen Python runtime, so target machines do
not need Python, pip, a virtualenv, MySQL, or Redis for the default SQLite setup.
"""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging" / "linux"
BUILD = ROOT / "build" / "linux"
DIST = ROOT / "dist"


def run(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(args, cwd=cwd or ROOT, check=True)


def copy_file(source: Path, target: Path, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    target.chmod(mode)


def version() -> str:
    value = json.loads((ROOT / "desktop" / "version.json").read_text(encoding="utf-8"))["version"]
    parts = value.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise SystemExit("desktop/version.json must contain a stable x.y.z version")
    return value


def changelog_text(pkg_version: str) -> str:
    try:
        epoch = int(subprocess.check_output(
            ["git", "log", "-1", "--format=%ct"],
            cwd=ROOT,
            text=True,
        ).strip())
    except (OSError, ValueError, subprocess.CalledProcessError):
        epoch = 0
    date = datetime.fromtimestamp(epoch, timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    return f"""workbuddy2api ({pkg_version}) unstable; urgency=medium

  * Package upstream WorkBuddy2API {pkg_version} for Linux amd64.

 -- WorkBuddy2API contributors <noreply@github.com>  {date}
"""


def installed_size_kib(stage: Path) -> int:
    total_bytes = sum(
        path.stat().st_size
        for path in stage.rglob("*")
        if path.is_file() and "DEBIAN" not in path.relative_to(stage).parts
    )
    return max(1, (total_bytes + 1023) // 1024)


def control_text(pkg_version: str, installed_size: int) -> str:
    return f"""Package: workbuddy2api
Version: {pkg_version}
Section: net
Priority: optional
Architecture: amd64
Installed-Size: {installed_size}
Maintainer: WorkBuddy2API contributors <noreply@github.com>
Depends: libc6 (>= 2.35), ca-certificates, adduser
Homepage: https://github.com/xiaofan6ya/workbuddy2api
Description: WorkBuddy/CodeBuddy compatible API gateway and account pool
 Provides OpenAI, Responses, and Anthropic-compatible API endpoints,
 multi-account management, API keys, scheduling, and the web admin console.
 This package includes its Python runtime and defaults to SQLite.
"""


def main() -> None:
    if sys.platform != "linux" or platform.machine().lower() not in ("x86_64", "amd64"):
        raise SystemExit("Build the Debian package on Linux x86_64/amd64")
    if shutil.which("dpkg-deb") is None:
        raise SystemExit("dpkg-deb is required (install the dpkg package)")

    pkg_version = version()
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    DIST.mkdir(exist_ok=True)

    # Validate the application against an isolated temporary SQLite database.
    run(sys.executable, str(ROOT / "scripts" / "check_runtime.py"))

    pyinstaller_dist = BUILD / "pyinstaller-dist"
    pyinstaller_work = BUILD / "pyinstaller-work"
    run(
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(pyinstaller_dist),
        "--workpath",
        str(pyinstaller_work),
        str(PACKAGING / "workbuddy2api.spec"),
    )

    binary = pyinstaller_dist / "workbuddy2api"
    if not binary.is_file():
        raise SystemExit(f"PyInstaller did not create {binary}")
    run(str(binary), "--version")

    stage = BUILD / "deb-root"
    debian = stage / "DEBIAN"
    debian.mkdir(parents=True)

    copy_file(binary, stage / "usr/bin/workbuddy2api", 0o755)
    copy_file(
        PACKAGING / "workbuddy2api.service",
        stage / "lib/systemd/system/workbuddy2api.service",
        0o644,
    )
    copy_file(
        PACKAGING / "workbuddy2api.env.example",
        stage / "usr/share/workbuddy2api/workbuddy2api.env.example",
        0o644,
    )
    copy_file(ROOT / "LICENSE", stage / "usr/share/doc/workbuddy2api/copyright", 0o644)
    copy_file(ROOT / "README.md", stage / "usr/share/doc/workbuddy2api/README.md", 0o644)
    changelog = stage / "usr/share/doc/workbuddy2api/changelog.gz"
    changelog.parent.mkdir(parents=True, exist_ok=True)
    changelog.write_bytes(gzip.compress(changelog_text(pkg_version).encode("utf-8"), mtime=0))
    changelog.chmod(0o644)

    (debian / "control").write_text(
        control_text(pkg_version, installed_size_kib(stage)),
        encoding="utf-8",
    )
    for name in ("postinst", "prerm", "postrm"):
        copy_file(PACKAGING / name, debian / name, 0o755)

    output = DIST / f"WorkBuddy2API-{pkg_version}-linux-amd64.deb"
    output.unlink(missing_ok=True)
    run("dpkg-deb", "--root-owner-group", "--build", str(stage), str(output))
    run("dpkg-deb", "--info", str(output))
    run("dpkg-deb", "--contents", str(output))

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    checksum = Path(str(output) + ".sha256")
    checksum.write_text(f"{digest}  {output.name}\n", encoding="ascii")

    print(f"Built {output} ({output.stat().st_size / 1048576:.1f} MB)")
    print(f"SHA256 {digest}")


if __name__ == "__main__":
    main()
