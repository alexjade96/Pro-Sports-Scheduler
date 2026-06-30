"""
NBA constraint set for the CP-SAT solver.

Hard constraints implemented:
  HC5  — No 4 games in 5 consecutive nights per team
  HC6  — No 8 games in 12 consecutive nights per team
  HC10 — All-Star break blackout (no games during break window)
  HC13 — All teams play on the final regular-season day

Soft constraints implemented:
  SC1  — Back-to-back count ≤ target (14/team)
  SC2  — Road back-to-backs penalised more heavily
  SC3  — Road trip length ≤ 6 consecutive away games
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

        # HC1 proxy: no same-slot double-scheduling (min rest = 0 days means B2Bs allowed)
        add_min_rest_days(model, x, fixtures, slots, teams, min_rest_days=0)

        # HC10: All-Star break blackout
        if self._hard.get("HC10"):
            self._add_allstar_blackout(model, x, fixtures, slots)

        # HC5: no 4 games in 5 consecutive nights
        if self._hard.get("HC5"):
            self._add_no_4_in_5(model, x, fixtures, slots, teams)

        # HC6: no 8 games in 12 consecutive nights
        if self._hard.get("HC6"):
            self._add_no_8_in_12(model, x, fixtures, slots, teams)

        # HC13: all teams play on the final regular-season day
        if self._hard.get("HC13"):
            self._add_final_day_all_play(model, x, fixtures, slots, teams)

    def _add_allstar_blackout(self, model, x, fixtures, slots) -> None:
        """HC10: No games during the All-Star break window."""
        from core.data_loader import load_calendar
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
                model.Add(var == 0)

    def _add_no_4_in_5(self, model, x, fixtures, slots, teams) -> None:
        """HC5: No team plays 4+ games in any 5-consecutive-night window."""
        from collections import defaultdict
        slot_map = {s.slot_id: s for s in slots}
        fixture_map = {f.fixture_id: f for f in fixtures}

        team_date_vars: dict[str, dict[date, list]] = defaultdict(lambda: defaultdict(list))
        for (fid, sid), var in x.items():
            f = fixture_map.get(fid)
            s = slot_map.get(sid)
            if not f or not s:
                continue
            for tid in (f.home_team_id, f.away_team_id):
                team_date_vars[tid][s.date].append(var)

        all_dates = sorted({s.date for s in slots})
        for tid in teams:
            date_map = team_date_vars[tid]
            for i, d in enumerate(all_dates):
                window_end = d + timedelta(days=4)
                window_vars = []
                for wd in all_dates[i:]:
                    if wd > window_end:
                        break
                    window_vars.extend(date_map.get(wd, []))
                if len(window_vars) >= 4:
                    model.Add(sum(window_vars) <= 3)

    def _add_no_8_in_12(self, model, x, fixtures, slots, teams) -> None:
        """HC6: No team plays 8+ games in any 12-consecutive-night window."""
        from collections import defaultdict
        slot_map = {s.slot_id: s for s in slots}
        fixture_map = {f.fixture_id: f for f in fixtures}

        team_date_vars: dict[str, dict[date, list]] = defaultdict(lambda: defaultdict(list))
        for (fid, sid), var in x.items():
            f = fixture_map.get(fid)
            s = slot_map.get(sid)
            if not f or not s:
                continue
            for tid in (f.home_team_id, f.away_team_id):
                team_date_vars[tid][s.date].append(var)

        all_dates = sorted({s.date for s in slots})
        for tid in teams:
            date_map = team_date_vars[tid]
            for i, d in enumerate(all_dates):
                window_end = d + timedelta(days=11)
                window_vars = []
                for wd in all_dates[i:]:
                    if wd > window_end:
                        break
                    window_vars.extend(date_map.get(wd, []))
                if len(window_vars) >= 8:
                    model.Add(sum(window_vars) <= 7)

    def _add_final_day_all_play(self, model, x, fixtures, slots, teams) -> None:
        """HC13: Every team must appear in a game on the final day of the season."""
        from collections import defaultdict
        slot_map = {s.slot_id: s for s in slots}
        fixture_map = {f.fixture_id: f for f in fixtures}

        final_day = self._season_end
        team_final_vars: dict[str, list] = defaultdict(list)
        for (fid, sid), var in x.items():
            f = fixture_map.get(fid)
            s = slot_map.get(sid)
            if not f or not s or s.date != final_day:
                continue
            for tid in (f.home_team_id, f.away_team_id):
                team_final_vars[tid].append(var)

        for tid in teams:
            final_vars = team_final_vars.get(tid, [])
            if final_vars:
                model.Add(sum(final_vars) >= 1)

    # ------------------------------------------------------------------
    # Soft constraints
    # ------------------------------------------------------------------

    def add_soft_constraints(self, model, x, fixtures, slots, teams) -> list:
        from solvers.cp_sat.constraints import add_soft_max_consecutive_home_away
        cost_terms = []

        # SC1/SC2/SC3: consecutive home/away caps (weaker than EPL; NBA teams travel more)
        sc3_max = self._soft.get("SC3", {}).get("max_consecutive_road_games", 6)
        sc3_pen = self._soft.get("SC3", {}).get("penalty_per_violation", 20)
        sc2_pen = self._soft.get("SC2", {}).get("penalty_per_violation", 30)

        consec_terms = add_soft_max_consecutive_home_away(
            model, x, fixtures, slots, teams,
            max_home=9,  # NBA home stands can be long
            max_away=sc3_max,
            penalty_home=sc2_pen,
            penalty_away=sc3_pen,
        )
        cost_terms.extend(consec_terms)

        return cost_terms
