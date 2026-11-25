r"""A ready-made read-only probe: one build in, PoB's numbers out (PERF22, 24 Nov 2026). Runs anywhere a probe runs:
through the probe server (tools/probe.py) or a normal booking; always a fresh engine of its own.

    python tools/probe.py POE2T<n> 5 "<lane>: <what>" -- uv run python -X utf8 tools/probe_build.py <request.json>

The request (a JSON file; every key but one of xml / code is optional):
    {"xml": "<PathOfBuilding2>...", "code": "eNrt...",          the build (XML, or a PoB code)
     "groups": [{"index": 2, "enabled": false},                 bridge.setGroup on existing groups, in order:
                {"index": 1, "main": true, "skillPart": 2}],     toggle a group, pick the main skill / part / stat set
     "config": {"conditionEnemyShocked": true, "enemyIsBoss": "Pinnacle"},   Configuration tab inputs (false = unset)
     "stats": ["FullDPS", "CombinedDPS", "Life"]}                the output fields to return (default: a few headline ones)
A line on or off: send the build's XML with that line removed (or added); that is what the item / passive holds.

Prints one JSON object: {"stats": {...}, "config": {...the Configuration tab after the request...}, "groups": [...]}.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_STATS = ["FullDPS", "CombinedDPS", "TotalDPS", "Life", "EnergyShield", "Mana", "Spirit"]


def answer(request: dict, engine=None) -> dict:
    from builder.pob import codes
    from builder.pob.engine import PobEngine

    xml = request.get("xml") or codes.decode(request["code"])
    fields = request.get("stats") or DEFAULT_STATS
    engine = engine or PobEngine()
    engine.call("loadXml", xml=xml)
    for group in request.get("groups", []):
        engine.call("setGroup", **group)
    if request.get("config"):
        stats = engine.call("setConfig", vars=request["config"], fields=fields)["stats"]
    else:
        stats = engine.call("stats", fields=fields)
    return {"stats": stats, "config": engine.call("getConfig"), "groups": engine.call("groups")}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    print(json.dumps(answer(request), indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
