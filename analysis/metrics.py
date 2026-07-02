"""
Metrics engine: computes a rich MetricsReport from any Schedule object.
Works identically on historical and generated schedules — that's what
makes the comparison meaningful — and identically across leagues, since
it reads the active league via core.data_loader.get_active_league().

Metric categories
-----------------
  REST          — days between consecutive fixtures per team (generic)
  RUNS          — consecutive home / away streaks (generic)
  DISTRIBUTION  — day-of-week and kickoff-time slot usage (generic)
  CITY          — same-city home game clashes (generic; needs city_groups)
  DERBY         — gap (days) between the two legs of each rivalry (generic;
                  gap threshold is read from the league's own rivalry-spread
                  constraint, not hardcoded)
  BALANCE       — home/away split per half-season (generic)
  COMPLIANCE    — fixtures falling in blocked calendar windows (generic)
  COVERAGE      — team participation on the schedule's final calendar date
                  (generic)
  SOLVER        — solve time, penalty, violations (generated schedules only)

Everything above is league-agnostic: it only reads whatever calendar/team
data the active league provides. League-specific rules (Atos Golden Rules,
UK festive coverage, Thanksgiving, back-to-backs, etc.) are NOT computed
here — they live in analysis/leagues/<league>/metrics.py and are invoked
via extend() below. See "Analysis architecture" in CLAUDE.md for the rule
on what belongs in this shared module vs. a league-scoped one.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from core.models import Schedule, ScheduledFixture
from core.data_loader import (
    load_city_groups,
    load_high_profile_derbies,
    load_calendar,
    load_constraints,
    get_active_league,
)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class MetricsReport:
    label: str

    # REST
    rest_days_per_team: dict[str, list[int]]         = field(default_factory=dict)
    rest_mean:          float                         = 0.0
    rest_min_global:    int                           = 0
    rest_max_global:    int                           = 0
    rest_min_per_team:  dict[str, int]                = field(default_factory=dict)

    # RUNS
    max_consec_home_per_team: dict[str, int]          = field(default_factory=dict)
    max_consec_away_per_team: dict[str, int]          = field(default_factory=dict)
    league_max_consec_home:   int                     = 0
    league_max_consec_away:   int                     = 0
    teams_over_3_away:        list[str]               = field(default_factory=list)
    teams_over_3_home:        list[str]               = field(default_factory=list)
    teams_over_5_away:        list[str]               = field(default_factory=list)
    teams_over_5_home:        list[str]               = field(default_factory=list)

    # DISTRIBUTION
    day_of_week_counts:   dict[str, int]              = field(default_factory=dict)
    day_of_week_pct:      dict[str, float]            = field(default_factory=dict)
    kickoff_time_counts:  dict[str, int]              = field(default_factory=dict)
    kickoff_time_pct:     dict[str, float]            = field(default_factory=dict)

    # CITY
    city_clash_dates:     list[tuple[str, str, list[str]]] = field(default_factory=list)
    city_clash_count:     int                         = 0   # legacy same-day count
    city_weekend_clash_count: int                     = 0   # SC7 widened: 4-day window
    london_cluster_violations: int                    = 0   # SC10 (EPL-only)

    # DERBY / RIVALRY (generic mechanism; gap threshold is league-derived —
    # see _derby_gap_threshold_days() below. Field name kept for backward
    # compatibility with existing EPL callers; it no longer implies a
    # hardcoded 56-day window for every league.)
    derby_gaps_days:      dict[str, int]              = field(default_factory=dict)
    derbies_under_56d:    list[str]                   = field(default_factory=list)
    derby_gap_threshold_days: int                     = 0

    # FESTIVE (EPL-only)
    boxing_day_teams:     list[str]                   = field(default_factory=list)
    new_years_day_teams:  list[str]                   = field(default_factory=list)
    boxing_day_coverage:  int                         = 0
    new_years_day_coverage: int                       = 0
    good_friday_coverage: int                         = 0   # SC9
    easter_monday_coverage: int                       = 0   # SC9

    # GOLDEN RULES (Atos, EPL-only)
    five_match_pattern_violations: int                = 0   # SC13
    season_boundary_violations:    int                = 0   # SC14
    boxing_day_nyd_violations:     int                = 0   # SC15

    # COMPLIANCE
    intl_break_violations:  list[tuple[str, str]]     = field(default_factory=list)
    intl_break_violation_count: int                   = 0
    christmas_day_violations: int                     = 0   # HC7 (EPL-only)

    # BALANCE
    home_pct_first_half:  dict[str, float]            = field(default_factory=dict)
    home_pct_second_half: dict[str, float]            = field(default_factory=dict)

    # COVERAGE (generic)
    final_day_team_coverage: int                      = 0

    # NFL-only
    thanksgiving_coverage: int                        = 0
    thanksgiving_fixed_host_violations: int            = 0
    primetime_game_pct:   float                        = 0.0

    # NBA-only
    back_to_back_counts:  dict[str, int]              = field(default_factory=dict)
    league_back_to_backs: int                         = 0
    four_in_five_violations: int                      = 0
    all_star_break_violations: int                    = 0

    # TOTALS
    total_fixtures:       int                         = 0
    teams_seen:           list[str]                   = field(default_factory=list)

    # SOLVER (optional — only for generated schedules)
    solve_time_seconds:   Optional[float]             = None
    penalty_score:        Optional[float]             = None
    hard_violations:      Optional[int]               = None
    soft_violations:      Optional[int]               = None
    # Per-constraint violation breakdown from validator (generated schedules only)
    constraint_violations: dict[str, int]             = field(default_factory=dict)
    final_day_enforced:   Optional[bool]              = None  # HC8 (EPL-only)


# ---------------------------------------------------------------------------
# League extension dispatch
# ---------------------------------------------------------------------------

def _league_extension(league: str):
    """Returns the extend(report, schedule, calendar, city_groups, all_team_ids)
    function for the given league, or None if it has no extensions."""
    if league == "epl":
        from analysis.leagues.epl.metrics import extend
        return extend
    if league == "nfl":
        from analysis.leagues.nfl.metrics import extend
        return extend
    if league == "nba":
        from analysis.leagues.nba.metrics import extend
        return extend
    return None


def _derby_gap_threshold_days(constraints: dict) -> int:
    """
    Derives a minimum-gap-in-days threshold for rivalry/derby fixtures from
    the active league's own constraint config, instead of hardcoding one
    league's convention. Falls back to 56 days (EPL's 8-round convention)
    if the league defines no rivalry-spread constraint.
    """
    for bucket in ("soft", "hard"):
        for c in constraints.get(bucket, []):
            ctype = c.get("type", "")
            if ctype == "derby_min_gap_rounds" and "value" in c:
                return int(c["value"]) * 7
            if ctype in ("division_rivalry_spread", "rivalry_spread") and "min_gap_weeks" in c:
                return int(c["min_gap_weeks"]) * 7
    return 56


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def compute(schedule: Schedule, solver_meta: dict | None = None) -> MetricsReport:
    """
    Main entry point. Pass solver_meta dict with keys:
        solve_time_seconds, penalty_score, hard_violations, soft_violations
    for generated schedules; leave None for historical.

    Reads the active league via core.data_loader.get_active_league() — call
    set_league() before compute() if analysing a non-EPL schedule.
    """
    report = MetricsReport(label=schedule.season)
    report.total_fixtures = len(schedule.fixtures)

    league       = get_active_league()
    calendar     = load_calendar()
    constraints  = load_constraints()
    city_groups  = load_city_groups()
    city_lookup  = {t: city for city, members in city_groups.items() for t in members}
    derby_pairs  = load_high_profile_derbies()

    all_team_ids: set[str] = set()
    for sf in schedule.fixtures:
        all_team_ids.add(sf.home_team_id)
        all_team_ids.add(sf.away_team_id)
    report.teams_seen = sorted(all_team_ids)

    season_start = date.fromisoformat(calendar["start_date"])
    season_end   = date.fromisoformat(calendar["end_date"])
    midpoint     = date.fromordinal((season_start.toordinal() + season_end.toordinal()) // 2)

    blocked_windows = [
        (date.fromisoformat(w["start"]), date.fromisoformat(w["end"]))
        for w in calendar["blocked_windows"]
    ]

    # --- REST days ---
    all_rest_days: list[int] = []
    for team_id in all_team_ids:
        team_fixtures = sorted(
            schedule.fixtures_for_team(team_id),
            key=lambda sf: sf.slot.date,
        )
        gaps = [
            (team_fixtures[i].slot.date - team_fixtures[i-1].slot.date).days
            for i in range(1, len(team_fixtures))
        ]
        report.rest_days_per_team[team_id] = gaps
        report.rest_min_per_team[team_id]  = min(gaps) if gaps else 0
        all_rest_days.extend(gaps)

    if all_rest_days:
        report.rest_mean       = round(statistics.mean(all_rest_days), 2)
        report.rest_min_global = min(all_rest_days)
        report.rest_max_global = max(all_rest_days)

    # --- RUNS (consecutive home/away) ---
    for team_id in all_team_ids:
        team_fixtures = sorted(
            schedule.fixtures_for_team(team_id),
            key=lambda sf: sf.slot.date,
        )
        max_home = max_away = cur_home = cur_away = 0
        for sf in team_fixtures:
            if sf.home_team_id == team_id:
                cur_home += 1; cur_away = 0
            else:
                cur_away += 1; cur_home = 0
            max_home = max(max_home, cur_home)
            max_away = max(max_away, cur_away)
        report.max_consec_home_per_team[team_id] = max_home
        report.max_consec_away_per_team[team_id] = max_away
        if max_away > 3:
            report.teams_over_3_away.append(team_id)
        if max_home > 3:
            report.teams_over_3_home.append(team_id)
        if max_away > 5:
            report.teams_over_5_away.append(team_id)
        if max_home > 5:
            report.teams_over_5_home.append(team_id)

    report.league_max_consec_home = max(report.max_consec_home_per_team.values(), default=0)
    report.league_max_consec_away = max(report.max_consec_away_per_team.values(), default=0)

    # --- DISTRIBUTION ---
    day_counts: dict[str, int] = defaultdict(int)
    ko_counts:  dict[str, int] = defaultdict(int)
    for sf in schedule.fixtures:
        day_counts[sf.slot.day_of_week] += 1
        ko_counts[sf.slot.kickoff]      += 1

    total = report.total_fixtures or 1
    report.day_of_week_counts  = dict(day_counts)
    report.day_of_week_pct     = {d: round(c / total * 100, 1) for d, c in day_counts.items()}
    report.kickoff_time_counts = dict(ko_counts)
    report.kickoff_time_pct    = {t: round(c / total * 100, 1) for t, c in ko_counts.items()}

    # --- CITY CLASHES (same-day + 4-day window) ---
    home_by_date: dict[str, list[str]] = defaultdict(list)
    home_dates_by_team: dict[str, list[date]] = defaultdict(list)
    for sf in schedule.fixtures:
        home_by_date[str(sf.slot.date)].append(sf.home_team_id)
        home_dates_by_team[sf.home_team_id].append(sf.slot.date)

    for date_str, home_teams in home_by_date.items():
        city_count: dict[str, list[str]] = defaultdict(list)
        for t in home_teams:
            city_count[city_lookup.get(t, "_unknown")].append(t)
        for city, clashing in city_count.items():
            if len(clashing) > 1 and city != "_unknown":
                report.city_clash_dates.append((date_str, city, clashing))

    report.city_clash_count = len(report.city_clash_dates)

    # 4-day-window same-city home clash count
    checked_pairs: set[frozenset] = set()
    for city, members in city_groups.items():
        for i, team_a in enumerate(members):
            for team_b in members[i+1:]:
                pair = frozenset([team_a, team_b])
                if pair in checked_pairs:
                    continue
                checked_pairs.add(pair)
                for da in home_dates_by_team.get(team_a, []):
                    for db in home_dates_by_team.get(team_b, []):
                        if abs((da - db).days) <= 4:
                            report.city_weekend_clash_count += 1

    # --- DERBY / RIVALRY GAPS ---
    report.derby_gap_threshold_days = _derby_gap_threshold_days(constraints)
    derby_fixture_dates: dict[str, list[date]] = defaultdict(list)
    for sf in schedule.fixtures:
        pair_key = "_v_".join(sorted([sf.home_team_id, sf.away_team_id]))
        for team_a, team_b in derby_pairs:
            if set([sf.home_team_id, sf.away_team_id]) == set([team_a, team_b]):
                derby_fixture_dates[pair_key].append(sf.slot.date)

    for key, dates in derby_fixture_dates.items():
        if len(dates) == 2:
            gap = abs((dates[1] - dates[0]).days)
            report.derby_gaps_days[key] = gap
            if gap < report.derby_gap_threshold_days:
                report.derbies_under_56d.append(key)

    # --- BLOCKED-WINDOW COMPLIANCE ---
    for sf in schedule.fixtures:
        match_date = sf.slot.date
        for start, end in blocked_windows:
            if start <= match_date <= end:
                report.intl_break_violations.append((
                    str(match_date),
                    f"{sf.home_team_id} v {sf.away_team_id}",
                ))
    report.intl_break_violation_count = len(report.intl_break_violations)

    # --- FINAL-DAY TEAM COVERAGE ---
    final_day_fixtures = [sf for sf in schedule.fixtures if sf.slot.date == season_end]
    final_day_teams: set[str] = set()
    for sf in final_day_fixtures:
        final_day_teams.add(sf.home_team_id)
        final_day_teams.add(sf.away_team_id)
    report.final_day_team_coverage = len(final_day_teams)

    # --- HOME/AWAY BALANCE per half-season ---
    for team_id in all_team_ids:
        h1_home = h1_away = h2_home = h2_away = 0
        for sf in schedule.fixtures_for_team(team_id):
            is_first_half = sf.slot.date <= midpoint
            is_home       = sf.home_team_id == team_id
            if is_first_half:
                if is_home: h1_home += 1
                else:       h1_away += 1
            else:
                if is_home: h2_home += 1
                else:       h2_away += 1
        h1_total = (h1_home + h1_away) or 1
        h2_total = (h2_home + h2_away) or 1
        report.home_pct_first_half[team_id]  = round(h1_home / h1_total * 100, 1)
        report.home_pct_second_half[team_id] = round(h2_home / h2_total * 100, 1)

    # --- LEAGUE-SPECIFIC EXTENSIONS ---
    extend = _league_extension(league)
    if extend is not None:
        extend(report, schedule, calendar, city_groups, all_team_ids)

    # --- SOLVER META ---
    if solver_meta:
        report.solve_time_seconds    = solver_meta.get("solve_time_seconds")
        report.penalty_score         = solver_meta.get("penalty_score")
        report.hard_violations       = solver_meta.get("hard_violations")
        report.soft_violations       = solver_meta.get("soft_violations")
        report.constraint_violations = solver_meta.get("constraint_violations", {})
        report.final_day_enforced    = solver_meta.get("final_day_enforced")

    return report
