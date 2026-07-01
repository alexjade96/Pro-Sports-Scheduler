"""
Option B — ILP: Generic linear constraint builders for PuLP.

Decision variable layout (sparse):
    x[(fixture_id, slot_id)] ∈ {0, 1}  — only exists for eligible pairs
    = 1 if fixture fixture_id is assigned to slot slot_id

This module is shared across all leagues — it must not contain
league-specific business rules, hardcoded team/city names, or calendar
constants tuned to one league's season length. League-specific constraint
logic (Atos Golden Rules, Boxing Day, London cluster caps, etc.) lives in
``solvers/leagues/<league>/*_helpers.py`` instead.
"""
from __future__ import annotations

from collections import defaultdict

import pulp

from core.models import Fixture, Slot, Team
from core.data_loader import load_calendar


def _fixture_slot_index(x: dict, slots: list[Slot]) -> dict[str, list[tuple[str, Slot]]]:
    """Build {fixture_id: [(slot_id, Slot), ...]} from the sparse x dict."""
    slot_map: dict[str, Slot] = {s.slot_id: s for s in slots}
    idx: dict[str, list[tuple[str, Slot]]] = defaultdict(list)
    for fid, sid in x:
        idx[fid].append((sid, slot_map[sid]))
    return dict(idx)


# ---------------------------------------------------------------------------
# Hard constraints
# ---------------------------------------------------------------------------

def add_each_fixture_assigned_exactly_once(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
) -> None:
    """Every fixture is scheduled exactly once (from eligible slots)."""
    fsi = _fixture_slot_index(x, slots)
    for fixture in fixtures:
        eligible_vars = [x[(fixture.fixture_id, sid)] for sid, _ in fsi.get(fixture.fixture_id, [])]
        if eligible_vars:
            prob += (
                pulp.lpSum(eligible_vars) == 1,
                f"fixture_once_{fixture.fixture_id}",
            )


def add_team_plays_at_most_once_per_day(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
) -> None:
    """A team can appear in at most one fixture per calendar day."""
    fsi = _fixture_slot_index(x, slots)

    team_date_vars: dict[tuple[str, str], list] = defaultdict(list)
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            date_str = str(slot.date)
            for team_id in (fixture.home_team_id, fixture.away_team_id):
                team_date_vars[(team_id, date_str)].append(x[(fixture.fixture_id, sid)])

    for (team_id, date_str), vars_on_date in team_date_vars.items():
        if len(vars_on_date) >= 2:
            prob += (
                pulp.lpSum(vars_on_date) <= 1,
                f"one_game_per_day_{team_id}_{date_str}",
            )


def add_min_rest_days(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    min_days: int = 3,
) -> None:
    """Minimum days between consecutive team fixtures.

    Uses a sliding-window formulation: for each team and each date d, the
    sum of assignment variables for all (fixture, slot) pairs belonging to
    that team where the slot falls in [d, d + min_days - 1] must be ≤ 1.
    """
    from datetime import timedelta

    fsi = _fixture_slot_index(x, slots)

    team_date_vars: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            for team_id in (fixture.home_team_id, fixture.away_team_id):
                team_date_vars[team_id][slot.date].append((fixture.fixture_id, sid))

    for team_id, date_map in team_date_vars.items():
        all_dates = sorted(date_map.keys())
        for d in all_dates:
            window_vars = []
            for offset in range(min_days):
                wd = d + timedelta(days=offset)
                for fid, sid in date_map.get(wd, []):
                    window_vars.append(x[(fid, sid)])
            if len(window_vars) >= 2:
                prob += (
                    pulp.lpSum(window_vars) <= 1,
                    f"rest_window_{team_id}_{d}",
                )


def add_max_games_on_day(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    day_of_week: str,
    max_games: int,
) -> None:
    """Generic per-team cap on games on a specific day of week."""
    fsi = _fixture_slot_index(x, slots)
    target_sids = {s.slot_id for s in slots if s.day_of_week == day_of_week}
    if not target_sids:
        return
    for team_id in teams:
        team_vars = [
            x[(f.fixture_id, sid)]
            for f in fixtures
            if f.home_team_id == team_id or f.away_team_id == team_id
            for sid, _ in fsi.get(f.fixture_id, [])
            if sid in target_sids
        ]
        if len(team_vars) > max_games:
            prob += (
                pulp.lpSum(team_vars) <= max_games,
                f"max_{day_of_week.lower()}_{team_id}",
            )


def add_max_midweek_games(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_games: int = 10,
) -> None:
    """Each team plays at most max_games games on Tuesday or Wednesday combined."""
    fsi = _fixture_slot_index(x, slots)
    mw_sids = {s.slot_id for s in slots if s.day_of_week in ("Tuesday", "Wednesday")}
    if not mw_sids:
        return
    for team_id in teams:
        team_vars = [
            x[(f.fixture_id, sid)]
            for f in fixtures
            if f.home_team_id == team_id or f.away_team_id == team_id
            for sid, _ in fsi.get(f.fixture_id, [])
            if sid in mw_sids
        ]
        if len(team_vars) > max_games:
            prob += (
                pulp.lpSum(team_vars) <= max_games,
                f"max_midweek_{team_id}",
            )


# ---------------------------------------------------------------------------
# Soft constraints (slack variables penalised in objective)
# ---------------------------------------------------------------------------

def add_soft_max_consecutive_home_away(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_run: int = 5,
    penalty: int = 15,
    window_days: int = 42,
) -> list[tuple[int, pulp.LpVariable]]:
    """Penalise >max_run same-type games in a window_days-wide date window.

    window_days should scale with the league's game cadence (the default of
    42 assumes roughly weekly fixtures); pass an explicit value for leagues
    with a denser or sparser schedule.
    """
    from datetime import timedelta

    fsi = _fixture_slot_index(x, slots)
    penalty_vars: list[tuple[int, pulp.LpVariable]] = []

    team_home_date: dict = defaultdict(lambda: defaultdict(list))
    team_away_date: dict = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            team_home_date[fixture.home_team_id][slot.date].append(
                x[(fixture.fixture_id, sid)]
            )
            team_away_date[fixture.away_team_id][slot.date].append(
                x[(fixture.fixture_id, sid)]
            )

    all_dates = sorted({slot.date for slot in slots})
    counter = [0]

    for team_id in teams:
        home_by_date = team_home_date[team_id]
        away_by_date = team_away_date[team_id]

        for i, d in enumerate(all_dates):
            end_d = d + timedelta(days=window_days)
            home_in_win: list = []
            away_in_win: list = []
            for wd in all_dates[i:]:
                if wd > end_d:
                    break
                home_in_win.extend(home_by_date.get(wd, []))
                away_in_win.extend(away_by_date.get(wd, []))

            counter[0] += 1
            k = counter[0]

            if len(home_in_win) > max_run:
                slack = pulp.LpVariable(f"sc2h_{k}", lowBound=0)
                prob += slack >= pulp.lpSum(home_in_win) - max_run
                penalty_vars.append((penalty, slack))

            if len(away_in_win) > max_run:
                slack = pulp.LpVariable(f"sc1a_{k}", lowBound=0)
                prob += slack >= pulp.lpSum(away_in_win) - max_run
                penalty_vars.append((penalty, slack))

    return penalty_vars


def add_soft_half_season_balance(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    tolerance: int = 2,
    penalty: int = 15,
) -> list[tuple[int, pulp.LpVariable]]:
    """Penalise unequal home/away distribution per half-season.

    Splits the season at the calendar midpoint. Each team's target H1 home
    count is computed dynamically as half of that team's eligible H1 fixture
    count — NOT a fixed league-wide constant — so this scales correctly
    regardless of season length.
    """
    from datetime import date as _date

    calendar = load_calendar()
    season_start = _date.fromisoformat(calendar["start_date"])
    season_end   = _date.fromisoformat(calendar["end_date"])
    midpoint     = _date.fromordinal(
        (season_start.toordinal() + season_end.toordinal()) // 2
    )

    fsi = _fixture_slot_index(x, slots)
    penalty_vars: list[tuple[int, pulp.LpVariable]] = []

    for team_id in teams:
        home_h1: list = []
        for fixture in fixtures:
            if fixture.home_team_id != team_id:
                continue
            for sid, slot in fsi.get(fixture.fixture_id, []):
                if slot.date <= midpoint:
                    home_h1.append(x[(fixture.fixture_id, sid)])

        if not home_h1:
            continue

        n      = len(home_h1)
        h1s    = pulp.lpSum(home_h1)
        target = n // 2
        lo     = max(0, target - tolerance)
        hi     = min(n, target + tolerance)

        ub = pulp.LpVariable(f"sc5_ub_{team_id}", lowBound=0)
        prob += ub >= h1s - hi
        penalty_vars.append((penalty, ub))

        lb = pulp.LpVariable(f"sc5_lb_{team_id}", lowBound=0)
        prob += lb >= lo - h1s
        penalty_vars.append((penalty, lb))

    return penalty_vars
