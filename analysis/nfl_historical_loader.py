"""
Loads real NFL game data from nflverse per-season CSVs and converts
them into Schedule objects compatible with the existing metrics engine.

CSV format (nflverse games.csv, REG games only):
    game_id, season, week, gameday (YYYY-MM-DD), weekday, gametime (HH:MM ET),
    away_team, away_score, home_team, home_score, div_game, away_rest, home_rest

Download real data via:
    python data/leagues/nfl/historical/download_seasons.py
"""
import csv
from datetime import date, datetime
from pathlib import Path

from core.models import Fixture, Slot, ScheduledFixture, Schedule

HISTORICAL_DIR = Path(__file__).parent.parent / "data" / "leagues" / "nfl" / "historical"


def available_seasons() -> list[str]:
    return sorted(
        p.stem for p in HISTORICAL_DIR.glob("*.csv")
        if p.stem.isdigit() and p.stem not in {"download_seasons"}
    )


def load_season(csv_path: Path | str, season_label: str | None = None) -> Schedule:
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
            day_name = row.get("weekday", match_date.strftime("%A")).strip()

            slot = Slot(date=match_date, kickoff=kickoff, day_of_week=day_name)
            fixture = Fixture(
                fixture_id=f"NFL_H{counter:04d}",
                home_team_id=home_id,
                away_team_id=away_id,
            )
            fixtures.append(ScheduledFixture(fixture=fixture, slot=slot))
            counter += 1

    return Schedule(fixtures=fixtures, label=label)


def load_all_seasons(seasons: list[str] | None = None) -> list[dict]:
    """
    Returns a list of metric summary dicts, one per season, in chronological
    order. Each dict has keys used by the cross-season analysis:

        season_label, total_games, rest_mean, rest_min_global,
        thursday_pct, sunday_pct, monday_pct, saturday_pct,
        primetime_pct, div_game_pct
    """
    target = seasons or available_seasons()
    results = []
    for s in target:
        path = HISTORICAL_DIR / f"{s}.csv"
        if not path.exists():
            continue
        rows = _read_rows(path)
        results.append(_compute_metrics(rows, s))
    return results


def _read_rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("game_type", "REG") == "REG"]


def _compute_metrics(rows: list[dict], label: str) -> dict:
    n = len(rows)
    if n == 0:
        return {"season_label": label}

    day_counts: dict[str, int] = {}
    rest_values: list[int] = []
    primetime = 0
    div_games = 0

    primetime_slots = {"20:20", "20:15", "20:00", "21:15"}

    for row in rows:
        weekday = row.get("weekday", "").strip()
        day_counts[weekday] = day_counts.get(weekday, 0) + 1

        gt = row.get("gametime", "").strip()
        if gt in primetime_slots:
            primetime += 1

        dg = row.get("div_game", "0").strip()
        if dg == "1":
            div_games += 1

        for key in ("away_rest", "home_rest"):
            v = row.get(key, "").strip()
            if v and v not in ("NA", ""):
                try:
                    rest_values.append(int(float(v)))
                except ValueError:
                    pass

    total = n * 2  # participations (home + away per game)

    return {
        "season_label": label,
        "total_games": n,
        "rest_mean": round(sum(rest_values) / len(rest_values), 1) if rest_values else None,
        "rest_min_global": min(rest_values) if rest_values else None,
        "thursday_pct": round(day_counts.get("Thursday", 0) / n * 100, 1),
        "sunday_pct": round(day_counts.get("Sunday", 0) / n * 100, 1),
        "monday_pct": round(day_counts.get("Monday", 0) / n * 100, 1),
        "saturday_pct": round(day_counts.get("Saturday", 0) / n * 100, 1),
        "primetime_pct": round(primetime / n * 100, 1),
        "div_game_pct": round(div_games / n * 100, 1),
        "day_distribution": day_counts,
    }
