"""
Generates a PNG calendar graphic of the full EPL season schedule.

Each month is rendered as a calendar grid. Day cells are colour-coded:

Full-season mode:
  - Sky blue    : regular matchday
  - Gold        : festive matchday (Boxing Day / Dec 28 / NYD)
  - Red         : high-profile derby
  - Amber       : festive derby (both)
  - Peach       : international break (hard block — no fixtures)
  - Violet      : cup reservation window (FA Cup / Carabao Cup)
  - Light grey  : other hard block (Christmas Day)
  - White       : no fixtures

Team mode (--team TEAM_ID):
  - Green       : home fixture
  - Lavender    : away fixture
  - Gold        : festive fixture
  - Red         : derby fixture
  - Amber       : festive derby
  - Peach/Violet: blocked window (same scheme as full-season)
  - White       : no fixture for this team

Usage:
    python tools/calendar_png.py
    python tools/calendar_png.py --csv output/schedule_ilp.csv
    python tools/calendar_png.py --out output/calendar.png
    python tools/calendar_png.py --month 12
    python tools/calendar_png.py --team LIV
    python tools/calendar_png.py --team ARS --month 11
"""
from __future__ import annotations

import argparse
import calendar as _cal
import csv
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch

from core.data_loader import load_teams, load_high_profile_derbies, load_calendar
from core.models import Fixture, Slot, ScheduledFixture, Schedule

# ---------------------------------------------------------------------------
# Constants / colours
# ---------------------------------------------------------------------------

DAY_ABBREVS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

C_EMPTY      = "#f8f9fa"   # no fixtures
C_REGULAR    = "#dbeafe"   # regular matchday       (light blue)
C_FESTIVE    = "#fef9c3"   # festive matchday       (light gold)
C_DERBY      = "#fee2e2"   # derby                  (light red)
C_BOTH       = "#fde68a"   # festive + derby        (amber)
C_HOME       = "#dcfce7"   # team home fixture      (light green)
C_AWAY       = "#ede9fe"   # team away fixture      (light lavender)
C_INTL       = "#ffedd5"   # international break    (peach)
C_CUP        = "#f3e8ff"   # cup reservation window (violet)
C_HARDBLOCK  = "#e5e7eb"   # other hard block       (light grey)
C_HEADER_BG  = "#1e3a5f"   # month header           (dark navy)
C_DAY_HDR    = "#374151"   # day-of-week header     (dark grey)
C_TEXT       = "#111827"
C_FAINT      = "#9ca3af"
C_ACCENT     = "#b91c1c"   # derby marker text
C_HOME_TEXT  = "#166534"   # home label text        (dark green)
C_AWAY_TEXT  = "#4c1d95"   # away label text        (dark purple)
C_INTL_TEXT  = "#7c2d12"   # international break label
C_CUP_TEXT   = "#581c87"   # cup window label
C_BLOCK_TEXT = "#374151"   # hard block label

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

MAX_FIXTURES_SHOWN = 5


# ---------------------------------------------------------------------------
# Blocked date index
# ---------------------------------------------------------------------------

def _window_style(label: str) -> tuple[str, str, str]:
    """Return (bg_colour, text_colour, short_label) for a window label."""
    if "International" in label:
        return C_INTL, C_INTL_TEXT, "INTL"
    if "Carabao Cup Final" == label:
        return C_CUP, C_CUP_TEXT, "CC FINAL"
    if "Carabao Cup" in label:
        parts = label.split()
        return C_CUP, C_CUP_TEXT, "CC " + parts[-1]
    if "FA Cup Final" in label:
        return C_CUP, C_CUP_TEXT, "FA FINAL"
    if "FA Cup" in label:
        parts = label.split()
        return C_CUP, C_CUP_TEXT, "FA " + parts[-1]
    if "Christmas" in label:
        return C_HARDBLOCK, C_BLOCK_TEXT, "XMAS"
    return C_HARDBLOCK, C_BLOCK_TEXT, "BLK"


def build_blocked_dates(calendar: dict) -> dict[date, tuple[str, str, str]]:
    """
    Return {date: (bg_colour, text_colour, short_label)} for every date that
    falls inside a hard blocked window or cup reservation window.
    blocked_windows take priority over cup_reservation_windows.
    """
    result: dict[date, tuple[str, str, str]] = {}

    for w in calendar.get("blocked_windows", []):
        bg, fg, short = _window_style(w["label"])
        d = date.fromisoformat(w["start"])
        end = date.fromisoformat(w["end"])
        while d <= end:
            result[d] = (bg, fg, short)
            d += timedelta(days=1)

    for w in calendar.get("cup_reservation_windows", []):
        bg, fg, short = _window_style(w["label"])
        d = date.fromisoformat(w["start"])
        end = date.fromisoformat(w["end"])
        while d <= end:
            if d not in result:   # don't overwrite hard blocks
                result[d] = (bg, fg, short)
            d += timedelta(days=1)

    return result


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
# Shared cell helpers
# ---------------------------------------------------------------------------

def _draw_cell(ax, x, y, bg, edge="#9ca3af", lw=0.4):
    ax.add_patch(FancyBboxPatch(
        (x, y), 1, 1,
        boxstyle="square,pad=0", linewidth=lw,
        edgecolor=edge, facecolor=bg, zorder=1,
    ))


def _draw_blocked_cell(ax, x, y, day_num, bg, fg, short_label):
    """Render a day cell that falls inside a constraint window (no fixtures)."""
    _draw_cell(ax, x, y, bg, edge="#d1d5db", lw=0.3)
    # Date number — subdued
    ax.text(
        x + 0.06, y + 0.92, str(day_num),
        ha="left", va="top", fontsize=6.5, color=fg,
        fontweight="bold", alpha=0.6, zorder=2,
    )
    # Centred block label
    ax.text(
        x + 0.5, y + 0.44, short_label,
        ha="center", va="center", fontsize=5.2, color=fg,
        fontweight="bold", alpha=0.75, zorder=2,
    )


# ---------------------------------------------------------------------------
# Single-month renderer — full season
# ---------------------------------------------------------------------------

def _render_month(
    ax: plt.Axes,
    year: int,
    month: int,
    by_date: dict,
    derby_pairs: set,
    blocked_dates: dict,
) -> None:
    """Draw one month calendar (all fixtures) onto ax."""
    weeks      = _cal.monthcalendar(year, month)
    month_name = _cal.month_name[month]
    n_weeks    = len(weeks)

    COLS = 7
    ROWS = n_weeks + 2

    ax.set_xlim(0, COLS)
    ax.set_ylim(0, ROWS)
    ax.axis("off")

    # ── Month header ──────────────────────────────────────────────────────────────────
    _draw_cell(ax, 0, ROWS - 1, C_HEADER_BG, lw=0)
    ax.patches[-1].set_width(COLS)
    ax.text(
        COLS / 2, ROWS - 0.5, f"{month_name} {year}",
        ha="center", va="center", fontsize=9, fontweight="bold",
        color="white", zorder=2,
    )

    # ── Day-of-week header ─────────────────────────────────────────────────────────────────
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

    # ── Day cells ───────────────────────────────────────────────────────────────────────
    for week_idx, week in enumerate(weeks):
        row_y = ROWS - 3 - week_idx

        for col, day_num in enumerate(week):
            x, y = col, row_y

            if day_num == 0:
                _draw_cell(ax, x, y, C_EMPTY, edge="#e5e7eb", lw=0.3)
                continue

            d          = date(year, month, day_num)
            fixtures   = sorted(by_date.get(d, []), key=lambda s: s.slot.kickoff)
            n_fix      = len(fixtures)
            is_festive = d in FESTIVE_DATES
            has_derby  = any((sf.home_team_id, sf.away_team_id) in derby_pairs for sf in fixtures)

            # ── Blocked window with no fixtures ───────────────────────────────────
            if n_fix == 0 and d in blocked_dates:
                bg, fg, short = blocked_dates[d]
                _draw_blocked_cell(ax, x, y, day_num, bg, fg, short)
                continue

            # ── Normal fixture / empty cell ────────────────────────────────────────
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

            _draw_cell(ax, x, y, bg)

            ax.text(
                x + 0.06, y + 0.92, str(day_num),
                ha="left", va="top", fontsize=6.5, color=C_TEXT,
                fontweight="bold", zorder=2,
            )

            if is_festive and n_fix > 0:
                ax.text(
                    x + 0.94, y + 0.92, FESTIVE_LABEL[d],
                    ha="right", va="top", fontsize=4.5,
                    color="#92400e", fontstyle="italic", zorder=2,
                )

            if n_fix > 0:
                shown    = fixtures[:MAX_FIXTURES_SHOWN]
                overflow = n_fix - MAX_FIXTURES_SHOWN
                line_h   = 0.13
                start_y  = y + 0.78

                for i, sf in enumerate(shown):
                    h, a      = sf.home_team_id, sf.away_team_id
                    is_derby  = (h, a) in derby_pairs
                    label     = f"{h}-{a}{'◆' if is_derby else ''}"
                    colour    = C_ACCENT if is_derby else C_TEXT
                    ax.text(
                        x + 0.06, start_y - i * line_h, label,
                        ha="left", va="top", fontsize=4.8,
                        color=colour, zorder=2, fontfamily="monospace",
                    )

                if overflow > 0:
                    ax.text(
                        x + 0.06, start_y - len(shown) * line_h,
                        f"+{overflow} more",
                        ha="left", va="top", fontsize=4.5,
                        color=C_FAINT, zorder=2,
                    )


# ---------------------------------------------------------------------------
# Single-month renderer — one team
# ---------------------------------------------------------------------------

def _render_month_team(
    ax: plt.Axes,
    year: int,
    month: int,
    by_date: dict,
    derby_pairs: set,
    team_id: str,
    team_name: str,
    blocked_dates: dict,
) -> None:
    """Draw one month calendar filtered to a single team's fixtures."""
    weeks      = _cal.monthcalendar(year, month)
    month_name = _cal.month_name[month]
    n_weeks    = len(weeks)

    COLS = 7
    ROWS = n_weeks + 2

    ax.set_xlim(0, COLS)
    ax.set_ylim(0, ROWS)
    ax.axis("off")

    # ── Month header ──────────────────────────────────────────────────────────────────
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

    # ── Day-of-week header ─────────────────────────────────────────────────────────────────
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

    # ── Day cells ───────────────────────────────────────────────────────────────────────
    for week_idx, week in enumerate(weeks):
        row_y = ROWS - 3 - week_idx

        for col, day_num in enumerate(week):
            x, y = col, row_y

            if day_num == 0:
                _draw_cell(ax, x, y, C_EMPTY, edge="#e5e7eb", lw=0.3)
                continue

            d            = date(year, month, day_num)
            all_fixtures = by_date.get(d, [])
            sf = next(
                (f for f in all_fixtures
                 if f.home_team_id == team_id or f.away_team_id == team_id),
                None,
            )

            # ── Blocked window with no team fixture ─────────────────────────────
            if sf is None and d in blocked_dates:
                bg, fg, short = blocked_dates[d]
                _draw_blocked_cell(ax, x, y, day_num, bg, fg, short)
                continue

            # ── Empty day (not blocked) ──────────────────────────────────────────
            if sf is None:
                _draw_cell(ax, x, y, C_EMPTY, edge="#e5e7eb", lw=0.3)
                ax.text(
                    x + 0.06, y + 0.92, str(day_num),
                    ha="left", va="top", fontsize=6.5, color=C_FAINT,
                    fontweight="bold", zorder=2,
                )
                continue

            # ── Team has a fixture ─────────────────────────────────────────────────
            is_home    = sf.home_team_id == team_id
            opponent   = sf.away_team_id if is_home else sf.home_team_id
            is_festive = d in FESTIVE_DATES
            is_derby   = (sf.home_team_id, sf.away_team_id) in derby_pairs

            if is_festive and is_derby:
                bg = C_BOTH
            elif is_festive:
                bg = C_FESTIVE
            elif is_derby:
                bg = C_DERBY
            elif is_home:
                bg = C_HOME
            else:
                bg = C_AWAY

            _draw_cell(ax, x, y, bg)

            ax.text(
                x + 0.06, y + 0.92, str(day_num),
                ha="left", va="top", fontsize=6.5, color=C_TEXT,
                fontweight="bold", zorder=2,
            )

            if is_festive:
                ax.text(
                    x + 0.94, y + 0.92, FESTIVE_LABEL[d],
                    ha="right", va="top", fontsize=4.2,
                    color="#92400e", fontstyle="italic", zorder=2,
                )

            ha_label  = "H" if is_home else "A"
            ha_colour = C_HOME_TEXT if is_home else C_AWAY_TEXT
            ax.text(
                x + 0.06, y + 0.72, ha_label,
                ha="left", va="top", fontsize=8, color=ha_colour,
                fontweight="bold", zorder=2,
            )

            derby_mark = "◆" if is_derby else ""
            opp_colour = C_ACCENT if is_derby else C_TEXT
            ax.text(
                x + 0.28, y + 0.74, f"vs {opponent}{derby_mark}",
                ha="left", va="top", fontsize=6, color=opp_colour,
                fontweight="bold", zorder=2, fontfamily="monospace",
            )

            ax.text(
                x + 0.06, y + 0.44, sf.slot.kickoff,
                ha="left", va="top", fontsize=5.5, color=C_FAINT, zorder=2,
            )


# ---------------------------------------------------------------------------
# Full-season PNG (both modes)
# ---------------------------------------------------------------------------

def render_season_png(
    by_date: dict,
    derby_pairs: set,
    months: list[tuple[int, int]],
    out_path: Path,
    blocked_dates: dict,
    solver_label: str = "",
    team_id: str | None = None,
    team_name: str = "",
) -> None:
    N_COLS  = 2
    N_ROWS  = (len(months) + N_COLS - 1) // N_COLS
    FIG_W   = 18
    MONTH_H = 3.6

    fig, axes = plt.subplots(
        N_ROWS, N_COLS,
        figsize=(FIG_W, MONTH_H * N_ROWS + 1.2),
        gridspec_kw={"hspace": 0.35, "wspace": 0.04},
    )
    axes_flat = axes.flatten()

    for i, (year, month) in enumerate(months):
        if team_id:
            _render_month_team(
                axes_flat[i], year, month, by_date, derby_pairs,
                team_id, team_name, blocked_dates,
            )
        else:
            _render_month(
                axes_flat[i], year, month, by_date, derby_pairs, blocked_dates,
            )

    for j in range(len(months), len(axes_flat)):
        axes_flat[j].axis("off")

    # ── Title ──────────────────────────────────────────────────────────────────────────
    if team_id:
        title = f"{team_name} ({team_id}) — EPL 2025/26 Fixture Calendar"
    else:
        title = "EPL 2025/26 Season Calendar"
    if solver_label:
        title += f"  —  {solver_label}"
    fig.suptitle(title, fontsize=14, fontweight="bold", color=C_HEADER_BG, y=0.995)

    # ── Legend ──────────────────────────────────────────────────────────────────────────
    if team_id:
        fixture_patches = [
            mpatches.Patch(facecolor=C_HOME,    edgecolor="#9ca3af", label="Home"),
            mpatches.Patch(facecolor=C_AWAY,    edgecolor="#9ca3af", label="Away"),
            mpatches.Patch(facecolor=C_FESTIVE, edgecolor="#9ca3af", label="Festive  ★"),
            mpatches.Patch(facecolor=C_DERBY,   edgecolor="#9ca3af", label="Derby  ◆"),
            mpatches.Patch(facecolor=C_BOTH,    edgecolor="#9ca3af", label="Festive derby"),
        ]
    else:
        fixture_patches = [
            mpatches.Patch(facecolor=C_REGULAR, edgecolor="#9ca3af", label="Matchday"),
            mpatches.Patch(facecolor=C_FESTIVE, edgecolor="#9ca3af", label="Festive  ★"),
            mpatches.Patch(facecolor=C_DERBY,   edgecolor="#9ca3af", label="Derby  ◆"),
            mpatches.Patch(facecolor=C_BOTH,    edgecolor="#9ca3af", label="Festive derby"),
        ]

    constraint_patches = [
        mpatches.Patch(facecolor=C_INTL,      edgecolor="#9ca3af", label="Intl break"),
        mpatches.Patch(facecolor=C_CUP,       edgecolor="#9ca3af", label="Cup window"),
        mpatches.Patch(facecolor=C_HARDBLOCK, edgecolor="#9ca3af", label="Hard block"),
        mpatches.Patch(facecolor=C_EMPTY,     edgecolor="#9ca3af", label="No fixture"),
    ]

    all_patches = fixture_patches + constraint_patches
    fig.legend(
        handles=all_patches, loc="lower center", ncol=len(all_patches),
        fontsize=7.5, frameon=True, framealpha=0.9,
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
    parser.add_argument(
        "--team", default=None,
        help="Team ID for individual team schedule (e.g. LIV, ARS, MCI)",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"Not found: {csv_path}\nRun a solver first: python -m solvers.cp_sat.main")
        sys.exit(1)

    schedule      = _load_csv(csv_path)
    teams         = load_teams()
    derby_pairs   = {(a, b) for a, b in load_high_profile_derbies()}
    derby_pairs  |= {(b, a) for a, b in derby_pairs}
    calendar      = load_calendar()
    blocked_dates = build_blocked_dates(calendar)

    team_id   = args.team.upper() if args.team else None
    team_name = ""
    if team_id:
        if team_id not in teams:
            valid = ", ".join(sorted(teams))
            print(f"Unknown team '{team_id}'. Valid IDs: {valid}")
            sys.exit(1)
        team_name = teams[team_id].name

    by_date: dict[date, list[ScheduledFixture]] = defaultdict(list)
    for sf in schedule.fixtures:
        by_date[sf.slot.date].append(sf)

    solver_label = csv_path.stem.replace("schedule_", "").upper()

    if args.month:
        year   = args.year or (2025 if args.month >= 8 else 2026)
        months = [(year, args.month)]
        stem   = f"calendar_{team_id.lower() + '_' if team_id else ''}{_cal.month_abbr[args.month].lower()}"
        out    = Path(args.out).with_name(f"{stem}.png")
    elif team_id:
        months = MONTHS
        out    = Path(args.out).with_name(f"calendar_{team_id.lower()}.png")
    else:
        months = MONTHS
        out    = Path(args.out)

    render_season_png(
        by_date, derby_pairs, months, out, blocked_dates, solver_label,
        team_id=team_id, team_name=team_name,
    )


if __name__ == "__main__":
    main()
