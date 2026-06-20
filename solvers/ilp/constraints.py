"""
Option B — ILP: Linear constraint builders for PuLP.

Decision variable layout (sparse):
    x[(fixture_id, slot_id)] ∈ {0, 1}  — only exists for eligible pairs
    = 1 if fixture fixture_id is assigned to slot slot_id
"""
from __future__ import annotations

from collections import defaultdict

import pulp

from core.models import Fixture, Slot, Team
from core.data_loader import load_city_groups, load_high_profile_derbies


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
    """HC4 — every fixture is scheduled exactly once (from eligible slots)."""
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
    """HC5 — a team can appear in at most one fixture per calendar day."""
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
    """HC1 — minimum days between consecutive team fixtures.

    Uses a sliding-window formulation: for each team and each date d, the
    sum of assignment variables for all (fixture, slot) pairs belonging to
    that team where the slot falls in [d, d + min_days - 1] must be ≤ 1.

    This produces O(teams × dates) ≈ 7,460 constraints instead of the
    O(fixture_pairs × slot_pairs) ≈ 563K from the naive pairwise approach,
    making CBC tractable within a 90-second time limit.
    """
    from datetime import timedelta

    fsi = _fixture_slot_index(x, slots)

    # Build {team_id: {date: [(fixture_id, slot_id), ...]}}
    team_date_vars: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            for team_id in (fixture.home_team_id, fixture.away_team_id):
                team_date_vars[team_id][slot.date].append((fixture.fixture_id, sid))

    for team_id, date_map in team_date_vars.items():
        all_dates = sorted(date_map.keys())
        for d in all_dates:
            # Gather vars for this team in the window [d, d + min_days - 1]
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


def add_no_same_city_home_clash(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
) -> None:
    """HC2 — at most one home game per city per day."""
    city_groups = load_city_groups()
    fsi = _fixture_slot_index(x, slots)

    home_date_vars: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            home_date_vars[fixture.home_team_id][str(slot.date)].append(
                x[(fixture.fixture_id, sid)]
            )

    for city, members in city_groups.items():
        if len(members) < 2:
            continue
        all_dates: set[str] = set()
        for team_id in members:
            all_dates |= home_date_vars[team_id].keys()
        for date_str in all_dates:
            city_vars = []
            for team_id in members:
                city_vars.extend(home_date_vars[team_id].get(date_str, []))
            if len(city_vars) >= 2:
                prob += (
                    pulp.lpSum(city_vars) <= 1,
                    f"city_clash_{city}_{date_str}",
                )


def add_max_thursday_games_per_team(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_thursday: int = 2,
) -> None:
    """HC13 — each team plays at most max_thursday games on Thursday."""
    fsi = _fixture_slot_index(x, slots)
    team_thu_vars: dict[str, list] = defaultdict(list)
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            if slot.day_of_week == "Thursday":
                for team_id in (fixture.home_team_id, fixture.away_team_id):
                    team_thu_vars[team_id].append(x[(fixture.fixture_id, sid)])
    for team_id, thu_vars in team_thu_vars.items():
        if len(thu_vars) > max_thursday:
            prob += (
                pulp.lpSum(thu_vars) <= max_thursday,
                f"max_thu_{team_id}",
            )


# ---------------------------------------------------------------------------
# Soft constraints (slack variables penalised in objective)
# ---------------------------------------------------------------------------

def add_soft_derby_gap(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    min_gap_days: int = 56,
    penalty: int = 30,
) -> list[tuple[int, pulp.LpVariable]]:
    """SC3 — penalise derby legs too close together."""
    derbies = load_high_profile_derbies()
    fsi = _fixture_slot_index(x, slots)
    penalty_vars: list[tuple[int, pulp.LpVariable]] = []
    pair_count = 0

    for team_a, team_b in derbies:
        leg1 = next((f for f in fixtures if f.home_team_id == team_a and f.away_team_id == team_b), None)
        leg2 = next((f for f in fixtures if f.home_team_id == team_b and f.away_team_id == team_a), None)
        if not (leg1 and leg2):
            continue
        for sid1, s1 in fsi.get(leg1.fixture_id, []):
            for sid2, s2 in fsi.get(leg2.fixture_id, []):
                gap = abs((s2.date - s1.date).days)
                if 0 < gap < min_gap_days:
                    slack = pulp.LpVariable(
                        f"derby_slack_{pair_count}", cat="Binary"
                    )
                    pair_count += 1
                    prob += x[(leg1.fixture_id, sid1)] + x[(leg2.fixture_id, sid2)] <= 1 + slack
                    penalty_vars.append((penalty, slack))

    return penalty_vars
