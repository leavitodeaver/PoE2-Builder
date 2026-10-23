# PoE 2 Build Optimizer - continuation notes

Last verified: 2026-08-11 (America/Chicago)

## Release status

- Current version: `0.2.0-alpha.24`.
- Schema version: `3`; normalized game-data version: `normalizer27`.
- Library suite: the 913-test run passed 911 tests with 1 ignored and exposed one stale fixture expectation; after correcting that fixture, its focused rerun passed. `cargo check --all-targets`, formatting, and the release build pass.
- Portable release executable: `dist/PoE2BuildOptimizer-alpha24-windows-x64.exe`.
- The versioned and rolling executables have SHA-256 `B4C0E6550CAEFE1D6308C4FFF9EE77124290D542B88D02C38F301950D6D942D0`.
- No optimizer or smoke-test process was left running.

## Features verified for the latest request

- Find Builds counters reserve enough width for comma-formatted ten-digit values and do not wrap.
- A >=5% replacement gain activates semantic family follow-up (critical, elemental, physical, minion, defence, and other related terms), then returns to the normal lane-diverse replacement scheduler after that family is exhausted.
- Results render the real passive-tree coordinates as an image with selected paths and nodes highlighted; node details appear on hover.
- Endgame normalization removes Transcendent arm/leg items and every ordinary Life/Mana Flask below the Ultimate base. Unique flasks remain legal.
- Player-input skill limits count only actions the player must press. Persistent, Meta, triggered, and item-granted skills do not consume the active-action limit.
- Hammer of the Gods requires a reachable Heavy Stun/Glory source. Explosive Transmutation requires a reachable Remnant producer. Unreachable payloads are rejected instead of being assigned free activations.
- Mjolner variants preserve Thundergod's Wrath levels 1 through 20, with the highest legal/economically nondominated roll searched first. The current acceptance result uses level 20.
- Socket augments are optimized against finalized equipment, assigned to legal item sockets with constrained sockets first, and displayed under their host items in the UI.
- Final rare-item pricing uses a bounded, rate-aware trade-search relaxation queue. Missing quotes remain `Price not available`, fallbacks are recorded, and missing prices do not block calculation.
- Ordinary market-age metadata is quiet; it does not block or warn.
- Every Require/Forbid choice, selected ascendancy, main-hand/offhand lock, and player-input skill-count bound is now rechecked at the exact-evaluation boundary. A search frontier cannot silently optimize away a user parameter.
- Required skills are preflighted against the locked weapon domain. An incompatible request is explained before any long-running tree/gear search starts.
- Passive allocation uses a filtered navigation graph rather than raw exported links. Main-tree, ascendancy, mastery/decorative, and special-root layers are no longer mixed.
- Final passive allocations must be reachable from the selected class root, selected ascendancy root, or a specifically enabled Split Personality root and must respect both point pools.
- The main tree is rendered with uniform scaling; the selected ascendancy is drawn in a separate inset, so far-away raw ascendancy coordinates and cross-layer edges cannot create giant gold lines.

## Fixes completed in the latest continuation

- Fixed the passive-tree coordinate source. PoB node group numbers are one-based while the serialized group array is zero-based; the old normalizer shifted every node into the following group. All 4,914 normalized nodes now use the correct group. Regenerated geometry has no ordinary edge longer than 2,000 coordinate units (the stale pack had 1,251 such edges and a 32,355 maximum).
- Passive generation and final legality now use the same filtered, undirected navigation graph, recovering valid one-way exported links without admitting ascendancy overlay links.
- Contextual tree rebuilding now targets the request's full passive-point budget instead of inheriting the incumbent's node count. An empty streaming seed therefore becomes a complete connected 122-point tree rather than a one-point tree.
- Contextual mechanic profiles now observe the equipment and augments actually selected in the candidate, not only user-locked items. Optional conversion, trigger, granted-skill, and damage mechanics therefore reshape the next tree, gear, and support neighborhoods.
- Item-granted damage rows now contribute to the measured source/payload split. A payload supplying 90% of damage gives payload scalers 90% contextual weight instead of being silently ignored.
- Scoped `+X to Level of ... Skills` modifiers are classified by the selected source/payload tags. The 268 current-pack gear records containing skill-level bonuses can now lead contextual replacements, while unrelated minion/spell/element scopes receive no false credit.
- Replacement exact evaluations now use the configured evaluator pool in deterministic parallel batches and retry transient evaluator failures instead of blacklisting the state before evaluation.
- The optimizer retains complete anchors by skill state, equipment, trigger/conversion, and granted-skill identity. Several distinct anchors receive a bounded contextual tree/gear/support refinement before the remaining budget is committed to the best refined chassis.
- Added coordinated mechanic-family commitments. Crit, skill levels, flat damage, more/extra damage, speed, penetration/exposure, conversion, ailments, charges/rage, projectile count, and lucky effects can be proposed atomically across tree, equipment, loadout, and augments even when the individual substitutions cross a local-loss valley.
- Support replacement no longer discards everything after a generic top-eight prefix. It preserves diverse mechanic-family seats and can exact-evaluate an otherwise ninth-ranked support package.
- Added a free-Spirit opportunity lane, delayed-payoff connected seats, actual-item granted skill profiles, and typed Frenzy-charge consumption for charged Explosive Spear states.
- Bumped app/data versions and invalidated stale optimizer results/checkpoints while preserving saved requests.

- Augment candidates now use incumbent equipment sockets and contextual damage tags rather than a generic catalog-wide set.
- Inactive Bonded effects, reflection, kill-only, dodge-only, incompatible slots, and Brutality-blocked elemental/chaos effects no longer win augment screening.
- The scheduler preserves the best augment lane during family-focused replacement passes.
- The best exact tree/gear/loadout/equipment single is now exact-tested atomically with its strongest legal socket addition before the generic connected queue. This prevents a larger single move from postponing a complementary rune forever.
- Augments are written to the most constrained compatible socket first, preventing a general-purpose augment from stealing a weapon-only socket.
- Checkpoint/component rendering no longer indexes a missing utility-support group. Baseline normalization permanently aligns utility skills, selected forms, and support-group arrays before evaluation and ablation. This fixed an end-to-end panic that looked like a frozen search.
- Cross-layer passive links are filtered before undirected symmetrization. Legal one-way upstream edges are still recovered, but raw ascendancy-to-main links cannot become optimizer paths.
- The passive renderer now fits only the official main-tree bounds, uses one scale factor for both axes, excludes mastery/proxy nodes, and renders ascendancy nodes independently.
- Old `normalizer25` checkpoints/results are invalidated while saved input fields are preserved.
- The `poe2-tools/poe2-build-planner` graph behavior was used as an MIT-licensed compatibility reference and recorded in `NOTICE.md` and `upstream.lock.json`.

## Latest saved-request acceptance

The 180-second, four-worker native PoB saved-request smoke completed successfully with the corrected `normalizer27` pack:

- 568,697 sustained DPS, up from the old saved result's 50,781 DPS.
- 5,960 EHP, 100% mana sustain, 132 exported passive IDs (122 ordinary points plus the free root and ascendancy allocation), 14 equipment entries, and 7 socketed augments.
- Ultimate Life and Mana Flasks, no arm/leg upgrade pseudo-items, no result warnings, and one non-blocking `Price not available` estimate for an unpriced generated rare target.
- The winning input skills are Spear of Solaris, Spear Throw, and Chaotic Surge; persistent/triggered skills include Purity of Fire and Cast on Elemental Ailment without consuming the player-input skill limit.
- 51 expensive complete native-PoB evaluations and 6 pruned states in 180.57 seconds. Best-so-far checkpoints improved monotonically through 12,516, 207,905, 505,023, and 568,697 DPS.
- The result is best-found, not globally proven: passive, support, gear, augment, and timing frontiers remain explicitly labelled heuristic or approximated in the coverage report.

## Important remaining work

1. **Finish true iterative commit-and-max.** The new atomic family packages cross two- and three-subsystem valleys, but each lever should receive two or three complete tree + gear + jewel/augment + support rebuild passes, followed by exact ablation, while time remains.
2. **Improve passive branch quality, not geometry.** Geometry and legality are fixed. Continue atomic dead-branch exchanges and require measured contribution or a connector/constraint role for every terminal passive; the current winner still contains travel and secondary-stat nodes that need final ablation evidence.
3. **Reserve a fixed exact-evaluation ablation budget.** A deadline can still leave components marked provisional. Final cleanup should remove structurally irrelevant terminal branches and then exact-test all surviving component roles.
4. **Use throughput-based frontier budgets.** Native PoB calls remain expensive, so practical runs exact-evaluate tens rather than thousands of complete states. Scale seats from measured evaluator latency and workers instead of interpolating mostly from wall minutes.
5. **Complete free-Spirit search.** The latest winner still reports 200 unreserved Spirit. This can be legitimate when no retained automatic actor improves the build, but the result should expose which legal persistent/meta actors were exact-tested and why each lost.
6. **Add independent companion/minion damage rows.** Their DPS is already included in ranking, but the damage-envelope explanation can still proportionally attribute it to the main skill.
7. **A literal global optimum is still not proven.** Exact PoB evaluation remains authoritative for retained candidates, but the complete game-space search is heuristic and time-bounded.
8. **Upgrade multi-goal passive routing.** Port the exact small-terminal Steiner-style solver and bounded metric-MST/randomized fallback shape from `traecneh/PoE2-Tree-Optimizer`, but only on this app's stricter legal navigation graph.
9. **Add a visible Requested / Delivered result ledger and better unsatisfiable explanations.** Every exact lock is enforced now, but the UI should show verified IDs and the smallest conflicting set/cheapest relaxation for infeasible requests.
10. **Make official `.build` round trips forward-compatible.** Preserve unknown fields and add game-accepted fixtures for stable passive IDs, weapon-set numbering, support nesting, and level intervals.

## Resume commands

```powershell
& 'C:\Users\conve\.cargo\bin\cargo.exe' fmt --all -- --check
& 'C:\Users\conve\.cargo\bin\cargo.exe' check --all-targets
& 'C:\Users\conve\.cargo\bin\cargo.exe' test --lib
& 'C:\Users\conve\.cargo\bin\cargo.exe' build --release --bin saved_request_smoke --bin poe2-build-optimizer

$env:POE2_SAVED_SMOKE_SECONDS='180'
$env:POE2_SAVED_SMOKE_WORKERS='4'
$env:POE2_SAVED_SMOKE_MIN_DPS='100000'
.\target\release\saved_request_smoke.exe
```
