# PoE 2 Build Optimizer

A native Windows build-search utility for Path of Exile 2. It searches game data rather than
copying player builds, evaluates retained candidates with a pinned Path of Building 2 engine,
checks budget and survival constraints, and generates a timed skill schedule.

## Current release boundary

This is an audited alpha. It is usable for the interaction families listed below, but it does not
claim that every live-game interaction is simulated yet.

The executable embeds the Path of Building 2 `v0.23.1` catalog at revision
`7d6f530cbdab20389ff8bc6ba97a37ac27f74e41`:

- 400 active-skill entries
- 567 support gems
- 4,914 passive-tree nodes
- 22 ascendancies
- 1,768 item bases
- 435 static uniques plus 443 uniques exported from the live pinned engine
- 9,365 item modifier tiers
- 570 runes, Soul Cores, Idols, Ancient Augments, and other socketable augments with their
  equipment-specific effects

Catalog presence and calculation support are deliberately different facts. Before release, the
runtime audit loads complete native PoB builds, checks only the item's selected variant, and
rejects any entity whose effect is neither calculated by PoB nor handled by a reviewed adapter.
The current effective unique audit accepts all 443 runtime unique records and reports zero
unsupported unique records. That is a unique-item-text milestone, not a claim that every skill,
support pairing, passive transformation, crafting transition, or encounter behavior is complete.
Those broader domains have separate proof gates, and a missing interaction is never silently
assigned a zero value.

The current search supports direct player attacks, spells, damage-over-time skills, and audited
stateful skills for which a complete configured PoB state or reviewed event adapter is available;
compatible support combinations; connected audited passive paths; two-handed, one-handed,
bow/quiver, shield, focus, and dual-wield legality; ordinary equipment slots; audited uniques;
bounded rare and unique roll frontiers; attribute requirements; elemental resistance caps;
effective health; resource sustain; and budget enforcement. It searches multiple active skills,
both weapon sets, legal swap timings, cooldowns, charges, corpses, ailments, and other represented
state in a deterministic event scheduler. Small rotation spaces are enumerated exactly; large
spaces use a deterministic beam search and say so in the proof report.

Socketable augments are loaded from PoB's generated `ModRunes` data, assigned only to compatible
equipment, written into the native PoB item text, and charged against the budget. The
one-per-character Ancient Augment family is enforced separately from ordinary Idols and Ancient
Runes.

These broader families are still capability-gated rather than guessed:

- Triggers, meta skills, minions, companions, totems, traps, transformations, and persistent-state
  mechanics without a completed event adapter
- Timeless/transforming jewels and other effects that rewrite passive nodes
- Charm and flask automation, plus crafting transitions whose real spawn probabilities are not
  available from the pinned sources
- Any passive, unique, or modifier rejected by the pinned PoB complete-build audit

“Exact” in a result means the supplied candidate was accepted in full by the pinned PoB revision.
It does not mean PoB itself is a perfect oracle for the live game. Patch parity still requires
maintained calculation fixtures, and search optimality is reported separately from calculation
coverage.

## Application behavior

- Four native UI steps: Build Goal → Build Choices → Find Builds → Best Build
- Searchable, keyboard-focused dropdowns with fixed width and window-scaled height
- Plain-English delayed tooltips throughout the interface
- Integer arrow controls with press-and-hold repeat
- Multiple required/forbidden skill, support, and passive selectors
- Required/forbidden rune, Soul Core, Idol, and Ancient Augment selectors
- Bow/quiver and one-handed offhand legality
- Armour, evasion, energy shield, and combined defense choices
- A current embedded price snapshot using poe.ninja as the primary market feed and PoE2Scout for
  missing-unique fallbacks and augment estimates, plus expected, P50, and P90 modeled crafting costs
- An explicit-button rare-item search that starts with the optimizer's chosen base and exact rolls,
  then lowers rolls or removes the least important optional modifier one step at a time. It sends
  at most eight searches, spaces them by at least five seconds, follows every returned rate-limit
  bucket, caches completed searches for ten minutes, and stops at the first tier with listings
- Signed game-data and price feeds, refreshed centrally; cached ETags avoid re-downloading unchanged
  upstream responses, and a PoE2Scout outage cannot invalidate the primary poe.ninja snapshot
- Continuous cancellation-safe checkpoints
- Official `.build`, native PoB XML, and detailed JSON report exports
- One portable `.exe`; no Python, Node, Lua, .NET, installer, or console command is needed

The app does not read `Content.ggpk`, inspect the installed game, automate input, or import player
profiles. It never polls or live-subscribes to trade data. A user can explicitly start one bounded
rare-item lookup through the same `trade2` search endpoint used by the pinned PoB2 requester; the
app does not fetch seller details and opens the resulting query on the official trade website.
PoE2DB may be used as a normalization cross-check, but is not scraped at runtime.

## Developer build

Requirements are stable Rust and Visual Studio 2022 Build Tools with the Desktop C++ workload.

```powershell
cargo test --all-targets
cargo run --release
```

The portable output is `target\release\poe2-build-optimizer.exe`. Runtime settings, data packs,
prices, and checkpoints are stored under `%LOCALAPPDATA%\poe2optimizer\PoE2BuildOptimizer`.

The data pipeline pins upstream revisions, validates stable IDs and graph integrity, audits modifier
and item text in the live pinned engine, loads passive batches as complete PoB builds, and records
excluded mechanics in each result’s coverage report. Downloaded packs must match their declared
patch and pass validation; mismatched packs are never combined silently.

## Legal notice

This product is not affiliated with or endorsed by Grinding Gear Games. See [NOTICE.md](NOTICE.md)
for attribution and third-party notices.
