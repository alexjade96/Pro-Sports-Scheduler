"""
Post-solve validation: checks every hard and soft constraint against
a completed Schedule and returns a structured report.
Shared by all three solver options for consistent output comparison.
"""
from datetime import timedelta
from collections import defaultdict

from scheduler.models import Schedule, ScheduledFixture
from scheduler.data_loader import load_constraints, load_city_groups


def validate(schedule: Schedule, teams: dict) -> dict:
    constraints = load_constraints()
    city_groups = load_city_groups()

    hard_violations: list[dict] = []
    soft_violations: list[dict] = []
    total_penalty = 0

    # --- HC1: Minimum rest days ---
    min_rest = next(c["value"] for c in constraints["hard"] if c["id"] == "HC1")
    for team_id in teams:
        team_fixtures = sorted(
            schedule.fixtures_for_team(team_id),
            key=lambda sf: sf.slot.date
        )
        for i in range(1, len(team_fixtures)):
            gap = (team_fixtures[i].slot.date - team_fixtures[i-1].slot.date).days
            if gap < min_rest:
                hard_violations.append({
                    "constraint": "HC1",
                    "team": team_id,
                    "fixture_a": team_fixtures[i-1].fixture.fixture_id,
                    "fixture_b": team_fixtures[i].fixture.fixture_id,
                    "gap_days": gap,
                })

    # --- HC2: No same-city home clash ---
    home_by_date: dict[str, list[str]] = defaultdict(list)
    for sf in schedule.fixtures:
        home_by_date[str(sf.slot.date)].append(sf.home_team_id)

    city_lookup = {}
    for city, members in city_groups.items():
        for m in members:
            city_lookup[m] = city

    for date_str, home_teams in home_by_date.items():
        city_count: dict[str, list[str]] = defaultdict(list)
        for t in home_teams:
            city_count[city_lookup.get(t, t)].append(t)
        for city, clashing in city_count.items():
            if len(clashing) > 1:
                hard_violations.append({
                    "constraint": "HC2",
                    "date": date_str,
                    "city": city,
                    "teams": clashing,
                })

    # --- SC1/SC2: Consecutive home/away runs ---
    sc_lookup = {c["id"]: c for c in constraints["soft"]}
    max_away = sc_lookup["SC1"]["value"]
    max_home = sc_lookup["SC2"]["value"]
    penalty_away = sc_lookup["SC1"]["penalty_per_violation"]
    penalty_home = sc_lookup["SC2"]["penalty_per_violation"]

    for team_id in teams:
        team_fixtures = sorted(
            schedule.fixtures_for_team(team_id),
            key=lambda sf: sf.slot.date
        )
        away_run = home_run = 0
        for sf in team_fixtures:
            if sf.away_team_id == team_id:
                away_run += 1
                home_run = 0
            else:
                home_run += 1
                away_run = 0
            if away_run > max_away:
                soft_violations.append({
                    "constraint": "SC1",
                    "team": team_id,
                    "run": away_run,
                })
                total_penalty += penalty_away
            if home_run > max_home:
                soft_violations.append({
                    "constraint": "SC2",
                    "team": team_id,
                    "run": home_run,
                })
                total_penalty += penalty_home

    return {
        "hard_violations": hard_violations,
        "hard_violation_count": len(hard_violations),
        "soft_violations": soft_violations,
        "soft_violation_count": len(soft_violations),
        "total_penalty_score": total_penalty,
        "feasible": len(hard_violations) == 0,
    }


def print_report(report: dict) -> None:
    print(f"\n{'='*50}")
    print(f"SCHEDULE VALIDATION REPORT")
    print(f"{'='*50}")
    print(f"Feasible (zero hard violations): {report['feasible']}")
    print(f"Hard violations : {report['hard_violation_count']}")
    print(f"Soft violations : {report['soft_violation_count']}")
    print(f"Total penalty   : {report['total_penalty_score']}")
    if report["hard_violations"]:
        print("\nHard Violations:")
        for v in report["hard_violations"]:
            print(f"  {v}")
    print(f"{'='*50}\n")
