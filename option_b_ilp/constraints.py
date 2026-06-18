"""
Option B — ILP: Linear constraint builders for PuLP.

Decision variable layout (same as CP-SAT):
    x[(fixture_id, slot_id)] ∈ {0, 1}

Each function mutates the PuLP problem `prob` by adding LpConstraints.
Soft constraints are expressed by adding slack/surplus variables whose
values are penalised in the objective.
"""
import pulp

from scheduler.models import Fixture, Slot, Team
from scheduler.data_loader import load_city_groups, load_high_profile_derbies


# ---------------------------------------------------------------------------
# Hard constraints
# ---------------------------------------------------------------------------

def add_each_fixture_assigned_exactly_once(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
) -> None:
    """HC4 — every fixture is scheduled exactly once."""
    for fixture in fixtures:
        prob += (
            pulp.lpSum(x[(fixture.fixture_id, slot.slot_id)] for slot in slots) == 1,
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
    slots_by_date: dict[str, list[Slot]] = {}
    for slot in slots:
        slots_by_date.setdefault(str(slot.date), []).append(slot)

    for team_id in teams:
        team_fixtures = [
            f for f in fixtures
            if f.home_team_id == team_id or f.away_team_id == team_id
        ]
        for date_str, date_slots in slots_by_date.items():
            date_slot_ids = {s.slot_id for s in date_slots}
            vars_on_date = [
                x[(f.fixture_id, sid)]
                for f in team_fixtures
                for sid in date_slot_ids
                if (f.fixture_id, sid) in x
            ]
            if vars_on_date:
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
    """HC1 — minimum days between consecutive team fixtures."""
    for team_id in teams:
        team_fixtures = [
            f for f in fixtures
            if f.home_team_id == team_id or f.away_team_id == team_id
        ]
        for i, f1 in enumerate(team_fixtures):
            for f2 in team_fixtures[i+1:]:
                for s1 in slots:
                    for s2 in slots:
                        gap = abs((s2.date - s1.date).days)
                        if 0 < gap < min_days:
                            if (f1.fixture_id, s1.slot_id) in x and (f2.fixture_id, s2.slot_id) in x:
                                prob += (
                                    x[(f1.fixture_id, s1.slot_id)] + x[(f2.fixture_id, s2.slot_id)] <= 1,
                                    f"rest_{team_id}_{f1.fixture_id}_{s1.slot_id}_{f2.fixture_id}_{s2.slot_id}",
                                )


def add_no_same_city_home_clash(
    prob: pulp.LpProblem,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
) -> None:
    """HC2 — at most one home game per city per day."""
    city_groups = load_city_groups()
    slots_by_date: dict[str, list[Slot]] = {}
    for slot in slots:
        slots_by_date.setdefault(str(slot.date), []).append(slot)

    for city, members in city_groups.items():
        if len(members) < 2:
            continue
        for date_str, date_slots in slots_by_date.items():
            date_slot_ids = {s.slot_id for s in date_slots}
            home_vars = []
            for team_id in members:
                home_fixtures = [f for f in fixtures if f.home_team_id == team_id]
                for f in home_fixtures:
                    for sid in date_slot_ids:
                        if (f.fixture_id, sid) in x:
                            home_vars.append(x[(f.fixture_id, sid)])
            if len(home_vars) >= 2:
                prob += (
                    pulp.lpSum(home_vars) <= 1,
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
    penalty_vars: list[tuple[int, pulp.LpVariable]] = []
    pair_count = 0

    for team_a, team_b in derbies:
        leg1 = next((f for f in fixtures if f.home_team_id == team_a and f.away_team_id == team_b), None)
        leg2 = next((f for f in fixtures if f.home_team_id == team_b and f.away_team_id == team_a), None)
        if not (leg1 and leg2):
            continue
        for s1 in slots:
            for s2 in slots:
                gap = abs((s2.date - s1.date).days)
                if 0 < gap < min_gap_days:
                    if (leg1.fixture_id, s1.slot_id) in x and (leg2.fixture_id, s2.slot_id) in x:
                        slack = pulp.LpVariable(
                            f"derby_gap_slack_{pair_count}_{s1.slot_id}_{s2.slot_id}",
                            cat="Binary"
                        )
                        pair_count += 1
                        # slack = 1 forces both vars to ≤ 1 together (big-M not needed for binary)
                        prob += x[(leg1.fixture_id, s1.slot_id)] + x[(leg2.fixture_id, s2.slot_id)] <= 1 + slack
                        penalty_vars.append((penalty, slack))

    return penalty_vars
