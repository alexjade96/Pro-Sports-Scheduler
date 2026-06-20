"""
Metrics engine: computes a rich MetricsReport from any Schedule object.
Works identically on historical and generated schedules — that's what
makes the comparison meaningful.

Metric categories
-----------------
  REST          — days between consecutive fixtures per team
  RUNS          — consecutive home / away streaks
  DISTRIBUTION  — day-of-week and kickoff-time slot usage
  CITY          — same-city home game clashes
  DERBY         — gap (days) between the two legs of each derby
  FESTIVE       — team coverage on Boxing Day and New Year's Day
  COMPLIANCE    — fixtures falling in international-break windows
  BALANCE       — home/away split per half-season
  SOLVER        — solve time, penalty, violations (generated schedules only)
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from core.models import Schedule, ScheduledFixture
from core.data_loader import load_city_groups, load_high_profile_derbies, load_calendar


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
    london_cluster_violations: int                    = 0   # SC10: >3 London home games/day

    # DERBY
    derby_gaps_days:      dict[str, int]              = field(default_factory=dict)
    derbies_under_56d:    list[str]                   = field(default_factory=list)

    # FESTIVE
    boxing_day_teams:     list[str]                   = field(default_factory=list)
    new_years_day_teams:  list[str]                   = field(default_factory=list)
    boxing_day_coverage:  int                         = 0
    new_years_day_coverage: int                       = 0
    good_friday_coverage: int                         = 0   # SC9
    easter_monday_coverage: int                       = 0   # SC9

    # GOLDEN RULES (Atos)
    five_match_pattern_violations: int                = 0   # SC13
    season_boundary_violations:    int                = 0   # SC14
    boxing_day_nyd_violations:     int                = 0   # SC15

    # COMPLIANCE
    intl_break_violations:  list[tuple[str, str]]     = field(default_factory=list)
    intl_break_violation_count: int                   = 0
    christmas_day_violations: int                     = 0   # HC7

    # BALANCE
    home_pct_first_half:  dict[str, float]            = field(default_factory=dict)
    home_pct_second_half: dict[str, float]            = field(default_factory=dict)

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
    final_day_enforced:   Optional[bool]              = None  # HC8


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def compute(schedule: Schedule, solver_meta: dict | None = None) -> MetricsReport:
    """
    Main entry point. Pass solver_meta dict with keys:
        solve_time_seconds, penalty_score, hard_violations, soft_violations
    for generated schedules; leave None for historical.
    """
    report = MetricsReport(label=schedule.season)
    report.total_fixtures = len(schedule.fixtures)

    calendar     = load_calendar()
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

    # --- CITY CLASHES (SC7 same-day + SC7 4-day window) + LONDON CLUSTER (SC10) ---
    home_by_date: dict[str, list[str]] = defaultdict(list)
    home_dates_by_team: dict[str, list[date]] = defaultdict(list)
    london_teams = set(city_groups.get("London", []))
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
        london_home = [t for t in home_teams if t in london_teams]
        if len(london_home) > 3:
            report.london_cluster_violations += 1

    report.city_clash_count = len(report.city_clash_dates)

    # SC7 widened: count same-city pairs with home fixtures within 4 days
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

    # --- DERBY GAPS ---
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
            if gap < 56:
                report.derbies_under_56d.append(key)

    # --- FESTIVE COVERAGE ---
    festive_dates = {d: [] for d in calendar.get("festive_matchdays", [])}
    for sf in schedule.fixtures:
        date_str = str(sf.slot.date)
        if date_str in festive_dates:
            festive_dates[date_str].append(sf.home_team_id)
            festive_dates[date_str].append(sf.away_team_id)

    for date_str, teams in festive_dates.items():
        if "2025-12-26" in date_str or "12-26" in date_str:
            report.boxing_day_teams     = sorted(set(teams))
            report.boxing_day_coverage  = len(set(teams))
        if "01-01" in date_str:
            report.new_years_day_teams    = sorted(set(teams))
            report.new_years_day_coverage = len(set(teams))

    # --- INTERNATIONAL BREAK COMPLIANCE + CHRISTMAS DAY (HC7) ---
    for sf in schedule.fixtures:
        match_date = sf.slot.date
        for start, end in blocked_windows:
            if start <= match_date <= end:
                report.intl_break_violations.append((
                    str(match_date),
                    f"{sf.home_team_id} v {sf.away_team_id}",
                ))
        if match_date.month == 12 and match_date.day == 25:
            report.christmas_day_violations += 1
    report.intl_break_violation_count = len(report.intl_break_violations)

    # --- EASTER COVERAGE (SC9) ---
    easter_cfg = calendar.get("easter_matchdays", {})
    for attr, date_key in [("good_friday_coverage", "good_friday"),
                            ("easter_monday_coverage", "easter_monday")]:
        if date_key not in easter_cfg:
            continue
        easter_date = date.fromisoformat(easter_cfg[date_key])
        playing: set[str] = set()
        for sf in schedule.fixtures:
            if sf.slot.date == easter_date:
                playing.add(sf.home_team_id)
                playing.add(sf.away_team_id)
        setattr(report, attr, len(playing))

    # --- SC13: five-match H/A pattern ---
    for team_id in all_team_ids:
        team_fx = sorted(
            schedule.fixtures_for_team(team_id),
            key=lambda sf: sf.slot.date,
        )
        for i in range(len(team_fx) - 4):
            home_count = sum(1 for sf in team_fx[i:i+5] if sf.home_team_id == team_id)
            if home_count not in (2, 3):
                report.five_match_pattern_violations += 1

    # --- SC14: season boundary H/A ---
    for team_id in all_team_ids:
        team_fx = sorted(
            schedule.fixtures_for_team(team_id),
            key=lambda sf: sf.slot.date,
        )
        if len(team_fx) >= 2:
            open_h = [sf.home_team_id == team_id for sf in team_fx[:2]]
            if open_h[0] == open_h[1]:
                report.season_boundary_violations += 1
            close_h = [sf.home_team_id == team_id for sf in team_fx[-2:]]
            if close_h[0] == close_h[1]:
                report.season_boundary_violations += 1

    # --- SC15: Boxing Day / NYD pairing ---
    for team_id in all_team_ids:
        bd_home: bool | None = None
        nyd_home: bool | None = None
        for sf in schedule.fixtures_for_team(team_id):
            d = sf.slot.date
            if d.month == 12 and d.day == 26:
                bd_home = (sf.home_team_id == team_id)
            if d.month == 1 and d.day == 1:
                nyd_home = (sf.home_team_id == team_id)
        if bd_home is not None and nyd_home is not None and bd_home == nyd_home:
            report.boxing_day_nyd_violations += 1

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

    # --- SOLVER META ---
    if solver_meta:
        report.solve_time_seconds    = solver_meta.get("solve_time_seconds")
        report.penalty_score         = solver_meta.get("penalty_score")
        report.hard_violations       = solver_meta.get("hard_violations")
        report.soft_violations       = solver_meta.get("soft_violations")
        report.constraint_violations = solver_meta.get("constraint_violations", {})
        report.final_day_enforced    = solver_meta.get("final_day_enforced")

    return report
