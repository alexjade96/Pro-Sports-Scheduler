"""
NBA fixture generator — 2025-26 regular season (1,230 games).

Each of 30 teams plays exactly 82 games (41 home, 41 away):

  Game breakdown per team
  ───────────────────────
  16  Division games        : 4 rivals × 4 games (2H + 2A each)
  36  Conference non-div    : 10 same-conf non-division opponents
                              6 opponents × 4 games (2H+2A) + 4 opponents × 3 games
  30  Inter-conference      : 15 opposite-conference teams × 2 games (1H + 1A each)
  ───
  82  Total

Conference non-division split (6×4 + 4×3 = 36) uses a symmetric rotation
so each unordered pair agrees on game count. The heavy/light split is
determined by (pos_a + pos_b) % 5: values {0,1,2} → 4 games; {3,4} → 3 games.
This ensures every team has exactly 6 heavy and 4 light opponents in each
pair of non-division conference divisions → 3+3 heavy split per team.

Home/away balance in 3-game series: (pos_a+pos_b)%5 == 3 → team_a gets 2H,
(pos_a+pos_b)%5 == 4 → team_b gets 2H. This gives each team exactly one
extra-home and one extra-away 3-game series per opponent division, balancing
out to 18H + 18A from conference non-division games.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.models import Fixture, Team

_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "leagues" / "nba"
_TEAMS_PATH = _DATA_DIR / "teams.json"

# Divisions (East: Atlantic, Central, Southeast; West: Pacific, Northwest, Southwest)
_EAST_DIVS = ["Atlantic", "Central", "Southeast"]
_WEST_DIVS = ["Pacific", "Northwest", "Southwest"]


def _load_division_map() -> dict[str, str]:
    with open(_TEAMS_PATH) as f:
        raw = json.load(f)
    return {t["id"]: t["division"] for t in raw["teams"]}


def _load_conference_map() -> dict[str, str]:
    with open(_TEAMS_PATH) as f:
        raw = json.load(f)
    return {t["id"]: t["conference"] for t in raw["teams"]}


def generate_fixtures(teams: dict[str, Team]) -> list[Fixture]:
    """
    Generate all 1,230 NBA 2025-26 regular-season fixtures (unscheduled).

    Args:
        teams: mapping of team_id → Team, all 30 NBA teams.

    Returns:
        List of Fixture objects. Slot assignment is done by the solver.
        Each team appears in exactly 82 fixtures (41 home, 41 away).
    """
    div_map = _load_division_map()
    conf_map = _load_conference_map()

    by_div: dict[str, list[str]] = {}
    by_conf: dict[str, list[str]] = {}
    for tid in sorted(teams):
        d = div_map.get(tid)
        c = conf_map.get(tid)
        if d:
            by_div.setdefault(d, []).append(tid)
        if c:
            by_conf.setdefault(c, []).append(tid)

    fixtures: list[Fixture] = []
    fid = 0

    # ── 1. Division games: 4 per pair (2H + 2A) ───────────────────────────────
    # 6 divisions × C(5,2)=10 pairs × 4 games = 240 fixtures
    # Achieved by iterating all ordered pairs (home→away) twice.
    for div_teams in by_div.values():
        for home in div_teams:
            for away in div_teams:
                if home == away:
                    continue
                # 2 home games for this ordered pair
                for _ in range(2):
                    fixtures.append(Fixture(
                        fixture_id=f"DIV_{fid}",
                        home_team_id=home,
                        away_team_id=away,
                    ))
                    fid += 1

    # ── 2. Conference non-division games ──────────────────────────────────────
    # For each pair of divisions within the same conference:
    #   25 team-pairs: (pos_a+pos_b)%5 ∈ {0,1,2} → 4 games (2H+2A)
    #                  (pos_a+pos_b)%5 ∈ {3,4}   → 3 games
    # Per-division-pair totals: 15 heavy×4 + 10 light×3 = 60+30 = 90 fixtures
    # 3 such pairs per conference × 2 conferences = 6 pairs → 540 fixtures

    for conf_divs in [_EAST_DIVS, _WEST_DIVS]:
        for ii, div_a in enumerate(conf_divs):
            for jj, div_b in enumerate(conf_divs):
                if ii >= jj:
                    continue
                teams_a = by_div.get(div_a, [])
                teams_b = by_div.get(div_b, [])
                for pos_a, ta in enumerate(teams_a):
                    for pos_b, tb in enumerate(teams_b):
                        mod = (pos_a + pos_b) % 5
                        if mod in (0, 1, 2):
                            # 4 games: 2H+2A for each team
                            for _ in range(2):
                                fixtures.append(Fixture(
                                    fixture_id=f"CND4H_{fid}",
                                    home_team_id=ta,
                                    away_team_id=tb,
                                ))
                                fid += 1
                            for _ in range(2):
                                fixtures.append(Fixture(
                                    fixture_id=f"CND4A_{fid}",
                                    home_team_id=tb,
                                    away_team_id=ta,
                                ))
                                fid += 1
                        elif mod == 3:
                            # 3 games: ta gets 2H+1A
                            for _ in range(2):
                                fixtures.append(Fixture(
                                    fixture_id=f"CND3HA_{fid}",
                                    home_team_id=ta,
                                    away_team_id=tb,
                                ))
                                fid += 1
                            fixtures.append(Fixture(
                                fixture_id=f"CND3AB_{fid}",
                                home_team_id=tb,
                                away_team_id=ta,
                            ))
                            fid += 1
                        else:  # mod == 4
                            # 3 games: tb gets 2H+1A
                            fixtures.append(Fixture(
                                fixture_id=f"CND3H_{fid}",
                                home_team_id=ta,
                                away_team_id=tb,
                            ))
                            fid += 1
                            for _ in range(2):
                                fixtures.append(Fixture(
                                    fixture_id=f"CND3A_{fid}",
                                    home_team_id=tb,
                                    away_team_id=ta,
                                ))
                                fid += 1

    # ── 3. Inter-conference games: 2 per pair (1H + 1A each) ──────────────────
    # 15 East × 15 West = 225 pairs × 2 games = 450 fixtures
    east_teams = by_conf.get("East", [])
    west_teams = by_conf.get("West", [])
    for i, te in enumerate(east_teams):
        for j, tw in enumerate(west_teams):
            # Parity determines who hosts game 1; game 2 is reversed
            if (i + j) % 2 == 0:
                home1, away1 = te, tw
            else:
                home1, away1 = tw, te
            fixtures.append(Fixture(
                fixture_id=f"INTER1_{fid}",
                home_team_id=home1,
                away_team_id=away1,
            ))
            fid += 1
            fixtures.append(Fixture(
                fixture_id=f"INTER2_{fid}",
                home_team_id=away1,
                away_team_id=home1,
            ))
            fid += 1

    return fixtures
