"""
NBA constraint set for the ILP (PuLP) solver.

Hard constraints implemented:
  HC5  — No 4 games in 5 consecutive nights per team
  HC6  — No 8 games in 12 consecutive nights per team
  HC10 — All-Star break blackout (no games during break window)
  HC13 — All teams play on the final regular-season day

Soft constraints implemented:
  SC3  — Road trip length ≤ 6 consecutive away games
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
        # NBA's ~94 natural rounds (vs EPL's 38) put far less real time behind
        # each round, so a window this dense needs many more rounds either
        # side than EPL's window_rounds=3-5 to stay feasible under HC5/HC6
        # (4-in-5, 8-in-12) — 20 is the smallest value confirmed feasible by
        # direct testing; narrower windows provably infeasible.
        eligible = build_eligible_slots(
            fixtures, slots, self._season_start, self._season_end, window_rounds=20
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

        # min_rest=0: B2Bs allowed, but same-day duplicates are not
        add_min_rest_days(prob, x, fixtures, slots, teams, min_days=0)

        if self._hard.get("HC10"):
            self._add_allstar_blackout(prob, x, fixtures, slots)

        if self._hard.get("HC5"):
            self._add_no_4_in_5(prob, x, fixtures, slots, teams)

        if self._hard.get("HC6"):
            self._add_no_8_in_12(prob, x, fixtures, slots, teams)

        if self._hard.get("HC13"):
            self._add_final_day_all_play(prob, x, fixtures, slots, teams)

    def _add_allstar_blackout(self, prob, x, fixtures, slots) -> None:
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
                prob += var == 0, f"allstar_blackout_{fid}_{sid}"

    def _add_no_4_in_5(self, prob, x, fixtures, slots, teams) -> None:
        """HC5: No team plays 4+ games in any 5-consecutive-night window."""
        import pulp
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
        counter = [0]
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
                    counter[0] += 1
                    prob += (
                        pulp.lpSum(window_vars) <= 3,
                        f"hc5_4in5_{tid}_{counter[0]}",
                    )

    def _add_no_8_in_12(self, prob, x, fixtures, slots, teams) -> None:
        """HC6: No team plays 8+ games in any 12-consecutive-night window."""
        import pulp
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
        counter = [0]
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
                    counter[0] += 1
                    prob += (
                        pulp.lpSum(window_vars) <= 7,
                        f"hc6_8in12_{tid}_{counter[0]}",
                    )

    def _add_final_day_all_play(self, prob, x, fixtures, slots, teams) -> None:
        """HC13: Every team must appear in a game on the final day of the season."""
        import pulp
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
                prob += (
                    pulp.lpSum(final_vars) >= 1,
                    f"hc13_final_day_{tid}",
                )

    def add_soft_constraints(self, prob, x, fixtures, slots, teams) -> list:
        import pulp
        from collections import defaultdict

        cost_terms = []
        sc3_max = self._soft.get("SC3", {}).get("max_consecutive_road_games", 6)
        sc3_pen = self._soft.get("SC3", {}).get("penalty_per_violation", 20)

        if sc3_max <= 0:
            return cost_terms

        slot_map = {s.slot_id: s for s in slots}
        fixture_map = {f.fixture_id: f for f in fixtures}

        team_away_date: dict[str, dict[date, list]] = defaultdict(lambda: defaultdict(list))
        for (fid, sid), var in x.items():
            f = fixture_map.get(fid)
            s = slot_map.get(sid)
            if not f or not s:
                continue
            team_away_date[f.away_team_id][s.date].append(var)

        all_dates = sorted({s.date for s in slots})
        window_days = sc3_max * 7  # approx window for max road games
        counter = [0]

        for tid in teams:
            away_by_date = team_away_date[tid]
            for i, d in enumerate(all_dates):
                end_d = d + timedelta(days=window_days)
                away_in_win: list = []
                for wd in all_dates[i:]:
                    if wd > end_d:
                        break
                    away_in_win.extend(away_by_date.get(wd, []))
                if len(away_in_win) > sc3_max:
                    counter[0] += 1
                    slack = pulp.LpVariable(f"nba_sc3_{counter[0]}", lowBound=0)
                    prob += slack >= pulp.lpSum(away_in_win) - sc3_max
                    cost_terms.append((sc3_pen, slack))

        return cost_terms
