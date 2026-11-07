"""For the PC queue's proof (POE2Q1): boot PoE2's real worker pool asking for more workers than its place allows, say what it got, and keep it
running so a hold can be seen pausing every process of it.

    uv run python tools/pool_probe.py <workers asked> <seconds to stay>
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from builder.pob import slots  # noqa: E402
from builder.pob.pool import EnginePool  # noqa: E402


def main() -> None:
    asked, stay = int(sys.argv[1]), float(sys.argv[2])
    with EnginePool(asked) as pool:
        print("pool: " + json.dumps({"asked": asked, "size": pool.size, "pids": [p.pid for p in pool._procs], "orchestrator": os.getpid(),
                                     "processors": slots.available_processors(), "free_slots": slots.free()}), flush=True)
        try:
            slots.take()  # the orchestrator has no engine yet: with the pool's 6 slots taken, a seventh must be refused
            print("seventh: allowed", flush=True)
        except slots.PoolFull:
            print("seventh: refused", flush=True)
        end = time.time() + stay
        while time.time() < end:
            time.sleep(0.5)
            print("tick %.3f" % time.time(), flush=True)


if __name__ == "__main__":
    main()
