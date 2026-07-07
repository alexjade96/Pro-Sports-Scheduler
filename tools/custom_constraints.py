"""
CLI for the custom / experimental constraint add-on framework
(solvers/custom_constraints.py).

Use it to prototype scheduling constraints that are not in any league's core
constraint set yet — travel distance, timezone shifts, opponent-rest inequality
— either by measuring them against an already-solved schedule, or by folding
them into the metaheuristic objective and re-solving to see whether optimizing
for them helps.

Examples
--------
    # List every registered custom constraint
    python tools/custom_constraints.py --list

    # Evaluate all custom constraints against the solved NBA CP-SAT schedule
    python tools/custom_constraints.py --league nba

    # Evaluate specific constraints against a specific schedule CSV
    python tools/custom_constraints.py --league nba \
        --csv output/schedule_nba_cp_sat.csv \
        --constraints back_to_back_travel,timezone_shift

    # Re-solve NBA with the metaheuristic *optimizing* for two custom add-ons,
    # then report before/after on the custom metrics
    python tools/custom_constraints.py --league nba --solve --time-limit 120 \
        --constraints back_to_back_travel,timezone_shift
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

from core.data_loader import set_league, load_teams, load_calendar
from core.models import Fixture, ScheduledFixture, Schedule, Slot
from solvers import custom_constraints as cc
from solvers.runner import default_output_path

_ROOT = Path(__file__).parent.parent


def load_schedule_csv(path: Path, season: str = "") -> Schedule:
    """Reconstruct a Schedule from a runner-exported CSV
    (columns: fixture_id, date, kickoff, day, home, away)."""
    fixtures = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            slot = Slot(date=date.fromisoformat(row["date"]),
                        kickoff=row["kickoff"], day_of_week=row["day"])
            fx = Fixture(fixture_id=row["fixture_id"],
                         home_team_id=row["home"], away_team_id=row["away"])
            fixtures.append(ScheduledFixture(fixture=fx, slot=slot))
    return Schedule(season=season, fixtures=fixtures)


def _print_list() -> None:
    print("=" * 78)
    print("  Registered custom / experimental constraints")
    print("=" * 78)
    for c in cc.available():
        req = f"  requires={list(c.requires)}" if c.requires else "  (no external data)"
        print(f"\n[{c.id}]  kind={c.kind}  default_weight={c.default_weight:g}{req}")
        print(f"    {c.description}")
        if c.source:
            print(f"    source: {c.source}")
    print("\n" + "=" * 78)


def _resolve_csv(league: str, csv_arg: str | None) -> Path:
    if csv_arg:
        return Path(csv_arg)
    # prefer CP-SAT output, fall back to metaheuristic, then ILP
    for solver in ("cp_sat", "mh", "ilp"):
        p = default_output_path(league, solver)
        if p.exists():
            return p
    return default_output_path(league, "cp_sat")  # non-existent; caller reports


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate/optimize custom scheduling constraints.")
    ap.add_argument("--league", default="nba", choices=("epl", "nfl", "nba"))
    ap.add_argument("--csv", default=None, help="schedule CSV to evaluate (default: the league's solved output)")
    ap.add_argument("--constraints", default=None,
                    help="comma-separated custom-constraint ids (default: all registered)")
    ap.add_argument("--list", action="store_true", help="list registered custom constraints and exit")
    ap.add_argument("--solve", action="store_true",
                    help="re-solve with the metaheuristic optimizing for the selected custom constraints")
    ap.add_argument("--time-limit", type=int, default=120, help="MH time budget when --solve (seconds)")
    args = ap.parse_args()

    if args.list:
        _print_list()
        return

    set_league(args.league)
    teams = load_teams()
    ids = args.constraints.split(",") if args.constraints else None

    if args.solve:
        from solvers.runner import run_solver
        solve_ids = ids if ids is not None else [c.id for c in cc.available()]
        print(f"[{args.league}] baseline metaheuristic solve (no custom terms) …")
        base = run_solver("mh", league=args.league, time_limit=args.time_limit, quiet=True)
        print(f"[{args.league}] metaheuristic solve optimizing for: {', '.join(solve_ids)} …")
        aug = run_solver("mh", league=args.league, time_limit=args.time_limit, quiet=True,
                         custom_constraint_ids=solve_ids)
        if base:
            print("\n### BEFORE (baseline schedule) ###")
            cc.print_report(cc.evaluate(base, teams, ids=ids, league=args.league))
        if aug:
            print("\n### AFTER (custom-optimized schedule) ###")
            cc.print_report(cc.evaluate(aug, teams, ids=ids, league=args.league))
        return

    csv_path = _resolve_csv(args.league, args.csv)
    if not csv_path.exists():
        raise SystemExit(
            f"No schedule CSV found at {csv_path}. Run a solver first, e.g.\n"
            f"  python -m solvers.cp_sat.main --league {args.league}\n"
            f"or pass --csv, or use --solve to generate one.")
    print(f"Evaluating {csv_path}")
    schedule = load_schedule_csv(csv_path, season=load_calendar().get("season", ""))
    report = cc.evaluate(schedule, teams, ids=ids, league=args.league)
    cc.print_report(report)


if __name__ == "__main__":
    main()
