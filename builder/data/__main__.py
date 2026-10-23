"""Usage:
    uv run python -m builder.data dump              export game data from the pinned PoB
    uv run python -m builder.data prices [league]   refresh the price cache
"""

from __future__ import annotations

import sys

from builder.data import dump


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "dump":
        print(f"wrote {dump()}")
    elif command == "prices":
        from builder.data.prices import PriceFeed

        feed = PriceFeed()
        league = sys.argv[2] if len(sys.argv) > 2 else feed.default_league()
        summary = feed.refresh(league, force=True)
        print(summary)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
