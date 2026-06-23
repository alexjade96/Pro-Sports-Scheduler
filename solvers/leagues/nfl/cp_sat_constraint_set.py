"""
NFL constraint set stub for the CP-SAT solver.

Full NFL constraint implementation is a planned future milestone.
This stub satisfies the CpSatConstraintSet protocol so the solver
core can be called without modification once NFL constraints are ready.
"""
from __future__ import annotations

from datetime import date

from solvers.slot_filter import build_eligible_slots, log_filter_stats


class NFLCpSatConstraintSet:
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

    def build_eligible_slots(self, fixtures, slots) -> dict[str, list[str]]:
        eligible = build_eligible_slots(
            fixtures, slots, self._season_start, self._season_end, window_rounds=3
        )
        log_filter_stats(eligible)
        return eligible

    def add_hard_constraints(self, model, x, fixtures, slots, teams) -> None:
        from solvers.cp_sat.constraints import (
            add_each_fixture_assigned_exactly_once,
            add_team_plays_at_most_once_per_slot,
            add_min_rest_days,
        )
        add_each_fixture_assigned_exactly_once(model, x, fixtures, slots)
        add_team_plays_at_most_once_per_slot(model, x, fixtures, slots, teams)
        min_rest = self._hard.get("HC1", {}).get("value", 6)
        add_min_rest_days(model, x, fixtures, slots, teams, min_rest)

    def add_soft_constraints(self, model, x, fixtures, slots, teams) -> list:
        return []
