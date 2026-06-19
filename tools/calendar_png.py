"""
Generates a PNG calendar graphic of the full EPL season schedule.

Each month is rendered as a calendar grid. Day cells are colour-coded:
  - Sky blue   : regular matchday
  - Gold       : festive matchday (Boxing Day / Dec 28 / NYD)
  - Red        : high-profile derby
  - Red + gold : festive derby (both)
  - White      : no fixtures

Each cell lists the fixtures as HOME-AWY (up to 5; remaining count shown).

Usage:
    python tools/calendar_png.py
    python tools/calendar_png.py --csv output/schedule_ilp.csv
    python tools/calendar_png.py --out output/calendar.png
    python tools/calendar_png.py --month 12
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

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch

from core.data_loader import load_teams, load_high_profile_derbies
from core.models import Fixture, Slot, ScheduledFixture, Schedule

# ---------------------------------------------------------------------------
# Constants / colours
# ---------------------------------------------------------------------------

DAY_ABBREVS  = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

C_EMPTY      = "#f8f9fa"   # no fixtures
C_REGULAR    = "#dbeafe"   # regular matchday  (light blue)
C_FESTIVE    = "#fef9c3"   # festive            (light gold)
C_DERBY      = "#fee2e2"   # derby              (light red)
C_BOTH       = "#fde68a"   # festive + derby    (amber)
C_HEADER_BG  = "#1e3a5f"   # month header       (dark navy)
C_DAY_HDR    = "#374151"   # day-of-week header (dark grey)
C_TEXT       = "#111827"
C_FAINT      = "#9ca3af"
C_ACCENT     = "#b91c1c"   # derby marker text

FESTIVE_DATES = {date(2025, 12, 26), date(2025, 12, 28), date(2026, 1, 1)}
FESTIVE_LABEL = {
    date(2025, 12, 26): "Boxing Day",
    date(2025, 12, 28): "Dec 28",
    date(2026,  1,  1): "New Year's Day",
}

MONTHS = [
    (2025, 8), (2025, 9), (2025, 10), (2025, 11), (2025, 12),
    (2026, 1), (2026, 2), (2026, 3),  (2026, 4),  (2026, 5),
]

MAX_FIXTURES_SHOWN = 5   # max fixture lines per cell before "+N more"

# ---------------------------------------------------------------------------
# CSV loader
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
# Single-month renderer
# ---------------------------------------------------------------------------

def _render_month(
    ax: plt.Axes,
    year: int,
    month: int,
    by_date: dict,
    derby_pairs: set,
) -> None:
    """Draw one month calendar onto ax."""
    weeks      = _cal.monthcalendar(year, month)
    month_name = _cal.month_name[month]
    n_weeks    = len(weeks)

    COLS = 7
    ROWS = n_weeks + 2   # +1 month header, +1 day-of-week header

    ax.set_xlim(0, COLS)
    ax.set_ylim(0, ROWS)
    ax.axis("off")

    cell_h = 1.0   # normalised units

    # ── Month header ────────────────────────────────────────────────────────────────────
    ax.add_patch(FancyBboxPatch(
        (0, ROWS - 1), COLS, 1,
        boxstyle="square,pad=0", linewidth=0,
        facecolor=C_HEADER_BG, zorder=1,
    ))
    ax.text(
        COLS / 2, ROWS - 0.5, f"{month_name} {year}",
        ha="center", va="center", fontsize=9, fontweight="bold",
        color="white", zorder=2,
    )

    # ── Day-of-week header ────────────────────────────────────────────────────────────────────────
    for col, dname in enumerate(DAY_ABBREVS):
        ax.add_patch(FancyBboxPatch(
            (col, ROWS - 2), 1, 1,
            boxstyle="square,pad=0", linewidth=0.3,
            edgecolor="#d1d5db", facecolor=C_DAY_HDR, zorder=1,
        ))
        ax.text(
            col + 0.5, ROWS - 1.5, dname,
            ha="center", va="center", fontsize=7, color="white",
            fontweight="bold", zorder=2,
        )

    # ── Day cells ────────────────────────────────────────────────────────────────────────
    for week_idx, week in enumerate(weeks):
        row_y = ROWS - 3 - week_idx   # top of this week row

        for col, day_num in enumerate(week):
            x = col
            y = row_y

            if day_num == 0:
                # Outside month — blank cell
                ax.add_patch(FancyBboxPatch(
                    (x, y), 1, 1,
                    boxstyle="square,pad=0", linewidth=0.3,
                    edgecolor="#e5e7eb", facecolor=C_EMPTY, zorder=1,
                ))
                continue

            d = date(year, month, day_num)
            fixtures = sorted(by_date.get(d, []), key=lambda s: s.slot.kickoff)
            n_fix    = len(fixtures)
            is_festive = d in FESTIVE_DATES
            has_derby  = any((sf.home_team_id, sf.away_team_id) in derby_pairs for sf in fixtures)

            # Cell background colour
            if n_fix == 0:
                bg = C_EMPTY
            elif is_festive and has_derby:
                bg = C_BOTH
            elif is_festive:
                bg = C_FESTIVE
            elif has_derby:
                bg = C_DERBY
            else:
                bg = C_REGULAR

            ax.add_patch(FancyBboxPatch(
                (x, y), 1, 1,
                boxstyle="square,pad=0", linewidth=0.4,
                edgecolor="#9ca3af", facecolor=bg, zorder=1,
            ))

            # Date number
            ax.text(
                x + 0.06, y + 0.92, str(day_num),
                ha="left", va="top", fontsize=6.5, color=C_TEXT,
                fontweight="bold", zorder=2,
            )

            # Festive label
            if is_festive and n_fix > 0:
                ax.text(
                    x + 0.94, y + 0.92, FESTIVE_LABEL[d],
                    ha="right", va="top", fontsize=4.5,
                    color="#92400e", fontstyle="italic", zorder=2,
                )

            # Fixture lines
            if n_fix > 0:
                shown    = fixtures[:MAX_FIXTURES_SHOWN]
                overflow = n_fix - MAX_FIXTURES_SHOWN

                line_h   = 0.13
                start_y  = y + 0.78

                for i, sf in enumerate(shown):
                    h, a     = sf.home_team_id, sf.away_team_id
                    is_derby = (h, a) in derby_pairs
                    label    = f"{h}-{a}{'◆' if is_derby else ''}"
                    colour   = C_ACCENT if is_derby else C_TEXT

                    ax.text(
                        x + 0.06, start_y - i * line_h, label,
                        ha="left", va="top", fontsize=4.8,
                        color=colour, zorder=2,
                        fontfamily="monospace",
                    )

                if overflow > 0:
                    ax.text(
                        x + 0.06, start_y - len(shown) * line_h,
                        f"+{overflow} more",
                        ha="left", va="top", fontsize=4.5,
                        color=C_FAINT, zorder=2,
                    )


# ---------------------------------------------------------------------------
# Full season PNG
# ---------------------------------------------------------------------------

def render_season_png(
    by_date: dict,
    derby_pairs: set,
    months: list[tuple[int, int]],
    out_path: Path,
    solver_label: str = "",
) -> None:
    N_COLS   = 2          # months per row
    N_ROWS   = (len(months) + N_COLS - 1) // N_COLS
    FIG_W    = 18         # inches
    MONTH_H  = 3.6        # inches per month row

    fig, axes = plt.subplots(
        N_ROWS, N_COLS,
        figsize=(FIG_W, MONTH_H * N_ROWS + 1.2),
        gridspec_kw={"hspace": 0.35, "wspace": 0.04},
    )
    axes_flat = axes.flatten()

    for i, (year, month) in enumerate(months):
        _render_month(axes_flat[i], year, month, by_date, derby_pairs)

    # Hide any unused axes (if odd number of months)
    for j in range(len(months), len(axes_flat)):
        axes_flat[j].axis("off")

    # ── Title & legend ──────────────────────────────────────────────────────────────────────
    fig.suptitle(
        f"EPL 2025/26 Season Calendar{f'  —  {solver_label}' if solver_label else ''}",
        fontsize=14, fontweight="bold", color=C_HEADER_BG, y=0.995,
    )

    legend_patches = [
        mpatches.Patch(facecolor=C_REGULAR, edgecolor="#9ca3af", label="Matchday"),
        mpatches.Patch(facecolor=C_FESTIVE, edgecolor="#9ca3af", label="Festive matchday  ★"),
        mpatches.Patch(facecolor=C_DERBY,   edgecolor="#9ca3af", label="Derby fixture  ◆"),
        mpatches.Patch(facecolor=C_BOTH,    edgecolor="#9ca3af", label="Festive derby"),
        mpatches.Patch(facecolor=C_EMPTY,   edgecolor="#9ca3af", label="No fixtures"),
    ]
    fig.legend(
        handles=legend_patches, loc="lower center", ncol=5,
        fontsize=8, frameon=True, framealpha=0.9,
        bbox_to_anchor=(0.5, 0.001),
    )

    out_path.parent.mkdir(exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Saved: {out_path}  ({out_path.stat().st_size // 1024} KB)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a PNG season calendar")
    parser.add_argument(
        "--csv", default=str(ROOT / "output" / "schedule_cp_sat.csv"),
        help="Schedule CSV (default: output/schedule_cp_sat.csv)",
    )
    parser.add_argument(
        "--out", default=str(ROOT / "output" / "calendar.png"),
        help="Output PNG path (default: output/calendar.png)",
    )
    parser.add_argument("--month", type=int, default=None, help="Render only this month (1-12)")
    parser.add_argument("--year",  type=int, default=None)
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"Not found: {csv_path}\nRun a solver first: python -m solvers.cp_sat.main")
        sys.exit(1)

    schedule    = _load_csv(csv_path)
    derby_pairs = {(a, b) for a, b in load_high_profile_derbies()}
    derby_pairs |= {(b, a) for a, b in derby_pairs}

    by_date: dict[date, list[ScheduledFixture]] = defaultdict(list)
    for sf in schedule.fixtures:
        by_date[sf.slot.date].append(sf)

    if args.month:
        year   = args.year or (2025 if args.month >= 8 else 2026)
        months = [(year, args.month)]
        out    = Path(args.out).with_name(f"calendar_{_cal.month_abbr[args.month].lower()}.png")
    else:
        months = MONTHS
        out    = Path(args.out)

    render_season_png(
        by_date, derby_pairs, months, out,
        solver_label=csv_path.stem.replace("schedule_", "").upper(),
    )


if __name__ == "__main__":
    main()
