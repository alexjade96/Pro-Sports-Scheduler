"""
NBA constraint set for the metaheuristic (simulated annealing) solver.

Implements the MHConstraintSet protocol. The score() method penalises:
  SC1  — Back-to-back games (target ≤14 per team)
  SC2  — Road back-to-backs (heavier penalty)
  SC3  — Road trips > 6 consecutive away games
  HC5  — 4 games in 5 consecutive nights (hard violation, very large penalty)
  HC6  — 8 games in 12 consecutive nights (hard violation, very large penalty)
  HC10 — Games during All-Star break (forbidden)
  HC13 — All 30 teams must play on the final day of the season
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import TYPE_CHECKING

from core.models import Fixture, Slot

if TYPE_CHECKING:
    from core.models import Schedule, Team


class NBAMHConstraintSet:
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
        self._allstar_dates: set[date] = self._load_allstar_dates()

    def _load_allstar_dates(self) -> set[date]:
        try:
            from core.data_loader import load_calendar
            cal = load_calendar()
        except Exception:
            return set()
        blackout: set[date] = set()
        for bw in cal.get("blocked_windows", []):
            if "All-Star" in bw.get("label", ""):
                try:
                    start = date.fromisoformat(bw["start"])
                    end   = date.fromisoformat(bw["end"])
                    d = start
                    while d <= end:
                        blackout.add(d)
                        d += timedelta(days=1)
                except (KeyError, ValueError):
                    pass
        return blackout

    def pre_assign(
        self,
        fixtures: list[Fixture],
        slots: list[Slot],
    ) -> tuple[list[tuple[Fixture, Slot]], set[str]]:
        return [], set()

    def greedy_params(self) -> dict:
        return {
            "min_rest_days": 0,   # NBA allows B2Bs
            "day_caps": {},
        }

    def score(self, schedule: "Schedule", teams: dict[str, "Team"]) -> float:
        penalty = 0.0

        sc1 = self._soft.get("SC1", {})
        b2b_target = sc1.get("target_per_team", 14)
        b2b_penalty = sc1.get("penalty_per_occurrence_above_target", 10)

        sc2_penalty = self._soft.get("SC2", {}).get("penalty_per_violation", 30)
        sc3_max_road = self._soft.get("SC3", {}).get("max_consecutive_road_games", 6)
        sc3_penalty = self._soft.get("SC3", {}).get("penalty_per_violation", 20)
        hc5_penalty = 1000   # 4-in-5-nights hard violation
        hc6_penalty = 1000   # 8-in-12-nights hard violation
        hc13_penalty = 2000  # missing final-day appearance (seeding integrity)

        for team_id in teams:
            team_fixtures = sorted(
                schedule.fixtures_for_team(team_id),
                key=lambda sf: sf.slot.date,
            )
            if not team_fixtures:
                continue

            dates = [sf.slot.date for sf in team_fixtures]
            is_home = [sf.home_team_id == team_id for sf in team_fixtures]

            # All-Star blackout (HC10): each game on a blackout date
            for sf in team_fixtures:
                if sf.slot.date in self._allstar_dates:
                    penalty += 500

            # Back-to-back detection
            b2b_count = 0
            road_b2b_count = 0
            for i in range(1, len(dates)):
                gap = (dates[i] - dates[i - 1]).days
                if gap == 1:
                    b2b_count += 1
                    # Road B2B: either the first or second leg is away
                    if not is_home[i - 1] or not is_home[i]:
                        road_b2b_count += 1

            if b2b_count > b2b_target:
                penalty += (b2b_count - b2b_target) * b2b_penalty
            penalty += road_b2b_count * sc2_penalty

            # 4-in-5-nights (HC5)
            for i in range(len(dates)):
                window_end = dates[i] + timedelta(days=4)
                count_in_window = sum(1 for d in dates if dates[i] <= d <= window_end)
                if count_in_window >= 4:
                    penalty += hc5_penalty

            # 8-in-12-nights (HC6)
            for i in range(len(dates)):
                window_end = dates[i] + timedelta(days=11)
                count_in_window = sum(1 for d in dates if dates[i] <= d <= window_end)
                if count_in_window >= 8:
                    penalty += hc6_penalty

            # Road trip length (SC3)
            consecutive_road = 0
            for home in is_home:
                if not home:
                    consecutive_road += 1
                    if consecutive_road > sc3_max_road:
                        penalty += sc3_penalty
                else:
                    consecutive_road = 0

        # All teams play on the final day of the season (HC13)
        final_day_teams = {
            sf.home_team_id for sf in schedule.fixtures if sf.slot.date == self._season_end
        } | {
            sf.away_team_id for sf in schedule.fixtures if sf.slot.date == self._season_end
        }
        missing_final_day = set(teams) - final_day_teams
        penalty += len(missing_final_day) * hc13_penalty

        return penalty
