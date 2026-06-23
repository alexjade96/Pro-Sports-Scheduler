"""
Protocol interfaces for league-specific constraint sets.

Each league provides three implementations (one per solver type) placed under
``solvers/leagues/<league>/``.  The generic solver cores import only these
protocols — they contain no league-specific logic.

Usage
-----
    from solvers.leagues.epl.cp_sat_constraint_set import EPLCpSatConstraintSet
    from solvers.leagues.nfl.cp_sat_constraint_set import NFLCpSatConstraintSet

    constraint_set = EPLCpSatConstraintSet(constraint_config, season_start, season_end)
    schedule = cp_sat_solver.solve(fixtures, slots, teams, constraint_set, season)
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CpSatConstraintSet(Protocol):
    """Interface expected by ``solvers/cp_sat/solver.py``."""

    def build_eligible_slots(
        self,
        fixtures: list,
        slots: list,
    ) -> dict[str, list[str]]:
        """Return ``{fixture_id: [slot_id, ...]}``.

        Only slot IDs in the returned lists will have decision variables
        created.  Returning a singleton list pins a fixture to one slot.
        """
        ...

    def add_hard_constraints(
        self,
        model: Any,
        x: dict,
        fixtures: list,
        slots: list,
        teams: dict,
    ) -> None:
        """Add all hard constraints directly to the CP-SAT model."""
        ...

    def add_soft_constraints(
        self,
        model: Any,
        x: dict,
        fixtures: list,
        slots: list,
        teams: dict,
    ) -> list[tuple[int, Any]]:
        """Add soft constraints and return ``[(weight, BoolVar)]`` penalty terms."""
        ...


@runtime_checkable
class ILPConstraintSet(Protocol):
    """Interface expected by ``solvers/ilp/solver.py``."""

    def build_eligible_slots(self, fixtures: list, slots: list) -> dict[str, list[str]]: ...

    def add_hard_constraints(
        self, prob: Any, x: dict, fixtures: list, slots: list, teams: dict
    ) -> None: ...

    def add_soft_constraints(
        self, prob: Any, x: dict, fixtures: list, slots: list, teams: dict
    ) -> list[tuple[int, Any]]: ...


@runtime_checkable
class MHConstraintSet(Protocol):
    """Interface expected by ``solvers/metaheuristic/solver.py``."""

    def pre_assign(
        self,
        fixtures: list,
        slots: list,
    ) -> tuple[list[tuple], set[str]]:
        """
        Called once before the greedy initialisation.

        Returns
        -------
        assignments  : ``[(Fixture, Slot)]`` pairs that must be pre-assigned
                       (e.g. EPL Round 38, NFL Thanksgiving hosts).
        blocked_ids  : ``slot_ids`` to remove from the greedy available pool
                       after pre-assignments are applied.
        """
        ...

    def greedy_params(self) -> dict:
        """
        Parameters consumed by the generic greedy initialiser.

        Expected keys
        -------------
        min_rest_days : int
            Minimum days between a team's consecutive games.
        day_caps : dict[str, int]
            ``{day_of_week: max_games_per_team}`` hard caps enforced greedily.
            Omit a day to impose no per-day cap.
        """
        ...

    def score(self, schedule: Any, teams: dict) -> float:
        """Return total penalty for a schedule.  Lower is better; 0 = perfect."""
        ...
