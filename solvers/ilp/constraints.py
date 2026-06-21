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


def add_soft_sc14_season_boundary(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    penalty: int = 30,
) -> list[tuple[int, pulp.LpVariable]]:
    """SC14 — penalise HH or AA in the opening/closing 14-day boundary window."""
    from datetime import date as _date, timedelta

    calendar = load_calendar()
    season_start = _date.fromisoformat(calendar["start_date"])
    season_end = _date.fromisoformat(calendar["end_date"])
    window = timedelta(days=14)
    open_cutoff = season_start + window
    close_cutoff = season_end - window

    fsi = _fixture_slot_index(x, slots)
    penalty_vars: list[tuple[int, pulp.LpVariable]] = []
    counter = [0]

    for team_id in teams:
        for label, lo, hi in [
            ("open", season_start, open_cutoff),
            ("close", close_cutoff, season_end),
        ]:
            home_vars: list = []
            total_vars: list = []
            for fixture in fixtures:
                is_home = (fixture.home_team_id == team_id)
                is_away = (fixture.away_team_id == team_id)
                if not (is_home or is_away):
                    continue
                for sid, slot in fsi.get(fixture.fixture_id, []):
                    if lo <= slot.date <= hi:
                        v = x[(fixture.fixture_id, sid)]
                        total_vars.append(v)
                        if is_home:
                            home_vars.append(v)

            if len(total_vars) < 2 or not home_vars:
                continue

            counter[0] += 1
            k = counter[0]
            h_sum = pulp.lpSum(home_vars)
            n_home = len(home_vars)

            if n_home >= 2:
                hh = pulp.LpVariable(f"sc14_hh_{k}", cat="Binary")
                prob += h_sum >= 2 * hh
                prob += h_sum <= 1 + (n_home - 1) * hh
                penalty_vars.append((penalty, hh))

            aa = pulp.LpVariable(f"sc14_aa_{k}", cat="Binary")
            prob += h_sum <= n_home * (1 - aa)
            prob += aa >= 1 - h_sum
            penalty_vars.append((penalty, aa))

    return penalty_vars


def add_soft_sc15_boxing_day_nyd(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    penalty: int = 35,
) -> list[tuple[int, pulp.LpVariable]]:
    """SC15 — penalise matching H/A on Boxing Day and New Year's Day."""
    from datetime import date as _date

    calendar = load_calendar()
    yr = _date.fromisoformat(calendar["start_date"]).year
    boxing_day = _date(yr, 12, 26)
    nyd = _date(yr + 1, 1, 1)

    fsi = _fixture_slot_index(x, slots)
    penalty_vars: list[tuple[int, pulp.LpVariable]] = []

    for team_id in teams:
        home_bd = [
            x[(f.fixture_id, sid)]
            for f in fixtures if f.home_team_id == team_id
            for sid, slot in fsi.get(f.fixture_id, []) if slot.date == boxing_day
        ]
        home_nyd_v = [
            x[(f.fixture_id, sid)]
            for f in fixtures if f.home_team_id == team_id
            for sid, slot in fsi.get(f.fixture_id, []) if slot.date == nyd
        ]
        all_bd = [
            x[(f.fixture_id, sid)]
            for f in fixtures
            if f.home_team_id == team_id or f.away_team_id == team_id
            for sid, slot in fsi.get(f.fixture_id, []) if slot.date == boxing_day
        ]
        all_nyd = [
            x[(f.fixture_id, sid)]
            for f in fixtures
            if f.home_team_id == team_id or f.away_team_id == team_id
            for sid, slot in fsi.get(f.fixture_id, []) if slot.date == nyd
        ]

        if not all_bd or not all_nyd:
            continue

        h_bd_sum = pulp.lpSum(home_bd) if home_bd else 0
        h_nyd_sum = pulp.lpSum(home_nyd_v) if home_nyd_v else 0
        plays_bd_sum = pulp.lpSum(all_bd)
        plays_nyd_sum = pulp.lpSum(all_nyd)

        # HH: home on BD and home on NYD
        same_hh = pulp.LpVariable(f"sc15_hh_{team_id}", cat="Binary")
        prob += same_hh <= h_bd_sum
        prob += same_hh <= h_nyd_sum
        prob += same_hh >= h_bd_sum + h_nyd_sum - 1

        # AA: away on BD and away on NYD (plays both but not home on either)
        away_bd = plays_bd_sum - h_bd_sum
        away_nyd = plays_nyd_sum - h_nyd_sum
        same_aa = pulp.LpVariable(f"sc15_aa_{team_id}", cat="Binary")
        prob += same_aa <= away_bd
        prob += same_aa <= away_nyd
        prob += same_aa >= away_bd + away_nyd - 1

        penalty_vars.append((penalty, same_hh))
        penalty_vars.append((penalty, same_aa))

    return penalty_vars


def add_soft_min_sat_1500(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    min_per_team: int = 5,
    penalty: int = 10,
) -> list[tuple[int, pulp.LpVariable]]:
    """SC17 — penalise fewer than min_per_team Saturday 15:00 appearances per team."""
    fsi = _fixture_slot_index(x, slots)
    sat15_sids = {
        s.slot_id for s in slots
        if s.day_of_week == "Saturday" and s.kickoff == "15:00"
    }
    if not sat15_sids:
        return []

    penalty_vars: list[tuple[int, pulp.LpVariable]] = []
    for team_id in teams:
        team_vars = [
            x[(f.fixture_id, sid)]
            for f in fixtures
            if f.home_team_id == team_id or f.away_team_id == team_id
            for sid, _ in fsi.get(f.fixture_id, []) if sid in sat15_sids
        ]
        if len(team_vars) < min_per_team:
            continue

        deficit = pulp.LpVariable(f"sc17_def_{team_id}", lowBound=0)
        prob += deficit >= min_per_team - pulp.lpSum(team_vars)
        penalty_vars.append((penalty, deficit))

    return penalty_vars


def add_soft_min_monday(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    min_per_team: int = 3,
    penalty: int = 12,
) -> list[tuple[int, pulp.LpVariable]]:
    """SC18 — penalise fewer than min_per_team Monday appearances per team."""
    fsi = _fixture_slot_index(x, slots)
    mon_sids = {s.slot_id for s in slots if s.day_of_week == "Monday"}
    if not mon_sids:
        return []

    penalty_vars: list[tuple[int, pulp.LpVariable]] = []
    for team_id in teams:
        team_vars = [
            x[(f.fixture_id, sid)]
            for f in fixtures
            if f.home_team_id == team_id or f.away_team_id == team_id
            for sid, _ in fsi.get(f.fixture_id, []) if sid in mon_sids
        ]
        if len(team_vars) < min_per_team:
            continue

        deficit = pulp.LpVariable(f"sc18_def_{team_id}", lowBound=0)
        prob += deficit >= min_per_team - pulp.lpSum(team_vars)
        penalty_vars.append((penalty, deficit))

    return penalty_vars
