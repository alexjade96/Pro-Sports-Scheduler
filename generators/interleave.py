"""
Generic fixture-block interleaving for formula-based generators.

A generator that builds its fixture list as sequential matchup-type blocks
(e.g. all division games, then all conference games, then all
inter-conference games) produces a list where each team's own games of a
given type are clustered together in a contiguous run — fine for the
generator itself, but a poor input to solvers/slot_filter.py, which infers
each fixture's "natural round" from its position in the list and expects
that position to progress roughly chronologically for every team.

interleave_blocks() merges N such blocks into one list using a weighted
round-robin: at each step it emits the next fixture from whichever block is
furthest behind its proportional share of the output so far, so a block
that's 20% of the total contributes roughly one fixture in every five
output positions instead of one contiguous 20%-sized chunk. This spreads
each team's games across the full list regardless of how many distinct
matchup types or block sizes a league's generator uses — no scheduling
knowledge beyond block sizes.
"""
from __future__ import annotations

from core.models import Fixture


def interleave_blocks(blocks: list[list[Fixture]]) -> list[Fixture]:
    """Weighted round-robin merge of fixture blocks, proportional to block size."""
    blocks = [b for b in blocks if b]  # drop empty blocks
    if not blocks:
        return []
    if len(blocks) == 1:
        return list(blocks[0])

    counts = [0] * len(blocks)
    sizes = [len(b) for b in blocks]
    total = sum(sizes)
    result: list[Fixture] = []

    for _ in range(total):
        i = min(
            (i for i in range(len(blocks)) if counts[i] < sizes[i]),
            key=lambda i: (counts[i] + 1) / sizes[i],
        )
        result.append(blocks[i][counts[i]])
        counts[i] += 1

    return result
