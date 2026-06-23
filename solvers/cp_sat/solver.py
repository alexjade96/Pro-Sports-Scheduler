"""
Generic CP-SAT solver core (OR-Tools).

Approach:
  - Boolean decision variable x[(fixture_id, slot_id)] = 1 if assigned
  - Hard constraints added directly to the model
  - Soft constraints expressed as weighted penalty terms in the objective
  - CP-SAT minimises total penalty subject to all hard constraints

League-specific logic is fully delegated to the ``constraint_set`` argument,
which must satisfy the ``CpSatConstraintSet`` protocol defined in
``solvers/constraint_set.py``.

Install: pip install ortools
"""
from ortools.sat.python import cp_model

from core.models import Fixture, Slot, Schedule, ScheduledFixture, Team


def build_model(
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    constraint_set,
) -> tuple[cp_model.CpModel, dict]:
    model = cp_model.CpModel()

    eligible = constraint_set.build_eligible_slots(fixtures, slots)

    x = {}
    for fixture in fixtures:
        for sid in eligible[fixture.fixture_id]:
            x[(fixture.fixture_id, sid)] = model.new_bool_var(
                f"x_{fixture.fixture_id}_{sid}"
            )

    constraint_set.add_hard_constraints(model, x, fixtures, slots, teams)
    penalty_terms = constraint_set.add_soft_constraints(model, x, fixtures, slots, teams)

    if penalty_terms:
        model.minimize(sum(weight * var for weight, var in penalty_terms))

    return model, x


def extract_schedule(
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    solver: cp_model.CpSolver,
    season: str,
) -> Schedule:
    slot_map    = {s.slot_id: s for s in slots}
    fixture_map = {f.fixture_id: f for f in fixtures}
    scheduled   = []
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
    constraint_set,
    season: str,
    time_limit_seconds: int = 300,
) -> Schedule | None:
    model, x = build_model(fixtures, slots, teams, constraint_set)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_workers = 8

    print(f"[CP-SAT] Solving with time limit {time_limit_seconds}s ...")
    status = solver.solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        label = "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE"
        print(f"[CP-SAT] Status: {label}")
        print(f"[CP-SAT] Objective (penalty): {solver.objective_value}")
        print(f"[CP-SAT] Wall time: {solver.wall_time:.1f}s")
        return extract_schedule(x, fixtures, slots, solver, season)

    print(f"[CP-SAT] No solution found. Status: {solver.status_name(status)}")
    return None
