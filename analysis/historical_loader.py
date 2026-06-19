"""
Loads historical EPL fixture data from football-data.co.uk CSV files
and converts them into Schedule objects that can be analysed with the
same metrics engine used on generated schedules.

CSV format (football-data.co.uk E0 division):
  Div, Date (DD/MM/YYYY or DD/MM/YY), Time, HomeTeam, AwayTeam, ...

Download seasons from: https://www.football-data.co.uk/englandm.php
"""
import csv
import json
from datetime import date, datetime
from pathlib import Path

from core.models import Fixture, Slot, ScheduledFixture, Schedule


def _historical_dir() -> Path:
    root = Path(__file__).parent.parent
    new_path = root / "data" / "leagues" / "epl" / "historical"
    if new_path.exists():
        return new_path
    return root / "leagues" / "epl" / "data" / "historical"

HISTORICAL_DIR = _historical_dir()
NAME_MAP_PATH  = HISTORICAL_DIR / "team_name_map.json"


def _load_name_map() -> dict[str, str]:
    with open(NAME_MAP_PATH) as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _parse_date(date_str: str) -> date:
    """Handles DD/MM/YYYY and DD/MM/YY formats."""
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date format: {date_str!r}")


def load_season(csv_path: Path | str, season_label: str | None = None) -> Schedule:
    """
    Reads a football-data.co.uk CSV and returns a Schedule object.

    Teams not found in the name map are kept as their raw string so the
    schedule is not silently truncated; callers can filter or warn.
    """
    csv_path = Path(csv_path)
    name_map = _load_name_map()
    label    = season_label or csv_path.stem

    fixtures: list[ScheduledFixture] = []
    fixture_counter = 1

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip blank/header rows that sometimes appear mid-file
            if not row.get("Date") or not row.get("HomeTeam"):
                continue

            try:
                match_date = _parse_date(row["Date"])
            except ValueError:
                continue

            home_raw = row["HomeTeam"].strip()
            away_raw = row["AwayTeam"].strip()
            home_id  = name_map.get(home_raw, home_raw)
            away_id  = name_map.get(away_raw, away_raw)

            kickoff = row.get("Time", "15:00").strip() or "15:00"
            day_name = match_date.strftime("%A")

            slot    = Slot(date=match_date, kickoff=kickoff, day_of_week=day_name)
            fixture = Fixture(
                fixture_id=f"H{fixture_counter:04d}",
                home_team_id=home_id,
                away_team_id=away_id,
            )
            fixtures.append(ScheduledFixture(fixture=fixture, slot=slot))
            fixture_counter += 1

    return Schedule(season=label, fixtures=fixtures)


def available_seasons() -> list[Path]:
    """Returns all CSV files in the historical data directory."""
    return sorted(HISTORICAL_DIR.glob("*.csv"))
