"""
NFL fixture generator — 2025-26 regular season (272 games).

Each of 32 teams plays exactly 17 games across 18 weeks (one bye week):

  Game breakdown per team
  ───────────────────────
  6  Division games      : H + A vs each of 3 division rivals
  4  Intra-conf rotation : vs all 4 teams in one same-conference division
  4  Inter-conf rotation : vs all 4 teams in one opposite-conference division
  2  Standings cross-div : 1 game each vs 2 same-conf division winners/losers
                           matched by prior-year division finish position
  1  17th game           : 1 inter-conf game matched by prior-year standing

Rotation tables are derived from the actual 2025 nflverse game data.
Home/away split within rotation pairs is deterministic based on team-list
position; it approximates but does not exactly reproduce the official schedule.
"""
from __future__ import annotations

import json
from itertools import permutations
from pathlib import Path

from core.models import Fixture, Team
from generators.interleave import interleave_blocks

# ── 2025 rotation configuration ───────────────────────────────────────────────
# Derived from nflverse 2025 REG game pairings (see data/leagues/nfl/historical/)

# Primary intra-conference matchup: each division plays all 4 teams of partner division
_INTRA_2025: dict[str, str] = {
    "AFC North": "AFC East",
    "AFC East":  "AFC North",
    "AFC South": "AFC West",
    "AFC West":  "AFC South",
    "NFC North": "NFC East",
    "NFC East":  "NFC North",
    "NFC South": "NFC West",
    "NFC West":  "NFC South",
}

# Primary inter-conference matchup: each division plays all 4 teams of opp-conf division
_INTER_2025: dict[str, str] = {
    "AFC North": "NFC North",
    "AFC East":  "NFC South",
    "AFC South": "NFC West",
    "AFC West":  "NFC East",
    "NFC North": "AFC North",
    "NFC South": "AFC East",
    "NFC West":  "AFC South",
    "NFC East":  "AFC West",
}

# 17th-game inter-conference division (1 game per team, matched by standings rank)
_SEVENTEENTH_2025: dict[str, str] = {
    "AFC North": "NFC West",
    "AFC East":  "NFC East",
    "AFC South": "NFC South",
    "AFC West":  "NFC North",
    "NFC North": "AFC West",
    "NFC South": "AFC South",
    "NFC West":  "AFC North",
    "NFC East":  "AFC East",
}

# For standings-based games (2 per team): the 2 same-conf divisions not in _INTRA_2025
_STANDINGS_DIVS_2025: dict[str, list[str]] = {
    "AFC North": ["AFC South", "AFC West"],
    "AFC East":  ["AFC South", "AFC West"],
    "AFC South": ["AFC North", "AFC East"],
    "AFC West":  ["AFC North", "AFC East"],
    "NFC North": ["NFC South", "NFC West"],
    "NFC East":  ["NFC South", "NFC West"],
    "NFC South": ["NFC North", "NFC East"],
    "NFC West":  ["NFC North", "NFC East"],
}

_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "leagues" / "nfl"
_STANDINGS_PATH = _DATA_DIR / "standings_2024.json"
_TEAMS_PATH = _DATA_DIR / "teams.json"


def _load_division_map() -> dict[str, str]:
    """Returns {team_id: division_name} from teams.json."""
    with open(_TEAMS_PATH) as f:
        raw = json.load(f)
    return {t["id"]: t["division"] for t in raw["teams"]}


# nflverse uses "LA" for the Rams; our teams.json uses "LAR"
_TEAM_REMAP = {"LA": "LAR"}


def _norm(tid: str) -> str:
    return _TEAM_REMAP.get(tid, tid)


def _load_standings() -> dict[str, list[str]]:
    """Returns {division: [team_id rank-1, rank-2, rank-3, rank-4]}."""
    if not _STANDINGS_PATH.exists():
        return {}
    with open(_STANDINGS_PATH) as f:
        data = json.load(f)
    return {
        div: [_norm(t) for t in info["rank"]]
        for div, info in data.items() if "rank" in info
    }


def _rotation_games(
    div_a_teams: list[str],
    div_b_teams: list[str],
    fid_start: int,
    prefix: str,
) -> list[Fixture]:
    """
    Generate 16 games (all 4 teams in div_a vs all 4 teams in div_b, each once).
    Home/away is interleaved so each team plays exactly 2H + 2A in the matchup.
    Returns fixtures where div_a teams are conceptually the "initiating" side.
    """
    fixtures: list[Fixture] = []
    n = len(div_a_teams)
    for i, ta in enumerate(div_a_teams):
        for j, tb in enumerate(div_b_teams):
            # ta is home when (i + j) is even, away otherwise
            if (i + j) % 2 == 0:
                home, away = ta, tb
            else:
                home, away = tb, ta
            fixtures.append(
                Fixture(
                    fixture_id=f"{prefix}_{fid_start + i * n + j}",
                    home_team_id=home,
                    away_team_id=away,
                )
            )
    return fixtures


def generate_fixtures(teams: dict[str, Team]) -> list[Fixture]:
    """
    Generate all 272 NFL 2025 regular-season fixtures (unscheduled).

    Args:
        teams: mapping of team_id → Team, all 32 NFL teams.

    Returns:
        List of Fixture objects. Slot assignment is done by the solver.
    """
    standings = _load_standings()
    div_map = _load_division_map()

    # Group teams by division, sort canonically (alphabetical by id)
    by_div: dict[str, list[str]] = {}
    for tid in teams:
        div = div_map.get(tid)
        if div:
            by_div.setdefault(div, []).append(tid)
    for div in by_div:
        by_div[div].sort()

    fixtures: list[Fixture] = []
    fid = 0
    block_bounds: list[int] = [0]   # index boundaries; block i = fixtures[block_bounds[i]:block_bounds[i+1]]

    # ── 1. Division games (6 per team, 96 total) ──────────────────────────────
    seen_div: set[frozenset[str]] = set()
    for div, div_teams in by_div.items():
        for home in div_teams:
            for away in div_teams:
                if home == away:
                    continue
                pair = frozenset({home, away})
                # H+A: generate both directions
                fixtures.append(Fixture(
                    fixture_id=f"DIV_{fid}",
                    home_team_id=home,
                    away_team_id=away,
                ))
                fid += 1

    block_bounds.append(len(fixtures))

    # ── 2. Primary intra-conference rotation (4 per team, 64 total) ──────────
    seen_intra: set[tuple[str, str]] = set()
    for div_a, div_b in _INTRA_2025.items():
        pair_key = tuple(sorted([div_a, div_b]))
        if pair_key in seen_intra:
            continue
        seen_intra.add(pair_key)
        teams_a = by_div.get(div_a, [])
        teams_b = by_div.get(div_b, [])
        fixtures.extend(_rotation_games(teams_a, teams_b, fid, "INTRA"))
        fid += len(teams_a) * len(teams_b)

    block_bounds.append(len(fixtures))

    # ── 3. Primary inter-conference rotation (4 per team, 64 total) ──────────
    seen_inter: set[tuple[str, str]] = set()
    for div_a, div_b in _INTER_2025.items():
        pair_key = tuple(sorted([div_a, div_b]))
        if pair_key in seen_inter:
            continue
        seen_inter.add(pair_key)
        teams_a = by_div.get(div_a, [])
        teams_b = by_div.get(div_b, [])
        fixtures.extend(_rotation_games(teams_a, teams_b, fid, "INTER"))
        fid += len(teams_a) * len(teams_b)

    block_bounds.append(len(fixtures))

    # ── 4. Standings-based cross-division games (2 per team, 32 total) ────────
    # Each team plays 1 game vs the team from each of 2 remaining same-conf
    # divisions that finished in the same position the prior year.
    if standings:
        seen_standings: set[frozenset[str]] = set()
        for div, rival_divs in _STANDINGS_DIVS_2025.items():
            div_rank = standings.get(div, by_div.get(div, []))
            for rank_idx, team_a in enumerate(div_rank[:4]):
                for rival_div in rival_divs:
                    rival_rank = standings.get(rival_div, by_div.get(rival_div, []))
                    if rank_idx >= len(rival_rank):
                        continue
                    team_b = rival_rank[rank_idx]
                    pair = frozenset({team_a, team_b})
                    if pair in seen_standings:
                        continue
                    seen_standings.add(pair)
                    # Alternate home/away by rank position parity
                    if rank_idx % 2 == 0:
                        home, away = team_a, team_b
                    else:
                        home, away = team_b, team_a
                    fixtures.append(Fixture(
                        fixture_id=f"STD_{fid}",
                        home_team_id=home,
                        away_team_id=away,
                    ))
                    fid += 1
    else:
        # Fallback: no standings data — pair by position in canonical sort order
        seen_fallback: set[frozenset[str]] = set()
        for div, rival_divs in _STANDINGS_DIVS_2025.items():
            for pos, team_a in enumerate(by_div.get(div, [])[:4]):
                for rival_div in rival_divs:
                    rival_teams = by_div.get(rival_div, [])
                    if pos >= len(rival_teams):
                        continue
                    team_b = rival_teams[pos]
                    pair = frozenset({team_a, team_b})
                    if pair in seen_fallback:
                        continue
                    seen_fallback.add(pair)
                    fixtures.append(Fixture(
                        fixture_id=f"STD_{fid}",
                        home_team_id=team_a if pos % 2 == 0 else team_b,
                        away_team_id=team_b if pos % 2 == 0 else team_a,
                    ))
                    fid += 1

    block_bounds.append(len(fixtures))

    # ── 5. 17th game (1 per team, 16 total) ──────────────────────────────────
    # 1 inter-conference game matched by prior-year division rank.
    seen_17: set[frozenset[str]] = set()
    for div_a, div_b in _SEVENTEENTH_2025.items():
        pair_key = tuple(sorted([div_a, div_b]))
        if pair_key in seen_17:
            continue
        seen_17.add(pair_key)
        rank_a = standings.get(div_a, by_div.get(div_a, []))
        rank_b = standings.get(div_b, by_div.get(div_b, []))
        for pos in range(min(len(rank_a), len(rank_b), 4)):
            team_a = rank_a[pos]
            team_b = rank_b[pos]
            # Alternate home: even rank positions → div_a is home
            if pos % 2 == 0:
                home, away = team_a, team_b
            else:
                home, away = team_b, team_a
            fixtures.append(Fixture(
                fixture_id=f"G17_{fid}",
                home_team_id=home,
                away_team_id=away,
            ))
            fid += 1

    block_bounds.append(len(fixtures))

    # Merge the matchup-type blocks (division/intra-conf/inter-conf/standings/
    # 17th-game) into a single round-interleaved order — see generators/
    # interleave.py. Without this, a team's entire block of e.g. division
    # games would cluster at one end of the list, which solvers/slot_filter.py
    # would then read as "these all happen in the season's first few weeks."
    blocks = [fixtures[block_bounds[i]:block_bounds[i + 1]] for i in range(len(block_bounds) - 1)]
    return interleave_blocks(blocks)
