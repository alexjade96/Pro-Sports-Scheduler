"""
Option A — Google OR-Tools CP-SAT solver.

Approach:
  - Boolean decision variable x[(fixture_id, slot_id)] = 1 if assigned
  - Hard constraints added directly to the model
  - Soft constraints expressed as weighted penalty terms in the objective
  - CP-SAT minimises total penalty subject to all hard constraints

Install: pip install ortools
"""
from ortools.sat.python import cp_model

from core.models import Fixture, Slot, Schedule, ScheduledFixture, Team
from core.data_loader import load_constraints
from solvers.slot_filter import build_eligible_slots, log_filter_stats
from solvers.cp_sat.constraints import (
    add_each_fixture_assigned_exactly_once,
    add_team_plays_at_most_once_per_slot,
    add_min_rest_days,
    add_no_same_city_home_clash,
    add_soft_max_consecutive_home_away,
    add_soft_derby_gap,
)


def build_model(
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    constraint_config: dict,
    season_start=None,
    season_end=None,
) -> tuple[cp_model.CpModel, dict]:
    model = cp_model.CpModel()

    # --- Decision variables (temporally filtered) ---
    # Only create variables for (fixture, slot) pairs within the fixture's
    # natural round window.  Reduces variable count from ~142K → ~19K.
    if season_start and season_end:
        eligible = build_eligible_slots(fixtures, slots, season_start, season_end)
        log_filter_stats(eligible)
    else:
        eligible = {f.fixture_id: [s.slot_id for s in slots] for f in fixtures}

    slot_map = {s.slot_id: s for s in slots}
    x = {}
    for fixture in fixtures:
        for sid in eligible[fixture.fixture_id]:
            x[(fixture.fixture_id, sid)] = model.new_bool_var(
                f"x_{fixture.fixture_id}_{sid}"
            )

    # --- Hard constraints ---
    hard = {c["id"]: c for c in constraint_config["hard"]}

    add_each_fixture_assigned_exactly_once(model, x, fixtures, slots)

    add_team_plays_at_most_once_per_slot(model, x, fixtures, slots, teams)

    min_rest = hard["HC1"]["value"]
    add_min_rest_days(model, x, fixtures, slots, teams, min_rest)

    add_no_same_city_home_clash(model, x, fixtures, slots)

    # --- Soft constraints (penalty objective) ---
    soft = {c["id"]: c for c in constraint_config["soft"]}
    penalty_terms = []

    penalty_terms += add_soft_max_consecutive_home_away(
        model, x, fixtures, slots, teams,
        max_run=soft["SC1"]["value"],
        penalty=soft["SC1"]["penalty_per_violation"],
    )

    penalty_terms += add_soft_derby_gap(
        model, x, fixtures, slots,
        penalty=soft["SC3"]["penalty_per_violation"],
    )

    # Minimise total weighted penalty
    if penalty_terms:
        model.minimize(
            sum(weight * var for weight, var in penalty_terms)
        )

    return model, x


def extract_schedule(
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    solver: cp_model.CpSolver,
    season: str,
) -> Schedule:
    slot_map   = {s.slot_id: s for s in slots}
    fixture_map = {f.fixture_id: f for f in fixtures}
    scheduled  = []
    for (fid, sid), var in x.items():
        if solver.value(var):
            scheduled.append(ScheduledFixture(
                fixture=fixture_map[fid],
                slot=slot_map[sid],
            ))
    return Schedule(season=season, fixtures=scheduled)


def solve(
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    constraint_config: dict,
    season: str,
    time_limit_seconds: int = 300,
    season_start=None,
    season_end=None,
) -> Schedule | None:
    model, x = build_model(
        fixtures, slots, teams, constraint_config,
        season_start=season_start, season_end=season_end,
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_workers = 2  # reduced to limit memory

    print(f"[CP-SAT] Solving with time limit {time_limit_seconds}s ...")
    status = solver.solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(f"[CP-SAT] Status: {'OPTIMAL' if status == cp_model.OPTIMAL else 'FEASIBLE'}")
        print(f"[CP-SAT] Objective (penalty): {solver.objective_value}")
        print(f"[CP-SAT] Wall time: {solver.wall_time:.1f}s")
        return extract_schedule(x, fixtures, slots, solver, season)

    print(f"[CP-SAT] No solution found. Status: {solver.status_name(status)}")
    return None
