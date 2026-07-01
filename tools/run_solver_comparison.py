"""
Runs all three solvers back-to-back with a capped time limit,
then produces a side-by-side comparison via the analysis framework.

Usage:
    python tools/run_solver_comparison.py [--time-limit 90]
"""
import argparse
import csv
import sys
import time
from pathlib import Path

# Ensure repo root is on the path when run as a script
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from datetime import date as _date

from core.data_loader import load_teams, load_calendar, load_constraints, generate_slots
from core.validator import validate, print_report
from generators.leagues.epl.generate_epl import generate_fixtures
from analysis.historical_loader import load_season, available_seasons
from analysis.metrics import compute
from analysis.comparator import compare_solvers, compare_to_historical
from analysis import report as rpt

OUTPUT_DIR = ROOT / "output"


def export_csv(schedule, path: Path) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["fixture_id", "date", "kickoff", "day", "home", "away"])
        for sf in sorted(schedule.fixtures, key=lambda s: (s.slot.date, s.slot.kickoff)):
            writer.writerow([
                sf.fixture.fixture_id,
                sf.slot.date,
                sf.slot.kickoff,
                sf.slot.day_of_week,
                sf.home_team_id,
                sf.away_team_id,
            ])


def run_cp_sat(fixtures, slots, teams, constraints, season, time_limit,
               season_start=None, season_end=None, final_day=None):
    from solvers.cp_sat.solver import solve
    from solvers.leagues.epl.cp_sat_constraint_set import EPLCpSatConstraintSet
    print(f"\n{'='*60}")
    print(f"  SOLVER A: CP-SAT (OR-Tools)  [limit: {time_limit}s]")
    print(f"{'='*60}")
    t0 = time.perf_counter()
    constraint_set = EPLCpSatConstraintSet(constraints, season_start, season_end, final_day)
    schedule = solve(
        fixtures=fixtures,
        slots=slots,
        teams=teams,
        constraint_set=constraint_set,
        season=season,
        time_limit_seconds=time_limit,
    )
    elapsed = round(time.perf_counter() - t0, 2)
    if schedule:
        print(f"  Done in {elapsed}s — {len(schedule.fixtures)} fixtures scheduled")
        return schedule, elapsed
    print(f"  INFEASIBLE / no solution in {elapsed}s")
    return None, elapsed


def run_ilp(fixtures, slots, teams, constraints, season, time_limit,
            season_start=None, season_end=None, final_day=None):
    from solvers.ilp.solver import solve
    from solvers.leagues.epl.ilp_constraint_set import EPLILPConstraintSet
    print(f"\n{'='*60}")
    print(f"  SOLVER B: ILP / PuLP+CBC      [limit: {time_limit}s]")
    print(f"{'='*60}")
    t0 = time.perf_counter()
    constraint_set = EPLILPConstraintSet(constraints, season_start, season_end, final_day)
    schedule = solve(
        fixtures=fixtures,
        slots=slots,
        teams=teams,
        constraint_set=constraint_set,
        season=season,
        time_limit_seconds=time_limit,
    )
    elapsed = round(time.perf_counter() - t0, 2)
    if schedule:
        print(f"  Done in {elapsed}s — {len(schedule.fixtures)} fixtures scheduled")
        return schedule, elapsed
    print(f"  INFEASIBLE / no solution in {elapsed}s")
    return None, elapsed


def run_metaheuristic(fixtures, slots, teams, constraints, season, time_limit,
                      season_start=None, season_end=None, final_day=None):
    from solvers.metaheuristic.solver import solve
    from solvers.leagues.epl.mh_constraint_set import EPLMHConstraintSet
    print(f"\n{'='*60}")
    print(f"  SOLVER C: Metaheuristic (SA)  [limit: {time_limit}s]")
    print(f"{'='*60}")
    t0 = time.perf_counter()
    constraint_set = EPLMHConstraintSet(constraints, season_start, season_end, final_day)
    schedule = solve(
        fixtures=fixtures,
        slots=slots,
        teams=teams,
        constraint_set=constraint_set,
        season=season,
        initial_temp=5000.0,
        cooling_rate=0.9997,
        max_iterations=5_000_000,
        tabu_size=200,
        time_limit_seconds=time_limit,
    )
    elapsed = round(time.perf_counter() - t0, 2)
    print(f"  Done in {elapsed}s — {len(schedule.fixtures)} fixtures scheduled")
    return schedule, elapsed


def main():
    parser = argparse.ArgumentParser(description="Solver comparison runner")
    parser.add_argument("--time-limit", type=int, default=300,
                        help="Per-solver time limit in seconds (default: 300)")
    parser.add_argument("--skip-cp-sat", action="store_true")
    parser.add_argument("--skip-ilp",    action="store_true")
    parser.add_argument("--skip-mh",     action="store_true")
    args = parser.parse_args()

    tl = args.time_limit

    # --- Shared data (loaded once) ---
    print("\nLoading shared data...")
    teams         = load_teams()
    calendar      = load_calendar()
    constraints   = load_constraints()
    slots         = generate_slots(calendar)
    fixtures      = generate_fixtures(teams)
    season        = calendar["season"]
    season_start  = _date.fromisoformat(calendar["start_date"])
    season_end    = _date.fromisoformat(calendar["end_date"])
    print(f"  {len(teams)} teams | {len(fixtures)} fixtures | {len(slots)} slots available")

    # --- Run solvers ---
    results: dict[str, tuple] = {}

    if not args.skip_cp_sat:
        sched, elapsed = run_cp_sat(fixtures, slots, teams, constraints, season, tl,
                                    season_start=season_start, season_end=season_end,
                                    final_day=calendar.get("final_day"))
        results["CP-SAT"] = (sched, elapsed)
        if sched:
            export_csv(sched, OUTPUT_DIR / "schedule_cp_sat.csv")

    if not args.skip_ilp:
        sched, elapsed = run_ilp(fixtures, slots, teams, constraints, season, tl,
                                  season_start=season_start, season_end=season_end,
                                  final_day=calendar.get("final_day"))
        results["ILP"] = (sched, elapsed)
        if sched:
            export_csv(sched, OUTPUT_DIR / "schedule_ilp.csv")

    if not args.skip_mh:
        sched, elapsed = run_metaheuristic(fixtures, slots, teams, constraints, season, tl,
                                           season_start=season_start, season_end=season_end,
                                           final_day=calendar.get("final_day"))
        results["Metaheuristic"] = (sched, elapsed)
        if sched:
            export_csv(sched, OUTPUT_DIR / "schedule_metaheuristic.csv")

    # --- Validate & collect metrics ---
    print(f"\n{'='*60}")
    print("  VALIDATION & METRICS")
    print(f"{'='*60}")

    solver_meta_map: dict[str, dict] = {}
    reports = []

    for name, (sched, elapsed) in results.items():
        if sched is None:
            print(f"\n  [{name}] skipped — no feasible schedule produced")
            continue
        print(f"\n  [{name}]")
        val_report = validate(sched, teams)
        print_report(val_report)

        # Compute metrics report with solver metadata
        from solvers.leagues.epl.mh_objective import score as mh_score
        penalty = val_report.get("total_penalty_score")
        hard_v  = val_report.get("hard_violation_count", 0)
        soft_v  = val_report.get("soft_violation_count", 0)
        if name == "Metaheuristic":
            try:
                penalty = mh_score(sched, teams)
            except Exception:
                pass

        meta = {
            "solve_time_seconds": elapsed,
            "penalty_score":      penalty,
            "hard_violations":    hard_v,
            "soft_violations":    soft_v,
        }
        solver_meta_map[name] = meta
        report = compute(sched, solver_meta=meta)
        report.label = name
        reports.append(report)

    # --- Historical baseline ---
    seasons = available_seasons()
    hist_report = None
    if seasons:
        latest = sorted(seasons)[-1]
        print(f"\n  [Historical] Loading {latest.name}...")
        hist_schedule = load_season(latest)
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
