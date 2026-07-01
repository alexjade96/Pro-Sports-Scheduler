"""
NFL constraint set for the metaheuristic (simulated annealing) solver.

Implements the MHConstraintSet protocol. The score() method penalises:
  SC1  — Max 3 consecutive road games (penalty 25/violation)
  SC2  — Max 4 consecutive home games (penalty 20/violation)
  SC8  — H/A half-season balance ±1 (penalty 15/violation)
  SC11 — Division rivalry legs < 5 weeks apart (penalty 20/violation)
  HC8  — Shared-venue conflict (hard, penalty 500)
  HC9  — Thanksgiving home constraint for DAL/DET (hard, penalty 500)
  HC10 — TNF played with < 10 days rest (hard, penalty 200)
  HC11 — Christmas B2B (hard, penalty 500)
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from core.models import Fixture, Slot

if TYPE_CHECKING:
    from core.models import Schedule, Team

_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "leagues" / "nfl"


def _load_div_map() -> dict[str, str]:
    with open(_DATA_DIR / "teams.json") as f:
        raw = json.load(f)
    return {t["id"]: t["division"] for t in raw["teams"]}


def _load_shared_venues() -> list[list[str]]:
    with open(_DATA_DIR / "teams.json") as f:
        raw = json.load(f)
    return [sv["teams"] for sv in raw.get("shared_venues", [])]


class NFLMHConstraintSet:
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
        self._thanksgiving_dates: list[date] = self._load_thanksgiving()
        self._xmas_dates: list[date] = self._load_xmas()

    def _load_thanksgiving(self) -> list[date]:
        try:
            from core.data_loader import load_calendar
            cal = load_calendar()
            return [
                date.fromisoformat(d)
                for d in cal.get("special_matchdays", {}).get("thanksgiving", [])
            ]
        except Exception:
            return []

    def _load_xmas(self) -> list[date]:
        candidates = [
            date(self._season_start.year, 12, 24),
            date(self._season_start.year, 12, 25),
        ]
        return [d for d in candidates if self._season_start <= d <= self._season_end]

    def pre_assign(
        self,
        fixtures: list[Fixture],
        slots: list[Slot],
    ) -> tuple[list[tuple[Fixture, Slot]], set[str]]:
        return [], set()

    def greedy_params(self) -> dict:
        return {
            "min_rest_days": self._hard.get("HC1", {}).get("value", 5),
            "day_caps": {},
        }

    def score(self, schedule: "Schedule", teams: dict[str, "Team"]) -> float:
        penalty = 0.0

        sc1_max = self._soft.get("SC1", {}).get("value", 3)
        sc1_pen = self._soft.get("SC1", {}).get("penalty_per_violation", 25)
        sc2_max = self._soft.get("SC2", {}).get("value", 4)
        sc2_pen = self._soft.get("SC2", {}).get("penalty_per_violation", 20)
        sc8_pen = self._soft.get("SC8", {}).get("penalty_per_violation", 15)
        sc11_pen = self._soft.get("SC11", {}).get("penalty_per_violation", 20)
        sc11_gap = self._soft.get("SC11", {}).get("min_gap_weeks", 5) * 7
        tnf_min_rest = self._hard.get("HC10", {}).get("min_days_since_last_game", 10)

        midpoint = date.fromordinal(
            (self._season_start.toordinal() + self._season_end.toordinal()) // 2
        )

        # Build per-team fixture list sorted by date
        team_fixtures: dict[str, list] = {tid: [] for tid in teams}
        for sf in schedule.fixtures:
            team_fixtures[sf.home_team_id].append(sf)
            team_fixtures[sf.away_team_id].append(sf)
        for tid in team_fixtures:
            team_fixtures[tid].sort(key=lambda sf: sf.slot.date)

        # Per-team constraints
        for team_id, sfs in team_fixtures.items():
            if not sfs:
                continue

            dates = [sf.slot.date for sf in sfs]
            is_home = [sf.home_team_id == team_id for sf in sfs]

            # SC1/SC2: consecutive road/home runs
            road_run = home_run = 0
            for h in is_home:
                if h:
                    home_run += 1
                    road_run = 0
                    if home_run > sc2_max:
                        penalty += sc2_pen
                else:
                    road_run += 1
                    home_run = 0
                    if road_run > sc1_max:
                        penalty += sc1_pen

            # SC8: H/A half-season balance
            h1_home = sum(
                1 for sf in sfs
                if sf.home_team_id == team_id and sf.slot.date <= midpoint
            )
            h1_total = sum(1 for sf in sfs if sf.slot.date <= midpoint)
            h2_home = sum(
                1 for sf in sfs
                if sf.home_team_id == team_id and sf.slot.date > midpoint
            )
            h2_total = sum(1 for sf in sfs if sf.slot.date > midpoint)
            if h1_total > 0 and h2_total > 0:
                balance = abs(h1_home - h2_home)
                if balance > 1:
                    penalty += (balance - 1) * sc8_pen

            # HC10: TNF min rest
            for i, sf in enumerate(sfs):
                if sf.slot.date.weekday() == 3:  # Thursday
                    for j in range(i - 1, -1, -1):
                        gap = (sf.slot.date - sfs[j].slot.date).days
                        if gap < 1:
                            continue
                        if gap < tnf_min_rest:
                            penalty += 200
                        break

        # SC11: division rivalry spread
        pair_fixtures: dict[frozenset, list] = defaultdict(list)
        for sf in schedule.fixtures:
            if self._div_map.get(sf.home_team_id) == self._div_map.get(sf.away_team_id):
                pair = frozenset({sf.home_team_id, sf.away_team_id})
                pair_fixtures[pair].append(sf)

        for pair, sfs in pair_fixtures.items():
            if len(sfs) < 2:
                continue
            for i in range(len(sfs)):
                for j in range(i + 1, len(sfs)):
                    gap = abs((sfs[i].slot.date - sfs[j].slot.date).days)
                    if gap < sc11_gap:
                        penalty += sc11_pen

        # HC8: shared venue (two co-tenants both home same day)
        home_by_team_date: dict[tuple[str, date], int] = defaultdict(int)
        for sf in schedule.fixtures:
            home_by_team_date[(sf.home_team_id, sf.slot.date)] += 1

        for venue_teams in self._shared_venues:
            if len(venue_teams) < 2:
                continue
            t1, t2 = venue_teams[0], venue_teams[1]
            all_dates = {d for (tid, d) in home_by_team_date if tid in (t1, t2)}
            for d in all_dates:
                if home_by_team_date.get((t1, d), 0) > 0 and home_by_team_date.get((t2, d), 0) > 0:
                    penalty += 500

        # HC9: Thanksgiving home constraint
        for td in self._thanksgiving_dates:
            for mandatory_home in ("DAL", "DET"):
                if mandatory_home not in teams:
                    continue
                plays_home = any(
                    sf.home_team_id == mandatory_home and sf.slot.date == td
                    for sf in schedule.fixtures
                )
                if not plays_home:
                    penalty += 500

        # HC11: Christmas B2B
        if len(self._xmas_dates) >= 2:
            dec24, dec25 = self._xmas_dates[0], self._xmas_dates[1]
            for tid in teams:
                plays_dec24 = any(
                    (sf.home_team_id == tid or sf.away_team_id == tid) and sf.slot.date == dec24
                    for sf in schedule.fixtures
                )
                plays_dec25 = any(
                    (sf.home_team_id == tid or sf.away_team_id == tid) and sf.slot.date == dec25
                    for sf in schedule.fixtures
                )
                if plays_dec24 and plays_dec25:
                    penalty += 500

        return penalty
