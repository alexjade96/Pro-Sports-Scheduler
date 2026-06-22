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
    add_no_same_city_home_clash,
    add_soft_derby_gap,
    add_soft_sc14_season_boundary,
    add_soft_sc15_boxing_day_nyd,
    add_soft_min_sat_1500,
    add_soft_min_monday,
    add_soft_same_city_home_clash,
    add_soft_festive_coverage,
    add_soft_london_cluster,
    add_soft_half_season_balance,
)


def build_problem(
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    constraint_config: dict,
    season_start=None,
    season_end=None,
    final_day=None,
) -> tuple[pulp.LpProblem, dict, list]:
    prob = pulp.LpProblem("EPL_Scheduler", pulp.LpMinimize)

    # --- Decision variables (temporally filtered) ---
    if season_start and season_end:
        eligible = build_eligible_slots(fixtures, slots, season_start, season_end)
        log_filter_stats(eligible)
    else:
        eligible = {f.fixture_id: [s.slot_id for s in slots] for f in fixtures}

    # HC8: pin Round 38 to the final-day slot; block all final-day slots from
    # earlier rounds (mirrors the CP-SAT implementation).
    _FIXTURES_PER_ROUND = 10
    if final_day:
        from datetime import date as _date
        fd_date = _date.fromisoformat(final_day["date"])
        final_day_sid = f"{fd_date}_{final_day['kickoff'].replace(':', '')}"
        slot_ids = {s.slot_id for s in slots}
        if final_day_sid in slot_ids:
            for f in fixtures[-_FIXTURES_PER_ROUND:]:
                eligible[f.fixture_id] = [final_day_sid]
            before_fd = {s.slot_id for s in slots if s.date < fd_date}
            for f in fixtures[:-_FIXTURES_PER_ROUND]:
                eligible[f.fixture_id] = [
                    sid for sid in eligible[f.fixture_id] if sid in before_fd
                ]
            print(f"[HC8] ILP: Round 38 pinned to {final_day_sid}; earlier rounds capped to < {fd_date}")
        else:
            print(f"[HC8] ILP WARNING: final-day slot {final_day_sid} not in pool — HC8 not enforced")

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

    # HC2 kept as hard same-day clash prevention; SC7 soft handles the
    # broader 4-day matchday window below.
    add_no_same_city_home_clash(prob, x, fixtures, slots)

    # --- Soft constraints (penalty terms) ---
    soft = {c["id"]: c for c in constraint_config["soft"]}
    penalty_terms: list[tuple[int, pulp.LpVariable]] = []

    penalty_terms += add_soft_derby_gap(
        prob, x, fixtures, slots,
        penalty=soft["SC3"]["penalty_per_violation"],
    )

    sc7 = soft.get("SC7", {})
    penalty_terms += add_soft_same_city_home_clash(
        prob, x, fixtures, slots,
        window_days=sc7.get("window_days", 4),
        penalty=sc7.get("penalty_per_clash", 80),
    )

    sc9 = soft.get("SC9", {})
    penalty_terms += add_soft_festive_coverage(
        prob, x, fixtures, slots, teams,
        penalty=sc9.get("penalty_per_missing_team", 50),
    )

    sc10 = soft.get("SC10", {})
    penalty_terms += add_soft_london_cluster(
        prob, x, fixtures, slots,
        max_per_day=sc10.get("max_home_same_day", 3),
        penalty=sc10.get("penalty_per_violation", 30),
    )

    sc5 = soft.get("SC5", {})
    penalty_terms += add_soft_half_season_balance(
        prob, x, fixtures, slots, teams,
        tolerance=sc5.get("tolerance", 2),
        penalty=sc5.get("penalty_per_violation", 15),
    )

    sc14 = soft.get("SC14", {})
    penalty_terms += add_soft_sc14_season_boundary(
        prob, x, fixtures, slots, teams,
        penalty=sc14.get("penalty_per_violation", 30),
    )

    sc15 = soft.get("SC15", {})
    penalty_terms += add_soft_sc15_boxing_day_nyd(
        prob, x, fixtures, slots, teams,
        penalty=sc15.get("penalty_per_violation", 35),
    )

    sc17 = soft.get("SC17", {})
    penalty_terms += add_soft_min_sat_1500(
        prob, x, fixtures, slots, teams,
        min_per_team=sc17.get("min_per_team", 5),
        penalty=sc17.get("penalty_per_violation", 10),
    )

    sc18 = soft.get("SC18", {})
    penalty_terms += add_soft_min_monday(
        prob, x, fixtures, slots, teams,
        min_per_team=sc18.get("min_per_team", 3),
        penalty=sc18.get("penalty_per_violation", 12),
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
    final_day=None,
) -> Schedule | None:
    prob, x, _ = build_problem(
        fixtures, slots, teams, constraint_config,
        season_start=season_start, season_end=season_end,
        final_day=final_day,
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
