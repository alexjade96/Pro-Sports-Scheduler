"""
EPL constraint set for the CP-SAT solver.

Wraps ``solvers/cp_sat/constraints.py`` with zero behaviour changes.
The generic solver core calls only the three protocol methods; this class
dispatches to the individual EPL constraint functions.
"""
from __future__ import annotations

from datetime import date

from solvers.slot_filter import build_eligible_slots, log_filter_stats
from solvers.cp_sat.constraints import (
    add_each_fixture_assigned_exactly_once,
    add_team_plays_at_most_once_per_slot,
    add_min_rest_days,
    add_max_single_day_games_per_team,
    add_max_midweek_games_per_team,
    add_soft_max_consecutive_home_away,
    add_soft_half_season_balance,
)

# Per-team single-day appearance caps (HC9, HC11, HC12, HC13): (constraint_id,
# weekday, default). These are officially-sourced Sky/PL slot allocations —
# each stays a named, sourced entry in constraints.json; only the enforcement
# is consolidated into one generic call here. HC10 (Tue+Wed combined) is a
# two-day union, handled separately. See "EPL constraint IDs" in CLAUDE.md.
# Defaults mirror constraints.json; HC9/HC11/HC13 were relaxed from their
# original broadcaster-derived values (3/7/2) to the observed 10-season
# historical maxima (6/8/4) so no real EPL season is falsely infeasible — see
# each rule's relaxation_note in data/leagues/epl/constraints.json.
_DAY_CAPS = [
    ("HC9",  "Friday",    6),
    ("HC11", "Monday",    8),
    ("HC12", "Wednesday", 6),
    ("HC13", "Thursday",  4),
]
from solvers.leagues.epl.cp_sat_helpers import (
    add_soft_ha_window,
    add_soft_derby_gap,
    add_soft_same_city_home_clash,
    add_soft_city_cluster,
    add_soft_festive_coverage,
    add_soft_sc14_season_boundary,
    add_soft_sc15_boxing_day_nyd,
    add_soft_min_sat_1500,
    add_soft_min_monday,
)

_FIXTURES_PER_ROUND = 10


class EPLCpSatConstraintSet:
    def __init__(
        self,
        constraint_config: dict,
        season_start: date,
        season_end: date,
        final_day: dict | None = None,
    ) -> None:
        self._hard = {c["id"]: c for c in constraint_config["hard"]}
        self._soft = {c["id"]: c for c in constraint_config["soft"]}
        self._season_start = season_start
        self._season_end = season_end
        self._final_day = final_day

    def build_eligible_slots(self, fixtures, slots) -> dict[str, list[str]]:
        eligible = build_eligible_slots(
            fixtures, slots, self._season_start, self._season_end, window_rounds=4
        )
        log_filter_stats(eligible)

        if self._final_day:
            fd_date = date.fromisoformat(self._final_day["date"])
            fd_ko = self._final_day["kickoff"]
            final_day_sid = f"{fd_date}_{fd_ko.replace(':', '')}"
            slot_ids = {s.slot_id for s in slots}
            if final_day_sid in slot_ids:
                for f in fixtures[-_FIXTURES_PER_ROUND:]:
                    eligible[f.fixture_id] = [final_day_sid]
                before_fd = {s.slot_id for s in slots if s.date < fd_date}
                for f in fixtures[:-_FIXTURES_PER_ROUND]:
                    eligible[f.fixture_id] = [
                        sid for sid in eligible[f.fixture_id] if sid in before_fd
                    ]
                print(f"[HC8] Round 38 pinned to {final_day_sid}; earlier rounds capped to < {fd_date}")
            else:
                print(f"[HC8] WARNING: final-day slot {final_day_sid} not in pool — HC8 not enforced")

        return eligible

    def add_hard_constraints(self, model, x, fixtures, slots, teams) -> None:
        h = self._hard
        add_each_fixture_assigned_exactly_once(model, x, fixtures, slots)
        add_team_plays_at_most_once_per_slot(model, x, fixtures, slots, teams)
        add_min_rest_days(model, x, fixtures, slots, teams, h["HC1"]["value"])
        for cid, day, default in _DAY_CAPS:
            add_max_single_day_games_per_team(
                model, x, fixtures, slots, teams, day,
                h.get(cid, {}).get("value", default))
        add_max_midweek_games_per_team(model, x, fixtures, slots, teams,
                                       h.get("HC10", {}).get("value", 10))

    def add_soft_constraints(self, model, x, fixtures, slots, teams) -> list:
        s = self._soft
        terms = []
        sc1 = s["SC1"]
        terms += add_soft_max_consecutive_home_away(
            model, x, fixtures, slots, teams,
            max_run=sc1["value"], penalty=sc1["penalty_per_violation"],
        )
        sc13 = s.get("SC13", {})
        terms += add_soft_ha_window(
            model, x, fixtures, slots, teams,
            penalty=sc13.get("penalty_per_violation", 25),
        )
        sc3 = s["SC3"]
        terms += add_soft_derby_gap(model, x, fixtures, slots,
                                    penalty=sc3["penalty_per_violation"])
        sc7 = s.get("SC7", {})
        terms += add_soft_same_city_home_clash(
            model, x, fixtures, slots,
            window_days=sc7.get("window_days", 4), penalty=sc7.get("penalty_per_clash", 80),
        )
        sc10 = s.get("SC10", {})
        terms += add_soft_city_cluster(
            model, x, fixtures, slots,
            city_name="London",
            max_per_day=sc10.get("max_home_same_day", 3),
            penalty=sc10.get("penalty_per_violation", 30),
        )
        sc9 = s.get("SC9", {})
        terms += add_soft_festive_coverage(
            model, x, fixtures, slots, teams,
            penalty=sc9.get("penalty_per_missing_team", 20),
        )
        sc5 = s.get("SC5", {})
        terms += add_soft_half_season_balance(
            model, x, fixtures, slots, teams,
            tolerance=sc5.get("tolerance", 2), penalty=200,
        )
        sc14 = s.get("SC14", {})
        terms += add_soft_sc14_season_boundary(
            model, x, fixtures, slots, teams, penalty=sc14.get("penalty_per_violation", 30),
        )
        sc15 = s.get("SC15", {})
        terms += add_soft_sc15_boxing_day_nyd(
            model, x, fixtures, slots, teams, penalty=sc15.get("penalty_per_violation", 35),
        )
        sc17 = s.get("SC17", {})
        terms += add_soft_min_sat_1500(
            model, x, fixtures, slots, teams,
            min_per_team=sc17.get("min_per_team", 5),
            penalty=sc17.get("penalty_per_violation", 10),
        )
        sc18 = s.get("SC18", {})
        terms += add_soft_min_monday(
            model, x, fixtures, slots, teams,
            min_per_team=sc18.get("min_per_team", 3),
            penalty=sc18.get("penalty_per_violation", 12),
        )
        return terms
