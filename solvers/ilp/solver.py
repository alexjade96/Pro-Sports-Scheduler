"""
Option B — Integer Linear Programming via PuLP.

Approach:
  - Binary decision vars x[(fixture_id, slot_id)]
  - Hard constraints as equality/inequality constraints
  - Soft constraints as slack binary variables penalised in the objective
  - Default solver: CBC (free, bundled with PuLP)
  - Can swap to Gurobi/CPLEX by changing solver= in prob.solve()

Install: pip install pulp
"""
import pulp

from core.models import Fixture, Slot, Schedule, ScheduledFixture, Team
from core.data_loader import load_constraints
from solvers.slot_filter import build_eligible_slots, log_filter_stats
from solvers.ilp.constraints import (
    add_each_fixture_assigned_exactly_once,
    add_team_plays_at_most_once_per_day,
    add_min_rest_days,
    add_max_thursday_games_per_team,
    add_soft_derby_gap,
)


def build_problem(
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    constraint_config: dict,
    season_start=None,
    season_end=None,
) -> tuple[pulp.LpProblem, dict, list]:
    prob = pulp.LpProblem("EPL_Scheduler", pulp.LpMinimize)

    # --- Decision variables (temporally filtered) ---
    if season_start and season_end:
        eligible = build_eligible_slots(fixtures, slots, season_start, season_end)
        log_filter_stats(eligible)
    else:
        eligible = {f.fixture_id: [s.slot_id for s in slots] for f in fixtures}

    x = {
        (fixture.fixture_id, sid): pulp.LpVariable(
            f"x_{fixture.fixture_id}_{sid}", cat="Binary"
        )
        for fixture in fixtures
        for sid in eligible[fixture.fixture_id]
    }

    # --- Hard constraints ---
    hard = {c["id"]: c for c in constraint_config["hard"]}

    add_each_fixture_assigned_exactly_once(prob, x, fixtures, slots)
    add_team_plays_at_most_once_per_day(prob, x, fixtures, slots, teams)

    min_rest = hard["HC1"]["value"]
    add_min_rest_days(prob, x, fixtures, slots, teams, min_rest)
    # HC2 demoted to SC7 — see cp_sat/solver.py for rationale

    max_thursday = hard.get("HC13", {}).get("value", 2)
    add_max_thursday_games_per_team(prob, x, fixtures, slots, teams, max_thursday)

    # --- Soft constraints (penalty terms) ---
    soft = {c["id"]: c for c in constraint_config["soft"]}
    penalty_terms: list[tuple[int, pulp.LpVariable]] = []

    penalty_terms += add_soft_derby_gap(
        prob, x, fixtures, slots,
        penalty=soft["SC3"]["penalty_per_violation"],
    )

    # Objective: minimise total penalty
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
    constraint_config: dict,
    season: str,
    time_limit_seconds: int = 300,
    season_start=None,
    season_end=None,
) -> Schedule | None:
    prob, x, _ = build_problem(
        fixtures, slots, teams, constraint_config,
        season_start=season_start, season_end=season_end,
    )

    # CBC solver (swap to pulp.GUROBI_CMD() or pulp.CPLEX_CMD() for commercial solvers)
    solver = pulp.PULP_CBC_CMD(
        timeLimit=time_limit_seconds,
        msg=True,
    )

    print(f"[ILP] Solving with CBC, time limit {time_limit_seconds}s ...")
    prob.solve(solver)

    status = pulp.LpStatus[prob.status]
    print(f"[ILP] Status: {status}")

    if prob.status == pulp.LpStatusNotSolved:
        print("[ILP] No feasible solution found.")
        return None

    print(f"[ILP] Objective (penalty): {pulp.value(prob.objective)}")
    return extract_schedule(x, fixtures, slots, season)
