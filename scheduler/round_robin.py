"""
Generates all fixtures for a double round-robin tournament using the
circle (polygon) method. Every team plays every other team once at home
and once away across two half-seasons.

Reference: Knuth TAOCP Vol 4A, or any combinatorics textbook.
"""
from scheduler.models import Fixture, Team


def _circle_method_rounds(team_ids: list[str]) -> list[list[tuple[str, str]]]:
    """
    Returns N-1 rounds of N//2 pairs for N teams (N must be even).
    Fixed team is team_ids[0]; the rest rotate clockwise each round.
    """
    teams = list(team_ids)
    if len(teams) % 2 != 0:
        teams.append("BYE")

    n = len(teams)
    fixed = teams[0]
    rotating = teams[1:]
    rounds = []

    for _ in range(n - 1):
        round_pairs = [(fixed, rotating[0])]
        for i in range(1, n // 2):
            round_pairs.append((rotating[-(i)], rotating[i]))
        rounds.append(round_pairs)
        rotating = [rotating[-1]] + rotating[:-1]  # rotate right

    return rounds


def generate_fixtures(teams: dict[str, Team]) -> list[Fixture]:
    """
    Generates 380 fixtures for a 20-team double round-robin.
    First 190 are the 'first half' (arbitrary home/away assignment).
    Second 190 flip home and away.
    """
    team_ids = list(teams.keys())
    first_half_rounds = _circle_method_rounds(team_ids)

    fixtures: list[Fixture] = []
    fixture_num = 1

    for round_idx, pairs in enumerate(first_half_rounds):
        for home, away in pairs:
            if home == "BYE" or away == "BYE":
                continue
            fixtures.append(Fixture(
                fixture_id=f"F{fixture_num:03d}",
                home_team_id=home,
                away_team_id=away,
            ))
            fixture_num += 1

    # Second half: swap home/away
    for round_idx, pairs in enumerate(first_half_rounds):
        for home, away in pairs:
            if home == "BYE" or away == "BYE":
                continue
            fixtures.append(Fixture(
                fixture_id=f"F{fixture_num:03d}",
                home_team_id=away,   # swapped
                away_team_id=home,   # swapped
            ))
            fixture_num += 1

    return fixtures
