"""
Option A — CP-SAT: Constraint builder modules.

Decision variable layout (sparse):
    x[(fixture_id, slot_id)] ∈ {0, 1}  — only exists for eligible pairs
    = 1 if fixture fixture_id is assigned to slot slot_id

All functions must guard against missing keys; use `if (fid, sid) in x`.
"""
from __future__ import annotations

from collections import defaultdict

from ortools.sat.python import cp_model

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

    Only forbids (fixture1, slot1) + (fixture2, slot2) pairs where both
    slots are in the eligible set for their respective fixtures.
    """
    fsi = _fixture_slot_index(x, slots)

    for team_id in teams:
        team_fixtures = [
            f for f in fixtures
            if f.home_team_id == team_id or f.away_team_id == team_id
        ]
        for i, f1 in enumerate(team_fixtures):
            f1_slots = fsi.get(f1.fixture_id, [])
            for f2 in team_fixtures[i + 1:]:
                f2_slots = fsi.get(f2.fixture_id, [])
                for sid1, s1 in f1_slots:
                    for sid2, s2 in f2_slots:
                        gap = abs((s2.date - s1.date).days)
                        if 0 < gap < min_days:
                            model.add(
                                x[(f1.fixture_id, sid1)] +
                                x[(f2.fixture_id, sid2)] <= 1
                            )


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
