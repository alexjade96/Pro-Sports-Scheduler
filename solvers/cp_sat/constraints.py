"""
Option A — CP-SAT: Constraint builder modules.

Each function receives the CP-SAT model, the decision variable dict,
and relevant data, then adds the appropriate constraints.

Decision variable layout:
    x[(fixture_id, slot_id)] ∈ {0, 1}
    = 1 if fixture fixture_id is assigned to slot slot_id
"""
from ortools.sat.python import cp_model

from core.models import Fixture, Slot, Team
from core.data_loader import load_constraints, load_city_groups, load_high_profile_derbies


# ---------------------------------------------------------------------------
# Hard constraints
# ---------------------------------------------------------------------------

def add_each_fixture_assigned_exactly_once(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
) -> None:
    """HC4 / HC5 — every fixture gets exactly one slot."""
    for fixture in fixtures:
        model.add_exactly_one(
            x[(fixture.fixture_id, slot.slot_id)]
            for slot in slots
        )


def add_team_plays_at_most_once_per_slot(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
) -> None:
    """HC5 — a team cannot appear in two fixtures on the same slot."""
    slot_ids_by_date: dict[str, list[str]] = {}
    for slot in slots:
        slot_ids_by_date.setdefault(str(slot.date), []).append(slot.slot_id)

    for team_id in teams:
        for date_str, date_slot_ids in slot_ids_by_date.items():
            team_fixtures_on_date = [
                f for f in fixtures
                if f.home_team_id == team_id or f.away_team_id == team_id
            ]
            slot_set = set(date_slot_ids)
            vars_on_date = [
                x[(f.fixture_id, s)]
                for f in team_fixtures_on_date
                for s in date_slot_ids
                if (f.fixture_id, s) in x
            ]
            if vars_on_date:
                model.add(sum(vars_on_date) <= 1)


def add_min_rest_days(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    min_days: int = 3,
) -> None:
    """HC1 — minimum gap between consecutive fixtures for each team."""
    slot_list = sorted(slots, key=lambda s: (s.date, s.kickoff))
    slot_index = {s.slot_id: i for i, s in enumerate(slot_list)}

    for team_id in teams:
        team_fixtures = [
            f for f in fixtures
            if f.home_team_id == team_id or f.away_team_id == team_id
        ]
        # For every pair of fixtures, forbid slot combos that are too close
        for i, f1 in enumerate(team_fixtures):
            for f2 in team_fixtures[i+1:]:
                for s1 in slots:
                    for s2 in slots:
                        gap = abs((s2.date - s1.date).days)
                        if 0 < gap < min_days:
                            # Cannot both be assigned these slots
                            model.add(
                                x[(f1.fixture_id, s1.slot_id)] +
                                x[(f2.fixture_id, s2.slot_id)] <= 1
                            )


def add_no_same_city_home_clash(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
) -> None:
    """HC2 — no two same-city teams can have home games on the same day."""
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
                model.add(sum(home_vars) <= 1)


# ---------------------------------------------------------------------------
# Soft constraints (returned as penalty terms for the objective)
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
    """SC1/SC2 — penalise runs of more than max_run consecutive home or away."""
    # This is a complex pattern-based soft constraint; a full implementation
    # uses auxiliary boolean variables to track runs.
    # Skeleton: returns list of (penalty_weight, bool_var) tuples for objective.
    penalty_terms = []
    # TODO: implement run-tracking with auxiliary vars
    # For each team, sort slots chronologically and add implication chains
    return penalty_terms


def add_soft_derby_gap(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    min_gap_days: int = 56,  # ~8 rounds × 7 days
    penalty: int = 30,
) -> list:
    """SC3 — penalise derby legs scheduled fewer than min_gap_days apart."""
    derbies = load_high_profile_derbies()
    penalty_terms = []

    for team_a, team_b in derbies:
        leg1 = next((f for f in fixtures if f.home_team_id == team_a and f.away_team_id == team_b), None)
        leg2 = next((f for f in fixtures if f.home_team_id == team_b and f.away_team_id == team_a), None)
        if not (leg1 and leg2):
            continue
        for s1 in slots:
            for s2 in slots:
                gap = abs((s2.date - s1.date).days)
                if 0 < gap < min_gap_days:
                    both_assigned = model.new_bool_var(
                        f"derby_gap_viol_{leg1.fixture_id}_{leg2.fixture_id}_{s1.slot_id}_{s2.slot_id}"
                    )
                    model.add_bool_and([
                        x[(leg1.fixture_id, s1.slot_id)],
                        x[(leg2.fixture_id, s2.slot_id)],
                    ]).only_enforce_if(both_assigned)
                    penalty_terms.append((penalty, both_assigned))

    return penalty_terms
