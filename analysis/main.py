"""
Analysis entry point.

Modes
-----
  1. Metrics only on historical data:
       python -m analysis.main --historical data/historical/2023-24.csv

  2. Accuracy: generated schedule vs historical baseline:
       python -m analysis.main \\
         --generated output/schedule_cp_sat.csv \\
         --historical data/historical/2023-24.csv

  3. Solver comparison (pass multiple generated CSVs):
       python -m analysis.main \\
         --solver-compare \\
           output/schedule_cp_sat.csv \\
           output/schedule_ilp.csv \\
           output/schedule_metaheuristic.csv \\
         --historical data/historical/2023-24.csv

  4. Full report (accuracy + solver comparison + per-team):
       same as mode 3 — all three outputs are generated when all flags present.

Output
------
  output/report_accuracy.txt / .html
  output/report_solvers.txt  / .html
  output/report_per_team.txt
"""
import argparse
import sys
from pathlib import Path

from analysis.historical_loader import load_season
from analysis.metrics import compute, MetricsReport
from analysis.comparator import compare_to_historical, compare_solvers
from analysis import report as rpt


def _load_generated_csv(csv_path: str) -> "Schedule":
    """
    Re-hydrates a generated schedule CSV (from any solver's main.py)
    back into a Schedule object for metric computation.
    """
    import csv as _csv
    from datetime import date as _date
    from scheduler.models import Fixture, Slot, ScheduledFixture, Schedule

    path  = Path(csv_path)
    label = path.stem
    fixtures = []
    with open(path, newline="") as f:
        reader = _csv.DictReader(f)
        for row in reader:
            slot = Slot(
                date=_date.fromisoformat(row["date"]),
                kickoff=row["kickoff"],
                day_of_week=row["day"],
            )
            fixture = Fixture(
                fixture_id=row["fixture_id"],
                home_team_id=row["home"],
                away_team_id=row["away"],
            )
            fixtures.append(ScheduledFixture(fixture=fixture, slot=slot))
    return Schedule(season=label, fixtures=fixtures)


def main():
    parser = argparse.ArgumentParser(description="EPL Scheduler — Analysis Report")
    parser.add_argument("--historical",     type=str, help="Path to historical CSV (football-data.co.uk format)")
    parser.add_argument("--generated",      type=str, help="Path to a single generated schedule CSV")
    parser.add_argument("--solver-compare", nargs="+", metavar="CSV", help="Two or more generated schedule CSVs to compare")
    parser.add_argument("--no-html",        action="store_true", help="Skip HTML output")
    args = parser.parse_args()

    hist_report: MetricsReport | None = None
    gen_reports: list[MetricsReport]  = []

    # --- Load historical ---
    if args.historical:
        hist_schedule = load_season(args.historical)
        hist_report   = compute(hist_schedule)
        print(f"[Analysis] Loaded historical: {hist_report.label} ({hist_report.total_fixtures} fixtures)")

    # --- Load single generated ---
    if args.generated:
        gen_schedule = _load_generated_csv(args.generated)
        gen_report   = compute(gen_schedule)
        gen_reports.append(gen_report)
        print(f"[Analysis] Loaded generated: {gen_report.label} ({gen_report.total_fixtures} fixtures)")

    # --- Load multiple for solver comparison ---
    if args.solver_compare:
        for csv_path in args.solver_compare:
            gen_schedule = _load_generated_csv(csv_path)
            gen_report   = compute(gen_schedule)
            gen_reports.append(gen_report)
            print(f"[Analysis] Loaded solver: {gen_report.label} ({gen_report.total_fixtures} fixtures)")

    if not hist_report and not gen_reports:
        print("Nothing to analyse. Pass --historical and/or --generated / --solver-compare.")
        sys.exit(1)

    accuracy_cmp = None
    solver_cmp   = None
    per_team_txt = None

    # --- Accuracy comparison ---
    if gen_reports and hist_report:
        # Compare first (or only) generated schedule to historical
        accuracy_cmp = compare_to_historical(gen_reports[0], hist_report)
        text = rpt.render_text_accuracy(accuracy_cmp)
        print(text)
        saved = rpt.save(text, "report_accuracy.txt")
        print(f"[Analysis] Saved: {saved}")

    # --- Solver comparison ---
    if len(gen_reports) > 1:
        solver_cmp = compare_solvers(gen_reports)
        text = rpt.render_text_solver_comparison(solver_cmp)
        print(text)
        saved = rpt.save(text, "report_solvers.txt")
        print(f"[Analysis] Saved: {saved}")

    # --- Per-team table (if any generated reports) ---
    all_reports = gen_reports[:]
    if hist_report:
        all_reports.append(hist_report)
    if all_reports:
        per_team_txt = rpt.render_per_team_table(all_reports)
        saved = rpt.save(per_team_txt, "report_per_team.txt")
        print(f"[Analysis] Per-team table saved: {saved}")

    # --- HTML ---
    if not args.no_html and (accuracy_cmp or solver_cmp):
        html = rpt.render_html(
            accuracy_comparison=accuracy_cmp,
            solver_comparison=solver_cmp,
            per_team_table=per_team_txt,
        )
        saved = rpt.save(html, "report.html")
        print(f"[Analysis] HTML report saved: {saved}")


if __name__ == "__main__":
    main()
