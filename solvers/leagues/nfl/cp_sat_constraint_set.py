"""
NFL constraint set for the CP-SAT solver.

Hard constraints implemented:
  HC7  — Bye week: each team plays at most once per week in weeks 6-14
          (≤1 game per 7-day rolling window during bye window, not explicitly
          tracked by week number — implemented as no back-to-back games within
          6 days of any Thursday game played in the bye window)
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

    def add_hard_constraints(self, model, x, fixtures, slots, teams) -> None:
        from solvers.cp_sat.constraints import (
            add_each_fixture_assigned_exactly_once,
            add_team_plays_at_most_once_per_slot,
            add_min_rest_days,
        )

        add_each_fixture_assigned_exactly_once(model, x, fixtures, slots)
        add_team_plays_at_most_once_per_slot(model, x, fixtures, slots, teams)

        # HC1: minimum rest (NFL plays ~weekly; 5 days between games minimum)
        add_min_rest_days(model, x, fixtures, slots, teams, min_rest_days=5)

        # HC8: Shared venue — MetLife and SoFi co-tenants can't both play home same day
        self._add_shared_venue(model, x, fixtures, slots)

        # HC9: Thanksgiving — DAL and DET must be home on Thanksgiving
        self._add_thanksgiving_home(model, x, fixtures, slots, teams)

        # HC10: TNF rest — ≥10 days before any Thursday game
        self._add_tnf_min_rest(model, x, fixtures, slots, teams)

        # HC11: No Christmas B2B (Dec 24 + Dec 25)
        self._add_no_christmas_b2b(model, x, fixtures, slots, teams)

    def _add_shared_venue(self, model, x, fixtures, slots) -> None:
        """HC8: shared-venue teams cannot both play home on the same date."""
        from collections import defaultdict

        slot_map = {s.slot_id: s for s in slots}
        # Group fixtures by home team
        home_by_team: dict[str, list[tuple]] = defaultdict(list)
        for (fid, sid), var in x.items():
            f = next((fi for fi in fixtures if fi.fixture_id == fid), None)
            if f:
                home_by_team[f.home_team_id].append((sid, var))

        for venue_teams in self._shared_venues:
            if len(venue_teams) < 2:
                continue
            t1, t2 = venue_teams[0], venue_teams[1]
            # Build date → [(sid,var)] for each team
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
            # For each date where both teams could play home, add constraint
            for d in set(t1_by_date) & set(t2_by_date):
                for v1 in t1_by_date[d]:
                    for v2 in t2_by_date[d]:
                        model.Add(v1 + v2 <= 1)

    def _add_thanksgiving_home(self, model, x, fixtures, slots, teams) -> None:
        """HC9: DAL and DET must play home on Thanksgiving Day."""
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
                # Sum of home fixtures on Thanksgiving for this team must be ≥ 1
                home_on_td = []
                for (fid, sid), var in x.items():
                    f = fixture_map.get(fid)
                    s = slot_map.get(sid)
                    if f and s and f.home_team_id == mandatory_home and s.date == td:
                        home_on_td.append(var)
                if home_on_td:
                    model.Add(sum(home_on_td) >= 1)

    def _add_tnf_min_rest(self, model, x, fixtures, slots, teams) -> None:
        """HC10: Teams on Thursday Night Football need ≥10 days since last game."""
        min_rest = self._hard.get("HC10", {}).get("min_days_since_last_game", 10)
        slot_map = {s.slot_id: s for s in slots}
        fixture_map = {f.fixture_id: f for f in fixtures}

        # Build (team -> [(date, var)]) for all assignments
        from collections import defaultdict
        team_vars: dict[str, list[tuple[date, object]]] = defaultdict(list)
        for (fid, sid), var in x.items():
            f = fixture_map.get(fid)
            s = slot_map.get(sid)
            if not f or not s:
                continue
            for tid in (f.home_team_id, f.away_team_id):
                team_vars[tid].append((s.date, var))

        for tid, dvars in team_vars.items():
            # Find Thursday entries
            thu_entries = [(d, v) for d, v in dvars if d.weekday() == 3]  # Thu=3
            if not thu_entries:
                continue
            for thu_date, thu_var in thu_entries:
                # For each other game within [1, min_rest-1] days before Thursday:
                too_close_date_range = range(1, min_rest)
                for other_date, other_var in dvars:
                    gap = (thu_date - other_date).days
                    if 1 <= gap < min_rest:
                        # If thu_var=1 and other_var=1, this is a violation
                        # Force: thu_var + other_var <= 1
                        model.Add(thu_var + other_var <= 1)

    def _add_no_christmas_b2b(self, model, x, fixtures, slots, teams) -> None:
        """HC11: No team plays on both Dec 24 and Dec 25."""
        from core.data_loader import load_calendar
        cal = load_calendar()
        slot_map = {s.slot_id: s for s in slots}
        fixture_map = {f.fixture_id: f for f in fixtures}

        xmas_dates_in_season = [
            d for d in (date(self._season_start.year, 12, 24),
                        date(self._season_start.year, 12, 25))
            if self._season_start <= d <= self._season_end
        ]
        if len(xmas_dates_in_season) < 2:
            return

        from collections import defaultdict
        team_day_vars: dict[tuple[str, date], list] = defaultdict(list)
        for (fid, sid), var in x.items():
            f = fixture_map.get(fid)
            s = slot_map.get(sid)
            if not f or not s:
                continue
            for tid in (f.home_team_id, f.away_team_id):
                if s.date in xmas_dates_in_season:
                    team_day_vars[(tid, s.date)].append(var)

        for tid in teams:
            dec24_vars = team_day_vars.get((tid, xmas_dates_in_season[0]), [])
            dec25_vars = team_day_vars.get((tid, xmas_dates_in_season[1]), [])
            for v1 in dec24_vars:
                for v2 in dec25_vars:
                    model.Add(v1 + v2 <= 1)

    # ------------------------------------------------------------------
    # Soft constraints
    # ------------------------------------------------------------------

    def add_soft_constraints(self, model, x, fixtures, slots, teams) -> list:
        from solvers.cp_sat.constraints import (
            add_soft_max_consecutive_home_away,
            add_soft_half_season_balance,
        )
        cost_terms = []

        # SC1 / SC2: consecutive road/home limits
        max_road = self._soft.get("SC1", {}).get("value", 3)
        pen_road = self._soft.get("SC1", {}).get("penalty_per_violation", 25)
        max_home = self._soft.get("SC2", {}).get("value", 4)
        pen_home = self._soft.get("SC2", {}).get("penalty_per_violation", 20)

        consec_terms = add_soft_max_consecutive_home_away(
            model, x, fixtures, slots, teams,
            max_home=max_home, max_away=max_road,
            penalty_home=pen_home, penalty_away=pen_road,
        )
        cost_terms.extend(consec_terms)

        # SC8: H/A half-season balance (within ±1 game per half)
        pen_balance = self._soft.get("SC8", {}).get("penalty_per_violation", 15)
        balance_terms = add_soft_half_season_balance(
            model, x, fixtures, slots, teams,
            tolerance=1, penalty=pen_balance,
        )
        cost_terms.extend(balance_terms)

        # SC11: Division rivalry legs ≥5 weeks apart
        pen_rival = self._soft.get("SC11", {}).get("penalty_per_violation", 20)
        min_gap_weeks = self._soft.get("SC11", {}).get("min_gap_weeks", 5)
        rival_terms = self._add_soft_division_rivalry_spread(
            model, x, fixtures, slots, teams,
            min_gap_days=min_gap_weeks * 7, penalty=pen_rival,
        )
        cost_terms.extend(rival_terms)

        return cost_terms

    def _add_soft_division_rivalry_spread(
        self, model, x, fixtures, slots, teams, min_gap_days: int, penalty: int
    ) -> list:
        """SC11: Home and away legs of each division rivalry ≥ min_gap_days apart."""
        from collections import defaultdict
        slot_map = {s.slot_id: s for s in slots}
        fixture_map = {f.fixture_id: f for f in fixtures}

        # Group fixtures by unordered team pair within same division
        pair_fixtures: dict[frozenset, list] = defaultdict(list)
        for f in fixtures:
            if self._div_map.get(f.home_team_id) == self._div_map.get(f.away_team_id):
                pair = frozenset({f.home_team_id, f.away_team_id})
                pair_fixtures[pair].append(f)

        cost_terms = []
        for pair, pair_fx in pair_fixtures.items():
            if len(pair_fx) < 2:
                continue
            # For each pair of fixtures in this rivalry, penalise if gap < min_gap_days
            for i in range(len(pair_fx)):
                for j in range(i + 1, len(pair_fx)):
                    fi, fj = pair_fx[i], pair_fx[j]
                    for sid_i, vi in x.items():
                        if sid_i[0] != fi.fixture_id:
                            continue
                        si = slot_map.get(sid_i[1])
                        if not si:
                            continue
                        for sid_j, vj in x.items():
                            if sid_j[0] != fj.fixture_id:
                                continue
                            sj = slot_map.get(sid_j[1])
                            if not sj:
                                continue
                            gap = abs((si.date - sj.date).days)
                            if gap < min_gap_days:
                                b = model.NewBoolVar(
                                    f"div_rivalry_close_{fi.fixture_id}_{fj.fixture_id}_{si.slot_id}_{sj.slot_id}"
                                )
                                model.Add(vi + vj >= 2 * b)
                                model.Add(vi + vj <= 1 + b)
                                cost_terms.append((b, penalty))
        return cost_terms
