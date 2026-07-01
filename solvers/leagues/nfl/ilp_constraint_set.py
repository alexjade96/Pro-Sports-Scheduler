"""
NFL constraint set for the ILP (PuLP) solver.

Hard constraints implemented:
  HC8  — Shared venues: MetLife (NYJ+NYG) and SoFi (LAC+LAR) can't host same date
  HC9  — Thanksgiving: DAL and DET must play home on Thanksgiving
  HC10 — TNF minimum rest: ≥10 days since last game before a Thursday game
  HC11 — Christmas B2B: no team plays Dec 24 AND Dec 25

Soft constraints implemented:
  SC1  — Max 3 consecutive road games (penalty 25/violation)
  SC2  — Max 4 consecutive home games (penalty 20/violation)
  SC8  — H/A half-season balance ±1 (penalty 15/violation)
  SC11 — Division rivalry legs ≥5 weeks apart (penalty 20/violation)
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from solvers.slot_filter import build_eligible_slots, log_filter_stats

_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "leagues" / "nfl"


def _load_div_map() -> dict[str, str]:
    with open(_DATA_DIR / "teams.json") as f:
        raw = json.load(f)
    return {t["id"]: t["division"] for t in raw["teams"]}


def _load_shared_venues() -> list[list[str]]:
    with open(_DATA_DIR / "teams.json") as f:
        raw = json.load(f)
    return [sv["teams"] for sv in raw.get("shared_venues", [])]


class NFLILPConstraintSet:
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
        self._div_map = _load_div_map()
        self._shared_venues = _load_shared_venues()

    # ------------------------------------------------------------------
    # Slot filtering
    # ------------------------------------------------------------------

    def build_eligible_slots(self, fixtures, slots) -> dict[str, list[str]]:
        eligible = build_eligible_slots(
            fixtures, slots, self._season_start, self._season_end, window_rounds=3
        )
        log_filter_stats(eligible)
        return eligible

    # ------------------------------------------------------------------
    # Hard constraints
    # ------------------------------------------------------------------

    def add_hard_constraints(self, prob, x, fixtures, slots, teams) -> None:
        from solvers.ilp.constraints import (
            add_each_fixture_assigned_exactly_once,
            add_team_plays_at_most_once_per_day,
            add_min_rest_days,
        )

        add_each_fixture_assigned_exactly_once(prob, x, fixtures, slots)
        add_team_plays_at_most_once_per_day(prob, x, fixtures, slots, teams)

        # HC1: minimum rest (NFL plays ~weekly; 5 days minimum)
        add_min_rest_days(prob, x, fixtures, slots, teams, min_days=5)

        # HC8: Shared venue — MetLife and SoFi co-tenants can't both play home same day
        self._add_shared_venue(prob, x, fixtures, slots)

        # HC9: Thanksgiving — DAL and DET must be home on Thanksgiving
        self._add_thanksgiving_home(prob, x, fixtures, slots, teams)

        # HC10: TNF rest — ≥10 days before any Thursday game
        self._add_tnf_min_rest(prob, x, fixtures, slots, teams)

        # HC11: No Christmas B2B (Dec 24 + Dec 25)
        self._add_no_christmas_b2b(prob, x, fixtures, slots, teams)

    def _add_shared_venue(self, prob, x, fixtures, slots) -> None:
        """HC8: shared-venue teams cannot both play home on the same date."""
        import pulp
        from collections import defaultdict

        slot_map = {s.slot_id: s for s in slots}
        home_by_team: dict[str, list[tuple]] = defaultdict(list)
        for (fid, sid), var in x.items():
            f = next((fi for fi in fixtures if fi.fixture_id == fid), None)
            if f:
                home_by_team[f.home_team_id].append((sid, var))

        for venue_teams in self._shared_venues:
            if len(venue_teams) < 2:
                continue
            t1, t2 = venue_teams[0], venue_teams[1]
            t1_by_date: dict[date, list] = defaultdict(list)
            t2_by_date: dict[date, list] = defaultdict(list)
            for sid, var in home_by_team.get(t1, []):
                s = slot_map.get(sid)
                if s:
                    t1_by_date[s.date].append(var)
            for sid, var in home_by_team.get(t2, []):
                s = slot_map.get(sid)
                if s:
                    t2_by_date[s.date].append(var)
            counter = [0]
            for d in set(t1_by_date) & set(t2_by_date):
                for v1 in t1_by_date[d]:
                    for v2 in t2_by_date[d]:
                        counter[0] += 1
                        prob += v1 + v2 <= 1, f"shared_venue_{t1}_{t2}_{d}_{counter[0]}"

    def _add_thanksgiving_home(self, prob, x, fixtures, slots, teams) -> None:
        """HC9: DAL and DET must play home on Thanksgiving Day."""
        import pulp
        from core.data_loader import load_calendar
        cal = load_calendar()
        thanks_dates = [
            date.fromisoformat(d)
            for d in cal.get("special_matchdays", {}).get("thanksgiving", [])
        ]
        if not thanks_dates:
            return

        slot_map = {s.slot_id: s for s in slots}
        fixture_map = {f.fixture_id: f for f in fixtures}

        for td in thanks_dates:
            for mandatory_home in ["DAL", "DET"]:
                if mandatory_home not in teams:
                    continue
                home_on_td = []
                for (fid, sid), var in x.items():
                    f = fixture_map.get(fid)
                    s = slot_map.get(sid)
                    if f and s and f.home_team_id == mandatory_home and s.date == td:
                        home_on_td.append(var)
                if home_on_td:
                    prob += (
                        pulp.lpSum(home_on_td) >= 1,
                        f"thanksgiving_home_{mandatory_home}_{td}",
                    )

    def _add_tnf_min_rest(self, prob, x, fixtures, slots, teams) -> None:
        """HC10: Teams on Thursday Night Football need ≥10 days since last game."""
        min_rest = self._hard.get("HC10", {}).get("min_days_since_last_game", 10)
        slot_map = {s.slot_id: s for s in slots}
        fixture_map = {f.fixture_id: f for f in fixtures}

        from collections import defaultdict
        team_vars: dict[str, list[tuple[date, object]]] = defaultdict(list)
        for (fid, sid), var in x.items():
            f = fixture_map.get(fid)
            s = slot_map.get(sid)
            if not f or not s:
                continue
            for tid in (f.home_team_id, f.away_team_id):
                team_vars[tid].append((s.date, var))

        counter = [0]
        for tid, dvars in team_vars.items():
            thu_entries = [(d, v) for d, v in dvars if d.weekday() == 3]
            if not thu_entries:
                continue
            for thu_date, thu_var in thu_entries:
                for other_date, other_var in dvars:
                    gap = (thu_date - other_date).days
                    if 1 <= gap < min_rest:
                        counter[0] += 1
                        prob += (
                            thu_var + other_var <= 1,
                            f"tnf_rest_{tid}_{counter[0]}",
                        )

    def _add_no_christmas_b2b(self, prob, x, fixtures, slots, teams) -> None:
        """HC11: No team plays on both Dec 24 and Dec 25."""
        slot_map = {s.slot_id: s for s in slots}
        fixture_map = {f.fixture_id: f for f in fixtures}

        xmas_dates = [
            d for d in (
                date(self._season_start.year, 12, 24),
                date(self._season_start.year, 12, 25),
            )
            if self._season_start <= d <= self._season_end
        ]
        if len(xmas_dates) < 2:
            return

        from collections import defaultdict
        team_day_vars: dict[tuple[str, date], list] = defaultdict(list)
        for (fid, sid), var in x.items():
            f = fixture_map.get(fid)
            s = slot_map.get(sid)
            if not f or not s:
                continue
            for tid in (f.home_team_id, f.away_team_id):
                if s.date in xmas_dates:
                    team_day_vars[(tid, s.date)].append(var)

        counter = [0]
        for tid in teams:
            dec24_vars = team_day_vars.get((tid, xmas_dates[0]), [])
            dec25_vars = team_day_vars.get((tid, xmas_dates[1]), [])
            for v1 in dec24_vars:
                for v2 in dec25_vars:
                    counter[0] += 1
                    prob += v1 + v2 <= 1, f"xmas_b2b_{tid}_{counter[0]}"

    # ------------------------------------------------------------------
    # Soft constraints
    # ------------------------------------------------------------------

    def add_soft_constraints(self, prob, x, fixtures, slots, teams) -> list:
        import pulp
        from collections import defaultdict

        cost_terms = []

        # SC1 / SC2: consecutive road/home limits
        max_road = self._soft.get("SC1", {}).get("value", 3)
        pen_road = self._soft.get("SC1", {}).get("penalty_per_violation", 25)
        max_home = self._soft.get("SC2", {}).get("value", 4)
        pen_home = self._soft.get("SC2", {}).get("penalty_per_violation", 20)

        cost_terms.extend(
            self._add_soft_consec_home_away(
                prob, x, fixtures, slots, teams,
                max_home=max_home, max_away=max_road,
                pen_home=pen_home, pen_away=pen_road,
            )
        )

        # SC8: H/A half-season balance ±1
        pen_balance = self._soft.get("SC8", {}).get("penalty_per_violation", 15)
        cost_terms.extend(
            self._add_soft_half_season_balance(
                prob, x, fixtures, slots, teams,
                tolerance=1, penalty=pen_balance,
            )
        )

        # SC11: Division rivalry legs ≥5 weeks apart
        pen_rival = self._soft.get("SC11", {}).get("penalty_per_violation", 20)
        min_gap_weeks = self._soft.get("SC11", {}).get("min_gap_weeks", 5)
        cost_terms.extend(
            self._add_soft_division_rivalry_spread(
                prob, x, fixtures, slots, teams,
                min_gap_days=min_gap_weeks * 7, penalty=pen_rival,
            )
        )

        return cost_terms

    def _add_soft_consec_home_away(
        self, prob, x, fixtures, slots, teams,
        max_home: int, max_away: int, pen_home: int, pen_away: int,
    ) -> list:
        """SC1/SC2: penalise runs of >max_home home or >max_away away games."""
        import pulp
        from collections import defaultdict

        slot_map = {s.slot_id: s for s in slots}
        fixture_map = {f.fixture_id: f for f in fixtures}

        team_home_date: dict = defaultdict(lambda: defaultdict(list))
        team_away_date: dict = defaultdict(lambda: defaultdict(list))
        for (fid, sid), var in x.items():
            f = fixture_map.get(fid)
            s = slot_map.get(sid)
            if not f or not s:
                continue
            team_home_date[f.home_team_id][s.date].append(var)
            team_away_date[f.away_team_id][s.date].append(var)

        all_dates = sorted({s.date for s in slots})
        window_days = 42
        cost_terms = []
        counter = [0]

        for team_id in teams:
            home_by_date = team_home_date[team_id]
            away_by_date = team_away_date[team_id]
            for i, d in enumerate(all_dates):
                end_d = d + timedelta(days=window_days)
                home_in_win: list = []
                away_in_win: list = []
                for wd in all_dates[i:]:
                    if wd > end_d:
                        break
                    home_in_win.extend(home_by_date.get(wd, []))
                    away_in_win.extend(away_by_date.get(wd, []))

                counter[0] += 1
                k = counter[0]
                if len(home_in_win) > max_home:
                    slack = pulp.LpVariable(f"nfl_sc2h_{k}", lowBound=0)
                    prob += slack >= pulp.lpSum(home_in_win) - max_home
                    cost_terms.append((pen_home, slack))
                if len(away_in_win) > max_away:
                    slack = pulp.LpVariable(f"nfl_sc1a_{k}", lowBound=0)
                    prob += slack >= pulp.lpSum(away_in_win) - max_away
                    cost_terms.append((pen_away, slack))

        return cost_terms

    def _add_soft_half_season_balance(
        self, prob, x, fixtures, slots, teams,
        tolerance: int, penalty: int,
    ) -> list:
        """SC8: penalise H/A imbalance across the season midpoint."""
        import pulp

        midpoint = date.fromordinal(
            (self._season_start.toordinal() + self._season_end.toordinal()) // 2
        )
        slot_map = {s.slot_id: s for s in slots}
        fixture_map = {f.fixture_id: f for f in fixtures}

        cost_terms = []
        for team_id in teams:
            home_h1 = []
            for (fid, sid), var in x.items():
                f = fixture_map.get(fid)
                s = slot_map.get(sid)
                if f and s and f.home_team_id == team_id and s.date <= midpoint:
                    home_h1.append(var)
            if not home_h1:
                continue

            h1s = pulp.lpSum(home_h1)
            target = len(home_h1) // 2
            hi = target + tolerance
            lo = max(0, target - tolerance)

            ub = pulp.LpVariable(f"nfl_sc8_ub_{team_id}", lowBound=0)
            prob += ub >= h1s - hi
            cost_terms.append((penalty, ub))

            lb = pulp.LpVariable(f"nfl_sc8_lb_{team_id}", lowBound=0)
            prob += lb >= lo - h1s
            cost_terms.append((penalty, lb))

        return cost_terms

    def _add_soft_division_rivalry_spread(
        self, prob, x, fixtures, slots, teams,
        min_gap_days: int, penalty: int,
    ) -> list:
        """SC11: Home and away legs of each division rivalry ≥ min_gap_days apart."""
        import pulp
        from collections import defaultdict

        slot_map = {s.slot_id: s for s in slots}
        fixture_map = {f.fixture_id: f for f in fixtures}

        pair_fixtures: dict[frozenset, list] = defaultdict(list)
        for f in fixtures:
            if self._div_map.get(f.home_team_id) == self._div_map.get(f.away_team_id):
                pair = frozenset({f.home_team_id, f.away_team_id})
                pair_fixtures[pair].append(f)

        cost_terms = []
        counter = [0]
        for pair, pair_fx in pair_fixtures.items():
            if len(pair_fx) < 2:
                continue
            for i in range(len(pair_fx)):
                for j in range(i + 1, len(pair_fx)):
                    fi, fj = pair_fx[i], pair_fx[j]
                    for (ki, si_id), vi in x.items():
                        if ki != fi.fixture_id:
                            continue
                        si = slot_map.get(si_id)
                        if not si:
                            continue
                        for (kj, sj_id), vj in x.items():
                            if kj != fj.fixture_id:
                                continue
                            sj = slot_map.get(sj_id)
                            if not sj:
                                continue
                            gap = abs((si.date - sj.date).days)
                            if gap < min_gap_days:
                                counter[0] += 1
                                b = pulp.LpVariable(
                                    f"nfl_rival_{counter[0]}", cat="Binary"
                                )
                                prob += vi + vj >= 2 * b
                                prob += vi + vj <= 1 + b
                                cost_terms.append((penalty, b))
        return cost_terms
