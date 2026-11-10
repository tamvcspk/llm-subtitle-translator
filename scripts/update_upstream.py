"""Update and verify the locked gemini-srt-translator release."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "gemini-srt-translator"


def run(*command: str) -> None:
    print(f"+ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def locked_version() -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    for package in lock["package"]:
        if package["name"] == PACKAGE:
            return str(package["version"])
    raise RuntimeError(f"{PACKAGE} is missing from uv.lock")


def main() -> int:
    previous = locked_version()

    run("uv", "lock", "--upgrade-package", PACKAGE)
    run("uv", "sync", "--locked", "--no-dev", "--all-packages")
    for member in ("apps/worker", "packages/subcore"):
        run(
            "uv",
            "run",
            "--locked",
            "python",
            "-m",
            "unittest",
            "discover",
            "-s",
            f"{member}/tests",
            "-t",
            member,
            "-v",
        )
    run(
        "uv",
        "run",
        "--locked",
        "python",
        "-m",
        "compileall",
        "-q",
        "-f",
        "apps/worker/worker.py",
        "apps/worker/gst_worker",
        "packages/subcore/src",
    )

    current = locked_version()
    if current == previous:
        print(f"{PACKAGE} is already current at {current}.")
    else:
        print(f"Updated {PACKAGE}: {previous} -> {current}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"Update verification failed with exit code {exc.returncode}.", file=sys.stderr)
        raise SystemExit(exc.returncode) from exc
