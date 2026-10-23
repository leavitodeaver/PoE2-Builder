# PoE2 Build Optimizer: rebuild plan

Written 2026-10-22. This replaces the old Rust optimizer (`legacy/` once Phase 0 runs). It is the
spec future work sessions build from. Update it when a decision changes.

## 1. What it does

You describe a build: a skill or concept, ascendancy, bans, button cap, defence style and floors,
divine budget plus gear-tier ceiling, and boss or mapping focus. Optionally you paste a PoB code
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
| Objective | Sustained single-target boss DPS by default; per-request mapping/clear mode |
| Exploits | Included when known to work, marked with an asterisk and a confidence level |
| PoB trust | PoB is the core calculator only. A correction ledger fixes its known gaps |
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
  Default worker count is cores − 2 (14 on the 9800X3D).
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
- **Objective:** boss DPS (default) or mapping mode. Optional **DPS acceptance gate**: if it isn't
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

A **skeleton** is ascendancy + main skill + weapon archetype (+ seed). The tool enumerates every
skeleton the request allows, gives each a short **probe** (about 20–40 s of inner optimization),
then puts the remaining time into the best K, in parallel across workers. If no main skill is
given, it shortlists skills by probe first. Ascendancy is never fixed by accident: every legal one
gets a probe.

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
   - Weapon-set passive points; attribute-node stat choices.
3. **Gear.** For each slot the candidates are:
   - affordable uniques from the price feed
   - **rare templates** built affix by affix: pick a base, then add the affix with the best score
     gain. Respects prefix/suffix limits, mod groups, item level and tier caps.

   Budget is spread across slots with a Lagrangian: maximize DPS − λ·cost, with λ bisected until
   total cost fits the divine cap.
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

### 5.5 Mapping mode

PoB doesn't model clear speed, so mapping mode is approximate: hit DPS × effective targets (from
area, projectile count, chain and pierce) × a movement/attack-speed factor. The result says it is
approximate.

## 6. Correction ledger (PoB gaps and exploits)

The ledger lives in `ledger/entries/*.yaml`, one entry per known PoB mistake or exploit:

```yaml
id: weapon-set-disabled-grant
title: Disabled weapon set still grants item skill/buff
kind: exploit            # pob_gap | pob_overcalc | exploit | game_bug
patches: ["0.5.4", "0.5.5"]
when:                    # predicate over the build
  item_in_inactive_weapon_set: { grants_skill: true }
effect:
  inject_mods: ["..."]   # PoB custom-modifier lines, so it interacts with the rest of the math
  # or: set_config / dps_multiplier / extra_damage_source / ban / discount
confidence: community_confirmed   # verified_by_you | community_confirmed | reported | theory
sources: ["https://..."]
test_in_game: "Equip X in weapon set 2, swap to set 1, check the buff icon persists."
status: approved         # proposed | approved | rejected | retired
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

## 7. Data and prices

- **Game data:** `uv run builder data dump` boots the pinned PoB and serializes its loaded tables
  (gems, skills, tree, bases, mods, uniques, runes, augments) to JSON. There is no regex parsing
  of Lua.
- **Prices:** documented poe.ninja PoE2 economy endpoints, with poe2scout as fallback. Cached with
  timestamps, refreshed on demand at most hourly. poe.ninja's build/character endpoints are **not**
  used (their policy forbids third-party use), which is why seeds come from pasted PoB codes.
- **Rare price model:** price as a function of slot, base and premium affix tiers. It is calibrated
  by a user-triggered sampling job against the official trade API. The job follows GGG's returned
  rate-limit headers and identifies itself with a User-Agent. Each estimate carries a confidence
  level, and **nothing is ever priced at 0**.
- **Trade links:** generated per item on demand, rate-limited, opened on the official site.

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
6. **Acceptance specs.** The three `Custom builds` concepts (Ice Shot Gemling pure-Armour MoM with
   3 buttons; Little Minions Disciple of Varashta with 2 buttons and its ban list; Explosive
   Transmutation ascendancy comparison) are re-expressed as requests and must produce results that
   honor every stated requirement.
7. **Throughput dashboard.** Evaluations per second, tracked per release.

## 9. Phases

| Phase | Work | Exit criteria |
|---|---|---|
| **0. Cleanup + engine spike** ✅ 2026-10-22 | Deleted `target*`, `dist`, stray `--help` (~262 GB freed); old source/docs in `legacy/`; `git init`; PoB2 dev pinned; headless boot on PoB's LuaJIT; bridge; test builds; gates; worker pool | Gates 1–3 pass on 4 generated builds; ~3,200 what-ifs/s measured. GUI fidelity (gate 1 against PoB-app exports) is still pending reference codes in `benchmarks/reference/` |
| **1. Data, requests, UI shell** | Data dump; price feed; request schema + constraint compiler; FastAPI + minimal UI (request form, progress stream, result view); PoB code import/export | Paste a code → see its stats; save a request |
| **2. Optimizer core** | Skeletons + probes; gems; tree (grow/prune/Steiner/ascendancy); uniques-only gear; condition resolver; feasibility penalties; kicks | Complete, legal builds for a given skill that beat naive baselines; gate 5 passes |
| **3. Rares, budget, sockets** | Rare templates; price model + trade calibration; λ budget allocation; runes/idols/soul cores/augments; jewels; flasks/charms | Gate 4 on the reference set |
| **4. Ledger + exploits** | Ledger schema and injection; asterisk reporting; approval queue; calibration form; first research pass for 0.5.x | Asterisked results; disabled-weapon-set exploit modeled |
| **5. Results polish** | Requested vs Delivered; shopping list + trade links; test-in-game checklist; saved history | Gate 6 |
| **6. 1.0 refresh** | After PoB2 ships 1.0 support: move pin, dump data, rerun benchmarks, ledger review + research | All gates green on 1.0 data |

A usable MVP (real builds with uniques and simple rares, constraints honored) arrives at the end
of Phase 2. Full v1 arrives at the end of Phase 5.

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
  data/     data dump loader, prices, rare price model
  request/  schema, constraint compiler
  search/   skeletons, gems, tree, gear, sockets, budget, loop
  ledger/   schema, predicates, injection
  report/   result assembly, PoB code, shopping list
  web/      FastAPI app + static UI
ledger/entries/  ledger/proposed/
benchmarks/    test builds, reference PoB codes, gate/throughput scripts, results history
scripts/
tests/
legacy/        old Rust source and docs, read-only reference (only docs tracked in git)
```
