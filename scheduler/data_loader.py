"""
Loads teams, calendar slots, and constraints from JSON config files.
"""
import json
from datetime import date, timedelta
from pathlib import Path

from scheduler.models import Team, Slot


DATA_DIR = Path(__file__).parent.parent / "data"


def load_teams() -> dict[str, Team]:
    with open(DATA_DIR / "teams.json") as f:
        raw = json.load(f)
    return {
        t["id"]: Team(
            id=t["id"],
            name=t["name"],
            city=t["city"],
            ground=t["ground"],
            european=t["european"],
            rivalries=t["rivalries"],
        )
        for t in raw["teams"]
    }


def load_calendar() -> dict:
    with open(DATA_DIR / "calendar.json") as f:
        return json.load(f)


def load_constraints() -> dict:
    with open(DATA_DIR / "constraints.json") as f:
        return json.load(f)


def load_city_groups() -> dict[str, list[str]]:
    with open(DATA_DIR / "teams.json") as f:
        raw = json.load(f)
    return raw["city_groups"]


def load_high_profile_derbies() -> list[tuple[str, str]]:
    with open(DATA_DIR / "teams.json") as f:
        raw = json.load(f)
    return [tuple(pair) for pair in raw["high_profile_derbies"]]


def generate_slots(calendar: dict) -> list[Slot]:
    """
    Walk every date in the season window and generate a Slot for each
    valid (day, time) combination, excluding blocked international-break
    windows.
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

    slots: list[Slot] = []
    current = start
    while current <= end:
        day_name = current.strftime("%A")
        if day_name in day_slot_map:
            in_blocked = any(s <= current <= e for s, e in blocked_ranges)
            if not in_blocked:
                for t in day_slot_map[day_name]:
                    slots.append(Slot(date=current, kickoff=t, day_of_week=day_name))
        current += timedelta(days=1)

    return slots
