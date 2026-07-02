"""
EPL-specific metric extensions: Atos Golden Rules, festive-matchday coverage,
London cluster cap, Christmas Day blackout. These depend on EPL's UK festive
calendar and Atos-contracted scheduling rules, so they don't generalise to
other leagues — see "Analysis architecture" in CLAUDE.md.
"""
from __future__ import annotations

from datetime import date

from analysis.metrics import MetricsReport
from core.models import Schedule


def extend(
    report: MetricsReport,
    schedule: Schedule,
    calendar: dict,
    city_groups: dict[str, list[str]],
    all_team_ids: set[str],
) -> None:
    """Fills in EPL-only MetricsReport fields in place."""
    _london_cluster(report, schedule, city_groups)
    _festive_coverage(report, schedule, calendar)
    _christmas_day(report, schedule)
    _easter_coverage(report, schedule, calendar)
    _five_match_pattern(report, schedule, all_team_ids)
    _season_boundary(report, schedule, all_team_ids)
    _boxing_day_nyd_pairing(report, schedule, all_team_ids)


def _london_cluster(report: MetricsReport, schedule: Schedule, city_groups: dict) -> None:
    """SC10: >3 London clubs at home on the same day."""
    london_teams = set(city_groups.get("London", []))
    if not london_teams:
        return
    home_by_date: dict[str, list[str]] = {}
    for sf in schedule.fixtures:
        home_by_date.setdefault(str(sf.slot.date), []).append(sf.home_team_id)
    for home_teams in home_by_date.values():
        london_home = [t for t in home_teams if t in london_teams]
        if len(london_home) > 3:
            report.london_cluster_violations += 1


def _festive_coverage(report: MetricsReport, schedule: Schedule, calendar: dict) -> None:
    """Boxing Day / New Year's Day team coverage."""
    festive_dates = {d: [] for d in calendar.get("festive_matchdays", [])}
    for sf in schedule.fixtures:
        date_str = str(sf.slot.date)
        if date_str in festive_dates:
            festive_dates[date_str].append(sf.home_team_id)
            festive_dates[date_str].append(sf.away_team_id)

    for date_str, teams in festive_dates.items():
        if "12-26" in date_str:
            report.boxing_day_teams    = sorted(set(teams))
            report.boxing_day_coverage = len(set(teams))
        if "01-01" in date_str:
            report.new_years_day_teams    = sorted(set(teams))
            report.new_years_day_coverage = len(set(teams))


def _christmas_day(report: MetricsReport, schedule: Schedule) -> None:
    """HC7: no fixtures on Christmas Day."""
    for sf in schedule.fixtures:
        d = sf.slot.date
        if d.month == 12 and d.day == 25:
            report.christmas_day_violations += 1


def _easter_coverage(report: MetricsReport, schedule: Schedule, calendar: dict) -> None:
    """SC9: all clubs play on Good Friday and Easter Monday."""
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


def _five_match_pattern(report: MetricsReport, schedule: Schedule, all_team_ids: set[str]) -> None:
    """SC13 (Atos Golden Rule): any 5-consecutive-game window must be 2-3 or 3-2 home/away."""
    for team_id in all_team_ids:
        team_fx = sorted(schedule.fixtures_for_team(team_id), key=lambda sf: sf.slot.date)
        for i in range(len(team_fx) - 4):
            home_count = sum(1 for sf in team_fx[i:i+5] if sf.home_team_id == team_id)
            if home_count not in (2, 3):
                report.five_match_pattern_violations += 1


def _season_boundary(report: MetricsReport, schedule: Schedule, all_team_ids: set[str]) -> None:
    """SC14 (Atos Golden Rule): no HH or AA at season start or end."""
    for team_id in all_team_ids:
        team_fx = sorted(schedule.fixtures_for_team(team_id), key=lambda sf: sf.slot.date)
        if len(team_fx) >= 2:
            open_h = [sf.home_team_id == team_id for sf in team_fx[:2]]
            if open_h[0] == open_h[1]:
                report.season_boundary_violations += 1
            close_h = [sf.home_team_id == team_id for sf in team_fx[-2:]]
            if close_h[0] == close_h[1]:
                report.season_boundary_violations += 1


def _boxing_day_nyd_pairing(report: MetricsReport, schedule: Schedule, all_team_ids: set[str]) -> None:
    """SC15 (Atos Golden Rule): opposite H/A on Boxing Day vs New Year's Day."""
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
