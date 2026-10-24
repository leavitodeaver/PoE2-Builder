# PoE2 Build Optimizer: rebuild plan

Written 2026-10-22. This replaces the old Rust optimizer (`legacy/` once Phase 0 runs). It is the
spec future work sessions build from. Update it when a decision changes.

## 1. What it does

You describe a build: a skill or concept, ascendancy, bans, button cap, defence style and floors,
divine budget plus gear-tier ceiling, and boss, mapping or balanced focus. Optionally you paste a PoB code
to start from. The tool searches for the highest-damage build that satisfies every hard
requirement. It returns:

- a PoB import code (verified by loading it back in the engine)
- the build: ascendancy and points, skills and supports, passive tree, every item including rares
  with target affixes, jewels, runes / Soul Cores / Idols / Augments, flasks / charms
- DPS shown twice: **PoB-calculated** and **ledger-adjusted**, with an asterisk on every number a
  correction or exploit touches, each with a confidence level
- a **Requested vs Delivered** table: every requirement, the value delivered, pass/fail
- a shopping list with estimated prices and official-trade-site search links
- a "test in game" checklist for the asterisked items

If a request can't be satisfied, it says so. It names the conflicting requirements and the
cheapest relaxation. It never relaxes a hard requirement silently.

### Decisions made (2026-10-22)

| Topic | Decision |
|---|---|
| Interface | Local web page served from your PC, opened in a browser |
| Budget | Divine cap (live prices) **and** a gear-tier ceiling |
| Starting point | From scratch by default; optionally a pasted PoB code |
| Combat assumptions | Only buffs and debuffs the build itself can sustain |
| Objective | Balanced (boss DPS and clear speed) by default; per-request boss-only or mapping/clear mode (changed 2026-10-24 at the user's request) |
| Exploits | Included when known to work, marked with an asterisk and a confidence level |
| PoB trust | PoB is the core calculator only. A correction ledger fixes its known gaps |
| PoB version | Newest dev snapshot (0.5.5 data), cross-checked against the latest PoB release; disagreements are explained and flagged |
| Ledger upkeep | Each patch, Claude researches and proposes entries; you approve them in the web page |
| Timeline | Build on 0.5 now; refresh for 1.0 (Dec 11, 2026) once PoB2 ships 1.0 support |
| Old code | Delete build output (done: ~262 GB freed); archive source and docs into `legacy/` |

## 2. Why the old tool failed (do not repeat)

These findings come from auditing the old source. Each one is a rule for the new tool.

1. **It evaluated almost nothing.** Every candidate rebuilt a full PoB XML and reloaded it from
   scratch, often 3–15 times per candidate. Result: about 14 worker-seconds per build and only
   ~50 complete builds tested in a 3-minute run. Everything else was ranked by keyword guesses
   from mod text. → **Rule: keep the build loaded in memory and change it in place. Use PoB's own
   incremental calculators (the ones behind node-power and item-compare tooltips). Measure
   throughput continuously.**
2. **It started from bad seeds and only climbed locally.** The first tree ignored the chosen skill.
   Its baseline gear had no damage stats. No search move could change ascendancy. → **Rule:
   seed from the skill, explore ascendancies as separate skeletons, and use restarts/kicks.**
3. **Constraints were weakened.** It treated "Require skill X" as "X appears somewhere", and sorted
   the required skill last among main-skill candidates. Required supports were relaxed during the
   search. Defence focus passed if any item had more than 0 of that defence. Unpriced items cost 0
   against the budget. Minimum-life floors were wiped on load. → **Rule: hard constraints are
   enforced at the source, so the generators never propose a violating move. They are rechecked
   at the end and reported line by line. Unpriced items get an estimated price, never 0.**
4. **It reimplemented mechanics in code.** About 1,500 scenario adapters and dozens of mechanic
   modules (200k lines of Rust) sat on top of PoB, and starved the search of time. It also patched
   15 PoB source files at load time, which made PoB upgrades very hard. → **Rule: corrections are
   data (the ledger, §6), not code. Zero edits to PoB files. Everything that touches PoB internals
   lives in one small bridge file with upgrade tests.**
5. **Conservative config killed the damage.** Charges, shock, onslaught and similar were off
   unless a separate simulated pass turned them on. Failed passes silently counted as zero.
   → **Rule: a condition resolver turns each condition on whenever the build has a real source for
   it (§5.4). Errors are surfaced, never treated as zero.**
6. **Data could not be rebuilt.** The build depended on files inside `target-*` folders, the price
   feed was a frozen August snapshot, and any revision mismatch dropped every skill. → **Rule:
   game data is dumped from the pinned PoB runtime with one command, and prices refresh on
   demand.**

What is worth salvaging from `legacy/`: the poe.ninja and poe2scout endpoint list
(`feed_builder.rs`), the passive-graph lessons (PoB group IDs are 1-based; filter ascendancy and
mastery links before treating edges as undirected), the official `.build` export format, and the
three Custom builds docs as acceptance specs.

## 3. Architecture

```
Browser UI  ──HTTP/WebSocket──  Python orchestrator (FastAPI)
                                   │  search control, constraints, budget, ledger, reporting
                                   ├── worker pool: N processes, each one LuaJIT + PoB2 headless
                                   │     (build kept loaded in memory; batch delta evaluation)
                                   └── data: PoB data dump · prices · rare price model · ledger · SQLite
```

- **Orchestrator: Python 3.14 managed by `uv`.** Fast to iterate on, good for web UI and data
  work. Ship it as a single folder or a PyInstaller .exe later if wanted.
- **Workers: PoB2 headless on PoB's own LuaJIT** (`vendor/.../runtime/lua51.dll`), hosted in each
  Python worker process through a small ctypes binding (`builder/pob/lua.py`). Decided in
  Phase 0: `lupa`'s stock LuaJIT can't run PoB, because PoB's source uses `+=`-style operators
  that only PoB's patched LuaJIT accepts. Using PoB's own DLL also means the same interpreter as
  the GUI, and PoB's native modules (lua-utf8, zlib) load as-is. Windows-only as a result.
  Worker count is a per-machine setting (page: "CPU workers"; `builder/settings.py`), default half
  the CPU threads (8 on the 9800X3D). Workers run at below-normal priority.
- **PoB2 is pinned as a git checkout** in `vendor/PathOfBuilding-PoE2` (not a re-zipped copy).
  The pin is in `engine/pob-pin.json` (dev branch, 2026-10-01, includes 0.5.5 data);
  `scripts/fetch_pob.py` checks it out. Upgrading means moving the pin, running the data dump,
  and running the gates.
- **Bootstrap:** `engine/bootstrap.lua` mirrors PoB's `HeadlessWrapper.lua` with what embedding
  needs (absolute paths, real clock, zlib through FFI, no stdin reads). No PoB file is edited.
- **Bridge:** `engine/bridge.lua` is the only code that touches PoB internals: one
  `bridge_dispatch(method, json)` entry point. Phase 0 methods: build setup (class, level, skill
  groups, items), `loadXml`/`exportXml`, `stats`, `recalc`, `frontier`/`allocatedNodes`,
  what-ifs (`nodeDeltas`, `itemDeltas`), and commits (`allocate`, `equip`).

### Measured performance (Phase 0, 2026-10-22)

Four generated level-90 test builds (spell, bow attack, melee, minion), Full DPS enabled:

| Operation | Measured |
|---|---|
| Engine boot | 0.8–2.1 s |
| Load a build from XML | 60–330 ms |
| Full recalculation through PoB's normal path | 10–45 ms |
| What-if: node / path / removal (PoB's misc calculator) | 1–6 ms; ~2× cheaper without Full DPS |
| What-if: item swap | 1.4–10 ms |
| Aggregate what-ifs, 14 workers (Boneshatter build) | **~3,200/s** (380/s on 1 worker) |
| Memory per worker | ~0.5–0.6 GB |
| Old tool, for comparison | ~0.3 complete builds/s |

Scaling is linear to 4 workers and flattens past the 8 physical cores. Results history is in
`benchmarks/results/`.

## 4. Requirements model

One JSON request schema (`builder/request/schema.py`), saved per request, editable in the web UI.

- **Identity:** class/ascendancy (any, one, or a shortlist); **main skill** (the skill whose DPS is
  maximized, which is different from "include somewhere"); other required/forbidden skills.
- **Buttons:** maximum player-pressed active skills. Persistent, meta, triggered and item-granted
  skills don't count.
- **Bans and requirements:** skills, supports, keystones, notables, uniques, item classes, mechanic
  families (e.g. no Skeletons/Companions), specific augments. Each entry is Require / Forbid /
  Optimize.
- **Weapons:** type or specific unique, handedness, offhand, weapon-swap policy (must/may/never).
- **Defence:** style (Armour, Evasion, ES, hybrids, CI, MoM, etc.). Numeric floors for: life, ES,
  EHP, max hit per damage type, each resistance including chaos, block/evade/suppression, spare
  Spirit. Every floor is optional and none is silently overridden.
- **Budget:** divine cap plus gear tier (League start / Mid / Endgame / Mirror). The tier caps
  affix tiers, the number of premium affixes per rare, unique price, and whether corruption,
  desecration or omens are allowed. SSF mode ignores prices and uses the tier only.
- **Objective:** what the search maximizes. **Balanced** (default): the geometric mean of boss
  DPS and the clear-speed estimate. **Boss:** single-target boss DPS (on-death damage left out).
  **Mapping:** the clear-speed estimate alone (§5.5). Feasibility penalties are identical in every
  mode. The result shows boss DPS and the clear score separately, the clear score marked
  approximate. Optional **DPS acceptance gate** (on PoB's DPS, whatever the mode): if it isn't
  met, the build is reported as infeasible along with the closest result.
- **Exploits:** allowed by default. Can be forbidden per request, or per individual ledger entry.
- **Level / points / league flags:** character level, passive points, weapon-set points,
  hardcore, SSF.
- **Seed:** optional PoB code. **Effort:** time limit (default 10 min). A first result streams
  within about 1–2 minutes, then keeps improving.

Constraints compile into three layers:
1. **Generator filters.** Banned or illegal things are never proposed.
2. **Feasibility penalties.** Deficits such as missing resistance, attributes, Spirit or a floor
   shape the score, so the search moves toward feasible builds.
3. **Final hard check.** Produces the Requested vs Delivered table. Anything failing is reported
   as infeasible with the cheapest relaxation.

## 5. Search design

### 5.1 Outer loop: skeletons

A **skeleton** is ascendancy + main skill + weapon archetype (main hand + off hand: Shield, Focus,
Quiver or a second weapon) (+ weapon set 2) (+ seed). A pasted PoB code is one more skeleton whose
searches start from it and never end below it. The tool enumerates every
skeleton the request allows, gives each a short **probe** (about 20–40 s of inner optimization),
then puts the remaining time into the best K, in parallel across workers. If no main skill is
given, it shortlists skills by probe first. Ascendancy is never fixed by accident: every legal one
gets a probe. In balanced mode a third of each skeleton's searches maximize boss DPS (§5.5).

### 5.2 Inner loop: block coordinate ascent with kicks

Repeat until no block improves, then **kick** and re-optimize (iterated local search). A kick
removes a tree branch, swaps a unique or reshuffles supports. A kicked build is kept only if it
ends better.

1. **Gems.** Swap rounds over the main skill's support slots, each candidate scored with real PoB
   numbers. Then persistent/meta skills under the Spirit budget and the button cap.
2. **Passive tree:**
   - **Grow:** for each reachable notable, keystone or small cluster, take the cheapest connecting
     path and score the whole path as one delta. Add the best value per point.
   - **Prune and regrow:** score removing each leaf branch, swap in better paths, repeat.
   - Multi-target Steiner heuristic for clusters reached through shared travel nodes.
   - Exact enumeration of ascendancy allocations (the space is small).
   - Attribute-node stat choices. (Weapon-set passive points: not modeled, see the Phase 3
     follow-up findings.)
3. **Gear.** For each slot the candidates are:
   - affordable uniques from the price feed
   - **rare templates** built affix by affix: pick a base, then add the affix with the best score
     gain. Respects prefix/suffix limits, mod groups, item level and tier caps.

   Budget is spread across slots with a Lagrangian: maximize DPS − λ·cost, with λ bisected until
   total cost fits the divine cap. Flasks (Life in Flask 1, Mana in Flask 2) and charms are slots
   too (normal bases, priced uniques, magic items with one prefix and one suffix), switched on only
   while a boss fight sustains them. With a weapon set 2, its weapons are slots of their own and
   a weapon-set block tries the other active skills on set 2.
4. **Sockets.** Runes, Soul Cores, Idols, Ancient Augments and jewels, assigned to compatible
   sockets. Bonded and one-per-character rules are enforced.
5. **Repair.** Fix any remaining deficit at the smallest DPS loss.

### 5.3 Guarding against PoB miscalculations

An optimizer gravitates toward whatever PoB overestimates. The defenses:
- A **suspicious-gain detector.** Any single change worth more than about 40% is checked against
  the ledger's known-overcalc entries and flagged in the result.
- A **damage-contribution breakdown** in the result, so a bogus source is visible.
- Ledger entries can **ban or discount** a known overcalc.

### 5.4 Condition resolver ("only what the build sustains")

A data table maps each DPS-relevant PoB config condition (frenzy/power/endurance charges,
onslaught, shocked/chilled/electrocuted/exposure, curses, rage, and so on) to the evidence that
justifies it, e.g. "has a frenzy charge generator" or "chance to shock ≥ X%". The resolver
re-runs whenever the build changes. This lets the search discover that adding a shock source is
worth it. The table is extendable through the ledger. The boss config uses PoB's pinnacle preset.

### 5.5 Clear speed (mapping and balanced modes)

PoB doesn't model clear speed, so it is estimated (`builder/search/objective.py`, `clear_estimate`)
and the result calls it approximate (confidence: low). The **clear score** is the monster life a
build clears per second in a typical map pack:

    clear = LIFE × PACK / ( PACK / targets × (LIFE / damage + 1 / uses per second) + TRAVEL / movement )

- **damage:** the boss objective's damage, except that on-death effects (Fiery Death's
  explosions, `ON_DEATH`) count, since clearing kills enemies and sets them off.
- **targets:** the enemies one use reaches, from the main skill's PoB output: `ProjectileCount` ×
  (1 + `ChainMax` + ½ × `PierceCount` + `ForkCountMax`) × (1 + (PACK − 1) × (`AreaOfEffectRadius` /
  40)²). That raw reach saturates smoothly at the pack size: 1 + (PACK − 1)(1 − e^−(reach − 1)/(PACK − 1)).
  A pierce counts half (only enemies behind the first); a fork counts one (its two projectiles
  leave at an angle). Minion skills read the `Minion.` fields. A skill whose area PoB doesn't report
  (Boneshatter's strike) counts as single-target.
- **uses per second:** PoB's `Speed` (the minion's for minion skills), 1 if PoB gives none.
  **movement:** `EffectiveMovementSpeedMod` (movement × action speed).
- **Constants (guesses):** a pack of 8 within a 4 m radius; 70,000 life per member (level-80
  base life 31,065 from PoB's table, ×1.75 for two magic and one rare among eight, +30% map
  modifiers); 2 s of walking between packs at base speed.

While damage is the limit, this reduces to the original formula, hit DPS × effective targets.
Once each use kills what it reaches, more damage hardly helps, and use speed, area and movement
speed set the pace, as they do in maps. The constants mostly decide where that switch happens.

**Balanced mode maximizes √(boss DPS × clear score)**, not a sum. The reasons:
- **Neither half can be traded to zero.** log √(B·C) = ½ log B + ½ log C, which falls without
  bound as either half nears 0. A sum would accept a build that clears well and can't kill a
  boss, or the reverse.
- **No unit problem.** The two numbers differ in size (a pack clearer's clear score can be several
  times its boss DPS). In a sum the bigger term, and with it the guessed constants, would decide
  the build. In a product, a constant factor on either half moves every build's score alike and
  changes no choice.
- **Equal value per percent:** +10% boss DPS is worth exactly +10% clear score. Scores stay
  logarithmic, so the feasibility weights keep their meaning (e^3 is still 20× the goal).

The skill screen, probe ranking and finalist all rank on the request's goal. Requirements first,
as before (`rank_key`).

**Measured (2026-10-24):** Lightning Arrow Deadeye with a bow, 20 divine, 240 s on 14 workers:

| Goal | Boss DPS | Clear (approx.) | √(boss × clear) | Uses/s | Movement | What changed |
|---|---|---|---|---|---|---|
| boss | 814,744 | 251,329 | 452,514 | 2.81 | ×1.12 | Wake of Destruction, Inexorable Critical I, Minion Pact II |
| mapping | 329,225 | 339,780 | 334,461 | 5.06 | ×1.65 | Voltaxic Rift, Rapid Attacks II, 35% movement-speed boots |
| balanced | 535,133 | 311,727 | 408,430 | 4.00 | ×1.45 | Inexorable Critical I, Lightning Attunement, the same boots |

Boss and mapping each won on their own goal. Balanced didn't: a second run found the same build
(406,723), but the boss build scores 452,514 on the balanced goal, 11% higher. The balanced search
settles among fast, lighter-hitting builds (attack speed pays twice there: damage and use time;
movement speed pays in clear). Boss DPS builds only pull ahead late: in the boss run, probes
ended at 118k DPS and the focus stage took one to 891k.

**So balanced mode runs a portfolio** (`run.py`, `replica_goals`): every third search of a
skeleton (5 of 14 here) maximizes boss DPS. Each goal keeps its own line through probes and
focus: boss searches continue from the best boss-DPS state, balanced searches from the best
state on the balanced value (which may be a boss search's). Every result is ranked on the
balanced value, recomputed from its boss DPS and clear score, so the finalist can be a boss
search's build, and the finalize polish then runs on the balanced goal. Starting every search
from the probe winner was tried first and changed nothing: the boss searches inherited the fast
build.

| Balanced run, same request | Boss DPS | Clear (approx.) | √(boss × clear) | Uses/s | Movement |
|---|---|---|---|---|---|
| before the portfolio | 535,133 | 311,727 | 408,430 | 4.00 | ×1.45 |
| with the portfolio | 849,693 | 299,077 | 504,106 | 2.86 | ×1.42 |
| (a fresh boss-mode run) | 897,135 | 247,517 | 471,228 | 2.80 | ×1.12 |

With the portfolio, a boss search found 890,871 DPS; the polish on the balanced goal swapped Wake
of Destruction for the 35% movement-speed boots. The result keeps 95% of boss mode's damage and
88% of mapping mode's clear, and beats the boss build on the balanced goal by 7%. Mapping mode
has no portfolio yet: it won on its own goal here.

## 6. Correction ledger (PoB gaps and exploits)

The ledger lives in `ledger/entries/*.yaml`, one entry per known PoB mistake or exploit:

```yaml
id: weapon-set-disabled-grant
title: Disabled weapon set still grants item skill/buff
kind: exploit            # pob_gap | pob_overcalc | exploit | game_bug
patches: ["0.5.4"]       # removed by GGG in 0.5.5
when:                    # predicate over the build
  item_in_inactive_weapon_set: { grants_skill: true }
effect:
  inject_mods: ["..."]   # PoB custom-modifier lines, so it interacts with the rest of the math
  # or: set_config / dps_multiplier / extra_damage_source / ban / discount
confidence: verified_by_you   # verified_by_you | datamined (game files) | community_confirmed | reported | theory
sources: ["user, 2026-10-22: removed in 0.5.5"]
test_in_game: "Equip X in weapon set 2, swap to set 1, check the buff icon persists."
status: retired          # proposed | approved | rejected | retired
reviewed_for: "0.5.5"
```

- **Effects are calculated inside PoB where possible.** Most entries inject custom modifier lines
  or config, so they stack correctly with everything else. A post-calculation multiplier or a
  separately computed damage source is the fallback.
- **Every number an entry touches gets an asterisk** in the result, along with the entry's
  confidence and its in-game test.
- **Calibration form:** after trying a build in game, you enter what the character sheet shows.
  The tool compares that with its prediction and proposes a new or adjusted entry.
- **Patch research workflow:** a repo skill (`/ledger-research`). For a patch, Claude reads patch
  notes, PoB2 bug reports and changelog, and community exploit threads. It writes proposed entries
  into `ledger/proposed/` and re-reviews existing entries, proposing to retire ones a patch fixed.
  You approve or reject each one in the web page's queue.

**As built (Phase 4, first pass).** Entries are `ledger/entries/*.yaml` lists. A `pob_gap` entry
has a regex `match` on a line PoB ignores and `replace` lines PoB parses (`{1}` = group 1,
`{g1*0.9}` = arithmetic, rounded; a unique's rolled range stays a range). On passives the bridge
adds the parsed modifiers to the tree node (and re-adds them whenever PoB re-parses an
ascendancy node); on uniques the lines are added to the item text with the original line's
variant tags. `flag:Name` / `if:Name <line>` make a correction conditional: a passive switches
Name on, or a `condition` entry names the skills that do (e.g. a skill that requires Glory). Every
correction a build uses is listed in its result with the entry's confidence; uncorrected ignored
lines are listed as "not modeled".

**Custom models (2026-10-24)** for mechanics a single modifier line can't express
(`builder/search/models.py`, inputs in `ledger/entries/custom-models.yaml`, tests in
`tests/test_models.py`). Each is a pure function of the build's lines, skills and a few PoB stats;
its effect goes to PoB as assumption lines (bridge `setAssumptions` kind `Line`: a parsed modifier
line, scaled, with ledger conditions, a model-input multiplier and a skill tag) or trigger rates,
and every effect is listed in the result with its basis and confidence:
- **Inputs inside PoB, so what-ifs see them.** The ledger turns each input line on a passive into
  `flag:Name` (a ledger condition) or `mult:Name N` (PoB multiplier `LedgerName`). The model's lines
  are linear in those multipliers around the current build (exact for the build, first order for
  a change: "more" lines as one factor per input, "increased" lines as value + slope), with one
  block of lines per on/off state of its conditions. Allocating Archon of the Storm or an Energy
  node shows its worth in a plain node what-if (Stormweaver Spark: what-if and commit agree
  exactly). Inputs on items count from the next resolve.
- **Elemental Archon:** 25% more Elemental spell damage × effect × uptime, and Exhaust All Power's
  crit grants × effect × uptime; uptime = D / (D + R + wait), D = 10 s × duration, R = 20 s /
  recovery speed (0 with Dominion), wait by source (Seething Body 0; Archon of the Storm half the
  time to spend maximum Mana; Archon of the Blizzard twice the Energy Shield recharge delay;
  Arbiter's Ignition one ignite). Spells with physical or chaos damage lose it while it lasts.
  Not counted for attack main skills. `theory` (durations datamined, regain timing assumed).
- **Meta-skill Energy:** a required meta gem that gains Energy (Cast on Critical, Cast on
  Elemental Ailment, Elemental Invocation) gets the other required spells socketed. Triggers per
  second = Energy per second from the main skill (crits × Power 20 × share of the boss's Ailment
  Threshold the crit deals; or shocks and ignites × 20) × Energy modifiers (gem 57/72/68%,
  increased, more, doubled, after a crit, refunds, half-cost Invocations) / (10 Energy per 0.1 s of
  the socketed spells' base cast times); Cast on Critical's 20% less damage, which PoB ignores, is
  added. PoB alone counts these spells at their full self-cast rate. Other metas' spells count 0.
- **Darkness:** added to every maximum-hit pool (objective `prepare`, also on final facts; PoB's
  own value kept as `PoB<Type>MaximumHitTaken`); PoB already removes Spirit ("You have no
  Spirit"), and the persistent-skill block skips adding skills without Spirit. `reported`.
- **Traveller's Wisdom:** attribute options 4-6 in the bridge (5% increased Damage, defences, Cost
  Efficiency), counted only while the notable is allocated; the attribute round scores them like
  Str/Dex/Int (falling back to the best half when switching all at once breaks a requirement), and
  the ascendancy round tries an allocation with it for real. PoB can't store the choice: the code
  shows those passives as Strength and carries the choice in a `<BuilderTraveller>` element the
  engine reads back; the result says which to pick in game.
- **Elemental Expression:** rate = min(main skill's melee crits per second, cooldown cap 4/s),
  counted as the most likely of its three effects (from Strength/Dexterity/Intelligence).
- **Extra damage sources count during the search:** while a build has spells a meta gem triggers
  or Elemental Expression, their groups are in Full DPS and the objective scores Full DPS.

Not modeled yet: other Archons (Undeath, Nature's; Archon of Chayula is a PoB skill), Seething
Body's hold at High Infernal Flame, Ritual Cadence, other meta gems, supports for spells a meta gem
triggers, a meta gem the optimizer picks by itself, and hits per use (Spark's projectiles) for
Energy. Invocations count as buttons (you press them to release their Energy); the spells a meta
gem triggers don't. Still needing a custom model: Blood Mage's Sanguine Tides, Thorns on melee hit,
Endurance-charge consumption, Empowered-attack extra damage.

## 7. Data and prices

- **Game data:** `uv run builder data dump` boots the pinned PoB and serializes its loaded tables
  (gems, skills, tree, bases, mods, uniques, runes, augments) to JSON. There is no regex parsing
  of Lua.
- **Prices:** documented poe.ninja PoE2 economy endpoints (`builder/data/prices.py`). Cached with
  timestamps and ETags, refreshed on demand at most hourly. poe.ninja's build/character endpoints are **not**
  used (their policy forbids third-party use), which is why seeds come from pasted PoB codes.
- **Rare price model** (`builder/data/rare_prices.py`): log price = base[slot] + Σ factor[tier] over
  the affixes + stack × (tier-1 affixes beyond the second, counting groups with lower tiers) (tier 1 = the best the base can roll,
  4 = lower; the base type isn't a term yet, too few samples). The Phase 3 estimate is the prior
  (stack 0). **Calibrate rare prices** on the web page
  (`builder/report/calibrate.py`) prices 24 representative packages on the official trade site: the
  latest result's rares (two per slot at most) and a generic set per slot (life, resistances,
  damage, speed). Each is one search (instant buyout, every affix at or above its tier's floor,
  cheapest first) and one fetch of the ten cheapest; the package's price is the median of the five
  cheapest, converted with poe.ninja's currency rates. A ridge regression in log space toward the
  prior fits the model (thin data stays near the prior; a sample backed by under five listings
  weighs in proportion). It's stored per league with the fit date
  and its samples (`data/prices/<league>/rare_model.json`), reaches the workers in the payload, and
  labels each rare "calibrated DATE (n trade samples)" or, under three samples for that slot,
  "estimate (thin data …)". Prices have a floor (0.002 divine), so **nothing is ever priced at 0**.
- **Trade API** (`builder/data/trade.py`): used only when the user clicks (a trade link, or the
  calibration job); never polled. It follows GGG's rate-limit headers (X-Rate-Limit-Rules, the
  limits and their -State), keeps one request per window spare for the user's own trade tabs,
  pads each window (arrivals jitter), honors Retry-After, keeps its history on disk across
  restarts, sends a descriptive User-Agent (no email; `POE2_BUILDER_CONTACT` adds a contact), and
  caches searches (a day) and calibration samples (six hours).
- **Trade links** (`builder/report/shopping.py`): one per shopping-list line, made when clicked. A
  rare's search starts strict and loosens only when nothing is listed: exact base and modeled rolls,
  then any base of the class at each tier's floor (weapons, jewels and flasks keep their base), then
  all but one affix. Stat ids come from the trade hashes PoB exports with every mod
  (`explicit.stat_<hash>`, checked against `/api/trade2/data/stats`, cached a week, or PoB's copy).

## 8. Quality gates (the old tool had none that mattered)

1. **Engine fidelity.** Reference PoB codes loaded headless must reproduce the PoB GUI's DPS and
   defences exactly.
2. **Delta agreement.** A random delta evaluation must match a full recalculation within 0.1%.
3. **Round trip.** Every exported code reloads to the same numbers.
4. **Optimizer quality.** For each reference build (PoB codes you paste from strong builds), run
   the same skeleton at a similar budget. The target is ≥ reference DPS with equal-or-better
   defences within 10 minutes. Re-optimizing a seeded reference must never make it worse.
5. **Constraint compliance.** Randomized request fuzzing: every result passes every hard
   constraint, or is reported infeasible with a reason. 100%, no exceptions.
   (`benchmarks/fuzz_constraints.py`: each result's PoB code is reloaded and rechecked
   independently; bans, button cap and budget must hold even in infeasible results.)
6. **Acceptance specs.** The three `Custom builds` concepts (Ice Shot Gemling pure-Armour MoM with
   3 buttons; Little Minions Disciple of Varashta with 2 buttons and its ban list; Explosive
   Transmutation ascendancy comparison) are re-expressed as requests and must produce results that
   honor every stated requirement.
7. **Throughput dashboard.** Evaluations per second, tracked per release.
8. **Version cross-check.** `benchmarks/compare_versions.py` on the reference builds after every
   PoB pin change; every difference is either explained (new data, newly supported modifier) or
   recorded as a suspected PoB bug in `ledger/proposed/`.

## 9. Phases

| Phase | Work | Exit criteria |
|---|---|---|
| **0. Cleanup + engine spike** ✅ 2026-10-22 | Deleted `target*`, `dist`, stray `--help` (~262 GB freed); old source/docs in `legacy/`; `git init`; PoB2 dev pinned; headless boot on PoB's LuaJIT; bridge; test builds; gates; worker pool; release cross-check tool | Gates 1–3 pass on 4 generated builds and 5 of the user's builds; the release reproduces the PoB app's numbers exactly on all 5; ~3,200 what-ifs/s measured |
| **1. Data, requests, UI shell** ✅ 2026-10-22 | Data dump (`python -m builder.data dump`); poe.ninja price feed; request schema + constraint compiler with tests; local web page (`python -m builder.web`): request form, saved requests, paste a code or share link → stats + Requested vs Delivered | Done. The progress stream moves to Phase 2 with the optimizer it reports on |
| **2. Optimizer core** ✅ 2026-10-23 | Skeletons + probes (successive halving, replicas); gems (accelerated gem what-ifs, persistent skills under Spirit); tree (lazy-greedy paths, Steiner pairs, prune/regrow, attribute choices, exact ascendancy enumeration); uniques-only gear with budget at the source; condition resolver + quest rewards; feasibility penalties; kicks; Full DPS finalize with reload check; web page runs (`Optimize`, streamed progress, result + code); CLI `python -m builder.search` | Done. Beats naive baselines on all four benchmark skeletons in 2 minutes: Ice Shot Deadeye ×3.2, Spark Stormweaver ×8.5, Boneshatter Titan ×3.6, Raise Zombie Infernalist ×9.7 Full DPS vs greedy + uniques (`benchmarks/results/optimizer-20261023-023030.json`), all feasible, code verified, independent recheck agrees. Gate 5: 30/30 random requests over three seeds. Gates 1–3 still pass, fidelity exact on the release |
| **3. Rares, budget, sockets** ✅ 2026-10-23 (partly) | Done: rares built affix by affix with tier/roll caps per gear tier; a rare price model (uncalibrated until the user calibrates it); λ budget allocation; runes/Soul Cores/Idols in sockets with per-character limits; rare and unique jewels in tree sockets; skill-cost sustain floor; every skill as a main skill (granted skills, trigger-rate assumptions, stat sets); rare price calibration against the official trade site ✅ 2026-10-24 (§7, Phase 5 findings); dual wielding, weapon swap, flasks and charms, seed codes and the Ritualist's third ring ✅ 2026-10-24 (Phase 3 follow-up findings). Not yet: radius/Time-Lost jewels, corruption/desecration, weapon-set passive points | Gate 4: 3 of 4 usable reference builds beaten at their own defences (Explosive Transmutation ×14, Resonating Shield ×4.4, Ice Nova ×24; `benchmarks/results/gate4-20261023-155225.json`). The miss: Ritualist Explosive Spear ×0.11, built on dual wielding and an item-granted keystone, neither modeled then; now dual wielding and the Ritualist's third ring are modeled: ×0.05 on the final code (`benchmarks/results/gate4-20261024-162050.json`) and ×0.23 in a run whose build found an ignite source (the resolver's "enemy ignited" alone was worth 5×), every floor met in both; under this tool's rules the reference itself measures 2.42M when loaded as a seed and is short of Spirit, and the rest of the gap is an item-granted keystone (Black Scythe Training) and Megalomaniac and Heart of the Well, which the search doesn't propose. The user's 4,000-Mana Gemling request is met; Little Minions meets its 1.5M DPS gate (1.92M) |
| **4. Ledger + exploits** (started 2026-10-24) | Done: PoB's own gap list (885 passive, 454 unique lines it ignores); per-build blind spots in every result; ledger kinds `pob_gap` (translate an ignored line into lines PoB calculates), `pob_wrong` (replace a unique line PoB miscalculates) and `condition` (skills that switch a correction on); first research pass over 30 high-impact mechanics (43 ledger entries in all); custom models for Elemental Archon, meta-skill Energy, Darkness, Traveller's Wisdom and Elemental Expression (§6). Not yet: approval queue, calibration form, exploits, the remaining custom models | Asterisked results; at least one exploit that works on the current patch modeled end to end |
| **5. Results polish** (started 2026-10-24) | Done: Requested vs Delivered; shopping list in every result (slot, base, a rare's target affixes with tier and roll range, a unique's name and variant, runes and lineage gems, price with its confidence, total against the budget) with official trade-site links made on click; test-in-game checklist for every asterisked or not-modeled line. Not yet: saved history | Gate 6 |
| **6. 1.0 refresh** | After PoB2 ships 1.0 support: move pin, dump data, rerun benchmarks, ledger review + research; recheck the 0.5 jewel craft (`JEWEL_CRAFTS`: four mods plus the effect line) and the tier-1 cap; recalibrate rare prices | All gates green on 1.0 data |

A usable MVP (real builds with uniques and simple rares, constraints honored) arrives at the end
of Phase 2. Full v1 arrives at the end of Phase 5.

### Phase 5 findings: shopping list, trade links, rare price calibration (2026-10-24)

- **The trade API answers anonymous searches and fetches** (lower limits than logged in). Limits seen:
  search 5/10 s, 15/60 s, 30/300 s, 600/6 h (lockouts 60 s, 300 s, 1,800 s, 1 h); fetch 12/4 s,
  16/12 s, 50/300 s, 1,000/6 h. The trade *pages* sit behind a Cloudflare check (the built-in
  browser pane loops on it; the user's Chrome passes) and show a search only to a signed-in Path of
  Exile account; the API needs neither. The page says so beside the Trade buttons.
- **Count windows by arrival, not by send time.** Three searches sent just as a 60 s window rolled
  over landed inside it on the server (network jitter) and earned a 10-minute lockout. The limiter
  now pads every window (1 s, or 5% of the period) and still keeps one request spare for the user.
- **A trade query id is the query itself**, gzipped and base64url-encoded (`H4sI…`), so a link can be
  checked offline by decoding it.
- **The market is convex in top tiers** (Forbidden Rites, 24 packages, 20 priced): one or two
  tier-1 affixes sell at the 1-Exalted floor (0.0014 divine) on helmets, gloves, body armours,
  amulets, rings and off-hands; belts, jewels and weapons a little more (0.1 to 0.4); a 4×T1
  resistance and accuracy helmet 19 divines (4 listed); a 5×T1 ring had two listings, at 4,000 and
  11,504. The uncalibrated ×4 per tier-1 affix from 0.02 divines was 10 to 50 times too dear for
  ordinary rares and far too cheap for stacked tier 1s. Fitted: tier 1 ×2.7, tier 2 ×1.7, tier 3
  ×1.1, ×16 for each tier-1 affix beyond the second, slot bases 0.0006 (helmet) to 0.17 (weapon),
  typical error ×3.3. Helmets, boots and rings have three samples each and count as calibrated;
  the other slots stay labelled "estimate (thin data)". Samples with few listings weigh less.
- **The optimizer builds rares nobody sells.** Four of the ten rares in the 2026-10-23 Infernalist
  result had no listings at all (6×T1 gloves, a 3×T1 amulet, two 4×T1 jewels): the endgame gear
  tier allows every affix at tier 1 with 70% rolls. The stack term now prices those out (the gloves
  near 19,500 divines, the jewels near 220).
- **Rares weigh tier 1 against tier 2** (`builder/search/rares.py`). Each pick offers a tier-1 affix
  and its tier 2, and the budget weight decides between them under the calibrated prices. A rare
  takes at most four tier-1 affixes (counting groups with lower tiers) below mirror tier, then only
  tier 2s (`BUY_RULES`). Four came from the user's knowledge of the market (rares with four tier-1
  rolls sell well below mirror prices). The calibration's "nobody sells four" was an artifact:
  instant-buyout-only searches for one exact stat combination each undercount supply. A live
  listing check during the search was rejected, since it would query the trade site without a
  click. On the same Deadeye request (4 minutes each, single runs, so noisy):
  - uncapped, tier 1 only: 796k Full DPS (8.9 of 20 divines on a 4×T1 helmet; rares of two or
    three affixes);
  - two tier-1s, tier 2 only as the fallback: 1.24M;
  - four tier-1s, tier 2 only as the fallback: 552k (back to dear stacks);
  - four tier-1s, tier 1 or 2 by price, crafted jewels: **1.20M** for 19.67 divines
    (`data/results/20261024-152053-run1.json`).

  Offering tier 2 at every pick is what matters: under calibrated prices, 2–3 tier-1s plus tier 2s
  cost cents and fill six affixes.
- **0.5 jewel crafting** (the user, 2026-10-24; `JEWEL_CRAFTS` in `builder/search/rares.py`):
  craft "+1 Prefix Modifier allowed" (a suffix), add a third prefix, then replace the +1 with
  "(40–60)% increased Effect of Prefixes" (also a suffix), or the same on the suffix side. The
  finished jewel has three boosted mods of one kind, one of the other kind and the effect line. The
  jewel builder tries that shape both ways beside the usual two prefixes and two suffixes. PoB
  ignores the effect line, so the boosted values go into the item text, as the game shows them. The
  shopping list marks the boosted and crafted lines, gives the crafting steps, and its trade search
  looks for the unboosted mods. The Deadeye run took 5-line crafted jewels in two of three sockets.
  **Likely to change in 1.0: recheck at the refresh.** The price model's stack term counts only
  tier 1s of groups with lower tiers, so single-tier jewel mods carry no stacking premium.
- **Strict searches rarely match.** A rare's exact base with every modeled roll usually finds
  nothing, so links loosen to any base at each tier's floor (the Deadeye ring below: 0 → 753).
  96% of item affix stat lines map to explicit trade stats; the rest (essence-only, a few shield
  and jewel lines) are listed as "check by eye".
- **A real result** (Ice Shot Deadeye, 20 divines, endgame, 4 minutes: `data/results/20261024-144446-run1.json`):
  feasible, 796k Full DPS, 15.84 divines. The shopping list has nine rares, two uniques, a unique
  jewel and seven runes, and sums to the spend. The checklist has 14 lines. Trade links checked:
  Original Sin (831 listed) and the 3×T1 added-damage ring (753 listed, loosened to tier floors).
  Both decode to the intended query, and both were checked by hand on the trade site in the user's
  Chrome (signed in): the item, league, buyout status and stat filters match, with 839 and 758
  listings later that day.
- The built-in browser pane blocks the page's pop-up tab, so a Trade button there only fills in
  the row's "open" link; in a normal browser it opens the search directly.

### Phase 3 follow-up findings: dual wielding, weapon swap, flasks and charms, seeds (2026-10-24)

- **Dual wielding follows PoB's slot rule** (`ItemsTab:IsItemValidForSlot`): a Sceptre goes beside
  any one-handed weapon; other one-handers except Spears too, but not beside a Wand or Sceptre;
  two-handers only with keystones (Giant's Blood and the like, not modeled). Offhand "weapon" gives
  a dual-wield skeleton and "any" adds one per one-handed main hand. The off-hand slot offers every
  legal type and the gear block picks one (the Ritualist reference holds a Sceptre). The type list
  is checked against PoB for Spear, Wand and One Hand Mace main hands (`tests/test_engine_items.py`).
  Gate 4 now maps a reference's Sceptre or one-handed off hand to "weapon".
- **Weapon swap is PoB's weapon-set model.** A skill group is on set 1, set 2 or both; PoB
  calculates a set-2-only group in a second environment with set 2's weapons and carries its buffs
  and debuffs to the main skill (what-ifs on the swap slots match commits; codes reload exactly).
  A skeleton gets a weapon set 2 when a required skill can't use set 1's weapons (Ice Shot beside an
  Explosive Spear: swap Bow + Quiver, its group on set 2 only) or when swap is a must (a copy of set
  1's weapon types). A weapon-set block tries each other active group on set 2 alone and keeps what
  scores better; under a must, the cheapest one goes there if none pays. Persistent skills never go
  on set 2: their Spirit and buffs would come from the set not in use (the disabled-weapon-set
  grant, removed in 0.5.5). The check wants a non-item group on set 2 and gear in the swap slots.
- **Weapon-set passive points aren't worth modeling yet.** In PoB's accounting, allocated passives
  minus min(set-1 points, set-2 points) must fit the normal budget and each set's points are capped
  at the quest points, so each set sees as many passives as it would without them. They only let set
  2's passives differ from set 1's, which pays only through set-2 skills' buffs; a build that never
  swaps gains nothing.
- **Flasks and charms, in a boss fight** (`builder/search/flasks.py`). Nothing dies, so charges come
  only from generation (passives, gear, a flask's own "gains charges per second"). An effect counts
  only while generation keeps it up 90% of the fight; otherwise its slot stays switched off in the
  code and the result says why. Life and Mana flask recovery counts toward the sustain floor at the
  rate generated charges pay for (one use per duration at most). Charms triggered by killing a Rare
  or Unique never count; charms beyond the belt's charm limit are dropped. So most builds carry none:
  without charge generation they're worth nothing, and the Ritualist reference's two unique flasks
  and three charms count nothing here (no generation, uptime 0).
- **Seed codes** (`seed_task`, `Search.setup_seed`): the code's ascendancy, main skill (the
  request's, if the code has it) and weapons make one more skeleton, first in line. Its search loads
  the code, takes out what the request doesn't allow (banned gems, uniques, item classes and
  passives; buttons over the cap; weapons the skeleton doesn't hold, swap gear included when no skill
  uses set 2; the dearest priced items while over budget; passives over the request level's points,
  weakest branches first), adds what it requires, resolves conditions from evidence (the code's own
  configuration isn't trusted) and switches flasks by the uptime rule. That start is a snapshot the
  search never ends below (gate 4's "never worse"). Uniques are priced from the table; the code's
  rares and magic items count as the user's own (no price). Share links are resolved as on the web
  page, and the result reports the code's own numbers under the request. The Ritualist reference
  starts 2.42M but short of Spirit once its weapon-set-2 items go (they fed set 1 through the removed
  grant), so its line trades damage for a legal build.
- **A third ring.** Ritualist's Unfurled Finger adds Ring 3, which PoB ignores until the passive is
  allocated, and the ascendancy round valued the passive at nothing while the slot was empty. Such
  skeletons now start with a free normal ring there.
- **Gate 5** also draws dual wielding, weapon swap (must and never), a required second attack,
  Flask/Charm/Sceptre bans and seed codes now: 30/30 over seeds 1–3 (`benchmarks/results/gate5-20261024-160117.json`, `-160710`, `-161031`). Two fixes came out of it: finalize adds PoB's warnings from a plain reload of the exported code to the checks, and the bridge wipes PoB's skill cache before every load. PoB keys that cache by skill name and slot and kept it across loads, so a build loaded after another with a weapon-set-2 Ice Shot was flagged "not enough Mana" (`ledger/proposed/pob-skill-cache-survives-build-load.yaml`). The same cache explains gate 1 on the release for reference 2b75e: the PoB app saved its stats with 2b75d's cache, so a clean load is 0.6% higher there; the other four references stay exact, and gates 2–3 pass on all nine builds.

### Phase 3 findings that shape later phases

- **Rares are built, not looked up** (`builder/search/rares.py`): PoB's "Item" modifier pool, filtered
  by the base's tags and each mod's spawn weights, one mod per group, three prefixes and three
  suffixes. Each affix is first scored alone, then the best gain is added until nothing pays. The
  gear tier sets item level, the best tier allowed and the roll: league start ilvl 68, tier ≥ 4,
  30% rolls; mid 78, tier ≥ 2, 50%; endgame 82, any tier, 70%; mirror 86, any, 100%. Rares took
  Ice Shot Deadeye from 446k to 1.28M DPS on the same 20 divines, and the user's first request
  (Gemling Glacial Lance, MoM, 4,000 Mana, 75% all resistances) from infeasible to met: 4,006 Mana,
  77% everywhere, 314k DPS.
- **Rare prices are an uncalibrated estimate**: 0.02 divine × 4 per tier-1 affix, × 1.8 per tier 2,
  × 1.25 per tier 3, × 1.05 otherwise, × 2 for the main weapon, labelled as such in the result.
  The trade-API calibration job isn't built yet.
- **Budget**: a weight λ (score per divine) makes every gear choice pay for its price once spending
  presses on the cap (doubled while above 90%, halved below 50%); the cap itself still holds at the
  source.
- **Sockets**: runes, Soul Cores and Idols per item category, priced from poe.ninja (231 of 305;
  the rest are left out), two sockets on body armours and two-handers and one elsewhere (no
  corruption). 211 augments have a per-character limit; the first gate 4 run broke one ("exceeding
  augment limit"), so limits are now enforced across all items.
- **Jewels**: rare jewels (two prefixes, two suffixes from the Jewel pool, on a Ruby, Emerald or
  Sapphire) and affordable uniques in the tree's sockets; each full round also tries taking one
  more socket nearby. Radius and Time-Lost jewels aren't proposed yet.
- **PoB doesn't add base implicits**: a normal base written without its implicit line has none, so
  every normal base before Phase 3 was missing it (e.g. Gemini Bow's extra-arrow chance).
- **Sustained damage needs sustained costs.** PoB's DPS assumes every cost is paid. A gate 4 Ice Nova
  reached "2.4M" while spending 3,263 Mana/s against 114 regeneration and 1,166 Life/s with no
  recovery. Recovery (regeneration, leech, on-hit) covering under 10% of the per-second cost is now a
  requirement-style shortfall, and the result shows the share. Flasks weren't modeled then (they
  now count at the rate their charges are sustained), so the bar is set from the user's own builds: their endgame references sustain 19% (Ritualist, 3.66M), 47% and
  71%; a 25% bar rejected the strongest of them and halved an Ice Shot build's attack speed.
- **Every skill is a candidate.** Skills a passive or unique grants become skeletons that require
  their source; the skill screen no longer filters by skill type; the main skill's stat set (e.g.
  the Fire, Cold or Lightning storm) is chosen by score. Where PoB has no trigger for a skill, an
  assumption supplies its rate, shown with an asterisk: Explosive Transmutation explodes at
  min(Remnants per second from the build's sources × additional-Remnant chance, cooldown cap), with
  source rates from the user's 0.5.4 audit (0 → 100k DPS on Acolyte with uniques); other triggered
  skills with a cooldown fire whenever it allows (low confidence). Exported codes don't carry
  assumptions, so PoB itself shows less for those skills.
- **Not done in Phase 3:** the trade-API price calibration, radius/Time-Lost jewels, corruption and
  desecration, weapon-set passive points. (Flasks and charms came in the follow-up above.)

### Phase 2 findings that shape later phases

- **PoB's `copyTableSafe` corrupted the shared passive tree.** It gives a copy its metatable
  before filling it, and PoB class objects keep parent-class proxies whose metatable `__newindex`
  points at the object, so deep-copying one wrote every field into the *original*. Each build load
  deep-copies tree nodes to switch attribute nodes, so the shared tree grew with every load
  (linearly or doubling, depending on table addresses) until loads took seconds and a worker hit
  20 GB. Intermittent, independent of the JIT, and present in the committed Phase 0 bridge too.
  The bridge now installs a corrected `copyTableSafe` (fields written with `rawset`; copies are
  identical) at runtime, no PoB file edited; gates 1–3 still pass exactly, including fidelity
  against the PoB app's release on all five reference builds. A watchdog in the worker pool also
  replaces any worker over 4 GB or silent for 3 minutes and reports its task as failed, so one
  runaway can't take a run down. Worth reporting upstream.
- **Gem what-ifs must recompute gem requirements.** PoB's GUI path keeps the replaced gem's
  attribute requirements (it accelerates requirement checks). The bridge's `gemDeltas` sets them
  like `ProcessSocketGroup` does; before that, persistent skills looked free and were reverted on
  commit. Every block now verifies each commit and reverts it if the score fell.
- **PoB's Full DPS counts on-death damage as sustained** (the §5.3 risk, seen at once): Fiery
  Death ("ignited enemies explode on death") was 920k of a Spark build's 1.12M Full DPS, against
  54k from Spark, and the Full DPS polish chose it on three builds. The boss objective now
  subtracts Full DPS entries from gems whose description needs enemies to die, and the result
  names what was left out (`ledger/proposed/pob-full-dps-on-death-damage.yaml`). Expect more of
  these; the result's per-skill breakdown is where they show up.
- **Explosive Transmutation isn't calculated by PoB.** Its "Transmutation Explosion" has no
  trigger rate (remnant pickups aren't modeled), so PoB gives 0 DPS on every skeleton and the
  search has nothing to climb. Results now say so (infeasible: damage not modeled) instead of
  returning a 0-DPS build. The Explosive Transmutation acceptance spec needs a ledger entry or a
  remnant pickup-rate model in Phase 4.
- **Skill costs are a game limit too.** A CI build (1 Life) happily took supports with Life
  costs; PoB only warns ("not enough Life to use ..."). The bridge reports PoB's cost-warning
  lists as counts and the score treats each as a hard deficit.
- **Feasibility comes first.** The score is log DPS minus weighted deficits plus a fixed cost for
  being short at all (3 for request floors, 8 for game limits: attributes, Spirit). Fixed weights
  weren't enough: in the user's first test (Gemling, Glacial Lance, MoM, pure Armour, 75% all
  resistances incl. chaos, 4,000 Mana) the search stopped at 1,758–2,165 Mana, because closing a
  big gap takes several moves that each lose a lot of DPS. Now a requirement still short after a
  pass, and no closer than before, gets its weight ×4 (up to ×64); on the second raise the tree is
  cleared and regrown; results are ranked by shortfall first, then DPS. That run then reached 3,560
  Mana with everything else met (with a Focus; 1H weapons now get Shield and Focus skeletons). Experiments put the ceiling there: ~2,900–3,100 Mana with the
  other requirements held (4,777 with Mana alone), so the request needs rare gear (Phase 3). The
  cost of a hard floor that can't be met is damage: the closest build had 8.5k DPS. The relaxation
  hint says so.
- **Where the time goes** (one worker, ~250 what-ifs/s): full support sweeps (5 slots × ~250
  candidates), full gear sweeps (~350 items) and ascendancy enumeration are the expensive blocks,
  so they run every third pass or after a kick that touched them; passes in between rescore
  shortlists. Tree growth is lazy greedy (top few and changed paths rechecked, full recheck every
  12 allocations). A level-95 tree fills in ~10 s.
- **Ascendancy enumeration** scores every maximal connected allocation exactly (with
  multiple-choice options) when there are at most 1,500; Smith of Kitava has ~52k connected
  allocations, so large ascendancies are first narrowed to their strongest notables (each scored
  alone).
- **Conditions:** evidence rules are data (`builder/search/conditions.py`); lines limited to a
  skill kind ("on Melee Hit", "Spells", "Minions") only count when the main skill is that kind.
  Stun, freeze, electrocute, daze and pin are never assumed on a boss. Supports that would be the
  first source of a condition get a real commit-and-resolve trial in the support rounds.
- **No main skill given:** every allowed skill is screened (starting weapon, greedy supports, no
  tree) within a quarter of the run, in shuffled order, then the top four go to probes. The
  Little Minions request (no main skill, minion bans) shortlisted Detonate Dead, Flame Breath,
  Oil Barrage and Hammer of the Gods; it doesn't require a minion skill, so none was chosen.
- **Campaign reward choices** (e.g. +5% Cold Resistance vs +5 Intelligence) are free stats and
  are chosen by score.
- **Not modeled yet:** meta gems (Cast on X) and supports on persistent skills; weapon-set passive
  points. (Dual wielding, weapon swap and seed codes followed in the Phase 3 follow-up.) Unique
  prices: a gear-tier cap per unique (league start 1,
  mid 20, endgame 300 divine) on top of the divine cap; unpriced uniques and lineage supports are
  left out rather than treated as free.

### Phase 1 findings that shape later phases

- **The game enforces limits PoB only warns about:** negative unreserved Spirit, more than 9
  non-item gem groups, unmet attribute requirements. They're always-on checks in the compiler,
  and the Phase 2 generators must treat them as hard limits (one of the user's own builds
  breaks two of them).
- **Button counting is a heuristic** from skill types: persistent, meta, triggered (by a meta gem
  in the same group or inbuilt) and item-granted skills don't count, except Invocations, which
  are pressed (2026-10-24; `constraints.presses`, shared by skeletons, setup and the final check).
  Results label it approximate; the user can correct it per build if it misjudges a skill.
- **Prices:** poe.ninja now covers runes, Soul Cores and Idols, so the poe2scout fallback was
  dropped (revisit only if poe.ninja has outages). 427 of PoB's 441 uniques have a price; the
  rest need Phase 3's estimate (never 0). Single-listing troll prices exist (38M divine), so
  every price carries a listing-count confidence.
- **PoB loads uniques a slice per frame;** the bootstrap now finishes that load, so boot is
  ~1.6–2 s.
- **Ascendancy notables resolve as passives** (e.g. "Advanced Thaumaturgy"), so requests can
  require them alongside keystones.

### Phase 0 findings that shape later phases

- **Attribute nodes are a decision, not travel.** Unallocated attribute nodes have no stats until
  Str/Dex/Int is chosen, so PoB's own node-power numbers undercount any path through them. The
  bridge applies the same choice to what-ifs and commits (default Strength, as PoB does). The
  tree optimizer in Phase 2 should choose the attribute per node.
- **Full DPS needs opting in.** Only socket groups flagged `includeInFullDPS` count, and Full DPS
  roughly doubles what-if cost. Use main-skill DPS for broad screening, Full DPS for finalists.
- **PoB has an accelerated path for gem swaps** (`fastCalcOptions`: reuse tree/item state, skip
  EHP). This is how the GUI sorts gems by DPS. Phase 2 should use it for support-gem search;
  node what-ifs must not use its `nodeAlloc` flag.
- **Commits cost 10–45 ms; what-ifs cost 1–6 ms.** Search loops should screen with what-ifs and
  commit only the winner of each round, as `benchmarks/make_test_builds.py` already does.
- **PoB warns but still calculates** when requirements aren't met (e.g. "You do not meet the
  Intelligence requirement"). The constraint layer must check requirements itself rather than
  rely on PoB rejecting the build.
- **Real endgame builds are much heavier than test builds.** On the user's 27-group Ritualist:
  load 1 s, full recalculation 0.4 s, what-if 100 ms with Full DPS vs 4.6 ms main-skill only.
  Main-skill screening is mandatory, not optional.

### Engine accuracy and the PoB version (verified 2026-10-22)

- **The engine is exact.** Running the PoB app's own version (release v0.23.1), all five of the
  user's builds reproduce the numbers the app saved in their codes.
- **The dev snapshot differs from the release, mostly for real reasons**, found with
  `benchmarks/compare_versions.py` (stat diff plus item modifier lines read differently):
  - newly supported mechanics: Abyssal Wasting (debuff, resistance reduction; the main cause of
    +19% DPS on the Ritualist and +6% on the Oracle), Runic Ward regeneration, more Bonded rune
    lines, Iron Reflexes and Meta reservation effects;
  - 0.5.5 data: rune values changed (e.g. Bonded +20 → +40 Life, chaos res rune 11% → 13%);
  - granted and weapon-set skills are resolved differently (duplicates removed, an item-granted
    Spear Throw dropped). Numbers unaffected on that build, and consistent with 0.5.5 removing
    the disabled-weapon-set grant exploit (confirmed by the user)
    (`ledger/proposed/pob-snapshot-weapon-set-granted-skills.yaml`).
- **One probable snapshot bug:** a Blood Mage's TotalEHP rises 13× (hits to die 3.1 → 41)
  while pools, max hit, block and evade barely change
  (`ledger/proposed/pob-snapshot-ehp-hits-inflated.yaml`). Consequence: **defence floors use
  maximum hit taken and pools, not TotalEHP alone**, and an optimizer must not be allowed to
  chase EHP from this.
- **Process going forward:** the optimizer calculates with the snapshot. Every finalist is
  recomputed on the release; where DPS, max hit or pools disagree by more than ~5%, the result
  lists the responsible modifier lines (newly supported vs changed) and flags anything
  unexplained as a suspected PoB bug for the ledger.

## 10. Risks

- **Rare pricing** will always be the roughest number. It is mitigated by confidence labels,
  calibration, and the gear-tier ceiling.
- **"Global optimum"** can't be proven for a space this large. The measurable promise is gate 4:
  match or beat strong reference builds under the same constraints.
- **PoB coverage:** mechanics PoB can't calculate depend on ledger entries. Until an entry exists,
  the tool says the mechanic isn't modeled instead of guessing.
- **1.0 timing:** the refresh waits on PoB2's 1.0 release. The game's launch date alone doesn't
  make it possible.
- **PoB internals change between versions.** All contact with them is confined to
  `engine/bridge.lua`, and gates 1–3 catch breakage on every upgrade.

## 11. Repo layout

```
PLAN.md
pyproject.toml                 uv project
vendor/PathOfBuilding-PoE2/    pinned checkout, never edited (scripts/fetch_pob.py)
engine/bootstrap.lua           boots PoB headless
engine/bridge.lua              only file touching PoB internals
engine/pob-pin.json            pinned PoB commit
builder/
  pob/      LuaJIT host (ctypes), engine, worker pool, build codes
  data/     data dump loader, prices, rare price model, trade API (rate limits, cache)
  request/  schema, constraint compiler
  search/   context, objective, skeletons, gems, tree, gear, conditions, inner + outer loop,
            baselines (sockets and budget allocation arrive in Phase 3)
  ledger/   schema, predicates, injection
  report/   shopping list + trade searches, rare price calibration job, test-in-game checklist
  web/      FastAPI app + static UI
ledger/entries/  ledger/proposed/
benchmarks/    test builds, reference PoB codes, gate/throughput/optimizer/fuzz scripts, results history
scripts/
tests/
legacy/        old Rust source and docs, read-only reference (only docs tracked in git)
```
