"""
EPL-specific constraint builders for the ILP (PuLP) solver.

Mirrors solvers/leagues/epl/cp_sat_helpers.py — these encode EPL-only rules
and Atos Golden Rule concepts that have no equivalent in other leagues, so
they live here rather than in solvers/ilp/constraints.py, which is shared
across all leagues and must stay league-agnostic.

Moved out of solvers/ilp/constraints.py with zero behaviour changes.
"""
from __future__ import annotations

from collections import defaultdict

import pulp

from core.models import Fixture, Slot, Team
from core.data_loader import load_city_groups, load_high_profile_derbies, load_calendar
from solvers.ilp.constraints import _fixture_slot_index


def add_no_same_city_home_clash(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
) -> None:
    """HC2 (demoted to SC7, kept for reference) — at most one home game per
    city per day."""
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
    """SC14 (Atos Golden Rule) — penalise HH or AA in the opening/closing
    14-day boundary window."""
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
    """SC15 (Atos Golden Rule) — penalise matching H/A on Boxing Day and New
    Year's Day."""
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

        same_hh = pulp.LpVariable(f"sc15_hh_{team_id}", cat="Binary")
        prob += same_hh <= h_bd_sum
        prob += same_hh <= h_nyd_sum
        prob += same_hh >= h_bd_sum + h_nyd_sum - 1

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


def add_soft_ha_window(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    window: int = 5,
    min_home: int = 2,
    max_home: int = 3,
    penalty: int = 25,
) -> list[tuple[int, pulp.LpVariable]]:
    """SC13 (Atos Golden Rule) — penalise home-game clusters within 35-day
    date windows."""
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
    window_days = 35
    max_away = window - min_home
    counter = [0]

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

            counter[0] += 1
            k = counter[0]

            if len(home_in_win) > max_home:
                slack = pulp.LpVariable(f"sc13h_{k}", lowBound=0)
                prob += slack >= pulp.lpSum(home_in_win) - max_home
                penalty_vars.append((penalty, slack))

            if len(away_in_win) > max_away:
                slack = pulp.LpVariable(f"sc13a_{k}", lowBound=0)
                prob += slack >= pulp.lpSum(away_in_win) - max_away
                penalty_vars.append((penalty, slack))

    return penalty_vars


def add_soft_same_city_home_clash(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    window_days: int = 4,
    penalty: int = 80,
) -> list[tuple[int, pulp.LpVariable]]:
    """SC7 — penalise same-city teams both at home within a 4-day window."""
    from datetime import timedelta

    city_groups = load_city_groups()
    fsi = _fixture_slot_index(x, slots)
    penalty_vars: list[tuple[int, pulp.LpVariable]] = []

    home_date_vars: dict = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            home_date_vars[fixture.home_team_id][slot.date].append(
                x[(fixture.fixture_id, sid)]
            )

    all_dates = sorted({slot.date for slot in slots})
    counter = [0]

    for city, members in city_groups.items():
        if len(members) < 2:
            continue
        for i, d in enumerate(all_dates):
            end_d = d + timedelta(days=window_days - 1)
            city_home: list = []
            for wd in all_dates[i:]:
                if wd > end_d:
                    break
                for team_id in members:
                    city_home.extend(home_date_vars[team_id].get(wd, []))

            if len(city_home) <= 1:
                continue

            counter[0] += 1
            slack = pulp.LpVariable(f"sc7_{counter[0]}", lowBound=0)
            prob += slack >= pulp.lpSum(city_home) - 1
            penalty_vars.append((penalty, slack))

    return penalty_vars


def add_soft_festive_coverage(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    penalty: int = 50,
) -> list[tuple[int, pulp.LpVariable]]:
    """SC9 — penalise missing team coverage on festive matchdays and Easter."""
    from datetime import date as _date

    calendar = load_calendar()
    fsi = _fixture_slot_index(x, slots)
    penalty_vars: list[tuple[int, pulp.LpVariable]] = []
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

    team_date_vars: dict = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            for team_id in (fixture.home_team_id, fixture.away_team_id):
                team_date_vars[team_id][slot.date].append(x[(fixture.fixture_id, sid)])

    counter = [0]
    for fest_date in active_festive:
        for team_id in all_team_ids:
            team_vars = team_date_vars[team_id].get(fest_date, [])
            if not team_vars:
                continue
            counter[0] += 1
            plays = pulp.LpVariable(f"sc9_plays_{counter[0]}", cat="Binary")
            prob += plays <= pulp.lpSum(team_vars)
            prob += plays * len(team_vars) >= pulp.lpSum(team_vars)
            not_plays = pulp.LpVariable(f"sc9_miss_{counter[0]}", cat="Binary")
            prob += not_plays == 1 - plays
            penalty_vars.append((penalty, not_plays))

    return penalty_vars


def add_soft_city_cluster(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    city_name: str = "London",
    max_per_day: int = 3,
    penalty: int = 30,
) -> list[tuple[int, pulp.LpVariable]]:
    """SC10 — penalise >max_per_day teams from city_name hosting on the same day."""
    city_groups = load_city_groups()
    cluster_teams = set(city_groups.get(city_name, []))
    if not cluster_teams:
        return []

    fsi = _fixture_slot_index(x, slots)
    penalty_vars: list[tuple[int, pulp.LpVariable]] = []

    home_date_vars: dict = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        if fixture.home_team_id in cluster_teams:
            for sid, slot in fsi.get(fixture.fixture_id, []):
                home_date_vars[fixture.home_team_id][slot.date].append(
                    x[(fixture.fixture_id, sid)]
                )

    all_dates = sorted({slot.date for slot in slots})
    counter = [0]
    for d in all_dates:
        day_vars: list = []
        for team_id in cluster_teams:
            day_vars.extend(home_date_vars[team_id].get(d, []))

        if len(day_vars) <= max_per_day:
            continue

        counter[0] += 1
        slack = pulp.LpVariable(f"sc10_{counter[0]}", lowBound=0)
        prob += slack >= pulp.lpSum(day_vars) - max_per_day
        penalty_vars.append((penalty, slack))

    return penalty_vars
