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
    add_max_friday_games_per_team,
    add_max_midweek_games_per_team,
    add_max_monday_games_per_team,
    add_max_wednesday_games_per_team,
    add_max_thursday_games_per_team,
    add_soft_max_consecutive_home_away,
    add_soft_ha_window,
    add_soft_derby_gap,
    add_soft_same_city_home_clash,
    add_soft_london_cluster,
    add_soft_festive_coverage,
    add_soft_sc14_season_boundary,
    add_soft_sc15_boxing_day_nyd,
    add_soft_min_sat_1500,
    add_soft_min_monday,
    add_hard_half_season_balance,
)


_FIXTURES_PER_ROUND = 10
_TOTAL_ROUNDS = 38


def build_model(
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    constraint_config: dict,
    season_start=None,
    season_end=None,
    final_day: dict | None = None,
) -> tuple[cp_model.CpModel, dict]:
    model = cp_model.CpModel()

    # --- Decision variables (temporally filtered) ---
    # Only create variables for (fixture, slot) pairs within the fixture's
    # natural round window.  Reduces variable count from ~142K → ~19K.
    if season_start and season_end:
        eligible = build_eligible_slots(fixtures, slots, season_start, season_end, window_rounds=4)
        log_filter_stats(eligible)
    else:
        eligible = {f.fixture_id: [s.slot_id for s in slots] for f in fixtures}

    # HC8: Round 38 fixtures must use only the final-day slot, and all
    # earlier fixtures must be scheduled strictly before that date (so they
    # don't violate HC1 rest days or appear out of round order).
    if final_day:
        from datetime import date as _date
        fd_date = _date.fromisoformat(final_day["date"])
        final_day_sid = f"{fd_date}_{final_day['kickoff'].replace(':', '')}"
        slot_ids = {s.slot_id for s in slots}
        if final_day_sid in slot_ids:
            # Pin Round 38 to final-day slot
            for f in fixtures[-_FIXTURES_PER_ROUND:]:
                eligible[f.fixture_id] = [final_day_sid]
            # Restrict all earlier fixtures to slots before the final day
            before_fd = {s.slot_id for s in slots if s.date < fd_date}
            for f in fixtures[:-_FIXTURES_PER_ROUND]:
                eligible[f.fixture_id] = [
                    sid for sid in eligible[f.fixture_id] if sid in before_fd
                ]
            print(f"[HC8] Round 38 pinned to {final_day_sid}; earlier rounds capped to < {fd_date}")
        else:
            print(f"[HC8] WARNING: final-day slot {final_day_sid} not in pool — HC8 not enforced")

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

    # HC2 was demoted to SC7 in constraints.json (14–34 same-city clashes/season
    # in real EPL history — too frequent to be a hard constraint, and incompatible
    # with HC8 when multiple same-city teams are home in Round 38).
    # Enforced as a soft penalty below (add_soft_same_city_home_clash).

    max_friday = hard.get("HC9", {}).get("value", 3)
    add_max_friday_games_per_team(model, x, fixtures, slots, teams, max_friday)

    max_midweek = hard.get("HC10", {}).get("value", 10)
    add_max_midweek_games_per_team(model, x, fixtures, slots, teams, max_midweek)

    max_monday = hard.get("HC11", {}).get("value", 3)
    add_max_monday_games_per_team(model, x, fixtures, slots, teams, max_monday)

    max_wednesday = hard.get("HC12", {}).get("value", 6)
    add_max_wednesday_games_per_team(model, x, fixtures, slots, teams, max_wednesday)

    max_thursday = hard.get("HC13", {}).get("value", 2)
    add_max_thursday_games_per_team(model, x, fixtures, slots, teams, max_thursday)

    # --- Soft constraints (penalty objective) ---
    soft = {c["id"]: c for c in constraint_config["soft"]}
    penalty_terms = []

    penalty_terms += add_soft_max_consecutive_home_away(
        model, x, fixtures, slots, teams,
        max_run=soft["SC1"]["value"],
        penalty=soft["SC1"]["penalty_per_violation"],
    )

    sc13 = soft.get("SC13", {})
    penalty_terms += add_soft_ha_window(
        model, x, fixtures, slots, teams,
        window=sc13.get("window", 5),
        min_home=sc13.get("min_home", 2),
        max_home=sc13.get("max_home", 3),
        penalty=sc13.get("penalty_per_violation", 25),
    )

    penalty_terms += add_soft_derby_gap(
        model, x, fixtures, slots,
        penalty=soft["SC3"]["penalty_per_violation"],
    )

    sc7 = soft.get("SC7", {})
    penalty_terms += add_soft_same_city_home_clash(
        model, x, fixtures, slots,
        window_days=sc7.get("window_days", 4),
        penalty=sc7.get("penalty_per_clash", 80),
    )

    sc10 = soft.get("SC10", {})
    penalty_terms += add_soft_london_cluster(
        model, x, fixtures, slots,
        max_per_day=sc10.get("max_home_same_day", 3),
        penalty=sc10.get("penalty_per_violation", 30),
    )

    sc9 = soft.get("SC9", {})
    penalty_terms += add_soft_festive_coverage(
        model, x, fixtures, slots, teams,
        penalty=sc9.get("penalty_per_missing_team", 20),
    )

    sc5 = soft.get("SC5", {})
    add_hard_half_season_balance(
        model, x, fixtures, slots, teams,
        tolerance=sc5.get("tolerance", 2),
    )

    sc14 = soft.get("SC14", {})
    penalty_terms += add_soft_sc14_season_boundary(
        model, x, fixtures, slots, teams,
        penalty=sc14.get("penalty_per_violation", 30),
    )

    sc15 = soft.get("SC15", {})
    penalty_terms += add_soft_sc15_boxing_day_nyd(
        model, x, fixtures, slots, teams,
        penalty=sc15.get("penalty_per_violation", 35),
    )

    sc17 = soft.get("SC17", {})
    penalty_terms += add_soft_min_sat_1500(
        model, x, fixtures, slots, teams,
        min_per_team=sc17.get("min_per_team", 5),
        penalty=sc17.get("penalty_per_violation", 10),
    )

    sc18 = soft.get("SC18", {})
    penalty_terms += add_soft_min_monday(
        model, x, fixtures, slots, teams,
        min_per_team=sc18.get("min_per_team", 3),
        penalty=sc18.get("penalty_per_violation", 12),
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
    final_day: dict | None = None,
) -> Schedule | None:
    model, x = build_model(
        fixtures, slots, teams, constraint_config,
        season_start=season_start, season_end=season_end,
        final_day=final_day,
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_workers = 8

    print(f"[CP-SAT] Solving with time limit {time_limit_seconds}s ...")
    status = solver.solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(f"[CP-SAT] Status: {'OPTIMAL' if status == cp_model.OPTIMAL else 'FEASIBLE'}")
        print(f"[CP-SAT] Objective (penalty): {solver.objective_value}")
        print(f"[CP-SAT] Wall time: {solver.wall_time:.1f}s")
        return extract_schedule(x, fixtures, slots, solver, season)

    print(f"[CP-SAT] No solution found. Status: {solver.status_name(status)}")
    return None
