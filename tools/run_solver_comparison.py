"""
Runs all three solvers back-to-back with a capped time limit for one league,
then produces a side-by-side comparison via the analysis framework.

Usage:
    python tools/run_solver_comparison.py [--league epl|nfl|nba] [--time-limit 90]

The solver comparison table is league-agnostic (built from MetricsReport). The
accuracy-vs-historical and per-team sections are most meaningful for EPL — for
NFL/NBA each schedule still populates the fields its own league's metrics
extension fills in.
"""
import argparse
import sys
import time
from pathlib import Path

# Ensure repo root is on the path when run as a script
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from datetime import date as _date

from core.data_loader import (
    set_league, load_teams, load_calendar, load_constraints, generate_slots,
)
from analysis.historical_loader import load_season, available_seasons
from analysis.metrics import compute
from analysis.comparator import compare_solvers, compare_to_historical
from analysis import report as rpt
from solvers import registry
from solvers.runner import export_csv, default_output_path, _solve_fn, _DEFAULT_KWARGS

OUTPUT_DIR = ROOT / "output"

_SOLVER_LABELS = {"cp_sat": "CP-SAT", "ilp": "ILP", "mh": "Metaheuristic"}
_SOLVER_HEADERS = {"cp_sat": "SOLVER A: CP-SAT (OR-Tools)",
                   "ilp":    "SOLVER B: ILP / PuLP+CBC",
                   "mh":     "SOLVER C: Metaheuristic (SA)"}


def run_one(solver, league, fixtures, slots, teams, constraints, calendar,
            season_start, season_end, time_limit):
    print(f"\n{'='*60}")
    print(f"  {_SOLVER_HEADERS[solver]}  [{league}, limit: {time_limit}s]")
    print(f"{'='*60}")
    constraint_set = registry.build_constraint_set(
        league, solver, constraints, season_start, season_end, calendar)
    kwargs = dict(_DEFAULT_KWARGS[solver]); kwargs["time_limit_seconds"] = time_limit
    t0 = time.perf_counter()
    schedule = _solve_fn(solver)(
        fixtures=fixtures, slots=slots, teams=teams,
        constraint_set=constraint_set, season=calendar["season"], **kwargs)
    elapsed = round(time.perf_counter() - t0, 2)
    if schedule:
        print(f"  Done in {elapsed}s — {len(schedule.fixtures)} fixtures scheduled")
    else:
        print(f"  INFEASIBLE / no solution in {elapsed}s")
    return schedule, elapsed


def main():
    parser = argparse.ArgumentParser(description="Solver comparison runner")
    parser.add_argument("--league", default="epl", choices=registry.LEAGUES)
    parser.add_argument("--time-limit", type=int, default=300,
                        help="Per-solver time limit in seconds (default: 300)")
    parser.add_argument("--skip-cp-sat", action="store_true")
    parser.add_argument("--skip-ilp",    action="store_true")
    parser.add_argument("--skip-mh",     action="store_true")
    args = parser.parse_args()

    tl = args.time_limit
    league = args.league
    set_league(league)

    # --- Shared data (loaded once) ---
    print(f"\nLoading shared data ({league})...")
    teams         = load_teams()
    calendar      = load_calendar()
    constraints   = load_constraints()
    slots         = generate_slots(calendar)
    fixtures      = registry.generate_fixtures(league, teams)
    season_start  = _date.fromisoformat(calendar["start_date"])
    season_end    = _date.fromisoformat(calendar["end_date"])
    print(f"  {len(teams)} teams | {len(fixtures)} fixtures | {len(slots)} slots available")

    # --- Run solvers ---
    results: dict[str, tuple] = {}
    plan = [("cp_sat", not args.skip_cp_sat), ("ilp", not args.skip_ilp), ("mh", not args.skip_mh)]
    for solver, enabled in plan:
        if not enabled:
            continue
        sched, elapsed = run_one(solver, league, fixtures, slots, teams, constraints,
                                 calendar, season_start, season_end, tl)
        results[_SOLVER_LABELS[solver]] = (sched, elapsed, solver)
        if sched:
            export_csv(sched, default_output_path(league, solver))

    # --- Validate & collect metrics ---
    print(f"\n{'='*60}")
    print("  VALIDATION & METRICS")
    print(f"{'='*60}")

    reports = []

    for name, (sched, elapsed, solver) in results.items():
        if sched is None:
            print(f"\n  [{name}] skipped — no feasible schedule produced")
            continue
        print(f"\n  [{name}]")

        # core.validator hardcodes EPL constraint IDs — only meaningful for EPL.
        meta = {"solve_time_seconds": elapsed}
        if league == "epl":
            from core.validator import validate, print_report
            val_report = validate(sched, teams)
            print_report(val_report)
            penalty = val_report.get("total_penalty_score")
            hard_v  = val_report.get("hard_violation_count", 0)
            soft_v  = val_report.get("soft_violation_count", 0)
            if solver == "mh":
                from solvers.leagues.epl.mh_objective import score as mh_score
                try:
                    penalty = mh_score(sched, teams)
                except Exception:
                    pass
            meta.update(penalty_score=penalty, hard_violations=hard_v, soft_violations=soft_v)
        else:
            m = compute(sched)
            print(f"    fixtures={m.total_fixtures} rest_min={m.rest_min_global} "
                  f"max_consec_home/away={m.league_max_consec_home}/{m.league_max_consec_away} "
                  f"(core.validator is EPL-only)")

        report = compute(sched, solver_meta=meta)
        report.label = name
        reports.append(report)

    # --- Historical baseline ---
    seasons = available_seasons(league=league)
    hist_report = None
    if seasons:
        latest = sorted(seasons)[-1]
        print(f"\n  [Historical] Loading {latest.name}...")
        hist_schedule = load_season(latest, league=league)
        hist_report   = compute(hist_schedule)
        print(f"  {hist_report.total_fixtures} historical fixtures loaded")

    # --- Solver comparison report ---
    if len(reports) >= 2:
        print(f"\n{'='*60}")
        print("  SOLVER COMPARISON REPORT")
        print(f"{'='*60}")
        solver_cmp = compare_solvers(reports)
        text = rpt.render_text_solver_comparison(solver_cmp)
        print(text)
        saved = rpt.save(text, "report_solvers.txt")
        print(f"  Saved: {saved}")

    # --- Accuracy vs historical ---
    if reports and hist_report:
        print(f"\n{'='*60}")
        print(f"  ACCURACY vs HISTORICAL ({hist_report.label})")
        print(f"{'='*60}")
        for r in reports:
            acc = compare_to_historical(r, hist_report)
            text = rpt.render_text_accuracy(acc)
            print(f"\n  --- {r.label} ---")
            print(text)
        saved = rpt.save(
            rpt.render_text_accuracy(compare_to_historical(reports[0], hist_report)),
            "report_accuracy.txt",
        )

    # --- Per-team table ---
    all_reports = reports[:]
    if hist_report:
        all_reports.append(hist_report)
    if all_reports:
        per_team = rpt.render_per_team_table(all_reports)
        saved = rpt.save(per_team, "report_per_team.txt")
        print(f"\n  Per-team table saved: {saved}")

    print(f"\n{'='*60}")
    print("  DONE")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
