"""
Downloads EPL fixture CSVs from football-data.co.uk for the specified seasons.
Run this script when you have open internet access to replace the synthetic data
with real historical fixtures.

Usage:
    python data/historical/download_seasons.py

Source: https://www.football-data.co.uk/englandm.php
Format:  E0 division, columns include Date, Time, HomeTeam, AwayTeam, FTHG, FTAG, FTR
"""
import urllib.request
import time
from pathlib import Path

SEASONS = {
    "2015-16": "1516",
    "2016-17": "1617",
    "2017-18": "1718",
    "2018-19": "1819",
    "2019-20": "1920",
    "2020-21": "2021",
    "2021-22": "2122",
    "2022-23": "2223",
    "2023-24": "2324",
    "2024-25": "2425",
}

BASE_URL = "https://www.football-data.co.uk/mmz4281/{code}/E0.csv"
OUT_DIR  = Path(__file__).parent


def download(season_label: str, code: str) -> None:
    url  = BASE_URL.format(code=code)
    dest = OUT_DIR / f"{season_label}.csv"
    print(f"Downloading {season_label} from {url} ...")
    try:
        urllib.request.urlretrieve(url, dest)
        size = dest.stat().st_size
        print(f"  -> saved {dest.name} ({size:,} bytes)")
    except Exception as e:
        print(f"  -> FAILED: {e}")


if __name__ == "__main__":
    for label, code in SEASONS.items():
        download(label, code)
        time.sleep(1)   # be polite to the server
    print("\nDone. Re-run analysis/cross_season.py to rebuild the report.")
