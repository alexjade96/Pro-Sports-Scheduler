"""
Generates synthetic-but-realistic NBA schedule CSVs for 10 seasons.

Uses the NBA fixture generator for game pairings and distributes games
across realistic NBA season dates. Output format matches download_seasons.py
so the same analysis loader handles both.

Run:  python data/leagues/nba/historical/generate_synthetic.py
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Season windows and roster notes
# ---------------------------------------------------------------------------
# NBA rosters are stable — all 30 current franchises have existed since 2002.
# We use the current 30-team set for all 10 seasons for simplicity.
SEASON_WINDOWS = {
    2015: ("2015-10-27", "2016-04-13"),   # 2015-16
    2016: ("2016-10-25", "2017-04-12"),   # 2016-17
    2017: ("2017-10-17", "2018-04-11"),   # 2017-18
    2018: ("2018-10-16", "2019-04-10"),   # 2018-19
    2019: ("2019-10-22", "2020-03-11"),   # 2019-20 (COVID-shortened)
    2020: ("2020-12-22", "2021-05-16"),   # 2020-21 (COVID-delayed)
    2021: ("2021-10-19", "2022-04-10"),   # 2021-22
    2022: ("2022-10-18", "2023-04-09"),   # 2022-23
    2023: ("2023-10-24", "2024-04-14"),   # 2023-24
    2024: ("2024-10-22", "2025-04-13"),   # 2024-25
}

# All-Star break windows (approximate) — no regular season games
ALLSTAR_BREAKS = {
    2015: ("2016-02-12", "2016-02-22"),
    2016: ("2017-02-17", "2017-02-27"),
    2017: ("2018-02-16", "2018-02-22"),
    2018: ("2019-02-15", "2019-02-21"),
    2019: ("2020-02-14", "2020-02-20"),
    2020: ("2021-03-05", "2021-03-10"),
    2021: ("2022-02-18", "2022-02-24"),
    2022: ("2023-02-17", "2023-02-23"),
    2023: ("2024-02-16", "2024-02-22"),
    2024: ("2025-02-14", "2025-02-24"),
}

# Slot distribution: (day_of_week, time, relative_weight)
# NBA plays every night of the week; peak days are Fri/Sat/Sun
SLOTS = [
    ("Monday",    "19:00", 6),
    ("Monday",    "22:00", 4),
    ("Tuesday",   "19:00", 8),
    ("Tuesday",   "19:30", 4),
    ("Tuesday",   "22:00", 4),
    ("Wednesday", "19:00", 7),
    ("Wednesday", "19:30", 4),
    ("Wednesday", "22:00", 4),
    ("Thursday",  "19:00", 5),
    ("Thursday",  "22:00", 3),
    ("Friday",    "19:00", 7),
    ("Friday",    "19:30", 4),
    ("Friday",    "22:00", 5),
    ("Saturday",  "19:00", 6),
    ("Saturday",  "19:30", 5),
    ("Saturday",  "22:00", 5),
    ("Saturday",  "22:30", 3),
    ("Sunday",    "15:00", 5),
    ("Sunday",    "17:00", 4),
    ("Sunday",    "19:30", 5),
]
_SLOT_WEIGHTS = [s[2] for s in SLOTS]

OUT_COLS = [
    "game_id", "season", "game_type", "game_date", "weekday", "gametime",
    "away_team", "away_score", "home_team", "home_score",
    "result", "overtime", "arena",
]

_TEAM_IDS = [
    "ATL","BOS","BKN","CHA","CHI","CLE","DAL","DEN","DET","GSW",
    "HOU","IND","LAC","LAL","MEM","MIA","MIL","MIN","NOP","NYK",
    "OKC","ORL","PHI","PHX","POR","SAC","SAS","TOR","UTA","WAS",
]


def _in_allstar(d: date, year: int) -> bool:
    lo, hi = ALLSTAR_BREAKS[year]
    return date.fromisoformat(lo) <= d <= date.fromisoformat(hi)


def _generate_pairs(teams: list[str]) -> list[tuple[str, str]]:
    """
    NBA game distribution per team:
      Division rivals × 4 (2H+2A)    = 16 games
      Conf non-div × 3 or 4          = 36 games
      Inter-conf × 2 (1H+1A)         = 30 games
    Total = 82 games

    We approximate with the fixture generator; for synthetic data we just
    need correct pair counts without exact rotation details.
    """
    import sys as _sys
    _root = str(Path(__file__).parent.parent.parent.parent.parent)
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    from generators.leagues.nba.generate_nba import generate_fixtures
    from core.data_loader import set_league, load_teams as _lt
    set_league("nba")
    team_objs = _lt()
    fixtures = generate_fixtures(team_objs)
    return [(f.home_team_id, f.away_team_id) for f in fixtures]


def _distribute_dates(
    n: int,
    start: date,
    end: date,
    allstar_lo: date,
    allstar_hi: date,
    rng: random.Random,
) -> list[tuple[date, str, str]]:
    """
    Spread n games across the season window, skipping All-Star break.
    Returns list of (game_date, weekday, gametime).
    """
    total_days = (end - start).days + 1
    valid_dates = []
    d = start
    while d <= end:
        if not (allstar_lo <= d <= allstar_hi):
            valid_dates.append(d)
        d += timedelta(days=1)

    # Pick n dates with replacement (multiple games per day is expected in NBA)
    game_dates = sorted(rng.choices(valid_dates, k=n))

    result = []
    for gd in game_dates:
        day_name = gd.strftime("%A")
        # Pick a time slot consistent with the day
        day_slots = [(s[1], s[2]) for s in SLOTS if s[0] == day_name]
        if not day_slots:
            day_slots = [("19:00", 1)]
        times, weights = zip(*day_slots)
        gametime = rng.choices(list(times), weights=list(weights))[0]
        result.append((gd, day_name, gametime))
    return result


def generate_season(start_year: int, rng: random.Random) -> None:
    s_start, s_end = SEASON_WINDOWS[start_year]
    as_lo, as_hi = ALLSTAR_BREAKS[start_year]
    season_label = f"{start_year}-{str(start_year + 1)[-2:]}"

    pairs = _generate_pairs(_TEAM_IDS)
    dates_info = _distribute_dates(
        len(pairs),
        date.fromisoformat(s_start),
        date.fromisoformat(s_end),
        date.fromisoformat(as_lo),
        date.fromisoformat(as_hi),
        rng,
    )

    rows = []
    for i, ((home, away), (gd, weekday, gametime)) in enumerate(zip(pairs, dates_info)):
        home_pts = rng.randint(95, 130)
        away_pts = rng.randint(95, 130)
        ot = 1 if abs(home_pts - away_pts) <= 3 and rng.random() < 0.1 else 0
        rows.append({
            "game_id":    f"{start_year}_{i+1:04d}_{home}_{away}",
            "season":     season_label,
            "game_type":  "REG",
            "game_date":  gd.strftime("%Y-%m-%d"),
            "weekday":    weekday,
            "gametime":   gametime,
            "away_team":  away,
            "away_score": away_pts,
            "home_team":  home,
            "home_score": home_pts,
            "result":     home_pts - away_pts,
            "overtime":   ot,
            "arena":      "",
        })

    rows.sort(key=lambda r: r["game_date"])
    path = OUT_DIR / f"{start_year}.csv"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_COLS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"  {start_year} ({season_label}): {len(rows)} games → {path.name}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))
    rng = random.Random(42)
    print("Generating synthetic NBA schedule data (10 seasons) ...")
    for year in sorted(SEASON_WINDOWS):
        generate_season(year, rng)
    print("\nDone. Replace with real data by running download_seasons.py when stats.nba.com is accessible.")
