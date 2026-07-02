"""
Generic "natural round" assignment for any pairwise fixture list.

Assigns each fixture the earliest round index (0-indexed) in which neither
of its two teams has already been assigned a fixture — the standard greedy
edge-coloring heuristic for round-robin-style scheduling. This makes no
assumption about team count, season length, or fixture-generation formula,
so it produces a meaningful round structure for any league:

  - EPL's generator already emits fixtures in strict round order (10 teams'
    worth of pairs per round), so this reconstructs EPL's existing 38
    rounds of 10 exactly.
  - NFL/NBA's generators emit fixtures grouped by matchup-type block
    (division, then conference, then inter-conference, ...) rather than
    round-interleaved, so running this directly over their raw output would
    still pack each block into its own low/high round range. Interleaving
    the blocks first (see generators/interleave.py) before calling this
    produces a much more realistic, evenly-spread round structure.

Used by solvers/slot_filter.py in place of the previous hardcoded
`n_rounds = 38` / `_FIXTURES_PER_ROUND = 10` constants.
"""
from __future__ import annotations

from collections import defaultdict

from core.models import Fixture


def assign_natural_rounds(fixtures: list[Fixture]) -> dict[str, int]:
    """Returns {fixture_id: round_index} via greedy earliest-available-round
    assignment. Round indices are 0-indexed and packed as tightly as each
    team's own fixture count allows."""
    team_rounds_used: dict[str, set[int]] = defaultdict(set)
    rounds: dict[str, int] = {}

    for fixture in fixtures:
        used_home = team_rounds_used[fixture.home_team_id]
        used_away = team_rounds_used[fixture.away_team_id]
        r = 0
        while r in used_home or r in used_away:
            r += 1
        used_home.add(r)
        used_away.add(r)
        rounds[fixture.fixture_id] = r

    return rounds
