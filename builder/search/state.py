"""A build loaded in one engine, as the search sees it: what-ifs, commits, snapshots, and the
current score. Everything else in the search talks to PoB through this class, which also counts
evaluations and streams progress.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from typing import Any

from builder import clock
from builder.search.alloys import ALLOY_FIELD, alloy_key
from builder.search.context import Context, ItemOption
from builder.search.objective import CLEAR_FIELDS, COST_WARNINGS, EHP_FIELDS_PREFIXES, POINTS_OVER, Objective

EPSILON = 1e-6  # smallest score gain worth committing
PROGRESS_INTERVAL = 1.0
DELTA_CHUNK = 150  # what-ifs per engine call

# (SEARCHB5, the manager's ruling 22 Nov: SEARCHB4's plan, on a loan of this path) A support our models value (GEM's
# gem_stat supports, the condition sources, the meta Energy supports, any support a model names) reads only its PoB part in
# a gem what-if (_scored adds the build's CURRENT model output to every case), often +0: on S2 Cadence and Blazing
# Critical read +0 and about +5% for real, Her Declaration +0 and +8.5%; the models also cut (Fist of Kalguur: what-if
# +0.90, real +0.025; POE2T6304 / 6295). With the switch on, gem_deltas gives the model-valued support cases (put in or
# taken out) and the best few by what-if a real trial: the group set, the conditions and models settled, scored, the group
# put back
GEM_RESCORE_SWITCH = "POE2_SEARCH_GEM_RESCORE"  # "1" on; OFF by default until its gate pair
RESCORE_GEMS = 4  # LOW, a search-effort setting: the best what-if support cases per call that get a real trial
MODEL_GEMS = 8  # LOW, the same: model-valued support cases per call that get one (the best by what-if first)
GEM_RESCORE_SECONDS = 15.0  # LOW, the same: the real trials' time per call (a trial took 0.9-3.9 s, POE2T6304)
GEM_RESCORE_SHARE = 0.1  # LOW, the same: the trials' share of a task's time so far (gate POE2T6312 without it: x0.77)


# (SEARCHB6; the manager's 08:30 23 Nov, after MECHB58's 65c921c0) Supports whose whole value lives in OUR models, which a
# PoB what-if reads as a loss: PoB adds no hit for a spell's Echo (it maps support_spell_echo_number_of_echo_cascades to
# RepeatCount and only averages modifiers, CalcOffence 3008 / 1229-1231), so Spell Echo reads as its 30% less cast speed
# and Zarokh's Refrain as no change, while models.spell_echo_model counts the Echoes as hits (Comet: PoB 188 -> 375).
# Without a real trial the support rounds never pick them and weakest_slot takes them out. The wide rescore
# (GEM_RESCORE_SWITCH) would, but it costs a timed run x0.41 (POE2T6316). This narrow one trials only these supports'
# cases, only on a group whose active skill has the types the model needs, and only within GEM_RESCORE_SHARE.
# Granted effect -> the active skill types the model needs (all of them). The supports that grant the stat (checked:
# support_spell_echo_number_of_echo_cascades, Stats row 17810, in GrantedEffectStatSets' constant and per-level stats,
# mapped through GrantedEffects' StatSet / AdditionalStatSets, game build 26650308: exactly SupportSpellEchoPlayer and
# SupportZarokhsRefrainPlayer; tests/test_searchb6_hooked.py rescans the tables; poe2db's support list, 23 Nov: only Spell
# Echo "Echo 1 times" and Zarokh's Refrain "Echo 3 times" make spells Echo, Echoing Cry being the warcry kind; its list
# page read truncated at the very end). Add a support here when a model gives
# it value PoB's what-if can't see.
MODEL_HOOKED_SUPPORTS: dict[str, tuple[str, ...]] = {
    "SupportSpellEchoPlayer": ("Spell", "Cascadable"),
    "SupportZarokhsRefrainPlayer": ("Spell", "Cascadable"),
}
HOOKED_SWITCH = "POE2_SEARCH_HOOKED_RESCORE"  # "0" off; ON by default (it does nothing for a build no hooked support fits)
HOOKED_GEMS = 3  # LOW, a search-effort setting: hooked support cases per call that get a real trial


# (SEARCH42; the adaptive search's Phase 1 piece 1.1, THE USER's approval 27 Nov ~02:55: OUR models judge, never the
# quick check alone) The support screen's what-if holds our models' inputs frozen (_scored adds the build's CURRENT model
# output to every case): SPEED5 / SPEED7's Phase 0 (research/adaptive-search-data.md 0.6) read its ranking against our full
# score at -0.56 on 2b75e seed 2 (its top 12 take Spell Echo out: all lose x0.67-0.88 on ours), -0.53 on Ice Shot
# (example 16), 0.61 on Spark (example 14), and all 699 cases <= 0 on 2b75b where ours gives Bleed II x1.082. With
# POE2_SEARCH_JUDGE=1 a gem what-if also returns our models' inputs (models.MODEL_FIELDS), and a support case whose change
# MOVES one (judge_inputs_moved: a rate, chance, cost, duration, count or Life input past JUDGE_TOL; a damage-size input
# only when it turns on or off) gets a real trial on our full score (the group set, our models settled, scored, put back:
# _rescore_model_gems), with the JUDGE_TOP best by what-if. The cases the time guard cuts are counted (judge_log), never
# judged by the screen alone in silence. OFF until its gate set (3 seeds a build, 2b75b-e + examples 14 / 16)
JUDGE_SWITCH = "POE2_SEARCH_JUDGE"
JUDGE_TOP = 4  # LOW, a search-effort setting: the best what-if cases tried whatever the inputs say
JUDGE_TOL = 0.02  # LOW: a model input's relative move that flags a case (a rate / chance / cost input)
JUDGE_SECONDS = 60.0  # LOW, a search-effort setting: the trials' time per call
JUDGE_SHARE = 0.3  # LOW, the same: the trials' share of a task's time so far
JUDGE_SIZE_WORDS = ("DPS", "HitAverage", "Damage", "AverageHit")
# (JUDGE, the tree's keep / revert: SPEED5's 0.6, a node our models value at 0 stays allocated: 2b75e's Marked for
# Sickness, 2b75b's Dependable Ward) In these phases Build.worse first settles our models (judge_settle: Search.resolve)
# and keeps a commit only for a REAL gain on the settled score; a revert's removal (Build.allocate) is settled again, so
# the next node is judged against a settled score too. The tree step's code (tree.py, SEARCH-B's) is unchanged
JUDGE_KEEP_PHASES = ("tree", "tree rebuild")  # (inputs that scale with any damage support: on / off)


def judge_enabled() -> bool:
    """The switch, read at call time (a gate arm sets it per run)."""
    return os.environ.get(JUDGE_SWITCH, "0") == "1"


def judge_fields() -> list[str]:
    """Our models' inputs a judged what-if returns (models.MODEL_FIELDS, less FullDPS: the fight's length only)."""
    from builder.search.models import MODEL_FIELDS

    return [f for f in MODEL_FIELDS if f != "FullDPS"]


def judge_inputs_moved(base: dict, stats: dict, fields: list[str]) -> list[str]:
    """The model inputs a what-if's stats move from the build's: turned on or off, or (not a damage-size input) moved
    past JUDGE_TOL of the larger. Non-numbers are compared as they are."""
    moved = []
    for field in fields:
        a, b = base.get(field), stats.get(field)
        if a == b:
            continue
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a or 0, b or 0)):
            moved.append(field)
            continue
        a, b = float(a or 0), float(b or 0)
        if (a == 0) != (b == 0):
            moved.append(field)
        elif not any(w in field for w in JUDGE_SIZE_WORDS) and abs(b - a) > JUDGE_TOL * max(abs(a), abs(b)):
            moved.append(field)
    return moved


def hooked_rescore_enabled() -> bool:
    return os.environ.get(HOOKED_SWITCH, "1") == "1"


# (SEARCH40; SEARCH39's item 2, the manager's 24 Nov 12:53 ruling: a close-calls real trial, OFF until a gate) An item
# what-if keeps the assumption mods of the last resolve: 10 of Tempest Bell's 29 moved on the real swap (the boss's Shock /
# Chill / Light Stun, the DPS factors, Glory shares; POE2T7490: what-if -0.94 ln, -0.41 for Ice Shot vs the real swap).
# _rescore_model_items re-scores only candidates whose TEXT a model reads (or a jewel holder). With the switch on, the best
# CLOSE_CALLS_TOP of the rest whose what-if lies within CLOSE_CALLS_LN of the current score (above or below: where the stale
# models can flip a decision) get the same real trial (equip, re-apply the models, score, put back). Time-guarded like
# the gem rescore (CLOSE_CALLS_SECONDS a call, CLOSE_CALLS_SHARE of a task's time so far, none after the hard stop)
CLOSE_CALLS_SWITCH = "POE2_SEARCH_CLOSE_CALLS"  # "1" on; OFF by default until its gate set
CLOSE_CALLS_TOP = 2  # LOW, a search-effort setting (the manager: "the best 1-2 candidates")
CLOSE_CALLS_LN = 1.0  # LOW, the same: the observed what-if gaps were 0.41-0.94 ln (POE2T7490)
CLOSE_CALLS_SECONDS = 15.0  # LOW, the same as GEM_RESCORE_SECONDS
CLOSE_CALLS_SHARE = 0.1  # LOW, the same as GEM_RESCORE_SHARE


def close_calls_enabled() -> bool:
    """The switch, read at call time (a gate arm sets it per run)."""
    return os.environ.get(CLOSE_CALLS_SWITCH, "0") == "1"


def gem_rescore_enabled() -> bool:
    """The switch, read at call time (a gate arm sets it per run)."""
    return os.environ.get(GEM_RESCORE_SWITCH, "0") == "1"


_MODEL_SOURCE: list[str] = []  # the models, conditions and assumptions modules' source, read once a process


def _model_named_supports(ctx: Context) -> set[str]:
    """The supports model_valued_supports names without the live condition check."""
    import inspect

    from builder.ledger import gem_stat_entries
    from builder.search import assumptions as assumptions_module
    from builder.search import conditions as conditions_module
    from builder.search import models

    named: set[str] = set(models.META_SUPPORTS)
    for entry in gem_stat_entries():
        if entry.raw.get("support"):
            skills = entry.raw.get("skill") or []
            named.update([skills] if isinstance(skills, str) else skills)
    if not _MODEL_SOURCE:
        _MODEL_SOURCE.append("".join(inspect.getsource(m) for m in (models, conditions_module, assumptions_module)))
    text = _MODEL_SOURCE[0]
    for name, gem in ctx.gems.items():
        effect = gem.get("grantedEffectId")
        if gem.get("support") and (f'"{name}"' in text or (effect and f'"{effect}"' in text)):
            named.add(name)
    named.add("")  # (never empty once read)
    return named


def model_valued_supports(ctx: Context, conditions: Any = None) -> Callable[[str], bool]:
    """Whether our models may value a support gem past its PoB part: GEM's gem_stat support entries (the ledger), the meta
    Energy supports (models.META_SUPPORTS), any support whose name or granted effect id the models, conditions or
    assumptions code names (a support-name constant such as CADENCE_SUPPORTS, conditions.HER_DECLARATION; also a name in
    a comment there: a wider net costs only a trial), and, live, a support whose description could turn on a condition
    that is off now or raise a charge count (conditions.would_enable, as Gems.discover). LABELLED: a search-effort net,
    not a list of every model; the RESCORE_GEMS best what-ifs are tried whatever it says."""
    named: set[str] = set()

    def valued(name: str) -> bool:
        if not named:  # (once, at the first rescore: the scan reads the three modules' source)
            named.update(_model_named_supports(ctx))
        if name in named:
            return True
        if conditions is None:
            return False
        return bool(conditions.would_enable((ctx.gems.get(name) or {}).get("description") or ""))

    return valued


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
        self.close_rescored = 0  # (SEARCH40) of those, the close calls (CLOSE_CALLS_SWITCH)
        self.close_seconds = 0.0  # (SEARCH40) the close calls' time so far (CLOSE_CALLS_SHARE)
        # (SEARCHB5) The support what-ifs' real trials (gem_deltas, GEM_RESCORE_SWITCH): `model_gem(name)` says whether a
        # model values the gem, `gem_settle()` settles the conditions and models after a change (Search.settle); both set
        # by the search. Counts and the last trials (gem, what-if, real) for reading a run
        self.model_gem: Callable[[str], bool] | None = None
        self.gem_settle: Callable[[], Any] | None = None
        self.gem_rescored = 0
        self.gem_rescore_restores = 0
        self.gem_rescore_log: list[dict] = []
        self.gem_rescore_seconds = 0.0  # the trials' time so far (GEM_RESCORE_SHARE)
        self.judge_seconds = 0.0  # (JUDGE) the judge's trials' time so far (JUDGE_SHARE)
        self.judge_log: list[dict] = []  # (JUDGE) one row a support screen: cases, flagged, tried, cut, seconds
        self.judge_settle: Callable[[], Any] | None = None  # (JUDGE) the search's settle (Search.resolve)
        self.judge_keeps = {"judged": 0, "reverted": 0, "seconds": 0.0}  # (JUDGE) the tree's keep / revert
        self._gem_trial = False
        self.stopped = False  # the hard stop has fired (tick): no rescore trials in the wrap-up after it
        self._model_texts: tuple[int, list[str]] = (-1, [])
        # The hard stop (Search.run sets it a little past the task's deadline): every step used to
        # finish its own trials first, so a 600 s gate run took about 966 s (MECH15's runs, 8 Nov)
        self.stop_at: float | None = None

    def judging_keeps(self) -> bool:
        """(JUDGE) Whether this phase's keep / revert is judged on the settled score (JUDGE_KEEP_PHASES)."""
        return (judge_enabled() and self.phase in JUDGE_KEEP_PHASES and self.judge_settle is not None
                and not getattr(self, "_gem_trial", False))

    def _judge_settle(self) -> None:
        started = clock.now()
        try:
            self.judge_settle()
        finally:
            self.judge_keeps["seconds"] += clock.now() - started

    def worse(self, before: float) -> bool:
        """After a commit a what-if said would gain: True (and counted) if it lost instead. (JUDGE, in its phases: our
        models settled first, and True unless the settled score really gained.)"""
        if self.judging_keeps():
            self._judge_settle()
            self.judge_keeps["judged"] += 1
            if self.score <= before + EPSILON:
                self.judge_keeps["reverted"] += 1
                self.reverts[self.phase] = self.reverts.get(self.phase, 0) + 1
                return True
            return False
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
            self.stopped = True
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
            fields = self.objective.fields
            if method == "gemDeltas" and judge_enabled() and not self._gem_trial:  # (JUDGE: our models' inputs too)
                fields = sorted(set(fields) | set(judge_fields()))
            result = self.call(method, cases=cases[start:start + DELTA_CHUNK], fields=fields,
                               fullDPS=self.objective.full, **kwargs)
            scored += self._scored(result["results"], fill_from_base, extras[start:start + DELTA_CHUNK])
        return scored

    def node_deltas(self, cases: list[dict]) -> list[tuple[float, dict]]:
        return self._deltas("nodeDeltas", cases)

    def gem_deltas(self, cases: list[dict], skip_ehp: bool = True) -> list[tuple[float, dict]]:
        scored = self._deltas("gemDeltas", cases, fill_from_base=skip_ehp, skipEHP=skip_ehp)
        if cases and self.model_gem is not None and self.gem_settle is not None and not self._gem_trial:
            if judge_enabled():
                scored = self._rescore_model_gems(cases, scored, judge=True)
            elif gem_rescore_enabled():
                scored = self._rescore_model_gems(cases, scored)
            elif hooked_rescore_enabled():
                scored = self._rescore_model_gems(cases, scored, hooked=True)
        return scored

    def _hooked_picks(self, cases: list[dict], groups: dict[int, dict], support: dict[int, str],
                      scored: list[tuple[float, dict]]) -> list[int]:
        """The cases a MODEL_HOOKED_SUPPORTS support is put in or taken out of a group whose enabled active skill has the
        types its model needs: for each (support, group), its best put-in case by what-if and its take-out case; at most
        HOOKED_GEMS, put-ins first."""
        def fits(name: str, group: dict) -> bool:
            need = MODEL_HOOKED_SUPPORTS.get((self.ctx.gems.get(name) or {}).get("grantedEffectId") or "")
            if not need:
                return False
            actives = [self.ctx.gems.get(g["name"]) or {} for g in group["gems"] if g["enabled"] and not g["support"]]
            return any(set(need) <= set(a.get("skillTypes") or ()) for a in actives)

        adds: dict[tuple[str, int], int] = {}
        removes: list[int] = []
        for i, name in support.items():
            group = groups[cases[i]["group"]]
            if not fits(name, group):
                continue
            if cases[i].get("remove"):
                removes.append(i)
            else:
                key = (name, group["index"])
                if key not in adds or scored[i][0] > scored[adds[key]][0]:
                    adds[key] = i
        return [*adds.values(), *removes][:HOOKED_GEMS]

    def _support_cases(self, cases: list[dict], groups: dict[int, dict]) -> dict[int, str]:
        """{case index: the support it puts in or takes out} for the cases that change one support slot of a group."""
        found = {}
        for i, case in enumerate(cases):
            group = groups.get(case.get("group")) if "newGroup" not in case else None
            slot = case.get("slot")
            if group is None or not isinstance(slot, int) or not 1 <= slot <= len(group["gems"]) + 1:
                continue
            if case.get("remove"):
                if slot <= len(group["gems"]) and group["gems"][slot - 1]["support"]:
                    found[i] = group["gems"][slot - 1]["name"]
            elif case.get("gem") and (self.ctx.gems.get(case["gem"]) or {}).get("support"):
                found[i] = case["gem"]
        return found

    @staticmethod
    def _group_gems(group: dict) -> list[dict]:
        """A group's gems as setGroup takes them, exactly as they are (level, quality, on or off, the granted effect)."""
        return [{"name": g["name"], "level": g["level"], "quality": g["quality"], "enabled": g["enabled"],
                 **({"skillId": g["skillId"]} if g.get("skillId") else {})} for g in group["gems"]]

    @classmethod
    def _trial_gems(cls, group: dict, case: dict) -> list[dict]:
        """The group's gems with the case's change, as gemDeltas makes it (a gem put in an existing slot keeps that slot's
        quality, a new slot's has none, a removed one is disabled; the case's level, else the gem's own default)."""
        gems = cls._group_gems(group)
        slot = case["slot"]
        level = {"level": case["level"]} if case.get("level") else {}
        if case.get("remove"):
            gems[slot - 1]["enabled"] = False
        elif slot == len(gems) + 1:
            gems.append({"name": case["gem"], **level})
        else:
            gems[slot - 1] = {"name": case["gem"], "quality": gems[slot - 1]["quality"], **level}
        return gems

    def _rescore_model_gems(self, cases: list[dict], scored: list[tuple[float, dict]],
                            hooked: bool = False, judge: bool = False) -> list[tuple[float, dict]]:
        """The RESCORE_GEMS best support cases by what-if, and the model-valued ones (at most MODEL_GEMS more, the best by
        what-if first), get a real trial: the group set with the change, the conditions and models settled (gem_settle),
        scored, and the group put back as it was. `remove` cases too (weakest_slot read a model-valued support as free to
        take out). At most GEM_RESCORE_SECONDS of trials a call, none in the last GEM_RESCORE_SECONDS before the hard
        stop; a trial PoB refuses keeps its what-if. The build ends where it began: settled again after the last put-back,
        and the snapshot restored if the score moved. `hooked` (SEARCHB6): only the MODEL_HOOKED_SUPPORTS cases
        (_hooked_picks), with the same time guards."""
        groups = {g["index"]: g for g in self.groups()}
        support = self._support_cases(cases, groups)
        if hooked:
            support = {i: support[i] for i in self._hooked_picks(cases, groups, support, scored)}
        if not support:
            return scored
        # (the time guards, gate POE2T6312: with only the per-call cap the trials took 2b75d's probe tasks' tree time and
        # ran 35-100 s past each task's end, 182,059 vs 235,907 off) none once the hard stop has fired (its wrap-up runs
        # with no stop), and all of a task's trials within GEM_RESCORE_SHARE of its time so far
        started = clock.now()
        by_whatif = sorted(support, key=lambda i: -scored[i][0])
        judged: dict = {}
        if judge:  # (JUDGE: the JUDGE_TOP best by what-if, then every case moving a model input, best what-if first)
            fields = judge_fields()
            base = self.call("stats", fields=fields)
            moves = {i: judge_inputs_moved(base, scored[i][1], fields) for i in by_whatif}
            flagged = {i: f for i, f in moves.items() if f}
            picks = by_whatif[:JUDGE_TOP] + [i for i in by_whatif[JUDGE_TOP:] if i in flagged]
            model = [i for i in picks if i in flagged]
            moved: dict[str, int] = {}
            for found in flagged.values():
                for field in found:
                    moved[field] = moved.get(field, 0) + 1
            judged = {"phase": self.phase, "cases": len(support), "flagged": len(flagged), "picks": len(picks),
                      "tried": 0, "seconds": 0.0, "moved": dict(sorted(moved.items(), key=lambda kv: -kv[1])[:8])}
            left = JUDGE_SHARE * (started - self.started) - self.judge_seconds
            limit = min(JUDGE_SECONDS, left)
            if self.stopped or left <= 0:
                judged["cut"] = len(picks)
                self.judge_log.append(judged)
                return scored
        else:
            left = GEM_RESCORE_SHARE * (started - self.started) - self.gem_rescore_seconds
            if self.stopped or left <= 0:
                return scored
            limit = min(GEM_RESCORE_SECONDS, left)
            picks = by_whatif[:RESCORE_GEMS] if not hooked else list(support)
            model = [] if hooked else [i for i in by_whatif if i not in picks and self.model_gem(support[i])][:MODEL_GEMS]
            picks += model
        before = self.score
        snap = self.snapshot()
        self._gem_trial = True
        try:
            for i in picks:
                now = clock.now()
                if now - started > limit or (self.stop_at is not None
                                                           and now > self.stop_at - GEM_RESCORE_SECONDS):
                    break
                case, group = cases[i], groups[cases[i]["group"]]
                whatif = scored[i][0]
                row: dict = {"gem": support[i], "remove": bool(case.get("remove")), "model": i in model,
                             "whatif": round(whatif - before, 4)}
                if judge:  # (JUDGE: the inputs this case moved, for calibrating judge_inputs_moved on real trials)
                    row["moved"] = flagged.get(i, [])
                try:
                    self.call("setGroup", index=group["index"], gems=self._trial_gems(group, case), fields=[])
                    self.gem_settle()
                    stats = self.objective.prepare({**self.call("stats", fields=self.objective.fields),
                                                    **self.added_stats()})
                    scored[i] = (self.objective.score(stats), stats)
                    self.gem_rescored += 1
                    if judge:
                        judged["tried"] += 1
                    row["real"] = round(scored[i][0] - before, 4)
                except OutOfTime:
                    raise
                except Exception as error:  # (a trial PoB refuses: its what-if stays)
                    row["error"] = repr(error)[:160]
                finally:
                    self.call("setGroup", index=group["index"], gems=self._group_gems(group), fields=[])
                self.gem_rescore_log.append(row)
                del self.gem_rescore_log[:-200]
        finally:
            self._gem_trial = False
            self.gem_settle()
            self.refresh()
            if abs(self.score - before) > EPSILON:
                self.gem_rescore_restores += 1
                self.restore(snap)
            if judge:
                self.judge_seconds += clock.now() - started
                judged["seconds"] = round(clock.now() - started, 2)
                judged["cut"] = judged["picks"] - judged["tried"]
                self.judge_log.append(judged)
                del self.judge_log[:-500]
            else:
                self.gem_rescore_seconds += clock.now() - started
        return scored

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
            plain = list(scored)
            scored = self._rescore_model_items(cases, scored)
            if close_calls_enabled():  # (SEARCH40) the cases it didn't re-score: a new tuple is a re-scored case
                scored = self._rescore_close_calls(cases, scored, {i for i, s in enumerate(scored) if s is not plain[i]})
        return scored

    def _rescore_close_calls(self, cases: list[dict], scored: list[tuple[float, dict]],
                             done: set[int]) -> list[tuple[float, dict]]:
        """(SEARCH40; CLOSE_CALLS_SWITCH's comment) The best CLOSE_CALLS_TOP cases not yet re-scored whose what-if is
        within CLOSE_CALLS_LN of the current score get a real trial: equip, re-apply the models, score, put back."""
        # (a case carrying an Alloy factor isn't tried: a plain equip doesn't carry the factor, so its real score would
        # be the wrong one)
        close = [i for i in range(len(cases)) if i not in done and not cases[i].get("extra")
                 and abs(scored[i][0] - self.score) <= CLOSE_CALLS_LN]
        if not close:
            return scored
        started = clock.now()
        left = CLOSE_CALLS_SHARE * (started - self.started) - self.close_seconds
        if self.stopped or left <= 0:
            return scored
        limit = min(CLOSE_CALLS_SECONDS, left)
        best = sorted(close, key=lambda i: -scored[i][0])[:CLOSE_CALLS_TOP]
        current = self.call("equipped")["slots"]
        before = (self.score, dict(self.stats))
        tried = 0
        try:
            for i in best:
                now = clock.now()
                if now - started > limit or (self.stop_at is not None and now > self.stop_at - CLOSE_CALLS_SECONDS):
                    break
                case = cases[i]
                old = current.get(case["slot"])
                self.call("equip", slot=case["slot"], raw=case.get("raw"), fields=[])
                try:
                    self.reassume()
                    stats = self.objective.prepare({**self.call("stats", fields=self.objective.fields),
                                                    **self.added_stats()})
                    scored[i] = (self.objective.score(stats), stats)
                finally:
                    self.call("equip", slot=case["slot"], raw=old, fields=[])
                tried += 1
                self.rescored += 1
                self.close_rescored += 1
        finally:
            if tried:
                self.reassume()
                self.score, self.stats = before
            self.close_seconds += clock.now() - started
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
        # (JEWEL2, on loan: this function only) A what-if replacing an item that holds jewels (Tabula Rasa, a socketed ring,
        # Cadigan's gloves): PoB drops the jewels, but the models keep what they read from them at the last resolve, so the
        # candidate is re-scored for real. Probe POE2T7946: up to +0.086 ln too high without it; POE2T7947: the gap closes.
        # Any jewel counts, model-read or not: the lines that moved there ("Debuffs on you expire faster", "Cooldown Recovery
        # Rate") are read by the assumptions, which item_read_by_models doesn't see. Cost: within RESCORE_TOP real trials
        holders = {s.split(" Jewel Socket ")[0] for s in current if " Jewel Socket " in s}
        touched = [i for i, case in enumerate(cases)
                   if on_stats or item_read_by_models(case.get("raw") or "", texts)
                   or item_read_by_models(current.get(case["slot"]) or "", texts) or case["slot"] in holders]
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
        score = self._committed(self.call("allocate", add=list(add), remove=list(remove), attribute=attribute,
                                          fields=self.objective.fields))
        if remove and not add and self.judging_keeps():  # (JUDGE: a revert's removal settled, the next judged on it)
            self._judge_settle()
            score = self.score
        return score

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
                "recovery_sources": dict(self.recovery_sources), "character_runes": dict(self.character_runes),
                "config": dict(self.call("getConfig")["input"])}
        if self.snapshot_state is not None:
            snap["search"] = self.snapshot_state[0]()
        return snap

    def restore(self, snap: dict) -> None:
        self.call("loadXml", xml=snap["xml"])
        # (SEARCH33, probes POE2T5484-5486) PoB writes a config input at its own default as a Placeholder, which loads as
        # unset: the search's "Boss at 2 m" (enemyDistance 20, PoB's default) came back unset after every restore,
        # conditions.sync dropped the distance choice for the 5 m natural one, and Blasphemy's Despair left the boss for
        # every trial after the first taken back (each support trial on 2b75b read x0.82: the 3-support main-group trap
        # and much of the seed spread). The inputs the reload dropped or changed go back before the hooks run
        if snap.get("config") is not None:
            now = self.call("getConfig")["input"]
            lost = {k: v for k, v in snap["config"].items() if now.get(k) != v}
            if lost:
                self.call("setConfig", vars=lost, fields=[])
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
