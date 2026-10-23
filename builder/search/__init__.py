"""The optimizer (PLAN.md §5): skeletons and probes, then block coordinate ascent with kicks.

- `context`: the compiled request, prices and game data a search needs, in one picklable bundle
- `objective`: the score (DPS with feasibility penalties)
- `state`: a build loaded in an engine, with what-ifs, commits and snapshots
- `gems`, `tree`, `gear`, `conditions`: the blocks of the inner loop
- `skeletons`: which ascendancy / main skill / weapon combinations a request allows
- `inner`: the inner loop, run inside a worker process
- `run`: the outer loop across the worker pool, and the final result
"""
