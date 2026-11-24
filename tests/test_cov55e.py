"""COV55 item 3: aggravate-bleed-jagged-ground is retired, so Perforation is counted once, by MECH104's aggravate_model."""
from builder.ledger import entries, translate
from builder.search import models

PERFORATION_LINE = "20% chance for Bleeding to be Aggravated when Inflicted against Enemies on Jagged Ground"
BLEEDER = {"Speed": 2.0, "HitChance": 100, "CritChance": 0, "BleedDuration": 5.0, "BleedChancePerHit": 100}


def test_perforation_is_counted_by_the_model_not_the_ledger():
    import yaml
    from builder.ledger import ENTRY_DIR

    raw = yaml.safe_load((ENTRY_DIR / "pob-gaps-boss.yaml").read_text(encoding="utf-8"))
    assert next(e for e in raw if e["id"] == "aggravate-bleed-jagged-ground")["status"] == "retired"
    assert all(e.id != "aggravate-bleed-jagged-ground" for e in entries())  # (retired entries aren't loaded)
    entry, out = translate(PERFORATION_LINE)  # (now a model link only: no share: line, nothing applied)
    assert entry.id == "model-read-cov55-perforation" and out == [] and entry.raw["model"] == models.AGGRAVATE_MODEL
    assert models.PERFORATION.match(PERFORATION_LINE)
    mods, notes = models.aggravate_model([PERFORATION_LINE], BLEEDER, {"Attack"}, jagged=(0.5, "Earthquake"))
    assert mods and mods[0]["scale"] > 0 and "Perforation" in notes[0]["basis"]


def test_cov55_train135_readers_are_marked():
    from builder.ledger import coverage
    from builder.search import conditions

    line = "25% chance to gain Archon of Undeath when you use a Command skill"
    entry, out = translate(line)
    assert entry.id == "model-read-cov55-archon-of-undeath-command" and out == [] and models.UNDEATH_ON_COMMAND.match(line)
    assert conditions.COMBAT_FRENZY_STAT == "skill_combat_frenzy_x_ms_cooldown"
    assert coverage.stat_covered(coverage.stat_entries(), "skill_combat_frenzy_x_ms_cooldown", "Combat Frenzy") == (
        "model", "model-read-cov55-combat-frenzy-cooldown")
    assert translate("Melee Attacks fire 2 additional Projectiles") is None
