"""
EPL constraint set for the metaheuristic (simulated annealing) solver.

Implements pre_assign (HC8 Round 38 pinning), greedy_params (min rest +
per-day caps from HC1/HC9/HC13), and score (delegates to the EPL objective).
"""
from __future__ import annotations

from datetime import date

from core.models import Fixture, Slot, ScheduledFixture

_FIXTURES_PER_ROUND = 10


class EPLMHConstraintSet:
    def __init__(
        self,
        constraint_config: dict,
        season_start: date,
        season_end: date,
        final_day: dict | None = None,
        fixtures_per_round: int = _FIXTURES_PER_ROUND,
    ) -> None:
        self._hard = {c["id"]: c for c in constraint_config["hard"]}
        self._soft = {c["id"]: c for c in constraint_config["soft"]}
        self._season_start = season_start
        self._season_end = season_end
        self._final_day = final_day
        self._fixtures_per_round = fixtures_per_round

    def pre_assign(
        self,
        fixtures: list[Fixture],
        slots: list[Slot],
    ) -> tuple[list[tuple[Fixture, Slot]], set[str]]:
        """
        Pre-assigns Round 38 to the final-day slot (HC8) and registers R38
        fixture IDs with the objective so HC8 violations are scored correctly.

        Returns
        -------
        pre_assigned : list of (Fixture, Slot) pairs
        blocked_ids  : slot_ids that should be removed from the greedy pool
                       (all slots on the final-day date)
        """
        from solvers.metaheuristic.objective import set_r38_fixture_ids

        if not self._final_day:
            set_r38_fixture_ids(frozenset())
            return [], set()

        fd_date = date.fromisoformat(self._final_day["date"])
        fd_ko = self._final_day["kickoff"]
        fd_slot = next(
            (s for s in slots if s.date == fd_date and s.kickoff == fd_ko),
            None,
        )

        if not fd_slot:
            print(f"[Greedy] WARNING: final-day slot ({fd_date} {fd_ko}) not found — HC8 not enforced")
            set_r38_fixture_ids(frozenset())
            return [], set()

        r38 = fixtures[-self._fixtures_per_round:]
        set_r38_fixture_ids(frozenset(f.fixture_id for f in r38))

        pre_assigned = [(f, fd_slot) for f in r38]
        blocked = {s.slot_id for s in slots if s.date == fd_date}

        print(f"[Greedy] Round 38 pinned to {fd_slot.slot_id}; final-day slots removed from pool")
        return pre_assigned, blocked

    def greedy_params(self) -> dict:
        return {
            "min_rest_days": self._hard.get("HC1", {}).get("value", 3),
            "day_caps": {
                "Thursday": self._hard.get("HC13", {}).get("value", 2),
                "Friday":   self._hard.get("HC9",  {}).get("value", 3),
            },
        }

    def score(self, schedule, teams) -> float:
        from solvers.metaheuristic.objective import score
        return score(schedule, teams)
