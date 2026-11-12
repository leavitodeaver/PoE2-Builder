"""Passive-tree lines whose worth the build decides (COV-B lane): each is a ledger `model:` entry
(ledger/entries/passives-batch3.yaml) read here, with its value worked out again by the bridge on every
calculation (engine/bridge.lua DERIVED.noMercy / DERIVED.rhythmOfIce), so what-ifs see it.

- No Mercy (Witchhunter): "Deal up to 40% more Damage to Enemies based on their missing Concentration". Only Hits
  break Concentration (Witchbane: 100% of Hit damage dealt, Concentration 30% of Life), so the average over a fight
  from full Life follows the build's Hit share of its damage (scratch research no-mercy-concentration.md, RESEARCH8).
- Rhythm of Ice: "6% increased Cast Speed per Spell Echoed Recently, up to 30%": the main spell's echoed casts in
  the past 4 s (KeywordPopups Recently), from its cast rate and its Echo sources (Spell Echo, Zarokh's Refrain, Now
  and Again's chance), not a flat 30% whenever Spell Echo is in the build (research rhythm-of-ice.md, RESEARCH5).
"""

from __future__ import annotations

import math
import re

from builder.search.models import UNIT, Inputs, Skill, derived, line, number

# No Mercy -------------------------------------------------------------------------------------------------------------

NO_MERCY = "NoMercy"  # flag (the passive) and, as NoMercyMore, the derived "more Damage" % x UNIT
NO_MERCY_MORE = "NoMercyMore"
WITCHBANE = "Witchbane"  # the condition Witchbane's ledger entry switches on (pob-gaps-boss.yaml witchbane-concentration)
NO_MERCY_LINE = re.compile(r"^Deal up to (\d+)% more Damage to Enemies based on their missing Concentration$", re.I)
CONCENTRATION = re.compile(r"^Enemies have Maximum Concentration equal to (\d+)% of their Maximum Life$", re.I)
BREAK = re.compile(r"^Break enemy Concentration on Hit equal to (\d+)% of Damage Dealt$", re.I)


def no_mercy_more(maximum: float, hit_share: float, concentration: float = 30.0, broken: float = 100.0) -> float:
    """No Mercy's average "more Damage" (%) over a fight from full Life to death, assuming the bonus grows linearly with
    the missing Concentration (the line's "up to ... based on": MEDIUM) and nothing restores it (no 5 s without Hits).

    In units of the boss's Life: hits break Concentration C = concentration% by broken% of the Hit damage dealt, the bonus
    included, so with total damage D dealt (a share h of it Hits) dD/dt = d (1 + B h b D / C) while it ramps (B = the
    maximum, b = broken%). The ramp ends at D1 = C / (h b): t1 = ln(1 + B) / a, a = B h b / C; then the rest at x(1 + B).
    If D1 >= 1 the boss dies on the ramp: t = ln(1 + a) / a. The average multiplier is 1 / t (vs 1 without it).
    Hits only: research no-mercy-concentration.md (h = 1: x1.329, 82% of 40%; h = 0.5: x1.265; h = 0.2: x1.128)."""
    B, h, C, b = maximum / 100.0, min(max(hit_share, 0.0), 1.0), concentration / 100.0, broken / 100.0
    if B <= 0 or h <= 0 or C <= 0 or b <= 0:
        return 0.0
    a = B * h * b / C
    ramp_end = C / (h * b)
    if ramp_end >= 1.0:
        t = math.log1p(a) / a
    else:
        t = math.log1p(B) / a + (1.0 - ramp_end) / (1.0 + B)
    return (1.0 / t - 1.0) * 100.0


def hit_share(stats: dict) -> float:
    """The Hit part of the main skill's damage: TotalDPS (its Hits) over CombinedDPS (Hits, damage over time and
    ailments). No damage: 0. (engine/bridge.lua DERIVED.noMercy works it out the same way.)"""
    combined, hits = number(stats.get("CombinedDPS")), number(stats.get("TotalDPS"))
    if combined <= 0:
        return 0.0
    return min(max(hits / combined, 0.0), 1.0)


def _first(texts: list[str], pattern: re.Pattern, default: float | None) -> float | None:
    for text in texts:
        if m := pattern.match(text.strip()):
            return float(m.group(1))
    return default


def no_mercy_model(inputs: Inputs, stats: dict, texts: list[str]) -> tuple[list[dict], list[dict]]:
    if not inputs.has(NO_MERCY):
        return [], []
    maximum = _first(texts, NO_MERCY_LINE, 0.0) or 0.0
    concentration = _first(texts, CONCENTRATION, None)
    if concentration is None:  # (only Witchbane gives enemies Concentration (checked: RESEARCH8 uses.py over every stat
        # holding "focus", KeywordPopups Concentration; poe2db Witchbane: research no-mercy-concentration.md)
        return [], [{"label": "No Mercy", "value": "not counted",
                     "basis": "enemies have no Concentration without Witchbane, so none is missing", "confidence": "datamined"}]
    broken = _first(texts, BREAK, 100.0) or 0.0
    share = hit_share(stats)
    more = no_mercy_more(maximum, share, concentration, broken)
    when = [{"var": NO_MERCY}, {"var": WITCHBANE}]
    mods = [line("1% more Damage", 1 / UNIT, when=when, per=NO_MERCY_MORE),
            derived(NO_MERCY_MORE, "noMercy", more * UNIT, maximum=maximum, concentration=concentration, broken=broken)]
    note = {
        "label": "No Mercy: average more Damage over the fight", "value": f"{more:.1f}%",
        "basis": f"{share:.0%} of the main skill's damage is Hits (its Hit DPS over its combined DPS); only Hits break "
                 f"Concentration ({broken:g}% of Hit damage, Concentration {concentration:g}% of Life), and the bonus "
                 f"grows with the missing share up to {maximum:g}%: averaged from full Life to death "
                 f"({more / maximum:.0%} of the maximum)" if maximum else "no bonus",
        "play": "Pauses of 5 s or more without Hits (boss phases) restore Concentration and lower it (not counted); other "
                "skills' Hits also break it (only the main skill's counted).",
        "confidence": "theory"}
    return mods, [note]


# Rhythm of Ice --------------------------------------------------------------------------------------------------------

RHYTHM = "RhythmOfIce"  # flag (the passive); RhythmOfIceSpeed: the derived increased Cast Speed % x UNIT
RHYTHM_SPEED = "RhythmOfIceSpeed"
RHYTHM_LINE = re.compile(r"^(\d+)% increased Cast Speed per Spell Echoed Recently, up to (\d+)%$", re.I)
ECHO_CHANCE = re.compile(r"^Cascadable Spells have (\d+)% chance to Echo$", re.I)  # Now and Again
ECHO_EVERY_CAST = ("Spell Echo", "Zarokh's Refrain")  # supports: "Supported Spells Echo" (thrice for Zarokh's, on a cooldown)
RECENTLY = 4.0  # KeywordPopups Recently: "the past 4 seconds"
# A9-3 (AUDIT9): the gem's own quality. GrantedEffectQualityStats: CometPlayer and SkyfallPlayer give
# base_spell_%_chance_to_echo at 500 permille, 0.5% per quality (PoB act_int.lua Comet, other.lua SkyfallPlayer). (checked:
# every quality stat, skill / support stat set, Mod and passive holding a stat with "echo" in its id, 12 Nov: the quality
# stat is on these two only; the passive is Now and Again; Tul's Avalanche below; poe2db Comet, Skyfall.)
QUALITY_ECHO = {"Comet": 0.5, "Skyfall": 0.5}  # % chance to Echo per quality
# Tul's Avalanche (SupportTulsAvalanche): "Supports Cascadable Spells you cast yourself, Sealing them when you Block. The
# Seals are broken when used, each making the Spell Echo. Cannot support skills with a Cooldown" (GemEffects); a Seal
# every 2 Blocks (skill_gain_seal_every_x_hits_blocked 2), up to 4, 1 Echo per Seal broken. Blocks per second as
# models.py's meta-gem block rate: (1 - Evade chance) x Block chance / the boss's time between hits.
TULS_AVALANCHE = "Tul's Avalanche"
TULS_BLOCKS_PER_SEAL = 2.0


def echo_chance(main: Skill | None, main_types: set[str], main_supports: set[str], texts: list[str],
                by_totem: bool = False, quality: float = 0.0) -> tuple[float, str]:
    """The chance a use of the main skill Echoes, and from what. A Cascadable spell you use yourself: Spell Echo or
    Zarokh's Refrain on it, every use (their PoB data: Cascadable, not Triggered / by proxy / Persistent); else Now and
    Again's "Cascadable Spells have 20% chance to Echo". Zarokh's three Echoes count as one Spell Echoed (LOW: the lower
    reading; research rhythm-of-ice.md)."""
    if main is None or not main.spell or "Cascadable" not in main_types:
        return 0.0, "the main skill isn't a Cascadable spell"
    if main.triggered or by_totem or {"Triggered", "Persistent", "UsedByProxy"} & main_types:
        return 0.0, "the main spell isn't one you cast yourself (Echo supports skip it)"
    support = next((s for s in ECHO_EVERY_CAST if s in main_supports), None)
    if support:
        return 1.0, support
    passive = sum(float(m.group(1)) for t in texts if (m := ECHO_CHANCE.match(t.strip())))
    own = QUALITY_ECHO.get(main.name, 0.0) * max(quality, 0.0)
    chance = min((passive + own) / 100.0, 1.0)
    if chance > 0:
        parts = ([f"Now and Again {passive:g}%"] if passive > 0 else []) + \
                ([f"{main.name}'s {quality:g}% quality {own:g}%"] if own > 0 else [])
        return chance, f"a {chance:.0%} chance to Echo ({', '.join(parts)})"
    return 0.0, "nothing makes the main spell Echo"


def seal_rate(main: Skill | None, main_types: set[str], main_supports: set[str], stats: dict,
              by_totem: bool = False) -> float:
    """Seals per second Tul's Avalanche gives the main spell (0 without it, or where it can't support the spell)."""
    if TULS_AVALANCHE not in main_supports or main is None or not main.spell or "Cascadable" not in main_types:
        return 0.0
    if main.triggered or by_totem or {"Triggered", "Persistent", "UsedByProxy"} & main_types:
        return 0.0
    if number(stats.get("Cooldown")) > 0:
        return 0.0
    every = number(stats.get("enemySkillTime")) or 0.7
    land = 1 - min(number(stats.get("EvadeChance")), 100) / 100
    block = min(number(stats.get("EffectiveBlockChance")), 100) / 100
    return land * block / every / TULS_BLOCKS_PER_SEAL


def echoed_per_second(uses: float, chance: float, seals: float = 0.0) -> float:
    """Spells Echoed per second: the uses that Echo by chance, and of the rest, one per Seal (a use breaks every Seal
    held; several Seals on one use count as one Spell Echoed, the lower reading as for Zarokh's Refrain: LOW)."""
    uses = max(uses, 0.0)
    return uses * chance + (1 - chance) * min(uses, max(seals, 0.0))


def rhythm_speed(per: float, cap: float, rate: float, chance: float, cooldown: float = 0.0, seals: float = 0.0) -> float:
    """Increased Cast Speed (%) from echoed casts per second: per x the spells Echoed in the past 4 s, up to cap. A
    cooldown (Zarokh's Refrain gives one) caps the uses at one per cooldown; Tul's Avalanche's Seals add Echoes."""
    if cooldown > 0:
        rate = min(rate, 1.0 / cooldown)
    return min(cap, per * RECENTLY * echoed_per_second(rate, chance, seals))


def rhythm_model(inputs: Inputs, stats: dict, texts: list[str], main: Skill | None, main_types: set[str],
                 main_supports: set[str], by_totem: bool = False, quality: float = 0.0) -> tuple[list[dict], list[dict]]:
    if not inputs.has(RHYTHM):
        return [], []
    m = next((m for t in texts if (m := RHYTHM_LINE.match(t.strip()))), None)
    per, cap = (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)
    chance, source = echo_chance(main, main_types, main_supports, texts, by_totem, quality)
    seals = seal_rate(main, main_types, main_supports, stats, by_totem)
    if seals > 0 and chance < 1:
        source = f"{source if chance > 0 else 'no chance to Echo'}; Tul's Avalanche: {seals:.2f} Seals/s from Blocks"
    rate, cooldown = number(stats.get("Speed")), number(stats.get("Cooldown"))
    gain = rhythm_speed(per, cap, rate, chance, cooldown, seals)
    when = [{"var": RHYTHM}]
    mods = [line("1% increased Cast Speed", 1 / UNIT, when=when, per=RHYTHM_SPEED),
            derived(RHYTHM_SPEED, "rhythmOfIce", gain * UNIT, per=per, cap=cap, chance=chance, seals=seals)]
    if chance <= 0 and seals <= 0:
        return mods, [{"label": "Rhythm of Ice", "value": "not counted", "confidence": "datamined",
                       "basis": f"{source}; only the main spell's Echoes are counted (an Echo-supported spell you press "
                                "beside it is not: a small undercount when you do)"}]
    uses = min(rate, 1 / cooldown) if cooldown > 0 else rate
    echoed = echoed_per_second(uses, chance, seals)
    return mods, [{
        "label": "Rhythm of Ice: increased Cast Speed", "value": f"{gain:.1f}%",
        "basis": f"the main spell Echoes {echoed:.2f} of its {uses:.2f} uses/s ({source}"
                 f"{f', one use per its {cooldown:.2f} s cooldown' if cooldown > 0 else ''}): "
                 f"{RECENTLY * echoed:.1f} Spells Echoed in the past 4 s x {per:g}%, up to {cap:g}%",
        "play": "Without Echoes on your main spell (or casting it under 1.25 times a second) it gives less.",
        "confidence": "theory"}]


# The hook (builder/search/assumptions.py Assumptions.models) ----------------------------------------------------------

FIELDS = ["TotalDPS", "CombinedDPS", "Speed", "Cooldown", "enemySkillTime", "EvadeChance", "EffectiveBlockChance"]


def main_quality(build, main: Skill | None) -> float:
    """The main skill gem's quality (0 for a skill an item or passive grants)."""
    if main is None or main.group is None:
        return 0.0
    for group in build.groups():
        if group.get("index") == main.group:
            return float(next((g.get("quality") or 0 for g in group["gems"] if g["name"] == main.name), 0))
    return 0.0


def build_models(build, inputs: Inputs, texts: list[str], skills: list[Skill], main: Skill | None,
                 supports: dict[int, list[str]]) -> tuple[list[dict], list[dict]]:
    """The lane's models for the current build: their lines (with derived inputs) and notes."""
    different = different_skills_model(inputs, texts)
    if not (inputs.has(NO_MERCY) or inputs.has(RHYTHM)):
        return different
    from builder.search.models import cast_by_totem

    stats = build.call("stats", fields=FIELDS)
    mods, notes = no_mercy_model(inputs, stats, texts)
    if inputs.has(RHYTHM):
        main_types = set((build.ctx.gems.get(main.name) or {}).get("skillTypes") or []) if main else set()
        main_supports = set(supports.get(main.group, [])) if main and main.group is not None else set()
        more, more_notes = rhythm_model(inputs, stats, texts, main, main_types, main_supports,
                                        bool(main and cast_by_totem(skills, main)), main_quality(build, main))
        mods, notes = mods + more, notes + more_notes
    return mods + different[0], notes + different[1]


# Emboldening Casts / Enhancing Attacks (AUDIT7 A7-2) ------------------------------------------------------------------
# "12% increased Attack Damage for each different Non-Instant Spell you've used in the past 8 seconds" and its mirror
# for Spell Damage per different Non-Instant Attack. PoB parses both only in part (ModCache leftover text), so never
# applies them. The count of different skills used per 8 s is MECH-B's (with Rapid Casting III, the Kurgal suffix and
# Casting Cascade: research cast-speed-per-different-spell.md): it is read here as the PoB multipliers
# Ledger<COUNT_NAME>, 0 until MECH-B sets them (an undercount, labelled).
DIFFERENT_LINES = {
    "EmboldeningCasts": (re.compile(r"^(\d+)% increased Attack Damage for each different Non-Instant Spell you've used "
                                    r"in the past 8 seconds$", re.I),
                         "1% increased Attack Damage", "DifferentNonInstantSpells8s", "different Non-Instant Spells"),
    "EnhancingAttacks": (re.compile(r"^(\d+)% increased Spell Damage for each different Non-Instant Attack you've used "
                                    r"in the past 8 seconds$", re.I),
                         "1% increased Spell Damage", "DifferentNonInstantAttacks8s", "different Non-Instant Attacks"),
}


def different_skills_model(inputs: Inputs, texts: list[str]) -> tuple[list[dict], list[dict]]:
    mods, notes = [], []
    for flag, (pattern, text, count, what) in DIFFERENT_LINES.items():
        if not inputs.has(flag):
            continue
        each = sum(float(m.group(1)) for t in texts if (m := pattern.match(t.strip())))
        if each <= 0:
            continue
        mods.append(line(text, each, when=[{"var": flag}], per=count))
        notes.append({
            "label": f"{'Emboldening Casts' if flag == 'EmboldeningCasts' else 'Enhancing Attacks'}: "
                     f"{each:g}% per {what[10:]} used in 8 s",
            "value": "0 until the count lands",
            "basis": f"reads the count of {what} the build uses in the past 8 s (PoB multiplier Ledger{count}), which "
                     "the skills model doesn't set yet: counted as 0 (an undercount when you use several)",
            "confidence": "theory"})
    return mods, notes


# Tree lines PoB parses but counts wrongly (COVB5) ---------------------------------------------------------------------
# A `pob_wrong` ledger entry whose match hits a passive tree line: the bridge (applyPassiveCorrections, `drop`) removes
# the modifiers PoB parsed from that line on the node and adds the entry's replacement lines instead (none for an empty
# `replace`: a model reads the line). Example: "N% chance to Gain Arcane Surge when you deal a Critical Hit", which PoB
# turns into Arcane Surge held whenever you've crit recently (chance and duration dropped); conditions.py
# arcane_surge_uptime rates it from the build's crits instead (MECHC3).

def wrong_tree_lines(nodes: list[dict]) -> list[dict]:
    """[{id, entry, lines, drop}] for the bridge: each tree line a `pob_wrong` entry covers, with its replacement."""
    from builder.ledger import entries

    wrongs = [e for e in entries() if e.kind == "pob_wrong" and e.pattern is not None]
    found = []
    for node in nodes:
        for stat in node.get("stats") or []:
            for text in stat.split("\n"):
                entry = next((e for e in wrongs if e.pattern.search(text)), None)
                if entry is not None:
                    found.append({"id": node["id"], "entry": entry.id, "lines": entry.translate(text) or [], "drop": text})
    return found
