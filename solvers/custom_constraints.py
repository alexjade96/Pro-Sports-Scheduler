"""
Custom / experimental constraint add-on framework (league-agnostic).

Purpose
-------
A place to prototype *additional* scheduling constraints that are not part of
any league's core `constraints.json` / solver constraint set yet — e.g. travel
distance between venues, time-zone changes on back-to-backs, opponent-rest
inequality. Several of these correspond directly to the rules flagged as
"❌ needs travel-distance / timezone data" in `tools/constraint_report.py`
(NBA HC7 back-to-back long travel, NBA SC9 timezone fairness, NFL SC7 travel
fairness); this module is where you can test them before deciding whether they
are worth wiring into a solver's core constraint set.

Two ways to use a custom constraint
-----------------------------------
1. **Evaluate** — measure it against an already-solved schedule to see how a
   given schedule scores on the candidate metric (does the constraint bind?
   is it worth optimizing for?). See `evaluate()` and `tools/custom_constraints.py`.
2. **Optimize** — fold it into the metaheuristic's objective so the solver
   actually tries to satisfy it, via `CustomAugmentedMHConstraintSet`. This is
   the "inclusion into scheduling" path — the MH is used because it scores a
   whole `Schedule` object each iteration, so any Schedule→penalty function
   drops in without touching the MIP model. (Wiring a custom term into CP-SAT /
   ILP requires expressing it in linear/boolean model terms and belongs in that
   league's constraint set, not here.)

Adding your own constraint
--------------------------
Decorate a function with `@register(...)`. It receives
`(schedule, teams, context, weight)` and returns
`(penalty: float, violations: list[dict], metric: dict)`.

    @register("my_rule", "One-line description", kind="soft",
              default_weight=10.0, requires=("geo",))
    def _my_rule(schedule, teams, context, weight):
        ...
        return penalty, violations, {"some_metric": ...}

`requires=("geo",)` marks a data dependency; if the active league has no
`data/leagues/<league>/geo.json`, the constraint is reported as *skipped*
rather than silently scoring 0. `context` is built once by `build_context()`
and carries the geo lookup plus `distance_miles` / `tz_gap` helpers.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from core.models import Schedule
from core.data_loader import get_active_league

_DATA_ROOT = Path(__file__).parent.parent / "data" / "leagues"


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────
# A constraint function: (schedule, teams, context, weight)
#   -> (penalty, violations, metric)
ConstraintFn = Callable[[Schedule, dict, dict, float], "tuple[float, list[dict], dict]"]


@dataclass(frozen=True)
class CustomConstraint:
    id: str
    description: str
    kind: str               # "hard" | "soft" — classification for reporting only
    default_weight: float
    requires: tuple[str, ...]   # data dependencies, e.g. ("geo",)
    fn: ConstraintFn
    source: str = ""        # rationale / provenance (e.g. which official rule it prototypes)


@dataclass
class CustomResult:
    constraint_id: str
    kind: str
    weight: float
    penalty: float
    violations: list[dict] = field(default_factory=list)
    metric: dict = field(default_factory=dict)
    skipped: bool = False
    skip_reason: str = ""


_REGISTRY: dict[str, CustomConstraint] = {}


def register(id: str, description: str, kind: str = "soft",
             default_weight: float = 1.0, requires=(), source: str = ""):
    """Decorator registering a custom constraint function under `id`."""
    def deco(fn: ConstraintFn) -> ConstraintFn:
        if id in _REGISTRY:
            raise ValueError(f"Custom constraint {id!r} already registered")
        _REGISTRY[id] = CustomConstraint(
            id=id, description=description, kind=kind,
            default_weight=float(default_weight), requires=tuple(requires),
            fn=fn, source=source)
        return fn
    return deco


def available() -> list[CustomConstraint]:
    """All registered custom constraints, sorted by id."""
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def get(id: str) -> CustomConstraint:
    if id not in _REGISTRY:
        raise KeyError(f"No custom constraint {id!r}. Registered: {sorted(_REGISTRY)}")
    return _REGISTRY[id]


# ─────────────────────────────────────────────────────────────────────────────
# Geo / context
# ─────────────────────────────────────────────────────────────────────────────
_EARTH_RADIUS_MILES = 3958.7613


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two lat/lon points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2)
    return 2 * _EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def load_geo(league: str) -> dict[str, dict]:
    """Return {team_id: {"lat","lon","tz","tz_offset"}} from
    data/leagues/<league>/geo.json, or {} if the league has no geo file."""
    path = _DATA_ROOT / league / "geo.json"
    if not path.exists():
        return {}
    with open(path) as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def build_context(league: str | None = None, teams: dict | None = None) -> dict:
    """Precompute shared lookups (geo + distance/timezone helpers) once, so a
    constraint evaluated thousands of times in an SA loop pays for it once."""
    league = league or get_active_league()
    geo = load_geo(league)

    def distance_miles(team_a: str, team_b: str) -> float | None:
        ga, gb = geo.get(team_a), geo.get(team_b)
        if not ga or not gb:
            return None
        return haversine_miles(ga["lat"], ga["lon"], gb["lat"], gb["lon"])

    def tz_gap(team_a: str, team_b: str) -> int | None:
        ga, gb = geo.get(team_a), geo.get(team_b)
        if not ga or not gb or "tz_offset" not in ga or "tz_offset" not in gb:
            return None
        return abs(int(ga["tz_offset"]) - int(gb["tz_offset"]))

    return {"league": league, "geo": geo,
            "distance_miles": distance_miles, "tz_gap": tz_gap}


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers for the built-in example constraints
# ─────────────────────────────────────────────────────────────────────────────
def _venue_team(sf) -> str:
    """The team whose home venue hosts this fixture (i.e. where it is played)."""
    return sf.home_team_id


def _ordered_fixtures_by_team(schedule: Schedule, team_ids) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for sf in schedule.fixtures:
        out[sf.home_team_id].append(sf)
        out[sf.away_team_id].append(sf)
    for tid in out:
        out[tid].sort(key=lambda s: (s.slot.date, s.slot.kickoff))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Built-in example custom constraints
# ─────────────────────────────────────────────────────────────────────────────
@register(
    "travel_fairness",
    "Season travel-mile imbalance: penalize teams whose total travel exceeds the "
    "league mean by more than `tolerance` (great-circle miles between venues).",
    kind="soft", default_weight=1.0, requires=("geo",),
    source="Prototype for NFL SC7 travel-fairness / general competitive-balance; "
           "not in any core constraint set (needs venue coordinates).")
def _travel_fairness(schedule, teams, context, weight):
    dist = context["distance_miles"]
    tolerance = 0.25  # penalize teams >25% above the league mean
    by_team = _ordered_fixtures_by_team(schedule, teams)
    miles: dict[str, float] = {}
    for tid in teams:
        fx = by_team.get(tid, [])
        loc = tid  # a team starts the season at its own home city
        total = 0.0
        for sf in fx:
            venue = _venue_team(sf)
            d = dist(loc, venue)
            if d is not None:
                total += d
            loc = venue
        miles[tid] = total
    if not miles:
        return 0.0, [], {}
    mean = sum(miles.values()) / len(miles)
    threshold = mean * (1 + tolerance)
    penalty = 0.0
    violations = []
    for tid, m in sorted(miles.items(), key=lambda kv: -kv[1]):
        if m > threshold:
            excess = m - threshold
            penalty += weight * (excess / 1000.0)  # 1 unit per 1000 excess miles
            violations.append({"team": tid, "travel_miles": round(m),
                               "over_threshold_miles": round(excess)})
    metric = {"mean_miles": round(mean), "max_miles": round(max(miles.values())),
              "min_miles": round(min(miles.values())),
              "spread_miles": round(max(miles.values()) - min(miles.values()))}
    return penalty, violations, metric


@register(
    "back_to_back_travel",
    "Back-to-back long travel: flag any back-to-back (consecutive games one day "
    "apart) where the trip between the two venues exceeds `max_miles` (1000).",
    kind="hard", default_weight=50.0, requires=("geo",),
    source="Prototype for NBA HC7 (no_back_to_back_long_travel, >1000mi on the "
           "second night); currently unimplemented in the NBA constraint set.")
def _back_to_back_travel(schedule, teams, context, weight):
    dist = context["distance_miles"]
    max_miles = 1000.0
    by_team = _ordered_fixtures_by_team(schedule, teams)
    penalty = 0.0
    violations = []
    worst = 0.0
    for tid in teams:
        fx = by_team.get(tid, [])
        for a, b in zip(fx, fx[1:]):
            if (b.slot.date - a.slot.date).days != 1:
                continue
            d = dist(_venue_team(a), _venue_team(b))
            if d is None:
                continue
            worst = max(worst, d)
            if d > max_miles:
                penalty += weight
                violations.append({"team": tid, "date": str(b.slot.date),
                                   "trip_miles": round(d),
                                   "from": _venue_team(a), "to": _venue_team(b)})
    metric = {"violation_count": len(violations),
              "max_b2b_trip_miles": round(worst), "max_miles_limit": max_miles}
    return penalty, violations, metric


@register(
    "timezone_shift",
    "Time-zone change on a back-to-back: penalize back-to-backs whose two venues "
    "differ by more than `max_zones` (2) time zones.",
    kind="soft", default_weight=20.0, requires=("geo",),
    source="Prototype for NBA SC9 (travel_timezone_fairness, >2 tz on a b2b "
           "second night); currently unimplemented (needs venue timezones).")
def _timezone_shift(schedule, teams, context, weight):
    tz_gap = context["tz_gap"]
    max_zones = 2
    by_team = _ordered_fixtures_by_team(schedule, teams)
    penalty = 0.0
    violations = []
    worst = 0
    for tid in teams:
        fx = by_team.get(tid, [])
        for a, b in zip(fx, fx[1:]):
            if (b.slot.date - a.slot.date).days != 1:
                continue
            g = tz_gap(_venue_team(a), _venue_team(b))
            if g is None:
                continue
            worst = max(worst, g)
            if g > max_zones:
                penalty += weight * (g - max_zones)
                violations.append({"team": tid, "date": str(b.slot.date),
                                   "zones_crossed": g,
                                   "from": _venue_team(a), "to": _venue_team(b)})
    metric = {"violation_count": len(violations),
              "max_zones_crossed_on_b2b": worst, "max_zones_limit": max_zones}
    return penalty, violations, metric


@register(
    "rest_disparity",
    "Opponent rest inequality: penalize games where the two teams' rest (days "
    "since their previous game) differ by more than `max_gap` (3) days.",
    kind="soft", default_weight=5.0, requires=(),  # no external data needed
    source="Prototype for NBA SC8 (Fresh-Tired-Even factor) / general fairness; "
           "works for any league with no extra data — demonstrates a data-free add-on.")
def _rest_disparity(schedule, teams, context, weight):
    max_gap = 3
    by_team = _ordered_fixtures_by_team(schedule, teams)
    # last game date per team as we sweep chronologically
    prev_date: dict[str, date] = {}
    penalty = 0.0
    violations = []
    worst = 0
    for sf in sorted(schedule.fixtures, key=lambda s: (s.slot.date, s.slot.kickoff)):
        h, a = sf.home_team_id, sf.away_team_id
        rest_h = (sf.slot.date - prev_date[h]).days if h in prev_date else None
        rest_a = (sf.slot.date - prev_date[a]).days if a in prev_date else None
        if rest_h is not None and rest_a is not None:
            gap = abs(rest_h - rest_a)
            worst = max(worst, gap)
            if gap > max_gap:
                penalty += weight * (gap - max_gap)
                violations.append({"date": str(sf.slot.date),
                                   "home": h, "away": a,
                                   "rest_home": rest_h, "rest_away": rest_a,
                                   "disparity_days": gap})
        prev_date[h] = sf.slot.date
        prev_date[a] = sf.slot.date
    metric = {"violation_count": len(violations), "max_disparity_days": worst}
    return penalty, violations, metric


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation
# ─────────────────────────────────────────────────────────────────────────────
def evaluate(schedule: Schedule, teams: dict, ids: list[str] | None = None,
             weights: dict[str, float] | None = None, league: str | None = None,
             context: dict | None = None) -> dict:
    """Run the selected custom constraints against a schedule.

    Returns a report dict:
        {"league", "results": [CustomResult...], "total_custom_penalty",
         "hard_violation_count", "soft_violation_count"}
    A constraint whose data requirement is unmet is returned as a skipped
    CustomResult (penalty 0) rather than being dropped, so the report shows
    what could not be measured.
    """
    league = league or get_active_league()
    context = context or build_context(league, teams)
    weights = weights or {}
    ids = ids if ids is not None else [c.id for c in available()]

    results: list[CustomResult] = []
    for cid in ids:
        c = get(cid)
        w = weights.get(cid, c.default_weight)
        missing = [r for r in c.requires if not context.get(r)]
        if missing:
            results.append(CustomResult(
                constraint_id=cid, kind=c.kind, weight=w, penalty=0.0,
                skipped=True,
                skip_reason=f"missing data: {', '.join(missing)} "
                            f"(add data/leagues/{league}/geo.json)"))
            continue
        penalty, violations, metric = c.fn(schedule, teams, context, w)
        results.append(CustomResult(
            constraint_id=cid, kind=c.kind, weight=w, penalty=float(penalty),
            violations=violations, metric=metric))

    total = sum(r.penalty for r in results)
    hard_v = sum(len(r.violations) for r in results if r.kind == "hard")
    soft_v = sum(len(r.violations) for r in results if r.kind == "soft")
    return {"league": league, "results": results,
            "total_custom_penalty": total,
            "hard_violation_count": hard_v, "soft_violation_count": soft_v}


def print_report(report: dict, max_examples: int = 5) -> None:
    print("=" * 78)
    print(f"  CUSTOM CONSTRAINT EVALUATION — league={report['league']}")
    print("=" * 78)
    for r in report["results"]:
        c = get(r.constraint_id)
        head = f"[{r.constraint_id}] ({r.kind}, weight={r.weight:g})"
        if r.skipped:
            print(f"\n{head}  — SKIPPED: {r.skip_reason}")
            continue
        print(f"\n{head}  penalty={r.penalty:.1f}  violations={sum(len(x) for x in [r.violations])}")
        print(f"      {c.description}")
        if r.metric:
            print(f"      metric : {r.metric}")
        for v in r.violations[:max_examples]:
            print(f"        - {v}")
        if len(r.violations) > max_examples:
            print(f"        … and {len(r.violations) - max_examples} more")
    print("\n" + "-" * 78)
    print(f"  TOTAL custom penalty : {report['total_custom_penalty']:.1f}"
          f"   (hard-style violations={report['hard_violation_count']}, "
          f"soft={report['soft_violation_count']})")
    print("=" * 78)


# ─────────────────────────────────────────────────────────────────────────────
# Optimization: fold custom constraints into the metaheuristic objective
# ─────────────────────────────────────────────────────────────────────────────
class CustomAugmentedMHConstraintSet:
    """Wraps any league's MH constraint set and adds the selected custom
    constraints' penalty to `score()`. Satisfies the MHConstraintSet protocol
    by delegating `pre_assign` / `greedy_params` to the base set unchanged.

    The geo/context lookup is built once at construction and reused every
    iteration. Note the SA loop calls `score()` thousands of times, so a custom
    constraint that sweeps every fixture per call adds real per-iteration cost —
    fine for prototyping / testing, but profile before treating results as a
    like-for-like time-budget comparison against the un-augmented solver.
    """

    def __init__(self, base, teams: dict, ids: list[str],
                 weights: dict[str, float] | None = None, league: str | None = None):
        self._base = base
        self._teams = teams
        self._ids = list(ids)
        self._weights = weights or {}
        self._league = league or get_active_league()
        self._context = build_context(self._league, teams)

    def pre_assign(self, fixtures, slots):
        return self._base.pre_assign(fixtures, slots)

    def greedy_params(self):
        return self._base.greedy_params()

    def score(self, schedule, teams) -> float:
        base = self._base.score(schedule, teams)
        rep = evaluate(schedule, teams, ids=self._ids, weights=self._weights,
                       league=self._league, context=self._context)
        return base + rep["total_custom_penalty"]
