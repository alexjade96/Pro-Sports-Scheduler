"""
NBA fixture generator.

Each team plays 82 games distributed as follows:
  - Division rivals    : 4 games each × 4 rivals = 16 games
  - Conference non-div : 3 or 4 games each × 10 teams = 36 games
  - Opposite conference: 2 games each × 15 teams = 30 games
  Total: 82 games

The 3-vs-4 game split for conference non-division opponents rotates
annually so over a 2-year cycle each team plays every conference
non-division opponent an equal number of times.

Arena availability must be considered for:
  - Teams sharing arenas with NHL franchises (many have concurrent seasons)
  - Special events (concerts, other bookings) — typically modelled as
    a set of arena-unavailability windows per team

Key scheduling constraints unique to the NBA:
  - No three-in-three-nights (HC3)
  - Minimise back-to-backs, especially consecutive road back-to-backs (SC1, SC2)
  - Christmas Day (5 marquee games, fixed teams — SC5)
  - All-Star break (no games in a ~10-day window)
  - In-Season Tournament group stage (November–December, specific game slots)

TODO: Implement the NBA fixture generation algorithm.
      This requires:
        1. The annual 3-vs-4 conference rotation table
        2. Arena unavailability windows per team (from arena operations)
        3. In-Season Tournament group assignments (announced annually by NBA)

Reference:
  NBA Official Rules: https://official.nba.com/rulebook/
  CBA Player Rest provisions: Article XXII, Section 3
"""
from __future__ import annotations

from core.models import Fixture, Team


def generate_fixtures(teams: dict[str, Team]) -> list[Fixture]:
    """
    Generate all 1,230 NBA regular-season fixtures.

    Args:
        teams: mapping of team_id → Team, must contain all 30 NBA teams.

    Returns:
        List of Fixture objects (unscheduled; assign to slots via solver).

    Raises:
        NotImplementedError: until the NBA distribution formula is implemented.
    """
    raise NotImplementedError(
        "NBA fixture generation requires the annual 3-vs-4 conference rotation "
        "table and arena unavailability windows. See module docstring for the algorithm spec."
    )
