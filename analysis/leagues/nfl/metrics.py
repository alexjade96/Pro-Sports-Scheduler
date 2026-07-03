"""
NFL-specific metric extensions: Thanksgiving coverage/hosts, primetime
broadcast-slot share. These depend on NFL's calendar.json special_matchdays
and broadcast_windows definitions, which have no equivalent structure in
other leagues — see "Analysis architecture" in CLAUDE.md.
"""
from __future__ import annotations

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
    """Fills in NFL-only MetricsReport fields in place."""
    _thanksgiving(report, schedule, calendar)
    _primetime_share(report, schedule, calendar)


def _fourth_thursday_of_november(year: int) -> date:
    """US Thanksgiving. Computed per calendar year present in the schedule
    rather than read from the active calendar's single special_matchdays
    entry, since Thanksgiving falls on a different date every year — a
    historical season needs its own date, not the currently-configured
    season's — mirrors EPL's _easter_sunday() fix for the same class of bug."""
    d = date(year, 11, 1)
    first_thursday = d + timedelta(days=(3 - d.weekday()) % 7)
    return first_thursday + timedelta(days=21)


def _thanksgiving(report: MetricsReport, schedule: Schedule, calendar: dict) -> None:
    """HC9: DAL and DET must play home on Thanksgiving; also tracks overall
    team coverage on the holiday (normally 6 teams across 3 games)."""
    years = {sf.slot.date.year for sf in schedule.fixtures}
    thanksgiving_dates = {_fourth_thursday_of_november(y) for y in years}
    if not thanksgiving_dates:
        return

    playing: set[str] = set()
    home_on_day: set[str] = set()
    for sf in schedule.fixtures:
        if sf.slot.date in thanksgiving_dates:
            playing.add(sf.home_team_id)
            playing.add(sf.away_team_id)
            home_on_day.add(sf.home_team_id)

    report.thanksgiving_coverage = len(playing)
    for mandatory_home in ("DAL", "DET"):
        if mandatory_home in report.teams_seen and mandatory_home not in home_on_day:
            report.thanksgiving_fixed_host_violations += 1


def _primetime_share(report: MetricsReport, schedule: Schedule, calendar: dict) -> None:
    """Share of fixtures landing in a designated single-game broadcast
    window (TNF/SNF/MNF), derived from calendar.json's broadcast_windows —
    not a hardcoded set of kickoff times."""
    primetime_slots: set[tuple[str, str]] = set()
    for w in calendar.get("broadcast_windows", []):
        if "night football" not in w.get("label", "").lower():
            continue
        parts = w.get("slot", "").split("_")
        if len(parts) >= 2:
            primetime_slots.add((parts[0], parts[1]))

    if not primetime_slots or not schedule.fixtures:
        return

    count = sum(
        1 for sf in schedule.fixtures
        if (sf.slot.day_of_week, sf.slot.kickoff) in primetime_slots
    )
    report.primetime_game_pct = round(count / len(schedule.fixtures) * 100, 1)
