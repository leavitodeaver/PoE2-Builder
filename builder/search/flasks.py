"""Flasks and charms (PLAN.md §5.2.3): what a boss fight sustains.

PoB counts a flask or charm in an active slot as up all the time. In a boss fight charges come from
charge generation (passives, gear, the item's own "gains charges per second") and from the boss
preset's killable adds (MECHC9, A23-4: kill_charges; none against a lone boss, whose death ends the
fight; your own minions aren't enemies, so killing them gives none). The search therefore counts:
- an item's effect (PoB's buffs while it is active) whole only when generated charges keep it up at least
  ACTIVE_UPTIME (conditions.FULL_UPTIME, 99.5%) of the fight, and at its share of the fight below that (the gear side,
  gear.FLASK_ON .. FLASK_WHOLE; the user's 9 Nov rule: defence floors count it only at full uptime)
- a Life or Mana flask's recovery at the rate generated charges pay for, toward the skill-cost
  sustain floor (objective.SUSTAIN_POOLS), whether or not its effect counts

Charms that trigger on killing a Rare or Unique enemy never count: the boss's own death ends the fight, and its
adds are taken as Normal or Magic (UNVERIFIED: the boss presets don't give their rarity).
Charge numbers per item come from PoB (bridge `flaskData`, local modifiers included); charge
generation and scaling from the committed build (bridge `chargeGain`).
"""

from __future__ import annotations

from dataclasses import dataclass

from builder.search.boss_hits import land_share, type_taken

# The share of a boss fight a flask or charm effect must be up to count whole (and toward the defence floors): the
# conditions' FULL_UPTIME, 99.5% (MECHC7, AUDIT19 A19-1, WRONG, an OVERCOUNT: before, 0.9, so an effect down 10% of the
# fight counted whole, up to x1.11 on what it adds, and met a floor; the user's 9 Nov rule counts floors only at full
# uptime). Below it the gear side counts the effect at its share (SEARCH5/6: gear.FLASK_ON .. FLASK_WHOLE).
ACTIVE_UPTIME = 0.995  # = conditions.FULL_UPTIME (not imported: conditions -> objective -> flasks; a test keeps them equal)
FLASK_SLOTS = {"Flask 1": "Life", "Flask 2": "Mana"}  # PoB: Life flasks in Flask 1, Mana in Flask 2
CHARM_SLOTS = ["Charm 1", "Charm 2", "Charm 3"]
RECOVERY_FIELDS = {"Life": "FlaskLifeRecoveryRate", "Mana": "FlaskManaRecoveryRate"}  # pseudo-stats
KILL_TRIGGER = "used when you kill"  # charm triggers that need enemies to die (Golden Charm: a Rare or Unique)



def taken_per_hit(data: dict, boss: str | None = None) -> float:
    """Damage one average landed boss hit deals after mitigation (MECHC6): each type PoB takes at the type's full hit size
    (boss_hits.config_vars), weighted by its share of `boss`'s hits (the request's; MECHC13: before, always the default
    boss's mix, as the bridge data carries no boss); PoB's totalTakenHit (all types in one hit) when the bridge sends no
    split."""
    by_type = data.get("takenByType")
    if not by_type:
        return float(data.get("takenHit") or 0.0)
    stats = {f"{t}TakenHit": float(v or 0.0) for t, v in by_type.items()}
    if boss:
        stats["_boss"] = boss
    return type_taken(stats)

@dataclass(frozen=True)
class Charges:
    """Charge generation and scaling from the committed build (bridge `chargeGain`)."""
    flask: float = 0.0
    life_flask: float = 0.0
    mana_flask: float = 0.0
    flask_gain_inc: float = 0.0
    charm: float = 0.0
    charm_gain_inc: float = 0.0
    flask_duration_inc: float = 0.0
    charm_duration_inc: float = 0.0
    life_recovery_inc: float = 0.0
    mana_recovery_inc: float = 0.0
    charm_limit: int = 0
    life_flask_blocked: bool = False  # "Flasks do not recover Life" (Sanguine Tides, UniqueLifeFlaskNoRecovery1)
    # What charm triggers need from the fight (trigger_rate): the boss's hits that land per second, the damage types it
    # deals, the build's Perfect Timing skills and the main skill's uses per second when it is one
    boss_hits: float = 0.0
    boss_types: tuple[str, ...] = ()
    perfect_skills: int = 0
    perfect_main_rate: float = 0.0
    main_skill: str = ""  # (a Concoction main skill spends the Mana Flask's charges: THROW_CHARGES, thrown)
    # Reverie (MECHC3): "Cannot use Life Flasks"; "Non-Unique Life Flasks apply their Effects constantly"; "% less Life
    # Flask Recovery" (summed). PoB parses none of them (ModCache leftover text)
    life_flask_unusable: bool = False
    life_flask_constant: bool = False
    life_flask_less: float = 0.0
    # What fills Life and Mana in the fight (MECHC4, full_pool_cap): a Life (Mana) flask's effect ends when Life (Mana) is
    # full. PoB's damage taken per hit after mitigation (totalTakenHit), Mind over Matter's share, Energy Shield and its
    # regeneration, Life and Mana regeneration, the build's Life and Mana costs per second; Enduring Elixirs' "Life Flask
    # Effects are not removed when Unreserved Life is Filled"
    taken_hit: float = 0.0
    mom: float = 0.0
    energy_shield: float = 0.0
    es_regen: float = 0.0
    life_regen: float = 0.0
    mana_regen: float = 0.0
    life_cost: float = 0.0
    mana_cost: float = 0.0
    life_effect_kept: bool = False
    life_degen: float = 0.0  # (MECHC9) the build's own damage over time on Life per second (PoB's degen; Heartbound Loop)
    # (MECHC5, AUDIT14 A14-2, COVD7) flask charges on crit (chance %, PoB's FlaskChargeOnCritChance) and the main skill's crits
    # per second; the Mana Flask charges a use costs: increased / reduced and more / less (a Concoction throw's 5);
    # Crown of Thorns' "Lose N Life when you use a Skill" and the main skill's uses per second
    flask_on_crit: float = 0.0
    crit_rate: float = 0.0
    mana_used_inc: float = 0.0
    mana_used_more: float = 1.0
    life_loss_per_use: float = 0.0
    main_rate: float = 0.0
    flask_not_consume: float = 0.0
    # (MECHC7, #105) Wildwood's Gifts: Ancient Blooms per second and the charges a Bloom of each kind gives (bloom_charges)
    bloom_rate: float = 0.0
    bloom_life: float = 0.0
    bloom_mana: float = 0.0
    bloom_charm: float = 0.0
    bloom_effect: float = 0.0
    # (MECHC9, A23-4) the boss preset's adds killed per second (models.kill_rate's enemies; Charges.of reads them)
    add_kills: float = 0.0
    # (MECHC12) the build's Perfect Timing skills other than the main one, pressed per second, and how (Charges.of:
    # models.press_use_rate over their groups, their cooldown, duration or the search's chosen rate; 0 with none)
    perfect_press_rate: float = 0.0
    perfect_press_how: str = ""

    @classmethod
    def of(cls, build) -> Charges:
        """from_bridge for the build, with the request's boss preset's add kills (bosses.kill_stats, MECH-D's)."""
        from builder.search import bosses

        boss = getattr(getattr(getattr(build, "ctx", None), "request", None), "boss", None) or bosses.DEFAULT
        data = build.call("chargeGain")
        gain = cls.from_bridge(data, bosses.kill_stats(boss).get(bosses.KILLS_STAT) or 0.0, boss)
        groups = [int(i) for i in data.get("perfectGroups") or [] if i]
        if groups and gain.perfect_main_rate <= 0:
            from dataclasses import replace

            from builder.search.models import press_choice, press_choices, press_use_rate

            choices, total, parts = press_choices(build), 0.0, []
            for rate in build.call("useRates", indices=sorted(set(groups)))["rates"]:
                uses, how, _ = press_use_rate(rate, press_choice(rate, choices))
                total += uses
                parts.append(f"{rate.get('name')} {how}")
            gain = replace(gain, perfect_press_rate=total, perfect_press_how="; ".join(parts))
        return gain

    @classmethod
    def from_bridge(cls, data: dict, add_kills: float = 0.0, boss: str | None = None) -> Charges:
        every = float(data.get("enemySkillTime") or 0.0)
        # (red-flash hits land whatever the evade and block: builder/search/boss_hits.py, MECHC5). `boss`: the request's
        # (MECHC13, an over- or undercount for a named boss: before, the shares and the per-type weights in taken_per_hit
        # were the default boss's; boss_hits reads a stats dict's "_boss"); None, the default
        seen = {"EvadeChance": float(data.get("evade") or 0.0), "EffectiveBlockChance": float(data.get("block") or 0.0)}
        if boss:
            seen["_boss"] = boss
        land = land_share(seen) / every if every > 0 else 0.0
        types = tuple(k for k, v in (data.get("bossDamage") or {}).items() if float(v or 0) > 0)
        return cls(data.get("flask", 0), data.get("lifeFlask", 0), data.get("manaFlask", 0), data.get("flaskGainInc", 0),
                   data.get("charm", 0), data.get("charmGainInc", 0), data.get("flaskDurationInc", 0),
                   data.get("charmDurationInc", 0), data.get("lifeRecoveryInc", 0), data.get("manaRecoveryInc", 0),
                   int(data.get("charmLimit", 0)), bool(data.get("lifeFlaskBlocked", False)), land, types,
                   int(data.get("perfectSkills") or 0), float(data.get("perfectMainRate") or 0.0),
                   str(data.get("mainSkill") or ""), bool(data.get("lifeFlaskUnusable", False)),
                   bool(data.get("lifeFlaskConstant", False)), float(data.get("lifeFlaskLess") or 0.0),
                   taken_hit=taken_per_hit(data, boss), mom=float(data.get("mom") or 0.0),
                   energy_shield=float(data.get("energyShield") or 0.0), es_regen=float(data.get("esRegen") or 0.0),
                   life_regen=float(data.get("lifeRegen") or 0.0), mana_regen=float(data.get("manaRegen") or 0.0),
                   life_cost=float(data.get("lifeCost") or 0.0), mana_cost=float(data.get("manaCost") or 0.0),
                   life_effect_kept=bool(data.get("lifeEffectKept", False)),
                   life_degen=float(data.get("lifeDegen") or 0.0),
                   flask_on_crit=float(data.get("flaskOnCrit") or 0.0), crit_rate=float(data.get("critRate") or 0.0),
                   mana_used_inc=float(data.get("manaChargesUsedInc") or 0.0),
                   mana_used_more=float(data.get("manaChargesUsedMore") or 1.0),
                   life_loss_per_use=float(data.get("lifeLossPerUse") or 0.0), main_rate=float(data.get("mainRate") or 0.0),
                   flask_not_consume=float(data.get("flaskNotConsume") or 0.0),
                   add_kills=max(float(add_kills or 0.0), 0.0), **bloom_fields(data.get("blooms") or {}))


# Charm triggers other than a kill (MECHC2, FIX_QUEUE: each counted as firing whenever its charges allowed, whether or not
# the event can happen on a boss: an overcount). Every charm trigger in PoB's bases and ModCache (checked: "Used when"
# lines of Data/Bases/flask.lua and ModCache.lua; the uniques Beira's Anguish, Nascent Hope, Opportunity, Sanguis Heroum
# and The Black Cat use the same wordings) is one of these. Events per second on a lone boss:
# - "take <Type> damage from a Hit": the boss's hits that land, if its preset deals that type (PoB's <Type>EnemyDamage);
# - "become Cursed": none against a pinnacle boss (RESEARCH #33, boss-curses-player.md, MEDIUM-HIGH: no pinnacle boss
#   skill carries a curse stat; checked by the research there against every GrantedEffect and poe2db);
# - "release a skill with Perfect Timing": the main skill's uses when it is a Perfect Timing skill, else the build's other
#   Perfect Timing skills' presses (MECHC12; before, one per models.PRESS_EVERY s, a flat guess): each on its cooldown,
#   else once per its duration, else at the rate the search chose for it (models.press_use_rate), else 0;
# - the boss's ailments, Stun and Slow on you ("become Ignited / Frozen / Shocked / Poisoned / Stunned", "start Bleeding",
#   "affected by a Slow"): only when the boss deals the damage type behind it (Fire, Cold, Lightning, Chaos, Physical; any
#   for Stun and Slow); how often the game's boss inflicts them no data gives: one per BOSS_AILMENT_EVERY s, at most one
#   per landing hit (a flat rate where the game decides it: LOW, named in the report for the manager).
BOSS_AILMENT_EVERY = 10.0
TRIGGER_DAMAGE = {"ignited": "Fire", "frozen": "Cold", "shocked": "Lightning", "poisoned": "Chaos", "bleeding": "Physical",
                  "stunned": None, "slow": None}


def trigger_rate(trigger: str, gain: Charges) -> tuple[float, str] | None:
    """Events per second that can fire a charm's "Used when ..." trigger on a lone boss, and how; None when the text has no
    trigger this reads (its charges alone decide, as before)."""
    import re

    text = trigger.lower()
    if m := re.search(r"used when you take (fire|cold|lightning|chaos|physical) damage from a hit", text):
        kind = m.group(1).capitalize()
        if kind not in gain.boss_types:
            return 0.0, f"the boss deals no {kind} damage"
        return gain.boss_hits, f"the boss's hits that land, {gain.boss_hits:.2f}/s ({kind} among its damage)"
    if "used when you become cursed" in text:
        return 0.0, "pinnacle bosses don't curse you (research: no pinnacle boss skill carries a curse stat)"
    if "perfect timing" in text and "used when" in text:
        if gain.perfect_main_rate > 0:
            return gain.perfect_main_rate, f"the main skill's Perfect Timing releases, {gain.perfect_main_rate:.2f}/s"
        if gain.perfect_press_rate > 0:
            return gain.perfect_press_rate, (f"the build's Perfect Timing skills pressed {gain.perfect_press_rate:.2f}/s "
                                             f"({gain.perfect_press_how})")
        if gain.perfect_skills > 0:
            return 0.0, ("a Perfect Timing skill with no cooldown or duration and no press rate chosen by the search: no "
                         "source, 0" + (f" ({gain.perfect_press_how})" if gain.perfect_press_how else ""))
        return 0.0, "no Perfect Timing skill in the build"
    m = re.search(r"used when you (?:become (ignited|frozen|shocked|poisoned|stunned)|start (bleeding)|are affected by a (slow))",
                  text)
    if m is None:
        return None
    event = next(g for g in m.groups() if g)
    kind = TRIGGER_DAMAGE[event]
    if kind is not None and kind not in gain.boss_types:
        return 0.0, f"the boss deals no {kind} damage to make you {event}"
    rate = min(1 / BOSS_AILMENT_EVERY, gain.boss_hits)
    return rate, (f"you {'are ' if event != 'slow' else ''}{event} by the boss once per {BOSS_AILMENT_EVERY:g} s assumed "
                  "(LOW: how often a boss does it no data gives)")


# Wildwood's Gifts' Ancient Blooms (MECHC7; RESEARCH19 #105, wildwood-gifts-flask-charges.md). KeywordPopups AncientBlooms:
# "Ancient Blooms are Remnants that grant the following bonuses when collected: Vivid Blooms grant 5 Charges to all of your
# Charms; Primal Blooms grant 5 Charges to all of your Mana Flasks; Wild Blooms grant 5 Charges to all of your Life Flasks"
# (HIGH; the skill's ancient_gifts_*_charges_restored 5). One Bloom per breach_flame_spawn_rate_ms (1.4 s at level 1,
# 1.21 s at 20; PoB's level stats via bridge.chargeGain). Which kind each Bloom is: no source gives the split, a third
# each (LOW-MEDIUM, UNVERIFIED). Every Bloom collected (as assumptions.py's Remnants; a play note: walk to each; it clashes
# with standing still, labelled there). Whether "Ancient Blooms have N% increased effect" (remnant_effect_+%, 100 at level
# 20) scales the 5: UNVERIFIED, leaning yes: the headline counts 5 (the lower reading), bloom_note gives the higher.
# Charges a Bloom gives go to every flask of that kind; generation modifiers ("increased Flask Charges gained") taken to
# apply (MEDIUM: they are charges gained).
BLOOM_KIND_SHARE = 1 / 3


def bloom_fields(blooms: dict) -> dict:
    return {"bloom_rate": float(blooms.get("rate") or 0.0), "bloom_life": float(blooms.get("life") or 0.0),
            "bloom_mana": float(blooms.get("mana") or 0.0), "bloom_charm": float(blooms.get("charm") or 0.0),
            "bloom_effect": float(blooms.get("effect") or 0.0)}


def bloom_charges(gain: Charges, kind: str | None, effect: bool = False) -> float:
    """Charges per second Ancient Blooms give one flask (kind "Life" / "Mana") or charm ("Charm"); with `effect`, scaled by
    the Blooms' increased effect (the upper reading)."""
    each = {"Life": gain.bloom_life, "Mana": gain.bloom_mana, "Charm": gain.bloom_charm}.get(kind or "", 0.0)
    scale = 1 + gain.bloom_effect / 100 if effect else 1.0
    return gain.bloom_rate * BLOOM_KIND_SHARE * each * scale


def bloom_note(gain: Charges) -> str:
    if gain.bloom_rate <= 0:
        return ""
    return (f"Wildwood's Gifts: {gain.bloom_rate:.2f} Ancient Blooms/s, a third of them each kind (LOW-MEDIUM), "
            f"{gain.bloom_mana:g} charges each: {bloom_charges(gain, 'Mana'):.2f} charges/s to each Mana Flask, "
            f"{bloom_charges(gain, 'Life'):.2f} to each Life Flask, {bloom_charges(gain, 'Charm'):.2f} to each Charm, "
            f"every Bloom collected (walk to each); {bloom_charges(gain, 'Mana', True):.2f} each if their "
            f"{gain.bloom_effect:g}% increased effect scales the charges (UNVERIFIED, not counted)")


# Charges from kills (MECHC9, AUDIT23 A23-4; before, none: "nothing dies"). The game files' KeywordPopups (build
# 26476662): "Flask": "[Rarity|Normal] enemies grant Flask charges equal to half their [Power], [Rarity|Magic] enemies
# ... equal to their [Power], [Rarity|Rare] and [Rarity|Unique] ... twice their [Power]"; "Charm": "Charm charges can be
# regained by killing monsters, granting charges equal to half of the monster's [Power]". The boss preset's adds
# (Charges.add_kills) are taken as Normal with Power models.ADD_POWER (1: KeywordPopups Power, "An average monster has a
# Power of 1"; UNVERIFIED, LOW: their rarity and Power aren't in the presets): half a charge each, for flasks and charms,
# before "increased Flask / Charm Charges gained". Your own minions aren't enemies or monsters you kill for charges (the
# popups' "killing enemies"), so they give none.
KILL_CHARGES_NORMAL = 0.5  # charges per kill of a Normal enemy, per Power (flasks and charms alike)


def kill_charges(gain: Charges) -> float:
    """Flask (or charm) charges per second from the boss's adds the build kills."""
    from builder.search.models import ADD_POWER

    return gain.add_kills * KILL_CHARGES_NORMAL * ADD_POWER


def charges_per_second(info: dict, gain: Charges, pool: str | None) -> float:
    """Charges an item gains per second: generated, and from the boss's adds killed (kill_charges)."""
    kills = kill_charges(gain) if gain.add_kills > 0 else 0.0
    if info.get("kind") == "Charm":
        generated, inc = (gain.charm + info.get("gainBase", 0) + bloom_charges(gain, "Charm") + kills,
                          gain.charm_gain_inc)
    else:
        specific = gain.life_flask if pool == "Life" else gain.mana_flask if pool == "Mana" else 0.0
        # (MECHC5, A14-2: "Gain a Flask Charge when you deal a Critical Hit" gives each flask a charge, at the main skill's
        # crits per second; PoB's own reading, CalcPerform's FlaskChargeOnCritChance, counted only in its flask outputs)
        on_crit = min(gain.flask_on_crit, 100) / 100 * gain.crit_rate
        generated, inc = (gain.flask + specific + on_crit + info.get("gainBase", 0) + bloom_charges(gain, pool) + kills,
                          gain.flask_gain_inc)
    return max(0.0, generated * (1 + (inc + info.get("gainInc", 0)) / 100) * info.get("gainMod", 1))


def charges_per_use(info: dict, gain: Charges) -> float:
    """Charges a use costs on average: a Flask's after "N% chance for Flasks you use to not consume Charges" (MECHC5;
    flasks_%_chance_to_not_consume_charges; PoB parses it, FlaskChanceNotConsumeCharges, but only shows it in its calcs
    tab). Charms' own "chance for Charms you use to not consume Charges" isn't read yet (an undercount, labelled)."""
    used = info.get("chargesUsed") or 0
    if info.get("kind") == "Flask":
        used *= 1 - min(max(gain.flask_not_consume, 0.0), 100) / 100
    return used


def duration(info: dict, gain: Charges) -> float:
    inc = gain.charm_duration_inc if info.get("kind") == "Charm" else gain.flask_duration_inc
    return max(0.0, info.get("duration", 0) * (1 + inc / 100))


def uptime(info: dict, gain: Charges, pool: str | None = None, trigger: str = "") -> float:
    """Share of a boss fight the item's effect is up: generated charges pay for one use per
    `chargesUsed`, and each use lasts its duration."""
    if not info.get("kind") or KILL_TRIGGER in trigger.lower():
        return 0.0
    if info.get("constant") or reverie_constant(info, gain, pool):  # (Lavianga's Spirits; Reverie's non-unique Life Flasks)
        return 1.0
    if thrown(gain, pool) or (pool == "Life" and gain.life_flask_unusable):
        return 0.0
    used = charges_per_use(info, gain)
    rate = charges_per_second(info, gain, pool)
    if used <= 0:
        return 1.0
    uses = rate / used
    if info.get("kind") == "Charm" and (event := trigger_rate(trigger, gain)) is not None:
        uses = min(uses, event[0])  # (a charm is used by its trigger only: charges and the event both needed)
    share = min(1.0, uses * duration(info, gain))
    if (cap := full_pool_cap(info, gain, pool, trigger)) is not None:
        share = min(share, cap[0])
    return share


# A Life flask's effect ends when Life is full, a Mana flask's when Mana is full (MECHC4; RESEARCH12 18ae,
# life-flask-effect-kept.md, HIGH for the rule: the game has three stats that switch it off, which would mean nothing
# without the default: local_flask_effect_not_removed_at_full_life (Blood of the Warrior and one more unique),
# life_flask_effects_are_not_removed_at_full_life (Pathfinder's Enduring Elixirs), local_flask_effect_not_removed_at_full_mana
# (one Mana unique); poe2wiki's Life flask page: the flask "ends its effect if maximum Life is reached"). Before, a flask's
# effect counted for its whole duration whenever charges allowed: an overcount of every "during Life / Mana Flask effect"
# line (Uhtred's Rite's 20% more damage, spell damage and cast speed during a Mana Flask, the Brews).
KEPT_AT_FULL = {"Life": "effect is not removed when unreserved life is filled",
                "Mana": "effect is not removed when unreserved mana is filled"}


def full_pool_cap(info: dict, gain: Charges, pool: str | None, raw: str = "") -> tuple[float, str] | None:
    """The share of a fight a Life (Mana) flask's effect can last before Life (Mana) is full, and how; None when the rule
    doesn't apply (a charm, a flask whose effect is kept at full, Enduring Elixirs for Life flasks).

    Over a long fight everything that recovers Life equals the Life lost (Life can't rise above full), so the flask can
    recover at its rate f only for the share (L - g) / f of the time, where L is the Life lost per second (the boss
    preset's hits that land, after mitigation, less Mind over Matter's share and Energy Shield regeneration while the
    build has Energy Shield, plus the build's Life cost per second and its own damage over time on Life, PoB's degen with
    Heartbound Loop's damage on Minion Death in it: MECHC9) and g the Life regeneration. Mana: L = the build's Mana
    cost per second plus Mind over Matter's share of the hits, g = Mana regeneration. No loss: 0. Leech and recoup aren't
    subtracted (they fill Life too: the share is high there, a small overcount, labelled); hits are taken as a steady
    stream, not as lumps (MEDIUM)."""
    if pool not in ("Life", "Mana") or info.get("kind") != "Flask" or KEPT_AT_FULL[pool] in raw.lower():
        return None
    if pool == "Life" and gain.life_effect_kept:
        return None
    total = info.get("lifeTotal" if pool == "Life" else "manaTotal") or 0
    length = duration(info, gain)
    inc = gain.life_recovery_inc if pool == "Life" else gain.mana_recovery_inc
    less = (1 - min(gain.life_flask_less, 100) / 100) if pool == "Life" else 1.0
    rate = total * (1 + inc / 100) * less / length if length > 0 else 0.0
    hits = gain.taken_hit * gain.boss_hits
    if pool == "Life":
        lost_hits = max(0.0, hits * (1 - min(gain.mom, 100) / 100) - (gain.es_regen if gain.energy_shield > 0 else 0.0))
        # (MECHC5, COVD7: Crown of Thorns' Life lost per skill use, the main skill's uses; other skills' uses not counted)
        spend = gain.life_cost + gain.life_loss_per_use * gain.main_rate + max(gain.life_degen, 0.0)
        lost, regen = lost_hits + spend, max(0.0, gain.life_regen)
    else:
        lost_hits = hits * min(gain.mom, 100) / 100
        lost, regen, spend = lost_hits + gain.mana_cost, max(0.0, gain.mana_regen), gain.mana_cost
    how = (f"{pool} lost {lost:,.0f}/s (the boss's hits {lost_hits:,.0f}/s, {pool} costs"
           + (" and your own damage over time" if pool == "Life" and gain.life_degen > 0 else "") + f" {spend:,.0f}/s) against "
           f"regeneration {regen:,.0f}/s and the flask's {rate:,.0f}/s")
    if rate <= 0:
        return 0.0, f"the {pool} Flask recovers nothing, so its effect ends at once ({how})"
    cap = max(0.0, min(1.0, (lost - regen) / rate))
    return cap, f"its effect ends when {pool} is full: up at most {cap:.0%} of the fight ({how})"


def charm_charges_used(charms: list[tuple[dict, str]], gain: Charges) -> tuple[float, str]:
    """Charm charges spent per second against a boss (Cast on Charm Use's Energy): each charm is used as its generated
    charges allow, never again while its effect lasts (as uptime), and not at all when it waits for a kill. `charms` is
    [(flaskData info, item text)]."""
    total, how = 0.0, []
    for info, raw in charms:
        used = info.get("chargesUsed") or 0
        if info.get("kind") != "Charm" or used <= 0 or info.get("constant") or KILL_TRIGGER in raw.lower():
            continue
        uses = charges_per_second(info, gain, None) / used
        length = duration(info, gain)
        if length > 0:
            uses = min(uses, 1 / length)
        event = trigger_rate(raw, gain)
        if event is not None:
            uses = min(uses, event[0])
        total += uses * used
        name = next((line for line in raw.splitlines()[1:3] if line and ":" not in line), "a charm")
        how.append(f"{name}: {uses:.3f} uses/s x {used:g} charges" + (f" (its trigger: {event[1]})" if event else ""))
    return total, "; ".join(how)


def uses_per_second(info: dict, gain: Charges, raw: str, pool: str | None) -> tuple[float, str]:
    """(uses per second, how) of one flask or charm against a boss: as often as its generated charges pay for a use,
    never again while its effect lasts (as charm_charges_used; a player feeding a "when you use a Flask" line uses it
    the moment it can be), and 0 when it waits for a kill or can't be used (MECH48: its "on use" lines were one per 4 s)."""
    used = charges_per_use(info, gain)
    if not info.get("kind") or info.get("constant") or KILL_TRIGGER in raw.lower():
        return 0.0, "can't be used against a lone boss (waits for a kill, or can't be used)"
    if used <= 0:
        return 0.0, "no charges per use read"
    # (MECHC3, merged in MECHC4: Reverie's "Cannot use Life Flasks"; a Mana Flask whose charges a Concoction main skill
    # throws. Life and Mana flask effects queue (RESEARCH A5-6, MEDIUM-HIGH: "Life Flask Effects do not Queue" exists only
    # as Enduring Elixirs' exception), so their uses aren't capped by the duration; charms keep the cap, no rule found)
    if pool == "Life" and gain.life_flask_unusable:
        return 0.0, "Life Flasks can't be used (Cannot use Life Flasks)"
    if thrown(gain, pool):
        return 0.0, f"its charges are thrown by {gain.main_skill}"
    made = charges_per_second(info, gain, pool)
    uses, how = made / used, f"{made:.2f} charges/s / {used:g} per use"
    if pool not in ("Life", "Mana") and (length := duration(info, gain)) > 0 and uses > 1 / length:
        uses, how = 1 / length, how + f", at most once per its {length:.1f} s effect"
    return uses, how


def recovery_per_second(info: dict, gain: Charges, pool: str) -> float:
    """A Life or Mana flask's recovery per second at the rate its generated charges allow (one use
    at a time: never faster than one per duration)."""
    if pool == "Life" and gain.life_flask_blocked:
        return 0.0  # "Flasks do not recover Life" (game stats base_/life_flasks_do_not_recover_life; PoB ignores it)
    total = info.get("lifeTotal" if pool == "Life" else "manaTotal") or 0
    used = charges_per_use(info, gain)
    inc = gain.life_recovery_inc if pool == "Life" else gain.mana_recovery_inc
    less = (1 - min(gain.life_flask_less, 100) / 100) if pool == "Life" else 1.0
    if (info.get("constant") or reverie_constant(info, gain, pool)) and total > 0:
        # theory: its effect is its recovery, applied without a use, so the whole amount over every duration, end to end
        length = info.get("duration") or 0
        return total * (1 + inc / 100) * less / length if length > 0 else 0.0
    if total <= 0 or used <= 0 or thrown(gain, pool) or (pool == "Life" and gain.life_flask_unusable):
        return 0.0
    uses = charges_per_second(info, gain, pool) / used
    length = info.get("duration") or 0
    if length > 0:
        uses = min(uses, 1 / length)
    return total * (1 + inc / 100) * less * uses


# Skills that spend flask charges per use (MECHC2, FIX_QUEUE's usage-rate cap; the manager's ruling 12 Nov 08:30). Game files
# (build 26476662, GrantedEffectStatSets constant stats): flask_throw_base_charges_used 5 on each Concoction, described
# "Consumes {0} Charges from your Mana [Flask]" (skill_stat_descriptions). PoB's data has the stat on three of the five
# (Acidic, Bleeding, Explosive) and none reads it: PoB throws at will. (checked: every Stats.json id holding "flask" with
# throw / consume / charges; their users in GrantedEffectStatSets, PassiveSkills, Mods and ModType: the five Concoctions,
# and two gem enchantments, Explosive / Poisonous Concoction charges consumed +%, not read here: labelled)
THROW_CHARGES = {"Explosive Concoction": 5, "Fulminating Concoction": 5, "Shattering Concoction": 5,
                 "Acidic Concoction": 5, "Bleeding Concoction": 5}
THROW_POOL = "Mana"


def throw_cost(gain: Charges, per_use: float) -> tuple[float, str]:
    """A Concoction throw's Mana Flask charges after "increased / reduced Flask Charges used" (and "from Mana Flasks") and
    "less Flask Charges used" (MECHC5; RESEARCH16: 5 charges a throw, HIGH, 0.1.0e's 3 reverted in 0.2.0; that these lines
    apply to a throw: MEDIUM, a March 2025 guide), and "N% chance for Flasks you use to not consume Charges" (a throw takes
    the Mana Flask's charges without using the flask: whether it applies is UNVERIFIED; counted, as RESEARCH16 / the manager
    ruled for these lines, MEDIUM)."""
    cost = per_use * max(1 + gain.mana_used_inc / 100, 0.0) * max(gain.mana_used_more, 0.0)
    cost *= 1 - min(max(gain.flask_not_consume, 0.0), 100) / 100
    if abs(cost - per_use) < 1e-9:
        return per_use, ""
    return cost, f"{per_use:g} charges a throw changed to {cost:.2f} by the build's Flask Charges used lines (MEDIUM)"


def charge_use_rate(info: dict | None, gain: Charges, pool: str, per_use: float) -> tuple[float, str]:
    """Uses per second a skill that spends `per_use` charges of the `pool` flask can sustain on a boss, from the flask's own
    charge generation and the boss's adds killed (none against a lone boss); the stock a fight starts with isn't counted (an undercount for a short
    fight). (0, why) with no such flask or no generation. For the main skill's cap (Assumptions.flask_main) and usage.py."""
    if info is None or not info.get("kind"):
        return 0.0, f"no {pool} Flask equipped: nothing to throw"
    generated = charges_per_second(info, gain, pool)
    if generated <= 0:
        return 0.0, (f"the {pool} Flask gains no charges in a boss fight (no adds killed; no 'charges per second' line): "
                     f"none for the {per_use:g} a use takes")
    blooms = f" ({bloom_note(gain)})" if gain.bloom_rate > 0 else ""
    if gain.add_kills > 0:
        blooms += f" (with {kill_charges(gain):.3f}/s from the boss's adds killed, LOW)"
    return generated / per_use, f"the {pool} Flask gains {generated:.2f} charges/s{blooms}, {per_use:g} per use"


def reverie_constant(info: dict, gain: Charges, pool: str | None) -> bool:
    """Reverie's "Non-Unique Life Flasks apply their Effects constantly" (MECHC3), for a non-unique Life Flask."""
    return pool == "Life" and gain.life_flask_constant and info.get("kind") == "Flask" and not info.get("unique")


def thrown(gain: Charges, pool: str | None) -> bool:
    """Whether a Concoction main skill throws away the `pool` flask's charges (MECHC3): its throw rate is set from every charge
    the flask gains (Assumptions.flask_main), so none are left for the flask's own use: no recovery, no effect uptime.
    Charges left over when a throw per cast time spends fewer than the flask gains (above 5 per cast time) aren't counted
    (an undercount only there, labelled in flask_main's note)."""
    return pool == THROW_POOL and gain.main_skill in THROW_CHARGES


def slot_pool(slot: str) -> str | None:
    return FLASK_SLOTS.get(slot)


def is_flask_slot(slot: str) -> bool:
    return slot in FLASK_SLOTS or slot in CHARM_SLOTS


# Flags one of several slots sets (MECHC10, task 1; an OVERCOUNT in Search.weigh_uptimes): PoB sets UsingFlask whenever
# any Life or Mana flask slot is active (CalcPerform mergeFlasks, 1726) and UsingCharm whenever any charm slot is
# (mergeCharms, 1845). Lines "during any Flask Effect" / "while using a Flask" / "while you have an active Charm" ride on
# them (ModParser 1874-1878), from the tree (Stimulants, Imbibed Power, Crystal Elixir, Heavy Draught, Lucky Rabbit Foot,
# Efficient Alchemy), jewel affixes (JewelCharmDamageWhileUsing, its radius version), essences and uniques (checked:
# game files build 26476662, every Stats id holding during_flask_effect / while_using_flask / during_any_flask /
# while_using_charm / using_flask / using_charm, non-local, against Mods.json and PassiveSkills.json; second source:
# PoB's ModCache, 48 lines on these flags). weigh_uptimes switches one part-time slot off at a time and multiplies the
# shares: with two part-time flasks (or charms) the other slot keeps the flag up in every off case, so the lines on the
# flag count whole, never at the share both are down. Charms never set UsingFlask nor flasks UsingCharm (KeywordPopups
# "Charm": "Similar to Flasks", a separate kind; the game's stats are separate, using_flask / using_charm).
SHARED_FLAGS = {"UsingFlask": tuple(FLASK_SLOTS), "UsingCharm": tuple(CHARM_SLOTS)}


def shared_flag_groups(part_time: dict) -> dict[str, list[str]]:
    """flag -> the part-time slots (`part_time`: slot -> anything, Gear.part_time's keys) that set it, where two or
    more do (one alone is weighed right already: its off case drops the flag)."""
    out = {}
    for flag, slots in SHARED_FLAGS.items():
        group = [slot for slot in slots if slot in part_time]
        if len(group) >= 2:
            out[flag] = group
    return out


def shared_flag_factor(ups: list[float], ratios: list[float], none_ratio: float) -> tuple[float, str]:
    """The multiplier to put on weigh_uptimes' product for one group of part-time slots sharing a flag: `ups` each
    slot's share of the fight, `ratios` DPS with that slot alone off / with all on, `none_ratio` DPS with the whole
    group off / with all on (one more recalculation). The product gives the all-off case the product of the single
    ratios; the true all-off case also drops the shared flag. Exact for two slots taken as used independently:
    product + prod(1 - u) x (none_ratio - prod(ratios)), returned as a factor on the product (with three, cases with
    one slot still up keep the flag, as they should, and are multiplied out as the other effects are).
    Labelled: the slots are taken as independent (the flag up 1 - prod(1 - u) of the fight). A player who staggers
    them (one used as the other ends) holds the flag up to min(1, sum u) of the fight: the upper bound, named in the
    note; charges and missing Life decide when a flask can be used, so the lower figure counts (accuracy first)."""
    product, ratio_product, down = 1.0, 1.0, 1.0
    for up, ratio in zip(ups, ratios):
        product *= up + (1.0 - up) * ratio
        ratio_product *= ratio
        down *= 1.0 - up
    if product <= 0:
        return 1.0, ""
    exact = max(product + down * (none_ratio - ratio_product), 0.0)
    flag_up = 1.0 - down
    note = (f"the slots' shared flag up {flag_up:.0%} of the fight taken as independent uses "
            f"(staggered uses: up to {min(1.0, sum(ups)):.0%}), not 100% (DPS with every one of them off x"
            f"{none_ratio:.3f}, the singles' product x{ratio_product:.3f})")
    return exact / product, note
