"""
Downloads real NBA game data and splits it into per-season CSV files
covering the regular season (2015-16 onward).

Source: NocturneBear/NBA-Data-2010-2024 (MIT licensed), which itself is
sourced from the NBA Stats API — used here instead of querying
stats.nba.com directly because that endpoint is unreachable from this
project's sandboxed network environment. The dataset covers 2010-11
through 2023-24; regenerate against a fresher mirror (or stats.nba.com
directly, via the commented-out fetch_season_live() path below) once a
2024-25+ source becomes reachable.

    https://github.com/NocturneBear/NBA-Data-2010-2024
    regular_season_totals_2010_2024.csv — one row per team per game
    (TEAM_ABBREVIATION, GAME_ID, GAME_DATE, MATCHUP "TEAM vs. OPP" / "TEAM @ OPP",
    WL, PTS, MIN, ...). Pivoted here into one row per game (home vs away),
    same shape as football-data-style historical CSVs used elsewhere in
    this project.

Column subset written (matches analysis/leagues/nba/historical.py):
    game_id, season, game_type, game_date, weekday, gametime,
    away_team, away_score, home_team, home_score, result, overtime, arena

Usage:
    python data/leagues/nba/historical/download_seasons.py [--seasons 2015-2023]

Season argument uses the *start year* of each season:
    2015 → 2015-16, 2023 → 2023-24
"""
import csv
import sys
import urllib.request
from pathlib import Path
from datetime import datetime

OUT_DIR = Path(__file__).parent
DEFAULT_SEASONS = range(2015, 2024)   # 2015-16 through 2023-24 (source's latest complete season)

_SOURCE_URL = (
    "https://raw.githubusercontent.com/NocturneBear/NBA-Data-2010-2024"
    "/main/regular_season_totals_2010_2024.csv"
)

OUT_COLS = [
    "game_id", "season", "game_type", "game_date", "weekday", "gametime",
    "away_team", "away_score", "home_team", "home_score",
    "result", "overtime", "arena",
]


def _season_str(start_year: int) -> str:
    """2015 → '2015-16', 2023 → '2023-24'."""
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def fetch_source_csv(cache_path: Path | None = None) -> Path:
    """Downloads the source CSV once (~9MB) and caches it locally."""
    cache_path = cache_path or (OUT_DIR / "_source_totals_2010_2024.csv")
    if cache_path.exists():
        return cache_path
    print(f"Downloading source dataset from {_SOURCE_URL} …")
    urllib.request.urlretrieve(_SOURCE_URL, cache_path)
    print(f"  → cached at {cache_path.name} ({cache_path.stat().st_size // 1024} KB)")
    return cache_path


def _pivot_to_games(rows: list[dict]) -> list[dict]:
    """One row per team per game -> one row per game (home vs away)."""
    home_rows: dict[str, dict] = {}
    away_rows: dict[str, dict] = {}

    for row in rows:
        gid = row["GAME_ID"]
        matchup = row.get("MATCHUP", "")
        if "vs." in matchup:
            home_rows[gid] = row
        elif "@" in matchup:
            away_rows[gid] = row

    games = []
    for gid, home in home_rows.items():
        away = away_rows.get(gid)
        if not away:
            continue

        date_str = home.get("GAME_DATE", "")
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue
        weekday   = dt.strftime("%A")
        game_date = dt.strftime("%Y-%m-%d")

        home_pts = int(float(home.get("PTS") or 0))
        away_pts = int(float(away.get("PTS") or 0))

        def _is_ot(row: dict) -> int:
            try:
                return 1 if float(row.get("MIN") or 0) > 240 else 0
            except ValueError:
                return 0

        games.append({
            "game_id":    gid,
            "season":     home.get("SEASON_YEAR", ""),
            "game_type":  "REG",
            "game_date":  game_date,
            "weekday":    weekday,
            "gametime":   "",
            "away_team":  away.get("TEAM_ABBREVIATION", ""),
            "away_score": away_pts,
            "home_team":  home.get("TEAM_ABBREVIATION", ""),
            "home_score": home_pts,
            "result":     home_pts - away_pts,
            "overtime":   _is_ot(home) or _is_ot(away),
            "arena":      "",
        })
    return games


def save_season(start_year: int, games: list[dict], out_dir: Path) -> None:
    path = out_dir / f"{start_year}.csv"
    games_sorted = sorted(games, key=lambda g: (g["game_date"], g["game_id"]))
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_COLS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(games_sorted)
    print(f"  → {path.name}  ({len(games_sorted)} games)")


def parse_seasons_arg(arg: str) -> set[int]:
    result = set()
    for part in arg.split(","):
        part = part.strip()
        if "-" in part and not part.startswith("-"):
            lo, hi = part.split("-", 1)
            result.update(range(int(lo), int(hi) + 1))
        else:
            result.add(int(part))
    return result


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--seasons", default="2015-2023",
        help="Start-year range or comma list, e.g. '2015-2023' or '2021,2022,2023'",
    )
    args = parser.parse_args()

    seasons = parse_seasons_arg(args.seasons)
    source_path = fetch_source_csv()

    print(f"Splitting {len(seasons)} season(s) from {source_path.name} …")
    by_season: dict[str, list[dict]] = {}
    with open(source_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_season.setdefault(row["SEASON_YEAR"], []).append(row)

    for year in sorted(seasons):
        season_key = _season_str(year)
        rows = by_season.get(season_key)
        if not rows:
            print(f"  {season_key}: not present in source dataset, skipping")
            continue
        games = _pivot_to_games(rows)
        save_season(year, games, OUT_DIR)

    print("Done.")


if __name__ == "__main__":
    main()
