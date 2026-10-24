"""The rare price model (PLAN.md §7): what a rare with these affixes costs, in divines.

    log(price) = base[slot] + sum over affixes of factor[tier] + stack * max(0, tier-1 affixes - 2)

(tier 1 = best, 4 = "4 or lower"). The stack term is there because the market is convex: on
2026-10-24 rares with one or two tier-1 affixes sold at the 1-Exalted floor while a 4x T1 helmet
cost 19 divines, and the optimizer's 6x T1 gloves had no listings at all.

The uncalibrated defaults (a 0.02-divine rare, x4 per tier-1 affix, x1.8 tier 2, x1.25 tier 3,
x1.05 lower, x2 for the main weapon) are the prior. Calibration (builder/report/calibrate.py)
prices representative affix packages on the official trade site and fits the model by ridge
regression toward that prior, so a slot with little data stays near the prior and is labelled an
estimate. Every price has a floor: nothing is ever priced at 0.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime

from builder.data import DATA_ROOT

CATEGORIES = ["weapon", "offhand", "helmet", "body", "gloves", "boots", "amulet", "ring", "belt", "jewel"]
TIERS = [1, 2, 3, 4]
PRIOR_BASE = 0.02
PRIOR_SLOT = {"weapon": 2.0}
PRIOR_TIER = {1: 4.0, 2: 1.8, 3: 1.25, 4: 1.05}
MIN_PRICE = 0.002  # divine (about an Exalted Orb): the floor for any rare
# Ridge strength toward the prior, in samples' worth: (sample noise / prior uncertainty)^2 in log
# space, with listings scattering about x2.7 around a package's price, base prices guessed within
# about x7 and tier factors within about x2.7
PRIOR_WEIGHT_BASE = 0.25
PRIOR_WEIGHT_TIER = 1.0
PRIOR_WEIGHT_STACK = 1.0
CALIBRATED_SAMPLES = 3  # samples a slot needs before its prices count as calibrated
FULL_SUPPORT = 5  # listings behind a sample for full weight; fewer weigh in proportion (two
# listings at 4,000 and 11,504 divines aren't a market price)
MODEL_FILE = "rare_model.json"


def category(slot: str) -> str:
    """Model category for an equipment slot ("Ring 2", "Jewel 26725", "Weapon 1", ...)."""
    if slot.startswith("Weapon 1"):
        return "weapon"
    if slot.startswith("Weapon 2"):
        return "offhand"
    if slot.startswith("Ring"):
        return "ring"
    if slot.startswith("Jewel"):
        return "jewel"
    return {"Helmet": "helmet", "Body Armour": "body", "Gloves": "gloves", "Boots": "boots",
            "Amulet": "amulet", "Belt": "belt"}.get(slot, "helmet")


def tier_bucket(tier: int) -> int:
    return min(max(int(tier), 1), 4)


def prior() -> dict:
    return {"base": {c: math.log(PRIOR_BASE * PRIOR_SLOT.get(c, 1.0)) for c in CATEGORIES},
            "tier": {str(t): math.log(PRIOR_TIER[t]) for t in TIERS}, "stack": {"t1": 0.0}}


def stacked(tiers: list[int]) -> int:
    """Tier-1 affixes beyond the second."""
    return max(0, sum(1 for t in tiers if tier_bucket(t) == 1) - 2)


@dataclass
class RarePriceModel:
    params: dict = field(default_factory=prior)
    fitted_at: float | None = None  # None: uncalibrated
    league: str | None = None
    samples: dict = field(default_factory=dict)  # category -> samples used
    spread: float | None = None  # residual standard deviation (log space)

    def price(self, slot: str, affixes) -> float:
        """Price of a rare in this slot; `affixes` are objects with a `tier` (or tiers)."""
        tiers = [affix if isinstance(affix, int) else affix.tier for affix in affixes]
        log_price = self.params["base"].get(category(slot), self.params["base"]["helmet"])
        log_price += sum(self.params["tier"][str(tier_bucket(t))] for t in tiers)
        log_price += self.params.get("stack", {}).get("t1", 0.0) * stacked(tiers)
        return max(MIN_PRICE, math.exp(min(log_price, 20.0)))

    def confidence(self, slot: str) -> str:
        if self.fitted_at is None:
            return "estimate (uncalibrated)"
        count = self.samples.get(category(slot), 0)
        date = datetime.fromtimestamp(self.fitted_at).strftime("%Y-%m-%d")
        if count < CALIBRATED_SAMPLES:
            return f"estimate (thin data: {count} trade sample{'' if count == 1 else 's'}, {date})"
        return f"calibrated {date} ({count} trade samples)"

    def describe(self) -> dict:
        thin = [c for c in CATEGORIES if self.samples.get(c, 0) < CALIBRATED_SAMPLES]
        return {"fitted_at": self.fitted_at, "league": self.league, "samples": self.samples,
                "total_samples": sum(self.samples.values()), "spread": self.spread,
                "error_factor": round(math.exp(self.spread), 2) if self.spread is not None else None,
                "thin": thin if self.fitted_at else CATEGORIES,
                "tier_factors": {t: round(math.exp(v), 2) for t, v in self.params["tier"].items()},
                "stack_factor": round(math.exp(self.params.get("stack", {}).get("t1", 0.0)), 2),
                "base_prices": {c: round(math.exp(v), 4) for c, v in self.params["base"].items()}}

    # Storage ---------------------------------------------------------------------------------

    def payload(self) -> dict:
        return {"params": self.params, "fitted_at": self.fitted_at, "league": self.league,
                "samples": self.samples, "spread": self.spread}

    @classmethod
    def from_payload(cls, payload: dict | None) -> "RarePriceModel":
        if not payload:
            return cls()
        return cls(payload["params"], payload.get("fitted_at"), payload.get("league"),
                   dict(payload.get("samples") or {}), payload.get("spread"))

    @classmethod
    def load(cls, league: str | None) -> "RarePriceModel":
        path = DATA_ROOT / "prices" / (league or "") / MODEL_FILE
        if league and path.exists():
            try:
                return cls.from_payload(json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, KeyError, OSError):
                pass
        return cls(league=league)

    def save(self, extra: dict | None = None) -> None:
        path = DATA_ROOT / "prices" / (self.league or "") / MODEL_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**self.payload(), **(extra or {})}, indent=1), encoding="utf-8")


# The model this process prices rares with: set from the payload when a search Context is created
# (each worker holds one Context at a time, builder.search.context.context_for)
_active = RarePriceModel()


def use(model: RarePriceModel) -> None:
    global _active
    _active = model


def active() -> RarePriceModel:
    return _active


def fit(samples: list[dict], league: str | None = None, now: float | None = None) -> RarePriceModel:
    """Fit the model to priced packages: [{"category", "tiers": [1, 2, ...], "price": divine,
    "listed": listings found}]. Weighted ridge regression in log space toward the prior; unpriced
    samples are ignored, and a sample backed by few listings counts less."""
    usable = [s for s in samples if s.get("price") and s["price"] > 0 and s.get("tiers")]
    names = [("base", c) for c in CATEGORIES] + [("tier", str(t)) for t in TIERS] + [("stack", "t1")]
    index = {name: i for i, name in enumerate(names)}
    start = prior()
    theta0 = [start[kind][key] for kind, key in names]
    weights = [PRIOR_WEIGHT_BASE] * len(CATEGORIES) + [PRIOR_WEIGHT_TIER] * len(TIERS) + [PRIOR_WEIGHT_STACK]
    size = len(names)
    # Normal equations (X'X + L) theta = X'y + L theta0
    a = [[0.0] * size for _ in range(size)]
    b = [0.0] * size
    rows = []
    for sample in usable:
        x = [0.0] * size
        x[index[("base", sample["category"])]] = 1.0
        for tier in sample["tiers"]:
            x[index[("tier", str(tier_bucket(tier)))]] += 1.0
        x[index[("stack", "t1")]] = stacked(sample["tiers"])
        y = math.log(sample["price"])
        w = min(1.0, (sample.get("listed") or FULL_SUPPORT) / FULL_SUPPORT)
        rows.append((x, y, w))
        for i in range(size):
            if x[i]:
                b[i] += w * x[i] * y
                for j in range(size):
                    a[i][j] += w * x[i] * x[j]
    for i in range(size):
        a[i][i] += weights[i]
        b[i] += weights[i] * theta0[i]
    theta = solve(a, b)
    params = {"base": {}, "tier": {}, "stack": {}}
    for (kind, key), value in zip(names, theta):
        params[kind][key] = value
    spread = None
    if len(rows) >= 2:
        residuals = [(w, y - sum(xi * ti for xi, ti in zip(x, theta))) for x, y, w in rows]
        spread = math.sqrt(sum(w * r * r for w, r in residuals) / sum(w for w, _ in residuals))
    counts: dict[str, int] = {}
    for sample in usable:
        counts[sample["category"]] = counts.get(sample["category"], 0) + 1
    return RarePriceModel(params, now or time.time(), league, counts, spread)


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (the system is small and well-conditioned)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col and m[r][col]:
                factor = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= factor * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]
