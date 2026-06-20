"""
Option A — CP-SAT: Constraint builder modules.

Decision variable layout (sparse):
    x[(fixture_id, slot_id)] ∈ {0, 1}  — only exists for eligible pairs
    = 1 if fixture fixture_id is assigned to slot slot_id

All functions must guard against missing keys; use `if (fid, sid) in x`.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ortools.sat.python import cp_model

from core.models import Fixture, Slot, Team
from core.data_loader import load_city_groups, load_high_profile_derbies, load_calendar


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
    """HC4 — every fixture gets exactly one slot (from its eligible set)."""
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
    """HC5 — a team cannot appear in two fixtures on the same calendar day."""
    fsi = _fixture_slot_index(x, slots)

    # Group eligible (fixture, slot) by team and date
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
    """HC1 — minimum gap between consecutive fixtures for each team.

    Sliding-window formulation: for each team and each date d, at most one
    assignment variable may be active in the window [d, d + min_days - 1].
    Produces O(teams × dates) ≈ 7,460 constraints vs the naive O(pairs²) ≈ 563K.
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


def add_no_same_city_home_clash(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
) -> None:
    """HC2 — no two same-city teams can have home games on the same day."""
    city_groups = load_city_groups()
    fsi = _fixture_slot_index(x, slots)

    # Build {team_id: {date_str: [vars]}} for home fixtures only
    home_date_vars: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            home_date_vars[fixture.home_team_id][str(slot.date)].append(
                x[(fixture.fixture_id, sid)]
            )

    for city, members in city_groups.items():
        if len(members) < 2:
            continue
        # Collect all dates where any city member has eligible home slots
        all_dates: set[str] = set()
        for team_id in members:
            all_dates |= home_date_vars[team_id].keys()
        for date_str in all_dates:
            city_vars = []
            for team_id in members:
                city_vars.extend(home_date_vars[team_id].get(date_str, []))
            if len(city_vars) >= 2:
                model.add(sum(city_vars) <= 1)


def add_max_midweek_games_per_team(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_midweek: int = 10,
) -> None:
    """HC10 — each team plays at most max_midweek games on Tuesday or Wednesday."""
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
    """HC11 — each team plays at most max_monday games on Monday."""
    add_max_single_day_games_per_team(model, x, fixtures, slots, teams, "Monday", max_monday)


def add_max_friday_games_per_team(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_friday: int = 3,
) -> None:
    """HC9 — each team plays at most max_friday games on a Friday."""
    add_max_single_day_games_per_team(model, x, fixtures, slots, teams, "Friday", max_friday)


def add_max_wednesday_games_per_team(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_wednesday: int = 6,
) -> None:
    """HC12 — each team plays at most max_wednesday games on Wednesday."""
    add_max_single_day_games_per_team(model, x, fixtures, slots, teams, "Wednesday", max_wednesday)


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
    penalty: int = 20,
) -> list:
    """SC1/SC2 — penalise runs of more than max_run consecutive home or away.
    Skeleton; full run-tracking requires auxiliary sequencing variables."""
    return []


def add_soft_ha_window(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    window: int = 5,
    min_home: int = 2,
    max_home: int = 3,
    penalty: int = 25,
) -> list:
    """SC13 (Atos Golden Rule) — penalise home-game clusters within date windows.

    For each team and each rolling date window, penalise excess home or away
    games assigned within that span.

    The primary constraint for SC13 correctness is the slot filter in
    build_eligible_slots (window_rounds=2): by limiting each fixture to dates
    within ±2 natural rounds, the solver cannot reorder fixtures by more than
    2 rounds, keeping consecutive-fixture H/A patterns close to the natural
    ordering (which has 0 SC13 violations by construction from generate_epl.py).

    This date-window penalty acts as a secondary soft push to discourage the
    residual reordering violations that can still occur within the ±2 window.
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
    if not all_dates:
        return penalty_terms

    # 35-day window: covers ~5 EPL fixtures in normal periods.
    # The slot filter (window_rounds=2) prevents fixtures from spanning
    # more than ±2 rounds (~15 days) from their natural position, so 5
    # consecutive games now fit comfortably within 35 days in most cases.
    window_days = 35

    for team_id in teams:
        home_by_date = team_home_date[team_id]
        away_by_date = team_away_date[team_id]

        for d in all_dates:
            end_d = d + timedelta(days=window_days)

            home_in_win: list = []
            away_in_win: list = []
            for wd in all_dates:
                if wd < d or wd > end_d:
                    continue
                home_in_win.extend(home_by_date.get(wd, []))
                away_in_win.extend(away_by_date.get(wd, []))

            if len(home_in_win) > max_home:
                hc = model.new_int_var(0, len(home_in_win), f"hc_{team_id}_{d}")
                model.add(hc == sum(home_in_win))
                exc = model.new_int_var(0, len(home_in_win) - max_home,
                                        f"exc_{team_id}_{d}")
                model.add(exc >= hc - max_home)
                penalty_terms.append((penalty, exc))

            max_away = window - min_home
            if len(away_in_win) > max_away:
                ac = model.new_int_var(0, len(away_in_win), f"ac_{team_id}_{d}")
                model.add(ac == sum(away_in_win))
                dfc = model.new_int_var(0, len(away_in_win) - max_away,
                                        f"dfc_{team_id}_{d}")
                model.add(dfc >= ac - max_away)
                penalty_terms.append((penalty, dfc))

    return penalty_terms


def add_soft_same_city_home_clash(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    window_days: int = 4,
    penalty: int = 40,
) -> list:
    """SC7 — penalise same-city teams both at home within a window_days window.

    HC2 was demoted to SC7 because 14–34 same-city home clashes occur per
    real EPL season, making a hard ban infeasible. The 4-day window captures
    the matchday-weekend planning horizon used by police.
    """
    city_groups = load_city_groups()
    fsi = _fixture_slot_index(x, slots)
    penalty_terms = []

    home_date_vars: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            home_date_vars[fixture.home_team_id][slot.date].append(
                x[(fixture.fixture_id, sid)]
            )

    all_dates = sorted({slot.date for slot in slots})
    if not all_dates:
        return penalty_terms

    for city, members in city_groups.items():
        if len(members) < 2:
            continue
        for i, d in enumerate(all_dates):
            end_d = d + timedelta(days=window_days - 1)
            city_home_in_win: list = []
            for wd in all_dates[i:]:
                if wd > end_d:
                    break
                for team_id in members:
                    city_home_in_win.extend(home_date_vars[team_id].get(wd, []))

            if len(city_home_in_win) <= 1:
                continue

            hc = model.new_int_var(0, len(city_home_in_win), f"sc7h_{city}_{d}")
            model.add(hc == sum(city_home_in_win))
            exc = model.new_int_var(0, len(city_home_in_win) - 1, f"sc7e_{city}_{d}")
            model.add(exc >= hc - 1)
            penalty_terms.append((penalty, exc))

    return penalty_terms


def add_soft_derby_gap(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    min_gap_days: int = 56,
    penalty: int = 30,
) -> list:
    """SC3 — penalise derby legs scheduled fewer than min_gap_days apart."""
    derbies = load_high_profile_derbies()
    fsi = _fixture_slot_index(x, slots)
    penalty_terms = []

    for team_a, team_b in derbies:
        leg1 = next(
            (f for f in fixtures if f.home_team_id == team_a and f.away_team_id == team_b), None
        )
        leg2 = next(
            (f for f in fixtures if f.home_team_id == team_b and f.away_team_id == team_a), None
        )
        if not (leg1 and leg2):
            continue

        for sid1, s1 in fsi.get(leg1.fixture_id, []):
            for sid2, s2 in fsi.get(leg2.fixture_id, []):
                gap = abs((s2.date - s1.date).days)
                if 0 < gap < min_gap_days:
                    viol = model.new_bool_var(
                        f"derby_{leg1.fixture_id}_{leg2.fixture_id}_{sid1}_{sid2}"
                    )
                    model.add_bool_and([
                        x[(leg1.fixture_id, sid1)],
                        x[(leg2.fixture_id, sid2)],
                    ]).only_enforce_if(viol)
                    penalty_terms.append((penalty, viol))

    return penalty_terms


def add_soft_london_cluster(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    max_per_day: int = 3,
    penalty: int = 30,
) -> list:
    """SC10 — penalise >max_per_day London teams hosting on the same calendar day."""
    city_groups = load_city_groups()
    london_teams = set(city_groups.get("London", []))
    if not london_teams:
        return []

    fsi = _fixture_slot_index(x, slots)
    penalty_terms = []

    home_date_vars: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        if fixture.home_team_id in london_teams:
            for sid, slot in fsi.get(fixture.fixture_id, []):
                home_date_vars[fixture.home_team_id][slot.date].append(
                    x[(fixture.fixture_id, sid)]
                )

    all_dates = sorted({slot.date for slot in slots})
    for d in all_dates:
        day_vars = []
        for team_id in london_teams:
            day_vars.extend(home_date_vars[team_id].get(d, []))

        if len(day_vars) <= max_per_day:
            continue

        total = model.new_int_var(0, len(day_vars), f"sc10_{d}")
        model.add(total == sum(day_vars))
        exc = model.new_int_var(0, len(day_vars) - max_per_day, f"sc10e_{d}")
        model.add(exc >= total - max_per_day)
        penalty_terms.append((penalty, exc))

    return penalty_terms


def add_soft_festive_coverage(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    penalty: int = 20,
) -> list:
    """SC9/PR2 — penalise missing team coverage on Boxing Day, Dec 28, Good Friday, Easter Monday.

    Only fires for festive dates that actually have eligible slots in the pool.
    NYD (Jan 1) is currently a Thursday with no slots defined — it will be
    skipped here until Thursday slots are added to calendar.json.
    """
    from datetime import date as _date

    fsi = _fixture_slot_index(x, slots)
    calendar = load_calendar()
    penalty_terms = []
    all_team_ids = set(teams.keys())

    festive_dates: set[_date] = set()
    for d in calendar.get("festive_matchdays", []):
        festive_dates.add(_date.fromisoformat(d))
    easter = calendar.get("easter_matchdays", {})
    for key in ("good_friday", "easter_monday"):
        if key in easter:
            festive_dates.add(_date.fromisoformat(easter[key]))

    slot_dates = {slot.date for slot in slots}
    active_festive = sorted(festive_dates & slot_dates)

    for fest_date in active_festive:
        for team_id in all_team_ids:
            team_vars = [
                x[(f.fixture_id, sid)]
                for f in fixtures
                if f.home_team_id == team_id or f.away_team_id == team_id
                for sid, slot in fsi.get(f.fixture_id, [])
                if slot.date == fest_date
            ]
            if not team_vars:
                continue
            plays = model.new_bool_var(f"fst_{team_id}_{fest_date}")
            model.add_max_equality(plays, team_vars)
            penalty_terms.append((penalty, plays.Not()))

    return penalty_terms
