"""
Option C — Metaheuristic: Objective / penalty function.

Evaluates a complete Schedule and returns a scalar penalty score.
Lower is better. Zero means all soft constraints satisfied.
Hard constraint violations are given a very large weight so they
dominate the search and are driven out first.
"""
from collections import defaultdict
from scheduler.models import Schedule, ScheduledFixture
from scheduler.data_loader import load_constraints, load_city_groups, load_high_profile_derbies


HARD_PENALTY = 10_000   # per hard violation — dominates soft penalties
SOFT_WEIGHTS: dict[str, int] = {}  # populated from constraints.json at runtime


def _load_weights() -> dict[str, int]:
    constraints = load_constraints()
    return {c["id"]: c["penalty_per_violation"] for c in constraints["soft"]}


def score(schedule: Schedule, teams: dict) -> float:
    global SOFT_WEIGHTS
    if not SOFT_WEIGHTS:
        SOFT_WEIGHTS = _load_weights()

    total = 0.0
    city_groups = load_city_groups()
    city_lookup = {t: city for city, members in city_groups.items() for t in members}
    derbies = set(tuple(sorted(p)) for p in load_high_profile_derbies())

    # --- Hard: min rest days (HC1) ---
    for team_id in teams:
        fixtures = sorted(
            schedule.fixtures_for_team(team_id),
            key=lambda sf: sf.slot.date,
        )
        for i in range(1, len(fixtures)):
            gap = (fixtures[i].slot.date - fixtures[i-1].slot.date).days
            if gap < 3:
                total += HARD_PENALTY * (3 - gap)

    # --- Hard: same-city home clash (HC2) ---
    home_by_date: dict[str, list[str]] = defaultdict(list)
    for sf in schedule.fixtures:
        home_by_date[str(sf.slot.date)].append(sf.home_team_id)

    for date_str, home_teams in home_by_date.items():
        city_count: dict[str, int] = defaultdict(int)
        for t in home_teams:
            city_count[city_lookup.get(t, t)] += 1
        for city, count in city_count.items():
            if count > 1:
                total += HARD_PENALTY * (count - 1)

    # --- Hard: team plays twice on same day ---
    for team_id in teams:
        date_counts: dict[str, int] = defaultdict(int)
        for sf in schedule.fixtures_for_team(team_id):
            date_counts[str(sf.slot.date)] += 1
        for count in date_counts.values():
            if count > 1:
                total += HARD_PENALTY * (count - 1)

    # --- Soft SC1/SC2: consecutive away/home runs ---
    max_run = 3
    for team_id in teams:
        fixtures = sorted(
            schedule.fixtures_for_team(team_id),
            key=lambda sf: sf.slot.date,
        )
        away_run = home_run = 0
        for sf in fixtures:
            if sf.away_team_id == team_id:
                away_run += 1; home_run = 0
            else:
                home_run += 1; away_run = 0
            if away_run > max_run:
                total += SOFT_WEIGHTS.get("SC1", 20)
            if home_run > max_run:
                total += SOFT_WEIGHTS.get("SC2", 20)

    # --- Soft SC3: derby gap ---
    derby_slots: dict[tuple, list] = defaultdict(list)
    for sf in schedule.fixtures:
        pair = tuple(sorted([sf.home_team_id, sf.away_team_id]))
        if pair in derbies:
            derby_slots[pair].append(sf.slot.date)

    for pair, dates in derby_slots.items():
        if len(dates) == 2:
            gap = abs((dates[1] - dates[0]).days)
            if gap < 56:
                total += SOFT_WEIGHTS.get("SC3", 30) * (1 + (56 - gap) // 7)

    return total
