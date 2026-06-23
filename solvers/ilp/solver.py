"""
Generic ILP solver core (PuLP / CBC).

Approach:
  - Binary decision vars x[(fixture_id, slot_id)]
  - Hard constraints as equality/inequality constraints
  - Soft constraints as slack binary variables penalised in the objective
  - Default solver: CBC (free, bundled with PuLP)
  - Can swap to Gurobi/CPLEX by changing solver= in prob.solve()

League-specific logic is fully delegated to the ``constraint_set`` argument,
which must satisfy the ``ILPConstraintSet`` protocol defined in
``solvers/constraint_set.py``.

Install: pip install pulp
"""
import pulp

from core.models import Fixture, Slot, Schedule, ScheduledFixture, Team


def build_problem(
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    constraint_set,
) -> tuple[pulp.LpProblem, dict, list]:
    prob = pulp.LpProblem("Scheduler", pulp.LpMinimize)

    eligible = constraint_set.build_eligible_slots(fixtures, slots)

    x = {
        (fixture.fixture_id, sid): pulp.LpVariable(
            f"x_{fixture.fixture_id}_{sid}", cat="Binary"
        )
        for fixture in fixtures
        for sid in eligible[fixture.fixture_id]
    }

    constraint_set.add_hard_constraints(prob, x, fixtures, slots, teams)
    penalty_terms = constraint_set.add_soft_constraints(prob, x, fixtures, slots, teams)

    prob += pulp.lpSum(weight * var for weight, var in penalty_terms), "total_penalty"

    return prob, x, penalty_terms


def extract_schedule(
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    season: str,
) -> Schedule:
    slot_map    = {s.slot_id: s for s in slots}
    fixture_map = {f.fixture_id: f for f in fixtures}
    scheduled   = []
    for (fid, sid), var in x.items():
        if pulp.value(var) == 1:
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
    prob, x, _ = build_problem(fixtures, slots, teams, constraint_set)

    solver = pulp.PULP_CBC_CMD(timeLimit=time_limit_seconds, msg=True)

    print(f"[ILP] Solving with CBC, time limit {time_limit_seconds}s ...")
    prob.solve(solver)

    status = pulp.LpStatus[prob.status]
    print(f"[ILP] Status: {status}")

    if prob.status == pulp.LpStatusNotSolved:
        print("[ILP] No feasible solution found.")
        return None

    print(f"[ILP] Objective (penalty): {pulp.value(prob.objective)}")
    return extract_schedule(x, fixtures, slots, season)
