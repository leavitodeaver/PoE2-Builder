# PoE2 Builder

A Path of Exile 2 build optimizer driven by a headless Path of Building (PoB) engine. The spec
and roadmap are in [PLAN.md](PLAN.md). The old Rust attempt lives untracked in `legacy/`.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and git. Windows only for now: the engine runs on
PoB's bundled 64-bit LuaJIT DLL.

```
uv sync
uv run python scripts/fetch_pob.py
```

## Use

```
uv run python -m builder.data dump     # export game data from the pinned PoB (after setup or a pin change)
uv run python -m builder.data prices   # refresh the poe.ninja price cache
uv run python -m builder.web           # open the local web page
uv run pytest                          # request/constraint tests
```

The web page holds the request form and saved requests, and checks any PoB code or poe.ninja /
pobb.in share link against the current request. Optional: set `POE2_BUILDER_CONTACT` to add a
contact to the User-Agent sent to poe.ninja, as their API docs ask.

## Engine checks

```
uv run python benchmarks/make_test_builds.py   # regenerate benchmarks/builds/*.xml
uv run python benchmarks/run_gates.py          # fidelity, delta agreement, round trip, timings
uv run python benchmarks/bench_pool.py         # parallel what-if throughput by worker count
uv run python benchmarks/compare_versions.py   # dev snapshot vs PoB release, per build
```

`scripts/fetch_pob.py` checks out both the pinned dev snapshot (what the optimizer calculates
with) and the matching PoB release (what the PoB app runs, used for cross-checks).

To check fidelity against real builds, save PoB codes exported from the PoB app as
`benchmarks/reference/<name>.txt`. `run_gates.py --pob-dir vendor/PathOfBuilding-PoE2-<release>`
compares them with the release the app runs; `compare_versions.py` explains what the snapshot
changes.

## Layout

- `engine/bootstrap.lua`: boots PoB headless (mirrors PoB's HeadlessWrapper.lua)
- `engine/bridge.lua`: the only code touching PoB internals; JSON API for the Python side
- `engine/pob-pin.json`: the pinned PoB commit
- `builder/pob/`: ctypes host for PoB's LuaJIT, engine wrapper, worker pool, build codes
- `benchmarks/`: test builds, gate and throughput scripts, results history
