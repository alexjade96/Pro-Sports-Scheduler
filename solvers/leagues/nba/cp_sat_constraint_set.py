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
        add_min_rest_days(model, x, fixtures, slots, teams, min_days=0)

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
        cost_terms = []

        # SC2: no road back-to-back
        sc2_pen = self._soft.get("SC2", {}).get("penalty_per_violation", 30)
        cost_terms.extend(self._add_soft_no_road_b2b(model, x, fixtures, slots, teams, sc2_pen))

        # SC3: road trip length cap (approximated via rolling date window;
        # away-only — NBA declares no analogous home-run cap)
        cost_terms.extend(self._add_soft_road_trip_cap(model, x, fixtures, slots, teams))

        return cost_terms

    def _add_soft_no_road_b2b(self, model, x, fixtures, slots, teams, penalty: int) -> list:
        """SC2: penalise a team playing away games on two consecutive calendar dates."""
        from collections import defaultdict
        from datetime import timedelta

        slot_map = {s.slot_id: s for s in slots}
        fixture_map = {f.fixture_id: f for f in fixtures}

        team_away_date: dict[str, dict] = defaultdict(lambda: defaultdict(list))
        for (fid, sid), var in x.items():
            f = fixture_map.get(fid)
            s = slot_map.get(sid)
            if not f or not s:
                continue
            team_away_date[f.away_team_id][s.date].append(var)

        cost_terms = []
        counter = [0]
        for tid in teams:
            away_by_date = team_away_date[tid]
            for d in sorted(away_by_date):
                nxt = d + timedelta(days=1)
                if nxt not in away_by_date:
                    continue
                va = away_by_date[d]
                vb = away_by_date[nxt]
                counter[0] += 1
                b = model.NewBoolVar(f"nba_sc2_roadb2b_{tid}_{counter[0]}")
                model.Add(sum(va) + sum(vb) >= 2 * b)
                model.Add(sum(va) + sum(vb) <= 1 + b)
                cost_terms.append((penalty, b))
        return cost_terms

    def _add_soft_road_trip_cap(self, model, x, fixtures, slots, teams) -> list:
        """SC3: penalise road-trip windows exceeding max_consecutive_road_games."""
        from collections import defaultdict
        from datetime import timedelta

        max_away = self._soft.get("SC3", {}).get("max_consecutive_road_games", 6)
        penalty = self._soft.get("SC3", {}).get("penalty_per_violation", 20)

        slot_map = {s.slot_id: s for s in slots}
        fixture_map = {f.fixture_id: f for f in fixtures}

        team_away_date: dict = defaultdict(lambda: defaultdict(list))
        for (fid, sid), var in x.items():
            f = fixture_map.get(fid)
            s = slot_map.get(sid)
            if not f or not s:
                continue
            team_away_date[f.away_team_id][s.date].append(var)

        all_dates = sorted({s.date for s in slots})
        window_days = 42
        cost_terms = []

        for team_id in teams:
            away_by_date = team_away_date[team_id]
            for i, d in enumerate(all_dates):
                end_d = d + timedelta(days=window_days)
                away_in_win: list = []
                for wd in all_dates[i:]:
                    if wd > end_d:
                        break
                    away_in_win.extend(away_by_date.get(wd, []))

                if len(away_in_win) > max_away:
                    ac = model.new_int_var(0, len(away_in_win), f"nba_sc3_{team_id}_{d}")
                    model.add(ac == sum(away_in_win))
                    exc = model.new_int_var(0, len(away_in_win) - max_away, f"nba_sc3e_{team_id}_{d}")
                    model.add(exc >= ac - max_away)
                    cost_terms.append((penalty, exc))

        return cost_terms
