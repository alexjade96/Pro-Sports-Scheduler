"""
NBA constraint set for the ILP (PuLP) solver.

Implements the ILPConstraintSet protocol. Mirrors the CP-SAT version;
soft constraints are a planned future milestone.
"""
from __future__ import annotations

from datetime import date, timedelta

from solvers.slot_filter import build_eligible_slots, log_filter_stats


class NBAILPConstraintSet:
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
            fixtures, slots, self._season_start, self._season_end, window_rounds=5
        )
        log_filter_stats(eligible)
        return eligible

    def add_hard_constraints(self, prob, x, fixtures, slots, teams) -> None:
        from solvers.ilp.constraints import (
            add_each_fixture_assigned_exactly_once,
            add_team_plays_at_most_once_per_day,
            add_min_rest_days,
        )

        add_each_fixture_assigned_exactly_once(prob, x, fixtures, slots)
        add_team_plays_at_most_once_per_day(prob, x, fixtures, slots, teams)

        min_rest = 0
        add_min_rest_days(prob, x, fixtures, slots, teams, min_rest)

        hc10 = self._hard.get("HC10")
        if hc10:
            self._add_allstar_blackout(prob, x, fixtures, slots)

    def _add_allstar_blackout(self, prob, x, fixtures, slots) -> None:
        from core.data_loader import load_calendar
        import pulp

        cal = load_calendar()
        blackout_dates: set[date] = set()
        for bw in cal.get("blocked_windows", []):
            if "All-Star" in bw.get("label", ""):
                try:
                    start = date.fromisoformat(bw["start"])
                    end   = date.fromisoformat(bw["end"])
                    d = start
                    while d <= end:
                        blackout_dates.add(d)
                        d += timedelta(days=1)
                except (KeyError, ValueError):
                    pass

        if not blackout_dates:
            return

        slot_map = {s.slot_id: s for s in slots}
        for (fid, sid), var in x.items():
            s = slot_map.get(sid)
            if s and s.date in blackout_dates:
                prob += var == 0, f"allstar_blackout_{fid}_{sid}"

    def add_soft_constraints(self, prob, x, fixtures, slots, teams) -> list:
        return []
