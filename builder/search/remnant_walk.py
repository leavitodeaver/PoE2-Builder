"""Walking to Flames of Chayula and Ancient Blooms (MECHC64; RESEARCH61 R61-1, research/into-the-breach-flame-radius-r61.md).

Into the Breach (and Wildwood's Gifts, the same stats and texts) makes a Remnant "within 8.5m of you every {0} seconds"
(skill_stat_descriptions.csd, breach_flame_spawn_rate_ms; IntoTheBreachPlayer breach_flame_spawn_radius 35 + the
5 m reveal range breach_walk_base_visibility_range 50, in 0.1 m units: HIGH). "Walking over a Remnant collects it"
(KeywordPopups Remnant). So a player standing at the boss collects only the flames that land within its pickup radius;
the rest take walking. Before, every flame counted as collected with no walking (a likely LARGE OVERCOUNT).

Two seeded, cached simulations of a lone boss fight (scratch/research61/breach_sim.py / breach_sim2.py, ported):
- incidental_share: the player moves only when the fight makes it (the boss's forced moves and dodged attacks, its own
  dodge rolls), each move a dodge roll of the build's own length; a roll goes toward the nearest revealed flame when one is
  in sight (a player who takes the skill plays to feed it), else anywhere, drifting back toward the boss beyond 4 m.
  No time cost: the moves are already the fight's.
- route_share: a nearest-flame walk every `every` seconds through the flames it can see, then back to the boss: the
  collected share and the share of the fight spent walking (time off the main skill).
Flames spawn evenly over the 8.5 m disc around the player (UNVERIFIED: "within"; the 5-8.5 m ring a lower reading),
each lasting the skill's Remnant duration (25 s base). The base pickup radius has no source (UNVERIFIED, LOW): rows at
1.0 / 1.5 / 2.25 m, the headline 1.5 m, x (1 + the build's remnant_pickup_range_+%).
"""
from __future__ import annotations

import math
import random
import re
from functools import lru_cache

SPAWN_METRES = 8.5  # "manifests within 8.5m of you" (HIGH)
REVEAL_METRES = 5.0  # "Flame of Chayula within 5m of you are revealed"; pickup-range modifiers scale it since 0.5.0
LIFE_SECONDS = 25.0  # base_remnant_duration_ms 25000 (IntoTheBreachPlayer / AncientGiftsPlayer)
BASE_PICKUP = 1.5  # metres: no source gives it (UNVERIFIED, LOW; 0.3.1 "+~50%"); rows below
PICKUP_ROWS = (1.0, 2.25)
ROUTE_EVERY = 20.0  # seconds between collection walks (R61-1's table: ~85% collected for ~35-40% of the fight walking)
DRIFT_METRES = 4.0  # beyond this from the boss a free roll heads back toward it
PICKUP_STAT = "remnant_pickup_range_+%"  # supports' stat (Fleeting / Magnetic / Harmonic Remnants) on the supported skill
PICKUP_LINE = re.compile(r"^Remnants (?:can|must) be collected from (\d+(?:\.\d+)?)% (further away|closer)(?: (if .+))?$",
                         re.I)


def pickup_percent(texts: list[str], support: float = 0.0) -> tuple[float, list[str], list[str]]:
    """(the summed increase in Remnant pickup range, lines counted, lines not counted): "Remnants can be collected from N%
    further away" (+N) / "must be collected from N% closer" (-N), plus a support's `support`; conditional lines ("if you
    have at least 100 Tribute") not counted (condition not read)."""
    total, used, skipped = support, [], []
    for text in texts:
        m = PICKUP_LINE.match(text.strip())
        if not m:
            continue
        if m.group(3):
            skipped.append(text.strip())
            continue
        total += float(m.group(1)) * (1 if m.group(2).lower() == "further away" else -1)
        used.append(text.strip())
    return total, used, skipped


def _spawn(px: float, py: float, rng: random.Random, inner: float = 0.0) -> list[float]:
    r = math.sqrt(rng.uniform(inner * inner, SPAWN_METRES * SPAWN_METRES))
    a = rng.uniform(0, 2 * math.pi)
    return [px + r * math.cos(a), py + r * math.sin(a)]


@lru_cache(maxsize=512)
def _incidental(moves: float, roll: float, roll_seconds: float, pickup: float, reveal: float, interval: float,
                life: float, steer: bool, inner: float, seconds: float = 1800.0, seed: int = 7) -> float:
    rng = random.Random(seed)
    dt = 0.1
    t, next_flame, made, got = 0.0, 0.0, 0, 0
    px = py = 0.0
    flames: list[list[float]] = []
    next_move = rng.expovariate(moves) if moves > 0 else math.inf
    vx = vy = 0.0
    rolling = 0.0
    while t < seconds:
        while next_flame <= t:
            flames.append(_spawn(px, py, rng, inner) + [t])
            made += 1
            next_flame += interval
        if rolling <= 0 and t >= next_move:
            seen = [f for f in flames if math.hypot(f[0] - px, f[1] - py) <= reveal] if steer else []
            if seen:
                f = min(seen, key=lambda f: math.hypot(f[0] - px, f[1] - py))
                angle = math.atan2(f[1] - py, f[0] - px)
            elif math.hypot(px, py) > DRIFT_METRES:
                angle = math.atan2(-py, -px) + rng.uniform(-0.5, 0.5)
            else:
                angle = rng.uniform(0, 2 * math.pi)
            speed = roll / roll_seconds
            vx, vy = speed * math.cos(angle), speed * math.sin(angle)
            rolling = roll_seconds
            next_move = t + rng.expovariate(moves)
        if rolling > 0:
            step = min(dt, rolling)
            px, py = px + vx * step, py + vy * step
            rolling -= step
        flames = [f for f in flames if t - f[2] < life]
        kept = [f for f in flames if math.hypot(f[0] - px, f[1] - py) > pickup]
        got += len(flames) - len(kept)
        flames = kept
        t += dt
    return got / made if made else 0.0


def incidental_share(moves: float, roll: float, pickup: float, reveal: float, interval: float,
                     life: float = LIFE_SECONDS, roll_seconds: float = 0.75, steer: bool = True,
                     ring: bool = False) -> float:
    """The share of the flames collected on the fight's own moves alone (`moves` a second, each a `roll` m dodge roll in
    `roll_seconds`), pickup radius `pickup` m, flames shown within `reveal` m, one every `interval` s lasting `life` s.
    `steer`: rolls aimed at the nearest shown flame (the headline); False: anywhere (the lower row). `ring`: flames 5-8.5 m
    out (the lower spawn reading). Inputs rounded for the cache."""
    return _incidental(round(max(moves, 0.0), 2), round(max(roll, 0.0), 1), round(max(roll_seconds, 0.05), 2),
                       round(max(pickup, 0.05), 2), round(max(reveal, 0.0), 1), round(max(interval, 0.2), 2),
                       round(life, 0), steer, 5.0 if ring else 0.0)


@lru_cache(maxsize=256)
def _route(pickup: float, speed: float, interval: float, life: float, every: float, back: bool,
           seconds: float = 1200.0, seed: int = 1) -> tuple[float, float]:
    rng = random.Random(seed)
    dt = 0.05
    t, next_flame, made, got, walk = 0.0, 0.0, 0, 0, 0.0
    px = py = 0.0
    flames: list[list[float]] = []
    next_tour = every

    def spawn_until(now: float) -> None:
        nonlocal next_flame, made
        while next_flame <= now:
            flames.append(_spawn(px, py, rng) + [next_flame])
            made += 1
            next_flame += interval

    while t < seconds:
        spawn_until(t)
        flames[:] = [f for f in flames if t - f[2] < life]
        kept = [f for f in flames if math.hypot(f[0] - px, f[1] - py) > pickup]
        got += len(flames) - len(kept)
        flames[:] = kept
        if t >= next_tour and flames:
            hx, hy = px, py
            targets = list(flames)
            while True:
                flames[:] = [f for f in flames if t - f[2] < life]
                if not flames:
                    break
                f = min(flames, key=lambda f: math.hypot(f[0] - px, f[1] - py))
                d = math.hypot(f[0] - px, f[1] - py)
                if d > 9 and f not in targets:
                    break
                step = max(0.0, d - pickup)
                if step > 0:
                    px += (f[0] - px) * step / d
                    py += (f[1] - py) * step / d
                    took = step / speed
                    t += took
                    walk += took
                    spawn_until(t)
                kept = [g for g in flames if math.hypot(g[0] - px, g[1] - py) > pickup + 1e-9]
                got += len(flames) - len(kept)
                flames[:] = kept
                targets = [g for g in targets if g in flames]
                if not targets:
                    break
            if back:
                took = math.hypot(px - hx, py - hy) / speed
                t += took
                walk += took
                px, py = hx, hy
            next_tour = t + every
        t += dt
    return (got / made if made else 0.0), (walk / t if t else 0.0)


def route_share(pickup: float, speed: float, interval: float, life: float = LIFE_SECONDS,
                every: float = ROUTE_EVERY, back: bool = True) -> tuple[float, float]:
    """(collected share, share of the fight walking) for a nearest-flame walk every `every` s at `speed` m/s, then back
    to the boss (`back`). Inputs rounded for the cache."""
    return _route(round(max(pickup, 0.05), 2), round(max(speed, 0.5), 1), round(max(interval, 0.2), 2), round(life, 0),
                  round(every, 0), back)
