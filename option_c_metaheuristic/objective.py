"""
Option C — Metaheuristic: Objective / penalty function.

Evaluates a complete Schedule and returns a scalar penalty score.
Lower is better; zero means all hard and soft constraints satisfied.

Hard violations carry HARD_PENALTY (dominates soft, drives them out first).
Soft violation weights are loaded from constraints.json.

Constraints scored
------------------
Hard : HC1 (rest), HC3 (blocked windows), HC7 (Christmas), HC8 (final day)
Soft : SC1, SC2 (consecutive runs), SC3 (derby gap), SC5 (H/A balance),
       SC7 (city clash), SC8 (UEFA Thu 5-day), SC9 (Easter), SC10 (London cap),
       SC12 (opening balance)
"""
from collections import defaultdict
from datetime import date

from scheduler.models import Schedule
from scheduler.data_loader import (
    load_constraints, load_city_groups, load_high_profile_derbies, load_calendar,
)


HARD_PENALTY = 10_000
_WEIGHTS: dict[str, int] = {}
_CALENDAR: dict = {}


def _init():
    global _WEIGHTS, _CALENDAR
    if not _WEIGHTS:
        constraints = load_constraints()
        _WEIGHTS    = {c["id"]: c.get("penalty_per_violation",
                                       c.get("penalty_per_clash",
                                       c.get("penalty_per_missing_team", 20)))
                       for c in constraints["soft"]}
    if not _CALENDAR:
        _CALENDAR = load_calendar()


def score(schedule: Schedule, teams: dict) -> float:
    _init()
    total        = 0.0
    city_groups  = load_city_groups()
    city_lookup  = {t: c for c, members in city_groups.items() for t in members}
    london_teams = set(city_groups.get("London", []))
    derbies      = set(tuple(sorted(p)) for p in load_high_profile_derbies())

    # blocked windows (HC3 + HC7 via calendar)
    blocked = [
        (date.fromisoformat(w["start"]), date.fromisoformat(w["end"]))
        for w in _CALENDAR.get("blocked_windows", [])
    ]

    def in_blocked(d: date) -> bool:
        return any(s <= d <= e for s, e in blocked)

    # ── HC1: min rest days ────────────────────────────────────────────────
    for team_id in teams:
        fx = sorted(schedule.fixtures_for_team(team_id), key=lambda sf: sf.slot.date)
        for i in range(1, len(fx)):
            gap = (fx[i].slot.date - fx[i-1].slot.date).days
            if gap < 3:
                total += HARD_PENALTY * (3 - gap)

    # ── HC3 + HC7: blocked windows and Christmas Day ──────────────────────
    for sf in schedule.fixtures:
        if in_blocked(sf.slot.date):
            total += HARD_PENALTY
        if sf.slot.date.month == 12 and sf.slot.date.day == 25:
            total += HARD_PENALTY

    # ── HC5: team plays twice on same day ─────────────────────────────────
    for team_id in teams:
        date_counts: dict[str, int] = defaultdict(int)
        for sf in schedule.fixtures_for_team(team_id):
            date_counts[str(sf.slot.date)] += 1
        for count in date_counts.values():
            if count > 1:
                total += HARD_PENALTY * (count - 1)

    # ── HC8: final-day simultaneous kickoff ───────────────────────────────
    final_cfg = _CALENDAR.get("final_day", {})
    if final_cfg:
        final_date = date.fromisoformat(final_cfg["date"])
        final_ko   = final_cfg["kickoff"]
        for sf in schedule.fixtures:
            if sf.slot.date == final_date and sf.slot.kickoff != final_ko:
                total += HARD_PENALTY

    # ── home-by-date map (used by SC7 and SC10) ───────────────────────────
    home_by_date: dict[str, list[str]] = defaultdict(list)
    for sf in schedule.fixtures:
        home_by_date[str(sf.slot.date)].append(sf.home_team_id)

    # ── SC7: same-city home clash (was HC2 — now soft) ────────────────────
    p_city = _WEIGHTS.get("SC7", 40)
    for home_teams in home_by_date.values():
        city_count: dict[str, int] = defaultdict(int)
        for t in home_teams:
            city_count[city_lookup.get(t, "_")] += 1
        for city, count in city_count.items():
            if count > 1 and city != "_":
                total += p_city * (count - 1)

    # ── SC10: London cluster cap ──────────────────────────────────────────
    p_london   = _WEIGHTS.get("SC10", 30)
    max_london = 3
    for home_teams in home_by_date.values():
        london_home = sum(1 for t in home_teams if t in london_teams)
        if london_home > max_london:
            total += p_london * (london_home - max_london)

    # ── SC1/SC2: consecutive home/away runs ───────────────────────────────
    max_run = 5
    p_away  = _WEIGHTS.get("SC1", 15)
    p_home  = _WEIGHTS.get("SC2", 15)
    for team_id in teams:
        fx = sorted(schedule.fixtures_for_team(team_id), key=lambda sf: sf.slot.date)
        away_run = home_run = 0
        for sf in fx:
            if sf.away_team_id == team_id:
                away_run += 1; home_run = 0
            else:
                home_run += 1; away_run = 0
            if away_run > max_run:
                total += p_away
            if home_run > max_run:
                total += p_home

    # ── SC3: derby gap ────────────────────────────────────────────────────
    derby_dates: dict[tuple, list] = defaultdict(list)
    for sf in schedule.fixtures:
        pair = tuple(sorted([sf.home_team_id, sf.away_team_id]))
        if pair in derbies:
            derby_dates[pair].append(sf.slot.date)
    p_derby = _WEIGHTS.get("SC3", 30)
    for dates in derby_dates.values():
        if len(dates) == 2:
            gap = abs((dates[1] - dates[0]).days)
            if gap < 56:
                total += p_derby * (1 + (56 - gap) // 7)

    # ── SC9: Easter coverage ──────────────────────────────────────────────
    easter_cfg = _CALENDAR.get("easter_matchdays", {})
    p_easter   = _WEIGHTS.get("SC9", 20)
    team_ids   = set(teams.keys())
    for date_key in ["good_friday", "easter_monday"]:
        if date_key not in easter_cfg:
            continue
        easter_date = date.fromisoformat(easter_cfg[date_key])
        playing: set[str] = set()
        for sf in schedule.fixtures:
            if sf.slot.date == easter_date:
                playing.add(sf.home_team_id)
                playing.add(sf.away_team_id)
        total += p_easter * len(team_ids - playing)

    # ── SC12: opening balance (rounds 1-5) ────────────────────────────────
    all_dates   = sorted({sf.slot.date for sf in schedule.fixtures})
    round_dates = set(all_dates[:5])
    p_open      = _WEIGHTS.get("SC12", 20)
    for team_id in teams:
        early = sorted(
            [sf for sf in schedule.fixtures_for_team(team_id) if sf.slot.date in round_dates],
            key=lambda sf: sf.slot.date,
        )
        h_run = a_run = 0
        for sf in early:
            if sf.home_team_id == team_id:
                h_run += 1; a_run = 0
            else:
                a_run += 1; h_run = 0
            if h_run > 3 or a_run > 3:
                total += p_open
                break

    return total
