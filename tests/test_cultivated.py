"""Cultivated uniques' data (CULT1): builder/data/cultivated.json against the game files and PoB's unique list."""
import json
from pathlib import Path

import pytest

from builder.search import cultivated

ROOT = Path(__file__).resolve().parents[1]
MODS = ROOT / "data" / "game-26476662" / "tables" / "Mods.json"
POB_UNIQUES = ROOT / "data" / "pob-bb52d6b368" / "uniques.json"


def test_the_48_uniques_poe2db_lists_are_all_in_pobs_unique_list():
    names = {u["name"] for u in json.loads(POB_UNIQUES.read_text(encoding="utf-8"))["uniques"]}
    found = cultivated.uniques()
    assert len(found) == 48
    assert set(found) <= names
    assert {"Atziri's Rule", "Saitha's Spear", "The Covenant", "Idle Hands", "Doryani's Prototype"} <= set(found)


@pytest.mark.skipif(not MODS.exists(), reason="needs the game-file export")
def test_every_outcome_is_a_game_cultivation_mod_with_the_games_ranges():
    rows = {r["Id"]: r for r in json.loads(MODS.read_text(encoding="utf-8"))}
    for name, unique in cultivated.uniques().items():
        assert unique["outcomes"], name
        for o in unique["outcomes"]:
            row = rows[o["mod"]]
            assert row["Id"].startswith("UniqueMutatedVaal") and row["GenerationType"] == 3, (name, o["mod"])
            assert [s[1:] for s in o["stats"]] == [row[f"Stat{i}Value"] for i in range(1, 9)
                                                    if row.get(f"Stat{i}") is not None and row.get(f"Stat{i}Value")]


def test_the_0_5_0_outcome_changes_are_in_the_data():
    def mods(name):
        return {o["mod"]: o for o in cultivated.uniques()[name]["outcomes"]}
    # 0.5.0: Shackles lost "Elemental Ailments other than Freeze ... Reflected to you", gained Damage per Curse on you
    assert "UniqueMutatedVaalDamagePerCurse" in mods("Shackles of the Wretched")
    assert "UniqueMutatedVaalReflectElementalAilmentsToSelf" not in mods("Shackles of the Wretched")
    # 0.5.0: The Vertex lost +2-4 to all Skills, gained +3-5 to Curse Skills
    assert mods("The Vertex")["UniqueMutatedVaalCurseGemLevel"]["stats"] == [["curse_skill_gem_level_+", 3, 5]]
    assert "UniqueMutatedVaalGlobalSkillGemLevel" not in mods("The Vertex")
    assert mods("Atziri's Splendour")["UniqueMutatedVaalAtziriSplendourEnergyShield1"]["stats"][0][1:] == [66, 100]
    conflict = cultivated.load()["_about"]["conflicts"][0]
    assert conflict["unique"] == "Hateforge" and "16-30" in conflict["patch_notes"]


def test_the_search_is_offered_only_outcomes_pob_scores_and_the_rest_stay_listed():
    spear = cultivated.outcomes("Saitha's Spear", offered=False)
    exposure = next(o for o in spear if o["mod"] == "UniqueMutatedVaalFireExposureOnHit")
    assert not exposure["pob_reads"]
    # offered only once the ledger stands in for it (COV27's lines; tested with a stand-in below)
    assert (exposure in cultivated.outcomes("Saitha's Spear")) == (cultivated.outcome_lines(exposure) is not None)
    assert ("Saitha's Spear", "UniqueMutatedVaalFireExposureOnHit",
            ["(30-50)% chance to inflict Exposure on Hit"]) in cultivated.unread_outcomes()
    keystone = next(o for o in cultivated.outcomes("Flesh Crucible", offered=False)
                    if o["mod"] == "UniqueMutatedVaalRandomKeystoneFromTable")
    assert "not_offered" in keystone and keystone not in cultivated.outcomes("Flesh Crucible")
    assert all(cultivated.outcome_lines(o) is not None for name in cultivated.uniques() for o in cultivated.outcomes(name))


def test_replaceable_lines_and_unknown_uniques():
    assert len(cultivated.replaceable_lines("Saitha's Spear")) == 5
    assert all(line["replaceable"] for u in cultivated.uniques().values() for line in u["lines"])
    assert not cultivated.cultivatable("Headhunter") and cultivated.outcomes("Headhunter") == []


# Pricing, item text and the search's trial (CULT2) ------------------------------------------------------------------

def pob_raw(name: str) -> str:
    return next(u["raw"] for u in json.loads(POB_UNIQUES.read_text(encoding="utf-8"))["uniques"] if u["name"] == name)


def test_the_craft_odds_are_data_and_count_only_the_exact_result_wanted():
    model = cultivated.odds()["model"]
    assert model["kind"] == "paired" and model["lines"] == {"1": 0.8, "2": 0.2} and model["confidence"] == "LOW"
    # one wanted outcome of 4, on the one acceptable line of 5, in the one-line 80% of uses
    assert cultivated.success_chance(4, 5, 1, 1) == pytest.approx(0.8 * (1 / 4) * (1 / 5))
    assert cultivated.success_chance(4, 5, 1, 2) == pytest.approx(0.8 * (1 / 4) * (2 / 5))
    # two wanted outcomes together (never the same twice), on two acceptable lines, in the two-line 20%
    assert cultivated.success_chance(4, 5, 2, 2) == pytest.approx(0.2 * (1 / 6) * (1 / 10))
    assert cultivated.success_chance(1, 5, 2, 5) == 0.0 and cultivated.success_chance(0, 5, 1, 1) == 0.0
    # the user's reading, shown beside the price: separate even odds of 0/1/2 added and 0/1/2 removed
    users = cultivated.odds()["compare"][0]
    assert users["kind"] == "separate" and users["confidence"] == "UNVERIFIED"
    assert cultivated.success_chance(4, 5, 1, 1, users) == pytest.approx((1 / 3) * (1 / 4) * (1 / 3) * (1 / 5))
    both = cultivated.chances(4, 5, 1, 1)
    assert list(both) == [model["name"], users["name"]]


class _Ctx:
    def __init__(self, costs, ssf=False, cap=1000.0):
        self.payload = {"cultivation_prices": costs}
        self.ssf = ssf
        self.price_cap = cap
        self.unique_gaps = {}
        self.data = type("D", (), {"uniques": [{"name": "Atziri's Acuity", "raw": pob_raw("Atziri's Acuity")}]})()


def test_a_craft_is_the_plain_price_plus_the_orbs_for_the_expected_uses_labelled_low_with_the_users_reading():
    ctx = _Ctx({"orb": 6.0})
    price, note = cultivated.craft_price(ctx, "Atziri's Acuity", 1.0, {"model A": 0.05, "the user's reading": 0.01})
    assert price == pytest.approx(1.0 + 20 * 6.0)
    assert "LOW" in note and "UNVERIFIED" in note and "~20 Vaal Cultivation Orbs" in note
    assert "compare the user's reading: ~100 uses, 601 div" in note
    assert cultivated.craft_price(ctx, "Atziri's Acuity", None, {"m": 0.05}) is None  # no poe.ninja price: not offered
    assert cultivated.craft_price(_Ctx({}), "Atziri's Acuity", 1.0, {"m": 0.05}) is None  # no orb price: never free
    assert cultivated.craft_price(ctx, "Atziri's Acuity", 1.0, {"m": 0.0}) is None


def test_outcomes_pob_doesnt_read_go_through_the_ledger_and_are_offered_once_it_stands_in(monkeypatch):
    import builder.ledger as ledger

    exposure = next(o for o in cultivated.outcomes("Saitha's Spear", offered=False)
                    if o["mod"] == "UniqueMutatedVaalFireExposureOnHit")
    monkeypatch.setattr(ledger, "translate", lambda line: None)
    assert cultivated.outcome_lines(exposure) is None and exposure not in cultivated.outcomes("Saitha's Spear")
    entry = type("E", (), {"on_items": True})()
    monkeypatch.setattr(ledger, "translate", lambda line: (entry, ["stand-in for " + line]) if "Exposure" in line else None)
    assert cultivated.outcome_lines(exposure) == ["(30-50)% chance to inflict Exposure on Hit",
                                                  "stand-in for (30-50)% chance to inflict Exposure on Hit"]
    assert exposure in cultivated.outcomes("Saitha's Spear")
    text = cultivated.edited(pob_raw("Saitha's Spear"), [], [exposure])
    assert "{mutated}{range:0.5}stand-in for (30-50)% chance to inflict Exposure on Hit" in text


def test_the_lines_lost_are_the_uniques_own_in_its_selected_variant_and_two_line_mods_go_whole():
    acuity = pob_raw("Atziri's Acuity")
    found = cultivated.explicit_lines("Atziri's Acuity", acuity)
    assert [text for _, text in found] == ["(150-200)% increased Armour", "+(100-150) to maximum Life",
                                           "10% of Physical damage dealt by your Hits causes Blood Loss"]
    # a ledger stand-in appended to the text is never the line lost; a line PoB ignores isn't either
    assert cultivated.explicit_lines("Atziri's Acuity", acuity + "\n(150-200)% increased Armour", acuity) == found
    assert all(t != "+(100-150) to maximum Life" for _, t in cultivated.explicit_lines(
        "Atziri's Acuity", acuity, ignored={"+(100-150) to maximum Life"}))
    blood = cultivated.explicit_lines("Bloodbarrier", pob_raw("Bloodbarrier"))
    two = [(at, t) for at, t in blood if t.startswith("Inflict Corrupted Blood")]
    assert len(two) == 1 and len(two[0][0]) == 2  # PoB writes it over two lines: both go
    adorned = cultivated.explicit_lines("The Adorned", pob_raw("The Adorned"))  # no Selected Variant: the last one
    assert len(adorned) == 1 and "(0-150)%" in adorned[0][1]


def test_the_cultivated_text_drops_the_line_adds_the_outcome_as_mutated_and_is_corrupted():
    acuity = pob_raw("Atziri's Acuity")
    (armour, _), = [x for x in cultivated.explicit_lines("Atziri's Acuity", acuity) if "Armour" in x[1]]
    es = next(o for o in cultivated.outcomes("Atziri's Acuity") if o["mod"] == "UniqueMutatedVaalAddedMaximumEnergyShield")
    text = cultivated.edited(acuity, list(armour), [es])
    assert "(150-200)% increased Armour" not in text
    assert text.endswith("{mutated}{range:0.5}+(100-150) to maximum Energy Shield\nCorrupted")


class _Gear:
    """A stand-in gear search: an outcome line is worth `worth[mod line]`, losing a line costs `cost[line]`."""

    def __init__(self, ctx, worth, cost, lam=0.0, left=1000.0):
        self.ctx, self.worth, self.cost, self.lam, self.left = ctx, worth, cost, lam, left
        self.build = type("B", (), {"score": 1.0})()
        self.calls = 0

    def deltas(self, slot, raws):
        self.calls += 1
        out = []
        for raw in raws:
            score = 1.0 + sum(v for k, v in self.worth.items() if "{mutated}{range:0.5}" + k in raw)
            score -= sum(v for k, v in self.cost.items() if k not in raw)
            out.append((score, {}))
        return out

    def left_for(self, slot):
        return self.left

    def adjusted(self, score, option):
        return score - self.lam * ((option.price or 0.0) if option else 0.0)


def _acuity_option():
    from builder.search.context import ItemOption
    return ItemOption("Atziri's Acuity", "Vaal Gloves", "Gloves", None, pob_raw("Atziri's Acuity"), True, 1.0, "high", 60)


def test_the_trial_crafts_the_outcome_that_helps_in_place_of_the_line_the_build_loses_least_by():
    ctx = _Ctx({"orb": 6.0})
    gear = _Gear(ctx, worth={"(5-10)% increased maximum Life": 0.10},
                 cost={"(150-200)% increased Armour": 0.01, "+(100-150) to maximum Life": 0.05,
                       "10% of Physical damage dealt by your Hits causes Blood Loss": 0.03})
    plain = _acuity_option()
    grown = cultivated.trial(gear, "Gloves", plain, deadline=float("inf"))
    assert grown is not plain and "(150-200)% increased Armour" not in grown.raw
    assert "{mutated}{range:0.5}(5-10)% increased maximum Life" in grown.raw and grown.raw.endswith("Corrupted")
    assert grown.variants[-1].startswith("Cultivated: MaximumLifeIncreasePercent")
    assert "craft estimate, LOW" in grown.confidence
    n = len(cultivated.outcomes("Atziri's Acuity", offered=False))
    k = max(3, len(cultivated._explicit(pob_raw("Atziri's Acuity"))))  # every explicit line might go: the cautious side
    assert grown.price == pytest.approx(1.0 + 6.0 / cultivated.success_chance(n, k, 1, 1))
    assert "compare the user's reading" in grown.confidence
    # an outcome that adds nothing is never crafted, and the trial isn't repeated at the same score
    gear = _Gear(ctx, worth={}, cost={})
    assert cultivated.trial(gear, "Gloves", plain, deadline=float("inf")) is plain
    calls = gear.calls
    assert cultivated.trial(gear, "Gloves", plain, deadline=float("inf")) is plain and gear.calls == calls


def test_the_trial_keeps_the_plain_unique_when_the_craft_costs_more_than_it_gives_or_cant_be_bought():
    ctx = _Ctx({"orb": 6.0})
    worth = {"(5-10)% increased maximum Life": 0.10}
    cost = {"(150-200)% increased Armour": 0.01}
    assert cultivated.trial(_Gear(ctx, worth, cost, lam=0.01), "Gloves", _acuity_option(), float("inf")).variants == ()
    assert cultivated.trial(_Gear(ctx, worth, cost, left=50.0), "Gloves", _acuity_option(), float("inf")).variants == ()
    assert cultivated.trial(_Gear(_Ctx({}), worth, cost), "Gloves", _acuity_option(), float("inf")).variants == ()
    from dataclasses import replace
    unpriced = replace(_acuity_option(), price=None)  # no poe.ninja price for the plain unique: no craft either
    assert cultivated.trial(_Gear(ctx, worth, cost), "Gloves", unpriced, float("inf")).variants == ()
    ssf = _Ctx({"orb": 6.0}, ssf=True)
    assert cultivated.trial(_Gear(ssf, worth, cost), "Gloves", _acuity_option(), float("inf")).variants == ()


@pytest.fixture(scope="module")
def engine():
    try:
        from builder.data import GameData
        GameData()
    except FileNotFoundError:
        pytest.skip("no game-data dump")
    from builder.pob.engine import PobEngine

    yield PobEngine()


def test_pob_scores_a_cultivated_item_with_its_outcome_and_without_the_line_it_lost(engine):
    """Real engine: Atziri's Acuity with '+(100-150) to maximum Energy Shield' in place of '(150-200)% increased Armour',
    against the plain gloves, on a Spark build: PoB reads the {mutated} line (more Energy Shield) and the lost line is
    gone (less Armour)."""
    from builder.request.schema import BuildRequest
    from builder.search.context import Context, make_payload
    from builder.search.inner import Search
    from builder.search.skeletons import enumerate_skeletons

    payload = make_payload(BuildRequest(main_skill="Spark", ascendancies=["Stormweaver"]))
    skeleton = enumerate_skeletons(Context(payload), ["Spark"])[0][0]
    search = Search(engine, None, payload, skeleton.as_dict(), 0)
    search.setup(quests=False)
    plain = Context(payload).corrected_raw("Atziri's Acuity", pob_raw("Atziri's Acuity"))
    (armour, _), = [x for x in cultivated.explicit_lines("Atziri's Acuity", plain, pob_raw("Atziri's Acuity"))
                    if "Armour" in x[1]]
    es = next(o for o in cultivated.outcomes("Atziri's Acuity") if o["mod"] == "UniqueMutatedVaalAddedMaximumEnergyShield")
    grown = cultivated.edited(plain, list(armour), [es])
    (_, before), (_, after) = search.build.item_deltas([{"slot": "Gloves", "raw": plain}, {"slot": "Gloves", "raw": grown}])
    assert after["EnergyShield"] >= before["EnergyShield"] + 100 * 0.9  # +125 at the middle roll, before any increase
    assert after["Armour"] < before["Armour"]


def test_the_lines_the_wiki_and_the_game_say_the_orb_cant_replace_are_never_the_line_lost():
    never = {"Atziri's Contempt": "Bloodstone Lance", "Atziri's Splendour": "Only Soul Cores can be Socketed",
             "Mask of the Stitched Demon": "Cannot have Energy Shield", "Quecholli": "no Critical Damage Bonus"}
    for name, words in never.items():
        assert words in pob_raw(name), name
        assert not any(words in text for _, text in cultivated.explicit_lines(name, pob_raw(name))), name
    wiki = cultivated.load()["_about"]["unreplaceable_per_wiki"]["lines"]
    assert [u for u, row in wiki.items() if row["in_game_OriginalMods"]] == ["The Vertex"]
