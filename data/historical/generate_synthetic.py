"""
Generates synthetic-but-realistic historical EPL fixture CSVs for 10 seasons.
Uses actual team rosters per season and realistic slot distributions derived
from published EPL scheduling patterns.

Output format matches football-data.co.uk so the same loader handles both.

Run:  python data/historical/generate_synthetic.py
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Actual EPL squads per season (20 teams each, real promotion/relegation)
# ---------------------------------------------------------------------------
SQUADS = {
    "2015-16": [
        "Arsenal","Aston Villa","Bournemouth","Chelsea","Crystal Palace",
        "Everton","Leicester","Liverpool","Man City","Man United",
        "Newcastle","Norwich","Southampton","Stoke","Sunderland",
        "Swansea","Tottenham","Watford","West Brom","West Ham",
    ],
    "2016-17": [
        "Arsenal","Bournemouth","Burnley","Chelsea","Crystal Palace",
        "Everton","Hull","Leicester","Liverpool","Man City",
        "Man United","Middlesbrough","Southampton","Stoke","Sunderland",
        "Swansea","Tottenham","Watford","West Brom","West Ham",
    ],
    "2017-18": [
        "Arsenal","Bournemouth","Brighton","Burnley","Chelsea",
        "Crystal Palace","Everton","Huddersfield","Leicester","Liverpool",
        "Man City","Man United","Newcastle","Southampton","Stoke",
        "Swansea","Tottenham","Watford","West Brom","West Ham",
    ],
    "2018-19": [
        "Arsenal","Bournemouth","Brighton","Burnley","Cardiff",
        "Chelsea","Crystal Palace","Everton","Fulham","Huddersfield",
        "Leicester","Liverpool","Man City","Man United","Newcastle",
        "Southampton","Tottenham","Watford","West Ham","Wolves",
    ],
    "2019-20": [
        "Arsenal","Aston Villa","Bournemouth","Brighton","Burnley",
        "Chelsea","Crystal Palace","Everton","Leicester","Liverpool",
        "Man City","Man United","Newcastle","Norwich","Sheffield United",
        "Southampton","Tottenham","Watford","West Ham","Wolves",
    ],
    "2020-21": [
        "Arsenal","Aston Villa","Brighton","Burnley","Chelsea",
        "Crystal Palace","Everton","Fulham","Leeds","Leicester",
        "Liverpool","Man City","Man United","Newcastle","Sheffield United",
        "Southampton","Tottenham","West Brom","West Ham","Wolves",
    ],
    "2021-22": [
        "Arsenal","Aston Villa","Brentford","Brighton","Burnley",
        "Chelsea","Crystal Palace","Everton","Leeds","Leicester",
        "Liverpool","Man City","Man United","Newcastle","Norwich",
        "Southampton","Tottenham","Watford","West Ham","Wolves",
    ],
    "2022-23": [
        "Arsenal","Aston Villa","Bournemouth","Brentford","Brighton",
        "Chelsea","Crystal Palace","Everton","Fulham","Leeds",
        "Leicester","Liverpool","Man City","Man United","Newcastle",
        "Nottm Forest","Southampton","Tottenham","West Ham","Wolves",
    ],
    "2023-24": [
        "Arsenal","Aston Villa","Bournemouth","Brentford","Brighton",
        "Burnley","Chelsea","Crystal Palace","Everton","Fulham",
        "Liverpool","Luton","Man City","Man United","Newcastle",
        "Nottm Forest","Sheffield United","Tottenham","West Ham","Wolves",
    ],
    "2024-25": [
        "Arsenal","Aston Villa","Bournemouth","Brentford","Brighton",
        "Chelsea","Crystal Palace","Everton","Fulham","Ipswich",
        "Leicester","Liverpool","Man City","Man United","Newcastle",
        "Nottm Forest","Southampton","Tottenham","West Ham","Wolves",
    ],
}

# Season windows (roughly Aug–May, skipping Dec25, Jan1 edge)
SEASON_WINDOWS = {
    "2015-16": ("2015-08-08", "2016-05-17"),
    "2016-17": ("2016-08-13", "2017-05-21"),
    "2017-18": ("2017-08-11", "2018-05-13"),
    "2018-19": ("2018-08-10", "2019-05-12"),
    "2019-20": ("2019-08-09", "2020-07-26"),   # COVID finish
    "2020-21": ("2020-09-12", "2021-05-23"),   # COVID bubble start
    "2021-22": ("2021-08-13", "2022-05-22"),
    "2022-23": ("2022-08-05", "2023-05-28"),
    "2023-24": ("2023-08-11", "2024-05-19"),
    "2024-25": ("2024-08-16", "2025-05-25"),
}

# International break windows (approximate, for blocking)
INTL_BREAKS = [
    (9, 1,  9, 14),   # early September
    (10, 7, 10, 21),  # October
    (11, 11,11,19),   # November
    (3, 20, 3, 31),   # March
]

# Slot distribution (day, time, weight) — mirrors real EPL broadcast mix
SLOTS = [
    ("Saturday",  "12:30", 10),
    ("Saturday",  "15:00", 50),
    ("Saturday",  "17:30", 10),
    ("Sunday",    "14:00", 20),
    ("Sunday",    "16:30", 20),
    ("Monday",    "20:00",  6),
    ("Tuesday",   "19:45",  6),
    ("Wednesday", "19:45",  6),
    ("Friday",    "20:00",  3),
]
SLOT_DAYS    = [s[0] for s in SLOTS]
SLOT_TIMES   = [s[1] for s in SLOTS]
SLOT_WEIGHTS = [s[2] for s in SLOTS]

DAY_OFFSETS = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
    "Friday": 4, "Saturday": 5, "Sunday": 6,
}


# ---------------------------------------------------------------------------
# Round-robin pairing (circle method)
# ---------------------------------------------------------------------------

def _circle_rounds(teams):
    teams = list(teams)
    if len(teams) % 2:
        teams.append("BYE")
    n = len(teams)
    fixed, rotating = teams[0], teams[1:]
    rounds = []
    for _ in range(n - 1):
        pairs = [(fixed, rotating[0])] + [
            (rotating[-(i)], rotating[i]) for i in range(1, n // 2)
        ]
        rounds.append(pairs)
        rotating = [rotating[-1]] + rotating[:-1]
    return rounds


def generate_fixtures(teams):
    """Returns list of (home, away) for a double round-robin."""
    r1 = _circle_rounds(teams)
    r2 = [(b, a) for a, b in (p for rnd in r1 for p in rnd)]
    first  = [(a, b) for rnd in r1 for a, b in rnd if "BYE" not in (a, b)]
    second = [(a, b) for a, b in r2 if "BYE" not in (a, b)]
    return first + second


# ---------------------------------------------------------------------------
# Date generation
# ---------------------------------------------------------------------------

def _in_intl_break(d: date) -> bool:
    for (sm, sd, em, ed) in INTL_BREAKS:
        start = date(d.year, sm, sd)
        end   = date(d.year, em, ed)
        if start <= d <= end:
            return True
    return False


def _next_valid_date(current: date, season_end: date) -> date:
    """Advance to next non-blocked date."""
    d = current
    while d <= season_end:
        if not _in_intl_break(d) and d.month != 12 or d.day not in (24, 25):
            return d
        d += timedelta(days=1)
    return season_end


def assign_dates(num_fixtures: int, season_start: date, season_end: date) -> list[tuple[date, str, str]]:
    """
    Distributes fixtures across the season window in ~38 roughly weekly
    matchday clusters, each cluster spanning 3-4 days.
    Returns list of (date, day_name, kickoff).
    """
    total_days = (season_end - season_start).days
    # Space 38 matchdays across the window
    matchday_starts = [
        season_start + timedelta(days=int(i * total_days / 38))
        for i in range(38)
    ]

    fixtures_per_md = num_fixtures // 38
    remainder       = num_fixtures - fixtures_per_md * 38

    result: list[tuple[date, str, str]] = []

    for md_idx, md_start in enumerate(matchday_starts):
        count = fixtures_per_md + (1 if md_idx < remainder else 0)
        used  = set()
        for _ in range(count):
            # Pick a slot type
            idx       = random.choices(range(len(SLOTS)), weights=SLOT_WEIGHTS)[0]
            day_name  = SLOT_DAYS[idx]
            kickoff   = SLOT_TIMES[idx]
            # Find nearest occurrence of that weekday from md_start
            base      = md_start
            target_wd = list(DAY_OFFSETS.keys()).index(day_name)   # Mon=0
            base_wd   = base.weekday()                              # Mon=0
            delta     = (target_wd - base_wd) % 7
            candidate = base + timedelta(days=delta)
            # Avoid duplicates on same (date, kickoff)
            attempts = 0
            while (str(candidate), kickoff) in used and attempts < 10:
                candidate += timedelta(days=1)
                attempts  += 1
            candidate = _next_valid_date(candidate, season_end)
            used.add((str(candidate), kickoff))
            result.append((candidate, day_name, kickoff))

    random.shuffle(result)
    return result


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def write_csv(season: str, rows: list[dict]) -> None:
    path = OUT_DIR / f"{season}.csv"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Div","Date","Time","HomeTeam","AwayTeam","FTHG","FTAG","FTR"])
        writer.writeheader()
        for row in sorted(rows, key=lambda r: r["Date"]):
            writer.writerow(row)
    print(f"  wrote {len(rows)} fixtures → {path.name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_season(season: str) -> None:
    teams  = SQUADS[season]
    start  = date.fromisoformat(SEASON_WINDOWS[season][0])
    end    = date.fromisoformat(SEASON_WINDOWS[season][1])
    pairs  = generate_fixtures(teams)
    slots  = assign_dates(len(pairs), start, end)

    rows = []
    for (home, away), (d, day, ko) in zip(pairs, slots):
        hg = random.randint(0, 4)
        ag = random.randint(0, 3)
        ftr = "H" if hg > ag else ("A" if ag > hg else "D")
        rows.append({
            "Div":      "E0",
            "Date":     d.strftime("%d/%m/%Y"),
            "Time":     ko,
            "HomeTeam": home,
            "AwayTeam": away,
            "FTHG":     hg,
            "FTAG":     ag,
            "FTR":      ftr,
        })

    write_csv(season, rows)


if __name__ == "__main__":
    random.seed(42)   # reproducible
    print("Generating synthetic EPL season data (10 seasons) ...")
    for season in sorted(SQUADS.keys()):
        print(f"  {season} ({len(SQUADS[season])} teams) ...", end=" ")
        generate_season(season)
    print("\nDone. Replace with real data by running download_seasons.py.")
