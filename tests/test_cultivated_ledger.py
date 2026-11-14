"""COV29: ledger entries for Cultivated outcome lines PoB doesn't parse whole (CULT1's list), reaching the search
through cultivated.outcome_lines (an entry makes its outcome offered, as CULT3's Saitha test does with a stand-in)."""
import json
from pathlib import Path

import pytest

from builder.ledger import entries, model_entry, translate
from builder.ledger.coverage import ignored_line_status, resolver_condition
from builder.search import cultivated

ROOT = Path(__file__).resolve().parents[1]
POB_UNIQUES = ROOT / "data" / "pob-bb52d6b368" / "uniques.json"


@pytest.fixture(scope="module")
def engine():
    try:
        from builder.data import GameData
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")
    from builder.pob.engine import PobEngine

    yield PobEngine()


def outcome(name: str, mod: str) -> dict:
    return next(o for o in cultivated.outcomes(name, offered=False) if o["mod"] == "UniqueMutatedVaal" + mod)


@pytest.mark.parametrize("name, mod, entry, status", [
    ("Saitha's Spear", "FireExposureOnHit", "cultivated-exposure-on-hit", "model"),
    ("Plaguefinger", "GlobalChanceToBlindOnHit", "cultivated-global-blind-on-hit", "model"),
    ("Redflare Conduit", "ManaGainedOnPowerChargeConsumption", "cultivated-mana-on-power-charge-consumed", "model"),
    ("Hateforge", "MaximumRagePerGlorySkillUsed", "cultivated-max-rage-after-glory-skill", "ledger"),
])
def test_an_entry_makes_its_cultivated_outcome_offered_and_the_audit_counts_its_line(name, mod, entry, status):
    o = outcome(name, mod)
    assert not o["pob_reads"] and o in cultivated.outcomes(name)
    (line,) = o["lines"]
    assert ignored_line_status(line, False)[:2] == (status, entry)
    # (a model entry adds nothing to PoB: the model reads the line itself; the Glory one stands in with a share line)
    assert cultivated.outcome_lines(o)[0] == line


def test_hateforges_glory_rage_stands_in_as_a_share_of_maximum_rage_from_its_own_20_second_window():
    (line,) = outcome("Hateforge", "MaximumRagePerGlorySkillUsed")["lines"]
    assert translate(line)[1] == ["share:GloryBuff20s +(8-10) to Maximum Rage"]
    assert cultivated.outcome_lines(outcome("Hateforge", "MaximumRagePerGlorySkillUsed")) == [
        line, "LedgerShare:GloryBuff20s +(8-10) to Maximum Rage"]
    condition = next(e.raw for e in entries() if e.id == "GloryBuff20s")
    assert condition["glory_window"] == 20 and not condition.get("glory_count")
    assert condition["usage_stat"] == "active_skill_requires_X_glory"
    # (the other Hateforge outcome, the Rage-on-Hit fill, has no reader yet: still not offered)
    assert outcome("Hateforge", "MaxRageFromRageOnHitChance") not in cultivated.outcomes("Hateforge")


def test_a_negative_reduced_energy_roll_is_an_increase_for_the_energy_model():
    entry = model_entry("Meta Skills gain -20% reduced Energy")
    assert entry is not None and entry.id == "meta-energy-reduced"
    assert entry.translate("Meta Skills gain -20% reduced Energy") == ["mult:MetaEnergy 20"]
    assert entry.translate("Meta Skills gain 15% reduced Energy") == ["mult:MetaEnergy -15"]
    assert ignored_line_status("Meta Skills gain (-30-30)% reduced Energy", False)[:2] == ("model", "meta-energy-reduced")


def test_the_audit_counts_a_plain_exposure_line_as_a_source_of_every_elements_exposure():
    """conditions._exposure_uptime reads plain "Exposure" as all three elements (MECH69); the audit's resolver check
    wanted an element word or "Elemental" and called a plain line open."""
    condition = resolver_condition("Attacks with this Weapon have 25% chance to inflict Exposure on Hit")
    assert condition is not None and condition.var.endswith("Exposure")
    assert resolver_condition("Cannot inflict Exposure") is None


def test_pob_counts_hateforges_cultivated_rage_line_at_its_glory_share(engine):
    """Real engine: Hateforge with its Glory outcome in place of a line: PoB parses the share line whole and adds
    +9 Maximum Rage (the middle roll) x Multiplier:LedgerShareGloryBuff20s / 100."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spark"))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    build = search.build
    raw = next(u["raw"] for u in json.loads(POB_UNIQUES.read_text(encoding="utf-8"))["uniques"] if u["name"] == "Hateforge")
    grown = cultivated.edited(raw, [], [outcome("Hateforge", "MaximumRagePerGlorySkillUsed")])
    build.call("equip", slot="Gloves", raw=grown, fields=[])
    base = [m for m in search.assumptions.mods if not m.get("name", "").startswith("Multiplier:LedgerShareGlory")]

    def rage(percent):
        mods = base + [{"kind": "Raw", "name": "Multiplier:LedgerShareGloryBuff20s", "type": "BASE", "value": percent}]
        return build.call("setAssumptions", mods=mods, fields=["MaximumRage"])["stats"]["MaximumRage"]

    none, half, full = rage(0), rage(50), rage(100)
    assert full == pytest.approx(none + 9) and half == pytest.approx(none + 4.5)
