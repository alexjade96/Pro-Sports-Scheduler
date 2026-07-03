"""
Downloads real EPL fixtures for 10 seasons (2015-16 .. 2024-25) and writes
them as football-data.co.uk-format CSVs so the existing historical loader
(analysis/leagues/epl/historical.py) reads them unchanged.

Source: openfootball/football.json — a public-domain (Unlicense) dataset of
real match fixtures/results, mirrored on GitHub raw content. football-data.co.uk
itself (the originally-intended source, see the git history of this file) is
blocked by the sandbox proxy, so this reads from the reachable GitHub mirror
instead — the same pattern used for NBA's download_seasons.py. The dates,
kickoff times, and results are the real historical ones, not synthetic.

generate_synthetic.py is kept as a fallback generator in case this mirror ever
becomes unreachable; it is no longer what's committed under historical/.

Run:  python data/leagues/epl/historical/download_seasons.py
"""
import csv
import json
import urllib.request
from datetime import datetime
from pathlib import Path

OUT_DIR = Path(__file__).parent

_SOURCE_URL = (
    "https://raw.githubusercontent.com/openfootball/football.json/master/"
    "{season}/en.1.json"
)

# 2015-16 is the earliest season openfootball covers with full kickoff times.
SEASONS = [
    "2015-16", "2016-17", "2017-18", "2018-19", "2019-20",
    "2020-21", "2021-22", "2022-23", "2023-24", "2024-25",
]

OUT_COLS = ["Div", "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"]

# openfootball uses full club names whose "FC"/"AFC" suffixes and long forms
# drifted between seasons. Normalise every variant to the football-data.co.uk
# short name that team_name_map.json already keys on. Strip a trailing " FC"
# first, then map the remainder.
_FULLNAME_TO_SHORT = {
    "AFC Bournemouth":            "Bournemouth",
    "Arsenal":                    "Arsenal",
    "Aston Villa":                "Aston Villa",
    "Brentford":                  "Brentford",
    "Brighton & Hove Albion":     "Brighton",
    "Burnley":                    "Burnley",
    "Cardiff City":               "Cardiff",
    "Chelsea":                    "Chelsea",
    "Crystal Palace":             "Crystal Palace",
    "Everton":                    "Everton",
    "Fulham":                     "Fulham",
    "Huddersfield Town":          "Huddersfield",
    "Hull City":                  "Hull",
    "Ipswich Town":               "Ipswich",
    "Leeds United":               "Leeds",
    "Leicester City":             "Leicester",
    "Liverpool":                  "Liverpool",
    "Luton Town":                 "Luton",
    "Manchester City":            "Man City",
    "Manchester United":          "Man United",
    "Middlesbrough":              "Middlesbrough",
    "Newcastle United":           "Newcastle",
    "Norwich City":               "Norwich",
    "Nottingham Forest":          "Nott'm Forest",
    "Sheffield United":           "Sheffield United",
    "Southampton":                "Southampton",
    "Stoke City":                 "Stoke",
    "Sunderland AFC":             "Sunderland",
    "Sunderland":                 "Sunderland",
    "Swansea City":               "Swansea",
    "Tottenham Hotspur":          "Tottenham",
    "Watford":                    "Watford",
    "West Bromwich Albion":       "West Brom",
    "West Ham United":            "West Ham",
    "Wolverhampton Wanderers":    "Wolves",
}


def _short_name(full: str) -> str:
    name = full.strip()
    if name.endswith(" FC"):
        name = name[: -len(" FC")]
    if name in _FULLNAME_TO_SHORT:
        return _FULLNAME_TO_SHORT[name]
    # Retry with the original (handles "AFC Bournemouth" / "Sunderland AFC")
    if full.strip() in _FULLNAME_TO_SHORT:
        return _FULLNAME_TO_SHORT[full.strip()]
    raise KeyError(f"Unmapped openfootball club name: {full!r}")


def _result(ft: list) -> str:
    h, a = ft
    return "H" if h > a else "A" if a > h else "D"


def fetch_season(season: str) -> list[dict]:
    url = _SOURCE_URL.format(season=season)
    print(f"Fetching {season} from {url} ...")
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.loads(resp.read())
    rows = []
    for m in data["matches"]:
        iso = m["date"]                     # YYYY-MM-DD
        d = datetime.strptime(iso, "%Y-%m-%d").date()
        score = m.get("score") or {}
        ft = score.get("ft")
        rows.append({
            "Div":      "E0",
            "Date":     d.strftime("%d/%m/%Y"),   # football-data.co.uk DD/MM/YYYY
            "Time":     (m.get("time") or "15:00").strip() or "15:00",
            "HomeTeam": _short_name(m["team1"]),
            "AwayTeam": _short_name(m["team2"]),
            "FTHG":     ft[0] if ft else "",
            "FTAG":     ft[1] if ft else "",
            "FTR":      _result(ft) if ft else "",
            "_iso":     iso,                       # chronological sort key only
        })
    # Real football-data CSVs are in chronological match order.
    rows.sort(key=lambda r: (r["_iso"], r["Time"]))
    for r in rows:
        del r["_iso"]
    return rows


def write_csv(season: str, rows: list[dict]) -> None:
    dest = OUT_DIR / f"{season}.csv"
    with open(dest, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_COLS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"  -> {dest.name}: {len(rows)} fixtures")


if __name__ == "__main__":
    print("Downloading real EPL fixtures (openfootball mirror) ...")
    for season in SEASONS:
        rows = fetch_season(season)
        write_csv(season, rows)
    print("\nDone. Re-run analysis/cross_season.py to rebuild the report.")
