"""
NFL fixture generator.

The NFL schedule is NOT a round-robin. Each team plays exactly 17 games
determined by a rotation formula that changes annually based on:

  1. Division games (6 fixed):
       Home + away vs each of the 3 division rivals.

  2. Intra-conference rotation (4 games):
       One entire AFC/NFC division rotates through every 3 years.
       e.g. AFC North plays every NFC North team — 2 home, 2 away.

  3. Inter-conference rotation (4 games):
       One opposite-conference division, rotating every 4 years.

  4. Same-conference, different division (2 games):
       Two games vs teams that finished in the same standing position
       in their respective divisions the prior year.

  5. 17th game (1 game, since 2021):
       One additional inter-conference game vs a team from the
       opposite conference division that rotates annually.

Slot assignment constraints:
  - Week 1 must include a Thursday night kickoff (season opener).
  - Thanksgiving week: DAL and DET are traditional home hosts; a
    third prime-time game is added on Thanksgiving evening.
  - Each team has exactly one bye week (weeks 6–14).
  - Shared-venue teams (NYJ/NYG, LAC/LAR) cannot be home the same day.

TODO: Implement the full NFL schedule generation formula.
      This requires prior-year standings as input to compute
      strength-of-schedule pairings for games 15–17.

Reference:
  NFL Constitution and Bylaws, Article XIX.
  https://operations.nfl.com/the-game/nfl-schedule/
"""
from __future__ import annotations

from core.models import Fixture, Team


def generate_fixtures(teams: dict[str, Team]) -> list[Fixture]:
    """
    Generate all 272 NFL regular-season fixtures.

    Args:
        teams: mapping of team_id → Team, must contain all 32 NFL teams.

    Returns:
        List of Fixture objects (unscheduled; assign to slots via solver).

    Raises:
        NotImplementedError: until the NFL schedule formula is implemented.
    """
    raise NotImplementedError(
        "NFL fixture generation requires the annual rotation formula and "
        "prior-year standings. See module docstring for the algorithm spec."
    )
