"""A gem name several PoB gems share means one fixed gem in every PoB process (PERF24).

PoB lists its gems in an order that changes from one process to the next, so a by-name read of a shared name
("Lightning Bolt": the Voltaic Staff's spell and Choir of the Storm's triggered one) used to answer with another
skill in another process. engine/bridge.lua's SHARED_GEM_NAMES settles each such name; this test reads PoB's own
gem list and fails when a PoB upgrade adds a shared name the bridge doesn't settle."""
import re
from collections import defaultdict
from pathlib import Path

from builder.pob.engine import POB_DIR

ROOT = Path(__file__).resolve().parents[1]


def _gems() -> dict[str, set[str]]:
    """PoB's gems: name -> the granted effect ids of every gem key with that name (Data/Gems.lua)"""
    text = (POB_DIR / "src" / "Data" / "Gems.lua").read_text(encoding="utf-8")
    by_name: dict[str, set[str]] = defaultdict(set)
    blocks = re.findall(r'\n\t\["Metadata/[^"]+"\] = \{(.*?)\n\t\},', text, re.S)
    assert len(blocks) > 900  # the pattern still reads the file
    for body in blocks:
        by_name[re.search(r'\bname = "([^"]*)"', body).group(1)].add(re.search(r'grantedEffectId = "([^"]*)"', body).group(1))
    return by_name


def _effect_flags() -> dict[str, tuple[bool, bool]]:
    """granted effect id -> (granted by an item or the tree, support), from Data/Skills/*.lua"""
    flags = {}
    for path in (POB_DIR / "src" / "Data" / "Skills").glob("*.lua"):
        for effect, body in re.findall(r'\nskills\["([^"]+)"\] = \{(.*?)\n\}', path.read_text(encoding="utf-8"), re.S):
            flags[effect] = (bool(re.search(r"\n\t(fromItem|fromTree) = true", body)), bool(re.search(r"\n\tsupport = true", body)))
    return flags


def _settled() -> dict[str, str]:
    """engine/bridge.lua's SHARED_GEM_NAMES"""
    text = (ROOT / "engine" / "bridge.lua").read_text(encoding="utf-8")
    table = re.search(r"local SHARED_GEM_NAMES = \{(.*?)\n\}", text, re.S).group(1)
    return dict(re.findall(r'\["([^"]+)"\] = "([^"]+)"', table))


def test_every_name_two_pob_skills_share_is_settled_in_the_bridge():
    flags, settled = _effect_flags(), _settled()
    open_names = {}
    for name, effects in _gems().items():
        if len(effects) < 2:
            continue
        # gemByName takes a socketable gem before an item's or the tree's: one socketable gem settles the name
        socketable = [e for e in effects if not flags[e][0]]
        if len(socketable) == 1:
            continue
        if settled.get(name) not in effects:
            open_names[name] = sorted(effects)
    assert not open_names, f"PoB gems share these names and SHARED_GEM_NAMES settles none of them: {open_names}"


def test_the_settled_names_are_the_ones_pob_ships_today():
    """(PoB bb52d6b) so a name PoB stops sharing leaves the table, and its note, too"""
    shared = {name: effects for name, effects in _gems().items() if len(effects) > 1}
    assert set(shared) == {"Lightning Bolt", "Mace Strike", "Spear Stab", "Unleash"}
    assert _settled() == {"Lightning Bolt": "UniqueBreachLightningBoltPlayer", "Mace Strike": "Melee2HMacePlayer",
                          "Spear Stab": "MeleeSpearPlayer"}
    assert shared["Unleash"] == {"UnleashPlayer", "SupportUnleashPlayer"}


def test_a_group_gems_skill_id_picks_its_own_gem_where_a_name_is_shared():
    """COV31: the Python side reads a group's gem (or an active skill) by its granted effect id (the bridge's skillId),
    so the Voltaic Staff's Lightning Bolt never reads as Choir of the Storm's triggered one (a Cooldown skill): the
    Warcry, consume and Glory checks read skill types and descriptions through gem_of. A stand-in context with
    only all_gems (the tests' SimpleNamespace ones) still reads by name."""
    from types import SimpleNamespace

    from builder.search.context import gem_of

    castable = {"grantedEffectId": "LightningBoltPlayer", "skillTypes": ["Spell"]}
    triggered = {"grantedEffectId": "UniqueBreachLightningBoltPlayer", "skillTypes": ["Spell", "Cooldown"]}
    ctx = SimpleNamespace(all_gems={"Lightning Bolt": triggered},
                          gems_by_skill_id={"LightningBoltPlayer": castable, "UniqueBreachLightningBoltPlayer": triggered})
    assert gem_of(ctx, "Lightning Bolt", "LightningBoltPlayer") is castable
    assert gem_of(ctx, "Lightning Bolt", "UniqueBreachLightningBoltPlayer") is triggered
    assert gem_of(ctx, "Lightning Bolt") is triggered  # no id: by name, as before
    assert gem_of(ctx, "Lightning Bolt", "NoSuchSkill") is triggered  # an id the data lacks: by name
    assert gem_of(SimpleNamespace(all_gems={"Spark": castable}), "Spark", "SparkPlayer") is castable
    assert gem_of(SimpleNamespace(), "Spark") == {}
    # The real data: one gem per granted effect id, and each shared name's ids find their own gems
    from builder.data import GameData

    gems = GameData().gems
    by_id = {g["grantedEffectId"]: g for g in gems}
    assert len(by_id) == len(gems)
    for name, ids in _gems().items():
        if len(ids) > 1:
            for effect in ids:
                if effect in by_id:
                    assert by_id[effect]["name"] == name
