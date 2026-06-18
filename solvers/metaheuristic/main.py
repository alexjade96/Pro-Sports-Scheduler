"""
Option C entry point — Metaheuristic (Simulated Annealing + Tabu).
Usage: python -m option_c_metaheuristic.main
"""
import csv
from pathlib import Path

from core.data_loader import load_teams, load_calendar, load_constraints, generate_slots
from leagues.epl.generator import generate_fixtures
from core.validator import validate, print_report
from solvers.metaheuristic.solver import solve


OUTPUT_DIR = Path(__file__).parent.parent / "output"


def export_csv(schedule, path: Path) -> None:
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
    print(f"Schedule exported to {path}")


def main():
    teams       = load_teams()
    calendar    = load_calendar()
    constraints = load_constraints()
    slots       = generate_slots(calendar)
    fixtures    = generate_fixtures(teams)

    print(f"Teams: {len(teams)} | Fixtures: {len(fixtures)} | Slots available: {len(slots)}")

    schedule = solve(
        fixtures=fixtures,
        slots=slots,
        teams=teams,
        season=calendar["season"],
        initial_temp=5000.0,
        cooling_rate=0.995,
        max_iterations=100_000,
        tabu_size=200,
        time_limit_seconds=300,
    )

    report = validate(schedule, teams)
    print_report(report)
    OUTPUT_DIR.mkdir(exist_ok=True)
    export_csv(schedule, OUTPUT_DIR / "schedule_metaheuristic.csv")


if __name__ == "__main__":
    main()
