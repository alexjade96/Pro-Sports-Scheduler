"""
Calendar-style visual schedule output.

Renders the full season (or a single month) as a month-by-month grid where
each date cell shows the day's fixtures as HOME-AWY abbreviations.

Markers:
  ★  festive matchday (Boxing Day / Dec 28 / New Year's Day)
  ◆  high-profile derby

Usage:
    python tools/calendar_view.py
    python tools/calendar_view.py --csv output/schedule_ilp.csv
    python tools/calendar_view.py --month 12
    python tools/calendar_view.py --month 8 --year 2025
"""
from __future__ import annotations

import argparse
import calendar as _cal
import csv
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.data_loader import load_teams, load_high_profile_derbies
from core.models import Fixture, Slot, ScheduledFixture, Schedule

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CELL_W = 12          # characters per day column
DAY_ABBREVS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

FESTIVE = {date(2025, 12, 26), date(2025, 12, 28), date(2026, 1, 1)}

# ---------------------------------------------------------------------------
# CSV loader (same as sample_schedule.py)
# ---------------------------------------------------------------------------

def _load_csv(path: Path) -> Schedule:
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            slot = Slot(
                date=date.fromisoformat(row["date"]),
                kickoff=row["kickoff"],
                day_of_week=row["day"],
            )
            fixture = Fixture(
                fixture_id=row["fixture_id"],
                home_team_id=row["home"],
                away_team_id=row["away"],
            )
            rows.append(ScheduledFixture(fixture=fixture, slot=slot))
    return Schedule(season="2025-26", fixtures=rows)


# ---------------------------------------------------------------------------
# Season overview bar chart
# ---------------------------------------------------------------------------

def _print_overview(by_date: dict, season: str) -> None:
    by_month: dict[tuple, int] = defaultdict(int)
    for d, fixtures in by_date.items():
        by_month[(d.year, d.month)] += len(fixtures)

    w = CELL_W * 7 + 2
    print()
    print("=" * w)
    print(f"  EPL 2025/26 SEASON CALENDAR  [{season}]")
    print("=" * w)
    print()
    print("  FIXTURE DENSITY BY MONTH")
    print(f"  {'':10}", end="")
    for _, m in sorted(by_month):
        print(f" {_cal.month_abbr[m]:<4}", end="")
    print()
    print(f"  {'Fixtures':10}", end="")
    for _, v in sorted(by_month.items()):
        print(f" {v:<4}", end="")
    print()
    print()
    # Mini bar chart
    max_n = max(by_month.values(), default=1)
    bar_h = 5
    for row in range(bar_h, 0, -1):
        threshold = (row / bar_h) * max_n
        print(f"  {int(threshold):>3} │", end="")
        for _, n in sorted(by_month.items()):
            block = "███" if n >= threshold else "   "
            print(f" {block} ", end="")
        print()
    print(f"      └" + "─────" * len(by_month))
    print(f"       ", end="")
    for _, m in sorted(by_month):
        print(f" {_cal.month_abbr[m]:<4}", end="")
    print()


# ---------------------------------------------------------------------------
# Month calendar renderer
# ---------------------------------------------------------------------------

def _render_month(
    year: int,
    month: int,
    by_date: dict,
    derby_pairs: set,
) -> None:
    weeks = _cal.monthcalendar(year, month)
    month_name = _cal.month_name[month]

    total = sum(
        len(by_date.get(date(year, month, day), []))
        for week in weeks
        for day in week
        if day != 0
    )
    if total == 0:
        return

    W  = CELL_W
    TW = W * 7 + 2
    sep = "─" * TW

    print()
    print(f"  {month_name.upper()} {year}   ({total} fixtures)")
    print(f"  {sep}")
    print("  " + "".join(f"{d:<{W}}" for d in DAY_ABBREVS))
    print(f"  {sep}")

    for week in weeks:
        # ── date-number row ───────────────────────────────────────────────────────────────────
        date_row = "  "
        has_any = False
        for day_num in week:
            if day_num == 0:
                date_row += " " * W
            else:
                d = date(year, month, day_num)
                fmark = "★" if d in FESTIVE else ""
                day_str = f"{day_num}{fmark}"
                date_row += f"{day_str:<{W}}"
                if by_date.get(d):
                    has_any = True
        print(date_row)

        # ── fixture rows ─────────────────────────────────────────────────────────────────
        week_cols: list[list[ScheduledFixture]] = []
        for day_num in week:
            if day_num == 0:
                week_cols.append([])
            else:
                d = date(year, month, day_num)
                week_cols.append(sorted(by_date.get(d, []), key=lambda s: s.slot.kickoff))

        max_f = max((len(col) for col in week_cols), default=0)
        for fi in range(max_f):
            row = "  "
            for col in week_cols:
                if fi < len(col):
                    sf = col[fi]
                    h, a = sf.home_team_id, sf.away_team_id
                    dmark = "◆" if (h, a) in derby_pairs else ""
                    cell = f"{h}-{a}{dmark}"
                    row += f"{cell:<{W}}"
                else:
                    row += " " * W
            print(row)

        if has_any:
            print()

    print(f"  {sep}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Calendar-style schedule view")
    parser.add_argument(
        "--csv", default=str(ROOT / "output" / "schedule_cp_sat.csv"),
        help="Schedule CSV (default: output/schedule_cp_sat.csv)",
    )
    parser.add_argument("--month", type=int, default=None, help="Show only this month (1–12)")
    parser.add_argument("--year",  type=int, default=None, help="Year for --month (default: inferred)")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"Not found: {csv_path}\nRun a solver first: python -m solvers.cp_sat.main")
        sys.exit(1)

    schedule     = _load_csv(csv_path)
    teams        = load_teams()
    derby_pairs  = {(a, b) for a, b in load_high_profile_derbies()}
    derby_pairs |= {(b, a) for a, b in derby_pairs}   # both directions

    by_date: dict[date, list[ScheduledFixture]] = defaultdict(list)
    for sf in schedule.fixtures:
        by_date[sf.slot.date].append(sf)

    if args.month:
        year = args.year or (2025 if args.month >= 8 else 2026)
        _print_overview(by_date, csv_path.stem)
        _render_month(year, args.month, by_date, derby_pairs)
    else:
        _print_overview(by_date, csv_path.stem)
        for year, month in [
            (2025, 8), (2025, 9), (2025, 10), (2025, 11), (2025, 12),
            (2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5),
        ]:
            _render_month(year, month, by_date, derby_pairs)

    print()


if __name__ == "__main__":
    main()
