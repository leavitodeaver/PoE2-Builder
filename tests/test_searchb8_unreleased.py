"""SEARCHB8 (THE USER, 24 Nov 04:45, card: Claws, Daggers, One / Two Hand Swords, One / Two Hand Axes and Flails are "Not
yet: exclude"; the 1.0 preview option brings them back): with the switch off, no open-search skeleton and no item option
holds one of game_rules.yaml's unreleased classes, even for a skill whose PoB weapon types list them all; with it on, the
classes 1.0 releases (builder.preview.PREVIEW_WEAPON_CLASSES) come back."""
from builder.data.rules import rules
from builder.preview import PREVIEW_WEAPON_CLASSES
from builder.request.schema import BuildRequest
from builder.search.context import Context, make_payload
from builder.search.skeletons import enumerate_skeletons

SKILL = "Ancestral Cry"  # PoB's weaponTypes: Claw, Dagger, Flail, both Axes, both Swords, and released classes


def _held(preview: bool) -> tuple[set[str], set[str], set[str]]:
    ctx = Context(make_payload(BuildRequest(main_skill=SKILL, preview_1_0=preview)))
    unreleased = set(rules().unreleased) | set(PREVIEW_WEAPON_CLASSES)
    skeletons, _ = enumerate_skeletons(ctx, [SKILL])
    hands = {h for s in skeletons for h in (s.weapon, s.offhand, s.swap_weapon, s.swap_offhand) if h}
    items = {o.type for options in ctx.item_options.values() for o in options}
    return hands & unreleased, items & unreleased, set(rules().unreleased)


def test_the_unreleased_weapon_classes_stay_out_and_the_preview_brings_back_1_0s():
    gem = Context(make_payload(BuildRequest(main_skill=SKILL))).gems[SKILL]
    assert {"Claw", "Dagger", "Flail", "One Hand Axe", "Two Hand Axe", "One Hand Sword", "Two Hand Sword"} \
        <= set(gem["weaponTypes"])
    hands, items, unreleased = _held(False)
    assert unreleased == {"Claw", "Dagger", "Flail", "One Hand Axe", "One Hand Sword", "Two Hand Axe", "Two Hand Sword"}
    assert not hands and not items
    try:
        hands, items, unreleased = _held(True)
        assert hands and hands <= set(PREVIEW_WEAPON_CLASSES)
        assert not unreleased & set(PREVIEW_WEAPON_CLASSES)
    finally:
        _held(False)  # (a request without the switch puts the 0.5 rules back)
    assert not rules().weapon_in_game("Dagger")
