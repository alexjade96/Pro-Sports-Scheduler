"""
Downloads NBA game data from the NBA Stats API and splits it into
per-season CSV files covering the regular season (2015-16 to 2024-25).

Source:
    https://stats.nba.com/stats/leaguegamelog
    Endpoint returns team-level game logs; we pivot to home/away game rows.

Column subset written:
    game_id, season, season_label, game_type, game_date, weekday, gametime,
    away_team, away_score, home_team, home_score, result, overtime,
    home_rest, away_rest, arena

Usage:
    python data/leagues/nba/historical/download_seasons.py [--seasons 2015-2024]

Season argument uses the *start year* of each season:
    2015 → 2015-16, 2023 → 2023-24, 2024 → 2024-25
"""
import csv
import json
import sys
import time
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta

OUT_DIR = Path(__file__).parent
DEFAULT_SEASONS = range(2015, 2025)   # 2015-16 through 2024-25

_NBA_API_BASE = "https://stats.nba.com/stats/leaguegamelog"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer":           "https://www.nba.com/",
    "Origin":            "https://www.nba.com",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Accept":            "application/json, text/plain, */*",
    "Accept-Language":   "en-US,en;q=0.9",
    "Connection":        "keep-alive",
}

_TEAM_ABBREV_REMAP = {
    "NJN": "BKN",   # New Jersey Nets → Brooklyn
    "NOH": "NOP",   # New Orleans Hornets → Pelicans
    "NOK": "NOP",
    "SEA": "OKC",   # Seattle SuperSonics → OKC
    "VAN": "MEM",   # Vancouver Grizzlies → Memphis
    "WSB": "WAS",   # Washington Bullets → Wizards
    "CHA": "CHA",   # Charlotte Bobcats/Hornets (kept as CHA)
    "CHH": "CHA",
}


def _remap(abbr: str) -> str:
    return _TEAM_ABBREV_REMAP.get(abbr, abbr)


def _season_str(start_year: int) -> str:
    """2015 → '2015-16', 2024 → '2024-25'."""
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def _fetch_json(url: str, params: dict, retries: int = 4) -> dict:
    import subprocess, tempfile, os
    query = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    full_url = f"{url}?{query}"
    header_args = []
    for k, v in _HEADERS.items():
        header_args += ["-H", f"{k}: {v}"]
    delay = 2
    for attempt in range(retries):
        try:
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
                tmp_path = tmp.name
            result = subprocess.run(
                [
                    "curl", "-s", "-L", "--max-time", "60",
                    *header_args,
                    "-o", tmp_path, full_url,
                ],
                capture_output=True, timeout=70,
            )
            if result.returncode != 0:
                raise RuntimeError(f"curl exit {result.returncode}: {result.stderr.decode()[:200]}")
            with open(tmp_path) as f:
                data = json.load(f)
            os.unlink(tmp_path)
            return data
        except Exception as exc:
            if attempt == retries - 1:
                raise
            print(f"  [attempt {attempt+1}] {exc!r} — retrying in {delay}s")
            time.sleep(delay)
            delay *= 2


def _pivot_to_games(rows: list[dict]) -> list[dict]:
    """
    NBA API returns one row per team per game. Convert to one row per game
    (home vs away). Rows with 'vs.' in MATCHUP are home games.
    """
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
            weekday = dt.strftime("%A")
            game_date = dt.strftime("%Y-%m-%d")
        except ValueError:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                weekday = dt.strftime("%A")
                game_date = date_str
            except ValueError:
                weekday = ""
                game_date = date_str

        home_pts = home.get("PTS") or 0
        away_pts = away.get("PTS") or 0
        ot = 1 if home.get("MIN", 0) and float(str(home.get("MIN", 240)).split(":")[0]) > 240 else 0

        games.append({
            "game_id":    gid,
            "season":     home.get("SEASON_ID", "")[-5:] if home.get("SEASON_ID") else "",
            "game_type":  "REG",
            "game_date":  game_date,
            "weekday":    weekday,
            "gametime":   "",
            "away_team":  _remap(away.get("TEAM_ABBREVIATION", "")),
            "away_score": int(away_pts),
            "home_team":  _remap(home.get("TEAM_ABBREVIATION", "")),
            "home_score": int(home_pts),
            "result":     int(home_pts) - int(away_pts),
            "overtime":   ot,
            "arena":      home.get("GAME_DATE", ""),
        })
    return games


def fetch_season(start_year: int) -> list[dict]:
    """Fetch all regular-season games for a given season start year."""
    import urllib.parse  # needed for _fetch_json
    season = _season_str(start_year)
    print(f"  Fetching {season} …", end=" ", flush=True)
    params = {
        "Counter":      "0",
        "DateFrom":     "",
        "DateTo":       "",
        "Direction":    "ASC",
        "LeagueID":     "00",
        "PlayerOrTeam": "T",
        "Season":       season,
        "SeasonType":   "Regular Season",
        "Sorter":       "DATE",
    }
    data = _fetch_json(_NBA_API_BASE, params)

    result_set = data.get("resultSets", [{}])[0]
    headers = result_set.get("headers", [])
    rows_raw = result_set.get("rowSet", [])
    rows = [dict(zip(headers, r)) for r in rows_raw]
    print(f"{len(rows)} team-game rows")
    return rows


OUT_COLS = [
    "game_id", "season", "game_type", "game_date", "weekday", "gametime",
    "away_team", "away_score", "home_team", "home_score",
    "result", "overtime", "arena",
]


def save_season(start_year: int, games: list[dict], out_dir: Path) -> None:
    path = out_dir / f"{start_year}.csv"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_COLS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(games)
    print(f"  → {path.name}  ({len(games)} games)")


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
    import urllib.parse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seasons", default="2015-2024",
        help="Start-year range or comma list, e.g. '2015-2024' or '2022,2023,2024'",
    )
    args = parser.parse_args()

    seasons = parse_seasons_arg(args.seasons)
    print(f"Downloading NBA game logs for {len(seasons)} seasons from stats.nba.com …")

    for year in sorted(seasons):
        try:
            rows = fetch_season(year)
            games = _pivot_to_games(rows)
            save_season(year, games, OUT_DIR)
            time.sleep(1.5)   # be polite to the API
        except Exception as e:
            print(f"  ERROR for {_season_str(year)}: {e}")

    print("Done.")
