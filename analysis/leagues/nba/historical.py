"""
Loads NBA game data (synthetic or real) and converts it into Schedule
objects compatible with the shared metrics engine.

CSV format:
    game_id, season, game_type, game_date (YYYY-MM-DD), weekday, gametime,
    away_team, away_score, home_team, home_score, result, overtime, arena

See data/leagues/nba/historical/generate_synthetic.py for the generator
used until real historical data is available.
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
            game_date = row.get("game_date", "").strip()
            if not game_date:
                continue
            try:
                match_date = datetime.strptime(game_date, "%Y-%m-%d").date()
            except ValueError:
                continue

            home_id = row.get("home_team", "").strip()
            away_id = row.get("away_team", "").strip()
            if not home_id or not away_id:
                continue

            kickoff = row.get("gametime", "").strip() or "19:00"
            day_name = row.get("weekday", "").strip() or match_date.strftime("%A")

            slot = Slot(date=match_date, kickoff=kickoff, day_of_week=day_name)
            fixture = Fixture(
                fixture_id=f"NBA_H{counter:04d}",
                home_team_id=home_id,
                away_team_id=away_id,
            )
            fixtures.append(ScheduledFixture(fixture=fixture, slot=slot))
            counter += 1

    return Schedule(season=label, fixtures=fixtures)
