"""
Downloads NFL game data from the nflverse open-data project (GitHub raw)
and splits it into per-season CSV files.

Source:
    https://github.com/nflverse/nfldata
    File: data/games.csv  (all seasons from 1999, updated weekly in-season)

Column subset kept (full nflverse schema preserved):
    game_id, season, game_type, week, gameday, weekday, gametime,
    away_team, away_score, home_team, home_score, location, result,
    overtime, div_game, away_rest, home_rest, away_coach, home_coach,
    referee, stadium_id, stadium

Usage:
    python data/leagues/nfl/historical/download_seasons.py [--seasons 2016-2025]
"""
import csv
import sys
import time
import urllib.request
from pathlib import Path

NFLVERSE_URL = (
    "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
)
OUT_DIR = Path(__file__).parent
DEFAULT_SEASONS = range(2016, 2026)   # 2016-2025 inclusive

# nflverse uses "LA" for the Los Angeles Rams; our teams.json uses "LAR".
_TEAM_REMAP = {"LA": "LAR"}


def _remap(team_id: str) -> str:
    return _TEAM_REMAP.get(team_id, team_id)


KEEP_COLS = {
    "game_id", "season", "game_type", "week", "gameday", "weekday",
    "gametime", "away_team", "away_score", "home_team", "home_score",
    "location", "result", "overtime", "div_game",
    "away_rest", "home_rest", "away_coach", "home_coach",
    "referee", "stadium_id", "stadium",
}


def download(url: str, retries: int = 4) -> list[dict]:
    import subprocess, tempfile, os
    delay = 2
    for attempt in range(retries):
        try:
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
                tmp_path = tmp.name
            result = subprocess.run(
                [
                    "curl", "-s", "-L", "--max-time", "120",
                    "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                    "-o", tmp_path, url,
                ],
                capture_output=True, timeout=130,
            )
            if result.returncode != 0:
                raise RuntimeError(f"curl exit {result.returncode}: {result.stderr.decode()}")
            with open(tmp_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            os.unlink(tmp_path)
            return rows
        except Exception as exc:
            if attempt == retries - 1:
                raise
            print(f"  [attempt {attempt+1}] {exc} — retrying in {delay}s")
            time.sleep(delay)
            delay *= 2


def split_and_save(rows: list[dict], seasons, out_dir: Path) -> None:
    season_rows: dict[int, list[dict]] = {}
    for row in rows:
        try:
            s = int(row["season"])
        except (KeyError, ValueError):
            continue
        if s not in seasons:
            continue
        if row.get("game_type") != "REG":
            continue
        filtered = {k: v for k, v in row.items() if k in KEEP_COLS}
        if "home_team" in filtered:
            filtered["home_team"] = _remap(filtered["home_team"])
        if "away_team" in filtered:
            filtered["away_team"] = _remap(filtered["away_team"])
        season_rows.setdefault(s, []).append(filtered)

    out_cols = [c for c in [
        "game_id", "season", "game_type", "week", "gameday", "weekday",
        "gametime", "away_team", "away_score", "home_team", "home_score",
        "location", "result", "overtime", "div_game",
        "away_rest", "home_rest", "away_coach", "home_coach",
        "referee", "stadium_id", "stadium",
    ] if c in KEEP_COLS]

    for s, srows in sorted(season_rows.items()):
        path = out_dir / f"{s}.csv"
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=out_cols, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(srows)
        print(f"  → {path.name}  ({len(srows)} games)")


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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--seasons", default="2016-2025",
                        help="Season range or comma list, e.g. '2016-2025' or '2023,2024,2025'")
    args = parser.parse_args()

    seasons = parse_seasons_arg(args.seasons)

    print(f"Downloading nflverse games.csv from GitHub …")
    rows = download(NFLVERSE_URL)
    print(f"  {len(rows)} total rows fetched")

    print(f"Splitting into per-season CSVs for seasons {sorted(seasons)} …")
    split_and_save(rows, seasons, OUT_DIR)
    print("Done.")
