"""COV35 (coverage report, never-on): "N% increased Melee Damage if you've dealt a Projectile Attack Hit in the past eight
seconds" counted 0 (PoB's Condition:HitProjectileRecently is set only by an unset Configuration box). It now counts at
the share of the fight with such a hit in the past 8 s, from the build's own Projectile Attack hits."""
import math

from builder.ledger.coverage import ignored_line_status, model_entry
from builder.search import models

LINE = "20% increased Melee Damage if you've dealt a Projectile Attack Hit in the past eight seconds"


def test_no_projectile_attack_counts_nothing():
    mods, notes = models.projectile_hit_recently_model([LINE], {})
    assert mods == [] and notes[0]["value"] == 0


def test_the_line_counts_at_the_share_of_the_fight_after_a_hit():
    mods, notes = models.projectile_hit_recently_model([LINE, "unrelated"], {"Spear Throw": (0.25, "chosen press rate")})
    share = 1 - math.exp(-0.25 * 8)
    assert abs(notes[0]["value"] - round(share, 3)) < 1e-9
    assert len(mods) == 1 and "Melee Damage" in str(mods[0])


def test_without_the_line_nothing_happens():
    assert models.projectile_hit_recently_model(["10% increased Melee Damage"], {"Spear Throw": (1.0, "")}) == ([], [])


def test_the_audit_knows_a_model_reads_it():
    assert model_entry("(10-20)% increased Melee Damage if you've dealt a Projectile Attack Hit in the past eight seconds")
    assert ignored_line_status(LINE, False)[0] == "model"


import pytest


@pytest.fixture(scope="module")
def engine():
    import faulthandler

    from builder.pob.engine import PobEngine

    enabled = faulthandler.is_enabled()
    faulthandler.disable()  # LuaJIT's Lua errors are Windows exceptions PoB's pcall catches
    yield PobEngine()
    if enabled:
        faulthandler.enable()


def test_a_real_build_reads_its_own_projectile_attack_hits(engine):
    """On a real Lightning Arrow skeleton (a Projectile Attack main skill) the line counts at a share near 1 of the
    fight, through the Assumptions pipeline (projectile_attack_rates -> projectile_hit_recently_model); on a Spark
    skeleton (a Spell: no Projectile Attack) it counts 0."""
    from builder.ledger import items as ledger_items
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    def share(skill):
        payload = make_payload(BuildRequest(main_skill=skill))
        search = Search(engine, None, payload, enumerate_skeletons(Context(payload), [skill])[0][0].as_dict(), 0)
        search.setup(quests=False)
        raw = "\n".join(["Rarity: Rare", "Probe Ring", "Gold Ring", "Item Level: 80", "Implicits: 0", LINE])
        search.build.call("equip", slot="Ring 2", raw=ledger_items.corrected_item(raw), fields=[])
        search.assumptions.apply()
        found = [n for n in search.assumptions.active if n["label"].startswith("Projectile Attack Hit in the past 8 s")]
        assert len(found) == 1, [n["label"] for n in search.assumptions.active]
        return found[0]["value"]

    assert share("Lightning Arrow") > 0.9
    assert share("Spark") == 0
