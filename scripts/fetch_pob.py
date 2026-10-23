"""Check out the pinned Path of Building (PoE2) commit into vendor/.

Usage: uv run python scripts/fetch_pob.py
"""

from __future__ import annotations

import json
import subprocess

from builder import ENGINE_DIR, POB_DIR


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def main() -> None:
    pin = json.loads((ENGINE_DIR / "pob-pin.json").read_text(encoding="utf-8"))
    if not (POB_DIR / ".git").exists():
        POB_DIR.parent.mkdir(parents=True, exist_ok=True)
        git("init", "-q", str(POB_DIR))
        git("-C", str(POB_DIR), "remote", "add", "origin", pin["repository"])
    try:
        current = git("-C", str(POB_DIR), "rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        current = ""  # freshly initialised, nothing checked out yet
    if current == pin["commit"]:
        print(f"already at {pin['commit'][:10]}")
        return
    git("-C", str(POB_DIR), "fetch", "-q", "--depth", "1", "origin", pin["commit"])
    git("-C", str(POB_DIR), "checkout", "-q", "--detach", "FETCH_HEAD")
    print(f"checked out {pin['commit'][:10]} ({pin['commit_date']})")


if __name__ == "__main__":
    main()
