"""Re-score older gate4 runs' "floors met" under the fair floors (SEARCH27; the manager's ruling, 18 Nov 06:25).

gate4 took each reference's floors from PoB before our models until SEARCH27; since then they come from the fair
scoring (gate4.fair_floor_stats). An older run's 'feasible' was judged against the old floors, so it isn't comparable
with a newer run's. This doesn't re-run the search: for each row it takes the reference's fair floors with this
checkout's code (gate4's own steps), scores the run's delivered build the fair way under them (gate4.fair_reference)
and adds to the row, keeping every original field: feasible_fair_floors, refloor_short, refloor_dps (the build's DPS
as this checkout scores it, for information: the verdicts keep the run's own DPS), fair_floors, refloor_commit.
gate_median.py then ranks the run by feasible_fair_floors and says so.

usage: uv run python benchmarks/refloor.py RUN.json ...
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
_spec = importlib.util.spec_from_file_location("gate4", HERE / "gate4.py")
gate4 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate4)


def reference_floors(engine, fair_engine, name: str) -> tuple[object, dict]:
    """(the gate request with the fair floors, the reference's fair scoring) for reference `name`, as gate4 makes them."""
    xml = gate4.codes.decode((gate4.REFERENCE_DIR / f"{name}.txt").read_text(encoding="utf-8").strip())
    engine.call("loadXml", xml=xml)
    if gate4.bosses is not None:
        engine.call("setConfig", vars=gate4.boss_vars())
    facts = engine.call("facts", fields=gate4.FACT_FIELDS + ["FullDPS", "SkillDPS"])
    tree_keystones = set(gate4.GameData().keystones)
    keystones = sorted(p["name"] for p in facts["passives"] if p["type"] == "Keystone" and p["name"] in tree_keystones)
    request = gate4.reference_request(name, facts, keystones)
    fair = gate4.fair_reference(fair_engine, xml, request)
    request = gate4.reference_request(name, facts, keystones, fair["floor_stats"])
    return request, gate4.fair_reference(fair_engine, xml, request)


def main(paths: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    engine, fair_engine = gate4.PobEngine(), gate4.PobEngine()
    requests: dict[str, object] = {}
    commit = gate4.commit()
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for row in data["rows"]:
            if row.get("floors_from") == "fair" or not row.get("code"):
                continue
            name = row["name"]
            if name not in requests:
                requests[name], ref = reference_floors(engine, fair_engine, name)
                print(f"{name}: fair floors {requests[name].floors.model_dump(mode='json', exclude_none=True)}; the "
                      f"reference reads short: {ref['short'] or 'nothing'}", flush=True)
            request = requests[name]
            scored = gate4.fair_reference(fair_engine, gate4.codes.decode(row["code"]), request)
            row.update({"feasible_fair_floors": not scored["short"], "refloor_short": scored["short"],
                        "refloor_dps": scored["dps"], "fair_floors": request.floors.model_dump(mode="json", exclude_none=True),
                        "refloor_commit": commit})
            print(f"{Path(path).name} {name}: as run {'feasible' if row['feasible'] else 'not feasible'} "
                  f"({', '.join(row.get('failing') or []) or 'all met'}); under the fair floors "
                  f"{'feasible' if not scored['short'] else 'short: ' + ', '.join(scored['short'])} "
                  f"(DPS as run {float(row['dps']):,.0f}, scored here {scored['dps']:,.0f})", flush=True)
        Path(path).write_text(json.dumps(data, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
