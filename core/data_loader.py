"""
Loads teams, calendar slots, and constraints from the active league's data directory.

Default league is "epl". Call set_league("nfl") etc. before loading to switch.
"""
import json
from datetime import date, timedelta
from pathlib import Path

from core.models import Team, Slot


_ACTIVE_LEAGUE = "epl"


def set_league(league: str) -> None:
    """Switch the active league; all subsequent loads use its data directory."""
    global _ACTIVE_LEAGUE
    _ACTIVE_LEAGUE = league


def _data_dir() -> Path:
    return Path(__file__).parent.parent / "data" / "leagues" / _ACTIVE_LEAGUE


def load_teams() -> dict[str, Team]:
    with open(_data_dir() / "teams.json") as f:
        raw = json.load(f)
    return {
        t["id"]: Team(
            id=t["id"],
            name=t["name"],
            city=t["city"],
            ground=t["ground"],
            european=t.get("european", False),
            rivalries=t.get("rivalries", []),
        )
        for t in raw["teams"]
    }


def load_calendar() -> dict:
    with open(_data_dir() / "calendar.json") as f:
        return json.load(f)


def load_constraints() -> dict:
    with open(_data_dir() / "constraints.json") as f:
        return json.load(f)


def load_city_groups() -> dict[str, list[str]]:
    with open(_data_dir() / "teams.json") as f:
        raw = json.load(f)
    return raw.get("city_groups", {})


def load_high_profile_derbies() -> list[tuple[str, str]]:
    with open(_data_dir() / "teams.json") as f:
        raw = json.load(f)
    return [tuple(pair) for pair in raw.get("high_profile_derbies", [])]


def generate_slots(calendar: dict) -> list[Slot]:
    """
    Walk every date in the season window and generate a Slot for each
    valid (day, time) combination, excluding blocked windows.

    If a matchday_slots entry has "max_per_season", that day's slots are
    thinned to that count by evenly spacing them across the season.

    Dates in "special_date_slots" override the day-of-week slot lookup,
    giving festive matchdays (Boxing Day, NYD, Easter) a full complement
    of kickoff times regardless of weekday.
    """
    start = date.fromisoformat(calendar["start_date"])
    end   = date.fromisoformat(calendar["end_date"])

    blocked_ranges = [
        (date.fromisoformat(w["start"]), date.fromisoformat(w["end"]))
        for w in calendar["blocked_windows"]
    ]

    day_slot_map = {
        entry["day"]: entry["times"]
        for entry in calendar["matchday_slots"]
    }
    day_max = {
        entry["day"]: entry["max_per_season"]
        for entry in calendar["matchday_slots"]
        if "max_per_season" in entry
    }

    # Special per-date slot overrides (festive matchdays)
    special_dates: dict[date, list[str]] = {
        date.fromisoformat(entry["date"]): entry["times"]
        for entry in calendar.get("special_date_slots", [])
    }

    # Collect all candidate slots per day type separately
    from collections import defaultdict
    by_day: dict[str, list[Slot]] = defaultdict(list)
    unlimited: list[Slot] = []

    current = start
    while current <= end:
        in_blocked = any(s <= current <= e for s, e in blocked_ranges)
        if in_blocked:
            current += timedelta(days=1)
            continue

        day_name = current.strftime("%A")

        if current in special_dates:
            # Override: use festive times, bypass day-of-week cap entirely
            for t in special_dates[current]:
                unlimited.append(Slot(date=current, kickoff=t, day_of_week=day_name))
        elif day_name in day_slot_map:
            for t in day_slot_map[day_name]:
                slot = Slot(date=current, kickoff=t, day_of_week=day_name)
                if day_name in day_max:
                    by_day[day_name].append(slot)
                else:
                    unlimited.append(slot)

        current += timedelta(days=1)

    # Thin capped days by even spacing
    slots = list(unlimited)
    for day_name, cap in day_max.items():
        candidates = by_day[day_name]
        if len(candidates) <= cap:
            slots.extend(candidates)
        else:
            step = len(candidates) / cap
            slots.extend(candidates[int(i * step)] for i in range(cap))

    # HC8: add the final-day slot explicitly (e.g. 16:00 on the last Sunday —
    # not part of regular matchday_slots so it must be injected here).
    fd = calendar.get("final_day", {})
    if fd:
        fd_date = date.fromisoformat(fd["date"])
        fd_slot = Slot(
            date=fd_date,
            kickoff=fd["kickoff"],
            day_of_week=fd_date.strftime("%A"),
        )
        existing_ids = {s.slot_id for s in slots}
        if fd_slot.slot_id not in existing_ids:
            slots.append(fd_slot)

    return slots
