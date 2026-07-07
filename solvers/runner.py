"""
League-agnostic solver runner. One flow that any (league, solver) pair goes
through: set the active league, load its data, generate its fixtures, build the
matching constraint set (via solvers/registry.py), solve, validate, and export.

Entry points (solvers/<solver>/main.py) and tools/run_solver_comparison.py all
call run_solver(); nothing here hardcodes a league.

Output filename convention matches webapp/app.py and the analytics tools: EPL
keeps the unprefixed output/schedule_<label>.csv; NFL/NBA use
output/schedule_<league>_<label>.csv. The metaheuristic's label is
"metaheuristic" for all leagues (not "mh"), so the webapp finds it directly.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from core.data_loader import (
    set_league, load_teams, load_calendar, load_constraints, generate_slots,
)
from solvers import registry

OUTPUT_DIR = Path(__file__).parent.parent / "output"

# solver key -> (solve callable, output label, default solve kwargs)
_OUTPUT_LABEL = {"cp_sat": "cp_sat", "ilp": "ilp", "mh": "metaheuristic"}
_DEFAULT_KWARGS = {
    "cp_sat": {"time_limit_seconds": 600},
    "ilp":    {"time_limit_seconds": 1800},
    "mh":     {"initial_temp": 5000.0, "cooling_rate": 0.9997,
               "max_iterations": 5_000_000, "tabu_size": 200,
               "time_limit_seconds": 600},
}


def _solve_fn(solver: str):
    if solver == "cp_sat":
        from solvers.cp_sat.solver import solve
    elif solver == "ilp":
        from solvers.ilp.solver import solve
    elif solver == "mh":
        from solvers.metaheuristic.solver import solve
    else:
        raise ValueError(f"Unknown solver {solver!r} (expected {registry.SOLVERS})")
    return solve


def default_output_path(league: str, solver: str) -> Path:
    label = _OUTPUT_LABEL[solver]
    if league == "epl":
        return OUTPUT_DIR / f"schedule_{label}.csv"
    return OUTPUT_DIR / f"schedule_{league}_{label}.csv"


def export_csv(schedule, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["fixture_id", "date", "kickoff", "day", "home", "away"])
        for sf in sorted(schedule.fixtures, key=lambda s: (s.slot.date, s.slot.kickoff)):
            writer.writerow([
                sf.fixture.fixture_id, sf.slot.date, sf.slot.kickoff,
                sf.slot.day_of_week, sf.home_team_id, sf.away_team_id,
            ])
    print(f"Schedule exported to {path}")


def _report(schedule, teams, league: str) -> None:
    """Run the per-league constraint validator (core.validator dispatches to
    the right one) and print the standard report — works for all leagues."""
    from core.validator import validate, print_report
    print_report(validate(schedule, teams, league=league))


def run_solver(
    solver: str,
    league: str = "epl",
    time_limit: int | None = None,
    out_path: Path | None = None,
    quiet: bool = False,
    custom_constraint_ids: list[str] | None = None,
    custom_weights: dict[str, float] | None = None,
    **solver_overrides,
):
    """Run one solver for one league end-to-end and export the schedule.
    Returns the Schedule (or None if no feasible schedule was found).

    `custom_constraint_ids` opts experimental add-on constraints
    (solvers/custom_constraints.py — travel distance, timezone shifts, …) into
    the objective. Only supported for the metaheuristic solver today, because it
    scores whole Schedule objects; a custom term for CP-SAT/ILP must be
    expressed in model variables inside that league's constraint set."""
    if solver not in registry.SOLVERS:
        raise ValueError(f"Unknown solver {solver!r} (expected {registry.SOLVERS})")
    if league not in registry.LEAGUES:
        raise ValueError(f"Unknown league {league!r} (expected {registry.LEAGUES})")

    set_league(league)
    teams       = load_teams()
    calendar    = load_calendar()
    constraints = load_constraints()
    slots       = generate_slots(calendar)
    fixtures    = registry.generate_fixtures(league, teams)

    season_start = date.fromisoformat(calendar["start_date"])
    season_end   = date.fromisoformat(calendar["end_date"])

    if not quiet:
        print(f"[{league}/{solver}] Teams: {len(teams)} | Fixtures: {len(fixtures)} "
              f"| Slots: {len(slots)}")

    constraint_set = registry.build_constraint_set(
        league, solver, constraints, season_start, season_end, calendar)

    if custom_constraint_ids:
        if solver != "mh":
            raise ValueError(
                "custom constraint add-ons are only supported for the metaheuristic "
                "solver (solver='mh') today; got solver=" + repr(solver))
        from solvers.custom_constraints import CustomAugmentedMHConstraintSet
        constraint_set = CustomAugmentedMHConstraintSet(
            constraint_set, teams, custom_constraint_ids, custom_weights, league)
        if not quiet:
            print(f"[{league}/{solver}] custom add-on constraints active: "
                  f"{', '.join(custom_constraint_ids)}")

    kwargs = dict(_DEFAULT_KWARGS[solver])
    kwargs.update(solver_overrides)
    if time_limit is not None:
        kwargs["time_limit_seconds"] = time_limit

    schedule = _solve_fn(solver)(
        fixtures=fixtures, slots=slots, teams=teams,
        constraint_set=constraint_set, season=calendar["season"], **kwargs)

    if not schedule:
        print(f"[{league}/{solver}] No feasible schedule found.")
        return None

    _report(schedule, teams, league)
    export_csv(schedule, out_path or default_output_path(league, solver))
    return schedule
