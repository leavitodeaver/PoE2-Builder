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

## Engine checks

```
uv run python benchmarks/make_test_builds.py   # regenerate benchmarks/builds/*.xml
uv run python benchmarks/run_gates.py          # fidelity, delta agreement, round trip, timings
uv run python benchmarks/bench_pool.py         # parallel what-if throughput by worker count
```

To check GUI fidelity against real builds, save PoB codes exported from the PoB app as
`benchmarks/reference/<name>.txt`; `run_gates.py` picks them up.

## Layout

- `engine/bootstrap.lua`: boots PoB headless (mirrors PoB's HeadlessWrapper.lua)
- `engine/bridge.lua`: the only code touching PoB internals; JSON API for the Python side
- `engine/pob-pin.json`: the pinned PoB commit
- `builder/pob/`: ctypes host for PoB's LuaJIT, engine wrapper, worker pool, build codes
- `benchmarks/`: test builds, gate and throughput scripts, results history
