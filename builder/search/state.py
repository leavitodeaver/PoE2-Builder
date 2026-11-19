"""A build loaded in one engine, as the search sees it: what-ifs, commits, snapshots, and the
current score. Everything else in the search talks to PoB through this class, which also counts
evaluations and streams progress.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from builder import clock
from builder.search.alloys import ALLOY_FIELD, alloy_key
from builder.search.context import Context, ItemOption
from builder.search.objective import CLEAR_FIELDS, COST_WARNINGS, EHP_FIELDS_PREFIXES, POINTS_OVER, Objective

EPSILON = 1e-6  # smallest score gain worth committing
PROGRESS_INTERVAL = 1.0
DELTA_CHUNK = 150  # what-ifs per engine call


class OutOfTime(Exception):
    """Raised once, at the first evaluation after `Build.stop_at`: a search step that doesn't watch
    the deadline itself (or watches it only between long trials) stops there. Search.run catches it
    and keeps the best build it holds, so a task ends on time with what it has."""


OVER_TRACES = 5


class Build:
    def __init__(self, engine: Any, ctx: Context, objective: Objective, report: Callable | None = None,
                 label: str = "") -> None:
        self.engine = engine
        self.ctx = ctx
        self.objective = objective
        self.report = report
        self.label = label
        self.stats: dict = {}
        self.score = -math.inf
        self.evals = 0
        # (COV34, the manager 15:45) Way of the Stonefist: None = not read since the tree last changed (call)
        self._stonefist: bool | None = None
        self.commits = 0
        self.started = clock.now()
        self.phase = "setup"
        self._last_report = 0.0
        # What the search put in each item slot, for prices and the result
        self.equipped: dict[str, ItemOption] = {}
        self.alloy_raws: dict[str, float] = {}  # (SEARCH29) the Alloy rares Gear made: alloys.alloy_key(raw) -> alloy_scale
        self.locked_slots: set[str] = set()
        self.reverts: dict[str, int] = {}  # phase -> commits undone because the score fell
        self.after_load: list = []  # called after every reload, before the score is refreshed
        # The scoring state that lives outside PoB's XML (the objective's weights and factors, the assumed mods, the
        # uptime weights): the search registers a (get, put) pair, and every snapshot keeps it, every restore puts it
        # back before the after_load hooks. Without it a restore gave back the XML under the current weights, not the
        # snapshot's score: finalize's supports guard ended 0.021-0.033 ln under its snapshot in POE2T730-732 (SEARCH2)
        self.snapshot_state: tuple[Callable[[], Any], Callable[[Any], None]] | None = None
        # Restore drift: a restore's score minus its snapshot's (count of restores, those off by more than EPSILON,
        # their sum and the worst), in the task events: a search must get back the score it kept
        self.restores = 0
        self.drifts: list[float] = []
        # Where a commit first took the tree over the level's points (SEARCH8, Defect C: which step leaks), the calling
        # functions innermost last, at most OVER_TRACES of them
        self.over_budget: list[str] = []
        # Stats PoB doesn't calculate, added to every result before scoring (flask recovery per
        # second, builder/search/flasks.py); what-if cases may override them with an "extra" entry
        self.extra_stats: dict = {}
        # Runes in character rune slots (Martial Artist's Runic Meridians; bridge.characterRunes): slot -> (rune, price)
        self.character_runes: dict[str, tuple[str, float]] = {}
        # The request's boss for models reading it from the stats ({"_boss": name}; inner.boss_stats), on every result
        self.boss_stats: dict = {}
        # Life / Mana recovery a second from the sources PoB gives per event or not at all (kills, Remnants, charms,
        # Warcry / Heavy Stun / Ignite / Shock events, charges and Glory spent: recovery.FIELDS), set by
        # Assumptions.life_mana_income on every refresh (MECHC12); merged into every result like extra_stats so the
        # sustain and recovery checks count them (SEARCH12; the user's rule, 12 Nov: every recovery source counts)
        self.recovery_sources: dict = {}
        # The what-if limit: re-scoring item moves a model reads (set by the search; see item_deltas)
        self.reassume: Callable[[], Any] | None = None  # re-applies the custom models to the current build
        self.rescored = 0  # item candidates re-scored with a real trial
        self._model_texts: tuple[int, list[str]] = (-1, [])
        # The hard stop (Search.run sets it a little past the task's deadline): every step used to
        # finish its own trials first, so a 600 s gate run took about 966 s (MECH15's runs, 8 Nov)
        self.stop_at: float | None = None

    def worse(self, before: float) -> bool:
        """After a commit a what-if said would gain: True (and counted) if it lost instead."""
        if self.score < before - EPSILON:
            self.reverts[self.phase] = self.reverts.get(self.phase, 0) + 1
            return True
        return False

    # Engine access -----------------------------------------------------------------------------

    def call(self, method: str, **kwargs: Any) -> Any:
        # Way of the Stonefist (COV34; the manager 15:45 allowed COV-A this hook): with the Martial Artist notable
        # allocated, equipped gloves are Fists of Stone with transformed mods (builder/search/stonefist.py), so the
        # Gloves item of an "equip" or "itemDeltas" call goes to PoB transformed; allocating or removing the notable
        # re-equips the gloves the other way. The item bought stays the plain one (self.equipped keeps it).
        if method in ("loadXml", "newBuild", "setClass"):
            self._stonefist = None
        if method in ("equip", "itemDeltas") and self._has_stonefist():
            from builder.search import stonefist

            kwargs = stonefist.item_kwargs(method, kwargs, self.ctx.data.mods)
        result = self.engine.call(method, **kwargs)
        if method == "allocate" and self._stonefist_toggled(kwargs):
            gloves = self.equipped.get("Gloves") if hasattr(self, "equipped") else None
            if gloves is not None and gloves.raw:
                result = self.call("equip", slot="Gloves", raw=gloves.raw, fields=kwargs.get("fields") or [])
        return result

    def _has_stonefist(self) -> bool:
        """Whether the tree has Way of the Stonefist allocated (read from PoB once per tree change)."""
        if self._stonefist is None:
            from builder.search import stonefist

            passives = self.engine.call("facts", fields=[]).get("passives") or []
            self._stonefist = any(p.get("name") == stonefist.NOTABLE and p.get("ascendancy") for p in passives)
        return self._stonefist

    def _stonefist_toggled(self, kwargs: dict) -> bool:
        """An allocation that adds or removes the notable (then read again)."""
        from builder.search import stonefist

        node = stonefist.node_id(self.ctx.data.tree)
        if node is None or node not in set(kwargs.get("add") or []) | set(kwargs.get("remove") or []):
            return False
        before = self._stonefist
        self._stonefist = None
        return before is None or before != self._has_stonefist()

    def _committed(self, result: dict) -> float:
        self.commits += 1
        before = (self.__dict__.get("stats") or {}).get(POINTS_OVER) or 0
        if ((result["stats"].get(POINTS_OVER) or 0) > 0 >= before and len(self.over_budget) < OVER_TRACES
                and not getattr(self.objective, "points_slack", 0)):  # (a screen allowed past the budget: not a leak)
            import traceback

            frames = traceback.extract_stack(limit=8)[:-1]
            self.over_budget.append(f"{self.phase}: +{result['stats'][POINTS_OVER]:g} via "
                                    + " < ".join(f"{f.name}:{f.lineno}" for f in reversed(frames)))
        self.stats = self.objective.prepare({**result["stats"], **self.added_stats()})
        self.score = self.objective.score(self.stats)
        self.tick()
        return self.score

    def refresh(self) -> float:
        return self._committed({"stats": self.call("stats", fields=self.objective.fields)})

    def set_objective(self, objective: Objective) -> None:
        self.objective = objective
        self.refresh()

    def tick(self, force: bool = False) -> None:
        """Stream progress about once a second; also where a cancelled task stops."""
        now = clock.now()
        if self.stop_at is not None and now > self.stop_at:
            self.stop_at = None  # once: what the search does next to wrap up runs to its end
            raise OutOfTime()
        if self.report is None:
            return
        if force or now - self._last_report >= PROGRESS_INTERVAL:
            self._last_report = now
            self.report(self.progress())
        else:
            self.report(None)

    def progress(self) -> dict:
        elapsed = clock.now() - self.started
        return {
            "skeleton": self.label, "phase": self.phase, "score": self.score,
            "dps": self.objective.dps(self.stats), "clear": self.objective.clear(self.stats)["score"],
            "value": self.objective.value(self.stats), "evals": self.evals,
            "evals_per_second": self.evals / elapsed if elapsed > 0 else 0.0,
            "deficits": [label for label, _, _ in self.objective.deficits(self.stats)],
        }

    # What-ifs ----------------------------------------------------------------------------------

    def _scored(self, results: list[dict], fill_from_base: bool = False,
                extras: list[dict | None] | None = None) -> list[tuple[float, dict]]:
        self.evals += len(results)
        scored = []
        for index, stats in enumerate(results):
            stats.update(self.added_stats())
            if extras and extras[index]:
                stats.update(extras[index])
            if fill_from_base:
                # A clear field the what-if lacks is gone (a support that stops chaining), not skipped
                for key, value in self.stats.items():
                    if key in COST_WARNINGS:
                        continue  # (a what-if has its own: the bridge's setCostShort, objective.cost_short; SEARCH9)
                    if (key not in stats and key not in CLEAR_FIELDS) or key.startswith(EHP_FIELDS_PREFIXES):
                        stats[key] = value
            self.objective.prepare(stats)
            scored.append((self.objective.score(stats), stats))
        self.tick()
        return scored

    def _deltas(self, method: str, cases: list[dict], fill_from_base: bool = False, **kwargs: Any) -> list[tuple[float, dict]]:
        """Run what-ifs in chunks, so progress (and cancellation) keeps flowing on slow builds."""
        scored: list[tuple[float, dict]] = []
        extras = [case.get("extra") for case in cases]
        cases = [{k: v for k, v in case.items() if k != "extra"} for case in cases]
        for start in range(0, len(cases), DELTA_CHUNK):
            result = self.call(method, cases=cases[start:start + DELTA_CHUNK], fields=self.objective.fields,
                               fullDPS=self.objective.full, **kwargs)
            scored += self._scored(result["results"], fill_from_base, extras[start:start + DELTA_CHUNK])
        return scored

    def node_deltas(self, cases: list[dict]) -> list[tuple[float, dict]]:
        return self._deltas("nodeDeltas", cases)

    def gem_deltas(self, cases: list[dict], skip_ehp: bool = True) -> list[tuple[float, dict]]:
        return self._deltas("gemDeltas", cases, fill_from_base=skip_ehp, skipEHP=skip_ehp)

    def item_deltas(self, cases: list[dict]) -> list[tuple[float, dict]]:
        # (SEARCH29) an item what-if replaces its slot's Alloy factor: the others' product x the candidate's own
        # (alloy_raws: the Alloy rares Gear made, by text; any other item 1.0). Nothing without an Alloy anywhere
        raws = {**(getattr(self, "alloy_raws", None) or {}),
                **{alloy_key(o.raw): o.alloy_scale for o in (getattr(self, "equipped", None) or {}).values()
                   if getattr(o, "alloy", "")}}
        if raws or self.alloy_product() != 1.0:
            cases = [{**case, "extra": {**(case.get("extra") or {}), ALLOY_FIELD: self.alloy_product(case.get("slot"))
                                        * raws.get(alloy_key(case.get("raw") or ""), 1.0)}} for case in cases]
        scored = self._deltas("itemDeltas", cases)
        if self.reassume is not None and cases:
            scored = self._rescore_model_items(cases, scored)
        return scored

    RESCORE_TOP = 3  # model-read candidates per what-if call that get a real trial

    def _build_texts(self) -> list[str]:
        if self._model_texts[0] != self.commits:
            self._model_texts = (self.commits, [l["text"] for l in self.call("sourceLines")["lines"]])
        return self._model_texts[1]

    def _rescore_model_items(self, cases: list[dict], scored: list[tuple[float, dict]]) -> list[tuple[float, dict]]:
        """A plain item what-if can't see a custom model that reads item lines (a resistance ring's count, Melting
        Maelstrom, model inputs on items). The best few candidates such a model reads, or that replace an item it reads,
        are re-scored with a real trial: equip, re-apply the models, score, then the old item and models back."""
        from builder.search.models import item_read_by_models

        texts = self._build_texts()
        current = self.call("equipped")["slots"]
        on_stats = getattr(self, "models_on_stats", False)
        touched = [i for i, case in enumerate(cases)
                   if on_stats or item_read_by_models(case.get("raw") or "", texts)
                   or item_read_by_models(current.get(case["slot"]) or "", texts)]
        if not touched:
            return scored
        best = sorted(touched, key=lambda i: -scored[i][0])[:self.RESCORE_TOP]
        before = (self.score, dict(self.stats), self.commits)
        for n, i in enumerate(best):
            case = cases[i]
            old = current.get(case["slot"])
            self.call("equip", slot=case["slot"], raw=case.get("raw"), fields=[])
            self.reassume()
            stats = self.objective.prepare({**self.call("stats", fields=self.objective.fields), **self.added_stats()})
            scored[i] = (self.objective.score(stats), stats)
            # (PERF23: between two trials the old item goes back without a recalculation of its own: the next trial's
            # equip is the very next engine call and recalculates the whole build; the last one recalculates as before)
            self.call("equip", slot=case["slot"], raw=old, fields=[], **({"recalc": False} if n + 1 < len(best) else {}))
            self.rescored += 1
        self.reassume()
        self.score, self.stats = before[0], before[1]
        return scored

    # Commits -----------------------------------------------------------------------------------

    def allocate(self, add: list[int] = (), remove: list[int] = (), attribute: int = 1) -> float:
        return self._committed(self.call("allocate", add=list(add), remove=list(remove), attribute=attribute,
                                         fields=self.objective.fields))

    def set_attributes(self, choices: dict[int, int]) -> float:
        return self._committed(self.call("setAttributes", choices={str(k): v for k, v in choices.items()},
                                         fields=self.objective.fields))

    def set_group(self, **kwargs: Any) -> dict:
        result = self.call("setGroup", fields=self.objective.fields, **kwargs)
        self._committed(result)
        return result["group"]

    def remove_group(self, index: int) -> float:
        return self._committed(self.call("removeGroup", index=index, fields=self.objective.fields))

    def equip(self, slot: str, option: ItemOption | None, active: bool | None = None) -> float:
        """`active` switches a flask or charm slot on or off (None leaves it as it is)."""
        extra = {} if active is None else {"active": active}
        result = self.call("equip", slot=slot, raw=option.raw if option else None, fields=self.objective.fields, **extra)
        if option is None:
            self.equipped.pop(slot, None)
        else:
            self.equipped[slot] = option
        return self._committed(result)

    def character_rune_deltas(self, cases: list[dict]) -> list[tuple[float, dict]]:
        """What-if runes in character rune slots: cases {slot, rune} (bridge.characterRuneDeltas)."""
        return self._deltas("characterRuneDeltas", cases)

    def set_character_rune(self, slot: str, rune: str, price: float | None) -> float:
        """Commit a rune ("None" empties it) into a character rune slot (bridge.setCharacterRune)."""
        result = self.call("setCharacterRune", slot=slot, rune=rune, fields=self.objective.fields)
        if rune == "None":
            self.character_runes.pop(slot, None)
        else:
            self.character_runes[slot] = (rune, price or 0.0)
        return self._committed(result)

    def set_config(self, values: dict[str, Any]) -> float:
        return self._committed(self.call("setConfig", vars=values, fields=self.objective.fields))

    def added_stats(self) -> dict:
        """The stats the search adds to every PoB result before scoring: the recovery sources (MECHC12's fields), the
        search's own flask recovery (extra_stats, which a what-if's "extra" entry may override after this) and the
        request's boss. With Heartbound Loop's damage on Minion Death (Assumptions.models: build.self_damage, per second,
        at build.minion_died's rate), that rate and damage, for the recharge shares (objective.own_events, SEARCH15)."""
        # With Fireflower's Fire damage per Ignite (build.ignite_self_damage = (per second, Ignites per second), MECHB25),
        # that too: each Ignite is one more event and its damage leaves the steady Fire loss (SEARCH21)
        own = {}
        damage = getattr(self, "self_damage", 0.0) or 0.0
        burn, ignites = getattr(self, "ignite_self_damage", None) or (0.0, 0.0)
        # (MECHC37: Scold's Bridle's Physical damage per paid use, build.cost_damage = (per second, uses per second))
        cost, cost_events = getattr(self, "cost_damage", None) or (0.0, 0.0)
        if damage > 0 or burn > 0 or cost > 0:
            died = getattr(self, "minion_died", None)
            events = ((float(died[0]) if damage > 0 and died else 0.0) + (max(float(ignites), 0.0) if burn > 0 else 0.0)
                      + (max(float(cost_events), 0.0) if cost > 0 else 0.0))
            own = {"SelfDamagePerSecond": damage + cost, "SelfFireDamagePerSecond": burn, "SelfDamageEvents": events}
        # (MECHC32, a MECH-C hook: Widow's Reign's window, objective.life_reserved_open)
        if (held := getattr(self, "life_reserved_seconds", 0.0) or 0.0) > 0:
            own["LifeReservedOnDamageSeconds"] = held
        # (MECHC32, a MECH-C hook: the Bleed your own item inflicts on you, Life only: objective.self_bleed_taken)
        if (bleed := getattr(self, "self_bleed", 0.0) or 0.0) > 0:
            own["SelfBleedPerSecond"] = bleed
        # (MECHC35, the same hook: the Poison your own item's "+N% chance to be Poisoned" brings, Life only)
        if (poison := getattr(self, "self_poison", 0.0) or 0.0) > 0:
            own["SelfPoisonPerSecond"] = poison
        # (MECHC37, the same hook: the boss's Ignites on you, objective.boss_ignite_taken; not PoB's degen)
        if (ignite := getattr(self, "boss_ignite", 0.0) or 0.0) > 0:
            own["BossIgnitePerSecond"] = ignite
        # (MECHC37: "N% increased Chance to be afflicted by Ailments when Hit", boss_hits.afflicted_scale)
        if (afflicted := getattr(self, "afflicted_inc", 0.0) or 0.0) != 0:
            own["SelfAilmentChanceInc"] = afflicted
        # (MECHC37: The Black Doubt's "Damage over Time bypasses your Energy Shield", objective.DOT_BYPASSES_ES)
        if getattr(self, "dot_bypasses_es", False):
            own["DotBypassesEnergyShield"] = 1
        # (MECHC35: Decree of Loyalty's "Damage over Time cannot bypass your Energy Shield", objective.DOT_HITS_ES)
        if getattr(self, "dot_hits_es", False):
            own["DotCannotBypassEnergyShield"] = 1
        # (SEARCH29: the equipped Alloy rares' mean-outcome DPS factor, alloys.py; none: no field)
        if (scale := alloy_product(getattr(self, "equipped", None))) != 1.0:
            own[ALLOY_FIELD] = scale
        return {**self.recovery_sources, **self.extra_stats, **self.boss_stats, **own}

    def alloy_product(self, skip: str | None = None) -> float:
        """The product of the equipped items' alloy_scale (every slot but `skip`)."""
        return alloy_product(getattr(self, "equipped", None), skip)

    # Snapshots ---------------------------------------------------------------------------------

    def snapshot(self) -> dict:
        snap = {"xml": self.call("exportXml")["xml"], "equipped": dict(self.equipped), "score": self.score,
                "stats": dict(self.stats), "extra_stats": dict(self.extra_stats),
                "recovery_sources": dict(self.recovery_sources), "character_runes": dict(self.character_runes)}
        if self.snapshot_state is not None:
            snap["search"] = self.snapshot_state[0]()
        return snap

    def restore(self, snap: dict) -> None:
        self.call("loadXml", xml=snap["xml"])
        self.equipped = dict(snap["equipped"])
        self.extra_stats = dict(snap.get("extra_stats") or {})
        self.recovery_sources = dict(snap.get("recovery_sources") or {})
        self.character_runes = dict(snap.get("character_runes") or {})  # (the XML carries the slots' runes)
        if self.snapshot_state is not None and "search" in snap:
            self.snapshot_state[1](snap["search"])
        for hook in self.after_load:
            hook()
        self.refresh()
        self.restores += 1
        drift = self.score - snap["score"]
        if abs(drift) > EPSILON and math.isfinite(drift):
            self.drifts.append(drift)

    # Facts -------------------------------------------------------------------------------------

    def groups(self) -> list[dict]:
        return self.call("groups")["groups"]

    def main_group(self) -> dict | None:
        result = self.call("groups")
        index = result["mainSocketGroup"]
        return result["groups"][index - 1] if 0 < index <= len(result["groups"]) else None

    def spent(self) -> float:
        """Divines spent on items, runes in character rune slots and priced gems."""
        total = sum(option.price or 0.0 for option in self.equipped.values())
        total += sum(price or 0.0 for _, price in self.character_runes.values())
        for group in self.groups():
            for gem in group["gems"]:
                priced = self.ctx.gem_price(gem["name"]) if gem["support"] else None
                if priced:
                    total += priced[0] or 0.0
        return total


def alloy_product(equipped: dict | None, skip: str | None = None) -> float:
    """(SEARCH29) The product of the equipped items' alloy_scale (alloys.py), every slot but `skip`."""
    scale = 1.0
    for slot, option in (equipped or {}).items():
        if slot != skip:
            scale *= getattr(option, "alloy_scale", 1.0) or 1.0
    return scale
