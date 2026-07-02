"""
NBA-specific metric extensions: back-to-back counts, 4-games-in-5-nights
violations, All-Star break compliance. These mirror the sliding-window
logic already used by solvers/leagues/nba/*_constraint_set.py (HC5/HC10),
which has no equivalent in other leagues — see "Analysis architecture" in
CLAUDE.md.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from analysis.metrics import MetricsReport
from core.models import Schedule


def extend(
    report: MetricsReport,
    schedule: Schedule,
    calendar: dict,
    city_groups: dict[str, list[str]],
    all_team_ids: set[str],
) -> None:
    """Fills in NBA-only MetricsReport fields in place."""
    _back_to_backs(report, schedule, all_team_ids)
    _four_in_five(report, schedule, all_team_ids)
    _all_star_break(report, schedule, calendar)


def _back_to_backs(report: MetricsReport, schedule: Schedule, all_team_ids: set[str]) -> None:
    """A back-to-back is a 1-day gap between consecutive fixtures for a team."""
    for team_id in all_team_ids:
        gaps = report.rest_days_per_team.get(team_id, [])
        b2b = sum(1 for g in gaps if g == 1)
        report.back_to_back_counts[team_id] = b2b
        report.league_back_to_backs += b2b


def _four_in_five(report: MetricsReport, schedule: Schedule, all_team_ids: set[str]) -> None:
    """HC5: no team should play 4+ games in any 5-consecutive-night window."""
    team_dates: dict[str, list[date]] = defaultdict(list)
    for sf in schedule.fixtures:
        team_dates[sf.home_team_id].append(sf.slot.date)
        team_dates[sf.away_team_id].append(sf.slot.date)

    for team_id in all_team_ids:
        dates = sorted(team_dates.get(team_id, []))
        for i, d in enumerate(dates):
            window_end = d + timedelta(days=4)
            count = sum(1 for wd in dates[i:] if wd <= window_end)
            if count >= 4:
                report.four_in_five_violations += 1


def _all_star_break(report: MetricsReport, schedule: Schedule, calendar: dict) -> None:
    """HC10: no games during the All-Star break window."""
    blackout: set[date] = set()
    for bw in calendar.get("blocked_windows", []):
        if "all-star" in bw.get("label", "").lower():
            start = date.fromisoformat(bw["start"])
            end   = date.fromisoformat(bw["end"])
            d = start
            while d <= end:
                blackout.add(d)
                d += timedelta(days=1)

    if not blackout:
        return
    for sf in schedule.fixtures:
        if sf.slot.date in blackout:
            report.all_star_break_violations += 1
