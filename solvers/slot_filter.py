"""
Shared slot-eligibility filter for MIP-based solvers (CP-SAT, ILP).

Problem: a full (fixture × slot) variable matrix for 380 fixtures and 373
slots creates 141,740 binary variables — too memory-intensive for the
container environment.

Solution: each fixture belongs to a natural round R (assigned generically
by solvers/round_assignment.py — the greedy earliest-available-round
algorithm, not a hardcoded round count), and should be assigned to a slot
whose date falls within a ±window_rounds window around the expected date
for round R. days_per_round is derived from however many rounds that
league's fixture set actually produces, so this works the same way for a
20-team, 38-round league or a 32-team, ~18-round league.

This reduces the active variable count to roughly:
    n_fixtures × (2 * window_rounds * slots_per_round)
which for EPL at window_rounds=5 and ~5 slots/round gives ~19,000
variables — 14× fewer than the full cross-product.
"""
from __future__ import annotations

from datetime import date, timedelta

from core.models import Fixture, Slot
from solvers.round_assignment import assign_natural_rounds


def build_eligible_slots(
    fixtures: list[Fixture],
    slots: list[Slot],
    season_start: date,
    season_end: date,
    window_rounds: int = 3,
) -> dict[str, list[str]]:
    """
    Returns {fixture_id: [slot_id, ...]} containing only slots that fall
    within the temporal window for that fixture's natural round.

    Parameters
    ----------
    fixtures       : list returned by generate_fixtures() — for formula-based
                     generators (NFL/NBA), pass the fixtures through
                     generators/interleave.py first so a team's fixtures of
                     different matchup types are spread across the list
                     instead of clustered by type.
    slots          : all available slots (after blocked-window filtering)
    season_start   : first day of the season
    season_end     : last day of the season
    window_rounds  : how many rounds either side of the natural round to allow
                     (default 5 → ~5 weeks either side)
    """
    fixture_rounds = assign_natural_rounds(fixtures)
    n_rounds        = max(fixture_rounds.values(), default=0) + 1
    season_days     = (season_end - season_start).days
    days_per_round  = season_days / n_rounds

    eligible: dict[str, list[str]] = {}

    for fixture in fixtures:
        natural_round = fixture_rounds[fixture.fixture_id]
        round_centre  = season_start + timedelta(
            days=natural_round * days_per_round + days_per_round / 2
        )
        window_days = window_rounds * days_per_round
        lo = round_centre - timedelta(days=window_days)
        hi = round_centre + timedelta(days=window_days)

        eligible[fixture.fixture_id] = [
            s.slot_id for s in slots if lo <= s.date <= hi
        ]

    return eligible


def log_filter_stats(eligible: dict[str, list[str]]) -> None:
    counts  = [len(v) for v in eligible.values()]
    total   = sum(counts)
    minimum = min(counts)
    maximum = max(counts)
    avg     = total / len(counts) if counts else 0
    print(f"[SlotFilter] {len(eligible)} fixtures | "
          f"eligible slots/fixture: min={minimum} avg={avg:.1f} max={maximum} | "
          f"total variables: {total:,}")
    if minimum == 0:
        starved = [fid for fid, v in eligible.items() if not v]
        print(f"[SlotFilter] WARNING: {len(starved)} fixtures have 0 eligible slots — "
              f"consider increasing window_rounds")
