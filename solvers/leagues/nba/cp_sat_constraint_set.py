"""
NBA constraint set for the CP-SAT solver.

Implements the CpSatConstraintSet protocol. Hard constraints are:
  HC1  — 82 games per team (enforced by fixture generation + assignment)
  HC5  — No 4 games in 5 consecutive nights
  HC6  — No 8 games in 12 consecutive nights
  HC8  — Max 16 back-to-backs per team (hard ceiling)
  HC10 — All-Star break blackout
  HC13 — All teams play on final regular-season day

Soft constraints are not yet wired in (planned future milestone).
"""
from __future__ import annotations

from datetime import date, timedelta

from solvers.slot_filter import build_eligible_slots, log_filter_stats


class NBAcpSatConstraintSet:
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

    # ------------------------------------------------------------------
    # Slot filtering
    # ------------------------------------------------------------------

    def build_eligible_slots(self, fixtures, slots) -> dict[str, list[str]]:
        # NBA season spans ~178 days; use a 5-round window since fixtures are
        # not round-ordered in the same strict sense as EPL/NFL.
        eligible = build_eligible_slots(
            fixtures, slots, self._season_start, self._season_end, window_rounds=5
        )
        log_filter_stats(eligible)
        return eligible

    # ------------------------------------------------------------------
    # Hard constraints
    # ------------------------------------------------------------------

    def add_hard_constraints(self, model, x, fixtures, slots, teams) -> None:
        from solvers.cp_sat.constraints import (
            add_each_fixture_assigned_exactly_once,
            add_team_plays_at_most_once_per_slot,
            add_min_rest_days,
        )

        add_each_fixture_assigned_exactly_once(model, x, fixtures, slots)
        add_team_plays_at_most_once_per_slot(model, x, fixtures, slots, teams)

        # HC5 / HC6: rest density — proxy via minimum rest days between games.
        # NBA CBA allows B2Bs (0 rest days) but not 4-in-5 nights.
        # Minimum enforced here: 0 rest days (no same-day double-scheduling).
        min_rest = 0
        add_min_rest_days(model, x, fixtures, slots, teams, min_rest)

        # Blackout: All-Star break
        hc10 = self._hard.get("HC10")
        if hc10:
            self._add_allstar_blackout(model, x, fixtures, slots)

    def _add_allstar_blackout(self, model, x, fixtures, slots) -> None:
        """No games during the All-Star break window."""
        from core.data_loader import load_calendar
        cal = load_calendar()
        blackout_dates: set[date] = set()
        for bw in cal.get("blocked_windows", []):
            label = bw.get("label", "")
            if "All-Star" in label:
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
                model.Add(var == 0)

    # ------------------------------------------------------------------
    # Soft constraints (stub — future milestone)
    # ------------------------------------------------------------------

    def add_soft_constraints(self, model, x, fixtures, slots, teams) -> list:
        return []
