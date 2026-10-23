"""Check out the pinned Path of Building (PoE2) snapshot, and its release for cross-checks, into vendor/.

Usage: uv run python scripts/fetch_pob.py
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from builder import ENGINE_DIR, POB_DIR, ROOT


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def checkout(target: Path, repository: str, ref: str, label: str) -> None:
    if not (target / ".git").exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        git("init", "-q", str(target))
        git("-C", str(target), "remote", "add", "origin", repository)
    # ref is a commit hash or a tag; fetching it shallowly is cheap and resolves both the same way
    git("-C", str(target), "fetch", "-q", "--depth", "1", "origin", ref)
    wanted = git("-C", str(target), "rev-parse", "FETCH_HEAD^{commit}")
    try:
        current = git("-C", str(target), "rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        current = ""  # freshly initialised, nothing checked out yet
    if current == wanted:
        print(f"{label}: already at {ref} ({wanted[:10]})")
        return
    git("-C", str(target), "checkout", "-q", "--detach", wanted)
    print(f"{label}: checked out {ref} ({wanted[:10]})")


def release_dir(pin: dict) -> Path:
    return ROOT / "vendor" / f"PathOfBuilding-PoE2-{pin['release_tag']}"


def main() -> None:
    pin = json.loads((ENGINE_DIR / "pob-pin.json").read_text(encoding="utf-8"))
    checkout(POB_DIR, pin["repository"], pin["commit"], "snapshot")
    checkout(release_dir(pin), pin["repository"], pin["release_tag"], "release")


if __name__ == "__main__":
    main()
