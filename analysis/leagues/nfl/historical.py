"""
Loads real NFL game data from nflverse per-season CSVs and converts
them into Schedule objects compatible with the shared metrics engine.

CSV format (nflverse games.csv, REG games only):
    game_id, season, game_type, week, gameday (YYYY-MM-DD), weekday,
    gametime (HH:MM ET), away_team, away_score, home_team, home_score, ...

Download real data via:
    python data/leagues/nfl/historical/download_seasons.py
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from core.models import Fixture, Slot, ScheduledFixture, Schedule


def load_season(csv_path: Path, season_label: str | None = None) -> Schedule:
    csv_path = Path(csv_path)
    label = season_label or csv_path.stem
    fixtures: list[ScheduledFixture] = []
    counter = 1

    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("game_type", "REG") != "REG":
                continue
            gameday = row.get("gameday", "").strip()
            if not gameday:
                continue
            try:
                match_date = datetime.strptime(gameday, "%Y-%m-%d").date()
            except ValueError:
                continue

            home_id = row.get("home_team", "").strip()
            away_id = row.get("away_team", "").strip()
            if not home_id or not away_id:
                continue

            kickoff = row.get("gametime", "").strip() or "13:00"
            day_name = row.get("weekday", "").strip() or match_date.strftime("%A")

            slot = Slot(date=match_date, kickoff=kickoff, day_of_week=day_name)
            fixture = Fixture(
                fixture_id=f"NFL_H{counter:04d}",
                home_team_id=home_id,
                away_team_id=away_id,
            )
            fixtures.append(ScheduledFixture(fixture=fixture, slot=slot))
            counter += 1

    return Schedule(season=label, fixtures=fixtures)
