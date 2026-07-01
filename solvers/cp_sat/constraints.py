"""
Option A — CP-SAT: Generic constraint builder modules.

Decision variable layout (sparse):
    x[(fixture_id, slot_id)] ∈ {0, 1}  — only exists for eligible pairs
    = 1 if fixture fixture_id is assigned to slot slot_id

All functions must guard against missing keys; use `if (fid, sid) in x`.

This module is shared across all leagues — it must not contain
league-specific business rules, hardcoded team/city names, or calendar
constants tuned to one league's season length. League-specific constraint
logic (Atos Golden Rules, Boxing Day, London cluster caps, etc.) lives in
``solvers/leagues/<league>/*_helpers.py`` instead.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ortools.sat.python import cp_model

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
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
) -> None:
    """Every fixture gets exactly one slot (from its eligible set)."""
    fsi = _fixture_slot_index(x, slots)
    for fixture in fixtures:
        eligible_vars = [x[(fixture.fixture_id, sid)] for sid, _ in fsi.get(fixture.fixture_id, [])]
        if eligible_vars:
            model.add_exactly_one(eligible_vars)


def add_team_plays_at_most_once_per_slot(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
) -> None:
    """A team cannot appear in two fixtures on the same calendar day."""
    fsi = _fixture_slot_index(x, slots)

    team_date_vars: dict[tuple[str, str], list] = defaultdict(list)
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            date_str = str(slot.date)
            for team_id in (fixture.home_team_id, fixture.away_team_id):
                team_date_vars[(team_id, date_str)].append(x[(fixture.fixture_id, sid)])

    for vars_on_date in team_date_vars.values():
        if len(vars_on_date) >= 2:
            model.add(sum(vars_on_date) <= 1)


def add_min_rest_days(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    min_days: int = 3,
) -> None:
    """Minimum gap between consecutive fixtures for each team.

    Sliding-window formulation: for each team and each date d, at most one
    assignment variable may be active in the window [d, d + min_days - 1].
    Produces O(teams × dates) constraints vs the naive O(pairs²).
    """
    fsi = _fixture_slot_index(x, slots)

    team_date_vars: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            for team_id in (fixture.home_team_id, fixture.away_team_id):
                team_date_vars[team_id][slot.date].append((fixture.fixture_id, sid))

    for team_id, date_map in team_date_vars.items():
        for d in sorted(date_map.keys()):
            window_vars = []
            for offset in range(min_days):
                wd = d + timedelta(days=offset)
                for fid, sid in date_map.get(wd, []):
                    window_vars.append(x[(fid, sid)])
            if len(window_vars) >= 2:
                model.add(sum(window_vars) <= 1)


def add_max_midweek_games_per_team(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_midweek: int = 10,
) -> None:
    """Each team plays at most max_midweek games on Tuesday or Wednesday combined."""
    fsi = _fixture_slot_index(x, slots)
    midweek_sids = {s.slot_id for s in slots if s.day_of_week in ("Tuesday", "Wednesday")}

    for team_id in teams:
        mw_vars = []
        for fixture in fixtures:
            if fixture.home_team_id != team_id and fixture.away_team_id != team_id:
                continue
            for sid, _ in fsi.get(fixture.fixture_id, []):
                if sid in midweek_sids:
                    mw_vars.append(x[(fixture.fixture_id, sid)])
        if len(mw_vars) > max_midweek:
            model.add(sum(mw_vars) <= max_midweek)


def add_max_single_day_games_per_team(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    day_of_week: str,
    max_games: int,
) -> None:
    """Generic per-team cap on games falling on a specific day of week."""
    fsi = _fixture_slot_index(x, slots)
    target_sids = {s.slot_id for s in slots if s.day_of_week == day_of_week}

    for team_id in teams:
        team_vars = []
        for fixture in fixtures:
            if fixture.home_team_id != team_id and fixture.away_team_id != team_id:
                continue
            for sid, _ in fsi.get(fixture.fixture_id, []):
                if sid in target_sids:
                    team_vars.append(x[(fixture.fixture_id, sid)])
        if len(team_vars) > max_games:
            model.add(sum(team_vars) <= max_games)


def add_max_monday_games_per_team(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_monday: int = 3,
) -> None:
    """Each team plays at most max_monday games on Monday."""
    add_max_single_day_games_per_team(model, x, fixtures, slots, teams, "Monday", max_monday)


def add_max_friday_games_per_team(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_friday: int = 3,
) -> None:
    """Each team plays at most max_friday games on a Friday."""
    add_max_single_day_games_per_team(model, x, fixtures, slots, teams, "Friday", max_friday)


def add_max_wednesday_games_per_team(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_wednesday: int = 6,
) -> None:
    """Each team plays at most max_wednesday games on Wednesday."""
    add_max_single_day_games_per_team(model, x, fixtures, slots, teams, "Wednesday", max_wednesday)


def add_max_thursday_games_per_team(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_thursday: int = 2,
) -> None:
    """Each team plays at most max_thursday games on Thursday."""
    add_max_single_day_games_per_team(model, x, fixtures, slots, teams, "Thursday", max_thursday)


# ---------------------------------------------------------------------------
# Soft constraints (returned as (weight, bool_var) penalty terms)
# ---------------------------------------------------------------------------

def add_soft_max_consecutive_home_away(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_run: int = 3,
    penalty: int = 15,
    window_days: int = 42,
) -> list:
    """Penalise runs of more than max_run consecutive home or away games.

    Uses a window_days-wide sliding window. If a team has more than max_run
    home fixtures assigned within that window the excess is penalised; same
    for away. window_days should scale with the league's game cadence (the
    default of 42 assumes roughly weekly fixtures); pass an explicit value
    for leagues with a denser or sparser schedule.
    """
    fsi = _fixture_slot_index(x, slots)
    penalty_terms = []

    team_home_date: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    team_away_date: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            team_home_date[fixture.home_team_id][slot.date].append(
                x[(fixture.fixture_id, sid)]
            )
            team_away_date[fixture.away_team_id][slot.date].append(
                x[(fixture.fixture_id, sid)]
            )

    all_dates = sorted({slot.date for slot in slots})

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

            if len(home_in_win) > max_run:
                hc = model.new_int_var(
                    0, len(home_in_win), f"sc2h_{team_id}_{d}"
                )
                model.add(hc == sum(home_in_win))
                exc = model.new_int_var(
                    0, len(home_in_win) - max_run, f"sc2he_{team_id}_{d}"
                )
                model.add(exc >= hc - max_run)
                penalty_terms.append((penalty, exc))

            if len(away_in_win) > max_run:
                ac = model.new_int_var(
                    0, len(away_in_win), f"sc1a_{team_id}_{d}"
                )
                model.add(ac == sum(away_in_win))
                exc = model.new_int_var(
                    0, len(away_in_win) - max_run, f"sc1ae_{team_id}_{d}"
                )
                model.add(exc >= ac - max_run)
                penalty_terms.append((penalty, exc))

    return penalty_terms


def add_soft_half_season_balance(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    tolerance: int = 2,
    penalty: int = 15,
) -> list:
    """Penalise unequal home/away distribution per half-season.

    Splits the season at the calendar midpoint. Each team's target H1 home
    count is computed dynamically as half of that team's eligible H1 fixture
    count — NOT a fixed league-wide constant — so this scales correctly
    regardless of season length (EPL's 19-game half, NFL's ~9-game half,
    NBA's 41-game half all work without a per-league override).
    """
    from datetime import date as _date

    calendar = load_calendar()
    season_start = _date.fromisoformat(calendar["start_date"])
    season_end   = _date.fromisoformat(calendar["end_date"])
    midpoint     = _date.fromordinal(
        (season_start.toordinal() + season_end.toordinal()) // 2
    )

    fsi = _fixture_slot_index(x, slots)
    penalty_terms = []

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
        h1s    = sum(home_h1)
        target = n // 2
        lo     = max(0, target - tolerance)
        hi     = min(n, target + tolerance)

        ub_exc = model.new_int_var(0, n, f"sc5_ub_{team_id}")
        model.add(ub_exc >= h1s - hi)
        penalty_terms.append((penalty, ub_exc))

        lb_exc = model.new_int_var(0, n, f"sc5_lb_{team_id}")
        model.add(lb_exc >= lo - h1s)
        penalty_terms.append((penalty, lb_exc))

    return penalty_terms
