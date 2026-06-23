"""
NFL constraint set stub for the metaheuristic solver.

Full NFL constraint implementation is a planned future milestone.
"""
from __future__ import annotations

from datetime import date

from core.models import Fixture, Slot


class NFLMHConstraintSet:
    def __init__(
        self,
        constraint_config: dict,
        season_start: date,
        season_end: date,
    ) -> None:
        self._hard = {c["id"]: c for c in constraint_config.get("hard", [])}
        self._soft = {c["id"]: c for c in constraint_config.get("soft", [])}
        self._season_start = season_start
        self._season_end = season_end

    def pre_assign(
        self,
        fixtures: list[Fixture],
        slots: list[Slot],
    ) -> tuple[list[tuple[Fixture, Slot]], set[str]]:
        return [], set()

    def greedy_params(self) -> dict:
        return {
            "min_rest_days": self._hard.get("HC1", {}).get("value", 6),
            "day_caps": {},
        }

    def score(self, schedule, teams) -> float:
        return 0.0
