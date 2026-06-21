"""
Generates a PNG calendar graphic of the full EPL season schedule.

Each month is rendered as a calendar grid. Day cells are colour-coded:

Full-season mode:
  - Sky blue    : regular matchday
  - Gold        : festive matchday (Boxing Day / Dec 28 / NYD)
  - Peach       : international break (hard block — no fixtures)
  - Violet      : cup reservation window (FA Cup / Carabao Cup)
  - Light grey  : other hard block (Christmas Day)
  - White       : no fixtures
  Derby fixtures are indicated by a ◆ symbol in the fixture text.

Team mode (--team TEAM_ID):
  - Green       : home fixture
  - Lavender    : away fixture
  - Gold        : festive fixture
  - Peach/Violet: blocked window (same scheme as full-season)
  - White       : no fixture for this team
  Derby fixtures are indicated by a ◆ next to the H/A label in the cell.

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

_cal.setfirstweekday(6)  # 6 = Sunday; shifts all monthcalendar() grids to Sun-start

from core.data_loader import load_teams, load_high_profile_derbies, load_calendar
from core.models import Fixture, Slot, ScheduledFixture, Schedule

# ---------------------------------------------------------------------------
# Constants / colours
# ---------------------------------------------------------------------------

DAY_ABBREVS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

C_EMPTY      = "#f8f9fa"   # no fixtures
C_REGULAR    = "#dbeafe"   # regular matchday       (light blue)
C_FESTIVE    = "#fef9c3"   # festive matchday       (light gold)
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

            # ── Blocked window with no fixtures ───────────────────────────────
            if n_fix == 0 and d in blocked_dates:
                bg, fg, short = blocked_dates[d]
                _draw_blocked_cell(ax, x, y, day_num, bg, fg, short)
                continue

            # ── Normal fixture / empty cell ────────────────────────────────────────
            if n_fix == 0:
                bg = C_EMPTY
            elif is_festive:
                bg = C_FESTIVE
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
                    h, a     = sf.home_team_id, sf.away_team_id
                    is_derby = (h, a) in derby_pairs
                    label    = f"{h}-{a}{'◆' if is_derby else ''}"
                    ax.text(
                        x + 0.06, start_y - i * line_h, label,
                        ha="left", va="top", fontsize=4.8,
                        color=C_TEXT, zorder=2, fontfamily="monospace",
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

            if is_festive:
                bg = C_FESTIVE
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

            ha_colour = C_HOME_TEXT if is_home else C_AWAY_TEXT
            ha_label  = ("H" if is_home else "A") + (" ◆" if is_derby else "")
            ax.text(
                x + 0.06, y + 0.72, ha_label,
                ha="left", va="top", fontsize=8, color=ha_colour,
                fontweight="bold", zorder=2,
            )

            ax.text(
                x + 0.06, y + 0.46, f"vs {opponent}",
                ha="left", va="top", fontsize=6, color=C_TEXT,
                fontweight="bold", zorder=2, fontfamily="monospace",
            )

            ax.text(
                x + 0.06, y + 0.22, sf.slot.kickoff,
                ha="left", va="top", fontsize=5.5, color=C_FAINT, zorder=2,
            )


# ---------------------------------------------------------------------------
# Fixture list panel (team mode only)
# ---------------------------------------------------------------------------

def _draw_fixture_list(
    ax: plt.Axes,
    team_id: str,
    by_date: dict,
    derby_pairs: set,
) -> None:
    """Draw an ordered season fixture list to the right of the calendar grid."""
    fixtures = sorted(
        (sf for sfs in by_date.values() for sf in sfs
         if sf.home_team_id == team_id or sf.away_team_id == team_id),
        key=lambda sf: (sf.slot.date, sf.slot.kickoff),
    )

    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    N_HDR   = 2          # header rows (title + col labels)
    N_ROWS  = len(fixtures) + N_HDR
    row_h   = 1.0 / N_ROWS
    fs_main = max(5.0, min(7.0, 210 / N_ROWS))   # adaptive font size

    # ── Panel background ────────────────────────────────────────────────────
    ax.add_patch(FancyBboxPatch(
        (0, 0), 1, 1,
        boxstyle="square,pad=0", linewidth=0.5,
        edgecolor="#d1d5db", facecolor="#f9fafb", zorder=0,
    ))

    # ── Title row ───────────────────────────────────────────────────────────
    title_h = row_h
    ax.add_patch(FancyBboxPatch(
        (0, 1 - title_h), 1, title_h,
        boxstyle="square,pad=0", linewidth=0,
        facecolor=C_HEADER_BG, zorder=1,
    ))
    ax.text(0.5, 1 - title_h / 2, "SEASON FIXTURES",
            ha="center", va="center", fontsize=fs_main,
            fontweight="bold", color="white", zorder=2)

    # ── Column header row ───────────────────────────────────────────────────
    col_y = 1 - title_h - row_h * 0.5
    ax.add_patch(FancyBboxPatch(
        (0, 1 - title_h - row_h), 1, row_h,
        boxstyle="square,pad=0", linewidth=0,
        facecolor="#374151", zorder=1,
    ))
    for label, x in [("#", 0.03), ("DATE", 0.12), ("H/A", 0.47), ("OPPONENT", 0.57)]:
        ax.text(x, col_y, label,
                ha="left", va="center", fontsize=fs_main - 0.5,
                fontweight="bold", color="white", zorder=2)

    # ── Fixture rows ────────────────────────────────────────────────────────
    top_of_rows = 1 - title_h - row_h
    for i, sf in enumerate(fixtures):
        y = top_of_rows - (i + 1) * row_h

        # Alternating row background
        row_bg = "#ffffff" if i % 2 == 0 else "#f3f4f6"
        ax.add_patch(FancyBboxPatch(
            (0, y), 1, row_h,
            boxstyle="square,pad=0", linewidth=0,
            facecolor=row_bg, zorder=1,
        ))

        is_home  = sf.home_team_id == team_id
        opponent = sf.away_team_id if is_home else sf.home_team_id
        is_derby = (sf.home_team_id, sf.away_team_id) in derby_pairs
        ha_label = "H" if is_home else "A"
        ha_color = C_HOME_TEXT if is_home else C_AWAY_TEXT
        date_str = sf.slot.date.strftime("%m/%d/%y")
        text_y   = y + row_h * 0.52

        # Match number
        ax.text(0.03, text_y, str(i + 1),
                ha="left", va="center", fontsize=fs_main - 1.0,
                color=C_FAINT, zorder=2)

        # Date
        ax.text(0.12, text_y, date_str,
                ha="left", va="center", fontsize=fs_main,
                color=C_TEXT, zorder=2, fontfamily="monospace")

        # H / A badge
        ax.text(0.47, text_y, ha_label,
                ha="left", va="center", fontsize=fs_main,
                fontweight="bold", color=ha_color, zorder=2)

        # Opponent + derby marker
        opp_label = opponent + (" ◆" if is_derby else "")
        ax.text(0.57, text_y, opp_label,
                ha="left", va="center", fontsize=fs_main,
                color=C_ACCENT if is_derby else C_TEXT,
                fontweight="bold" if is_derby else "normal",
                zorder=2, fontfamily="monospace")

        # Subtle row divider
        ax.axhline(y, color="#e5e7eb", linewidth=0.3, zorder=3)


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
    from matplotlib.gridspec import GridSpec

    N_ROWS  = (len(months) + 1) // 2   # always 2 calendar columns
    MONTH_H = 3.6

    if team_id:
        FIG_W = 24
        fig_h = MONTH_H * N_ROWS + 1.2
        fig   = plt.figure(figsize=(FIG_W, fig_h))

        # Size the fixture list to its content rather than stretching it to
        # match the calendar height.  ROW_IN sets the physical row height in
        # inches; the axes height is then exactly content_rows × ROW_IN.
        team_fx_count = sum(
            1 for sfs in by_date.values() for sf in sfs
            if sf.home_team_id == team_id or sf.away_team_id == team_id
        )
        N_LIST      = team_fx_count + 2        # +2: title row + column-header row
        ROW_IN      = 0.18                     # inches per row
        LIST_TOP    = 0.965                    # align to top of calendar grid
        LIST_W_FRAC = 0.21                     # fraction of figure width
        list_h_frac = min((ROW_IN * N_LIST) / fig_h, LIST_TOP - 0.035)
        cal_right   = 1.0 - LIST_W_FRAC - 0.012

        gs = GridSpec(
            N_ROWS, 2, figure=fig,
            hspace=0.35, wspace=0.04,
            left=0.01, right=cal_right, top=LIST_TOP, bottom=0.035,
        )
        axes_flat = [fig.add_subplot(gs[r, c]) for r in range(N_ROWS) for c in range(2)]
        list_ax   = fig.add_axes([
            cal_right + 0.008, LIST_TOP - list_h_frac, LIST_W_FRAC, list_h_frac,
        ])
    else:
        FIG_W = 18
        fig   = plt.figure(figsize=(FIG_W, MONTH_H * N_ROWS + 1.2))
        gs    = GridSpec(
            N_ROWS, 2, figure=fig,
            hspace=0.35, wspace=0.04,
            left=0.01, right=0.99, top=0.975, bottom=0.03,
        )
        axes_flat = [fig.add_subplot(gs[r, c]) for r in range(N_ROWS) for c in range(2)]
        list_ax   = None

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

    if list_ax is not None:
        _draw_fixture_list(list_ax, team_id, by_date, derby_pairs)

    # ── Title ─────────────────────────────────────────────────────────────────
    if team_id:
        title = f"{team_name} ({team_id}) — EPL 2025/26 Fixture Calendar"
    else:
        title = "EPL 2025/26 Season Calendar"
    if solver_label:
        title += f"  —  {solver_label}"
    fig.suptitle(title, fontsize=14, fontweight="bold", color=C_HEADER_BG, y=0.998)

    # ── Legend ────────────────────────────────────────────────────────────────
    if team_id:
        fixture_patches = [
            mpatches.Patch(facecolor=C_HOME,    edgecolor="#9ca3af", label="Home"),
            mpatches.Patch(facecolor=C_AWAY,    edgecolor="#9ca3af", label="Away"),
            mpatches.Patch(facecolor=C_FESTIVE, edgecolor="#9ca3af", label="Festive  ★"),
        ]
    else:
        fixture_patches = [
            mpatches.Patch(facecolor=C_REGULAR, edgecolor="#9ca3af", label="Matchday"),
            mpatches.Patch(facecolor=C_FESTIVE, edgecolor="#9ca3af", label="Festive  ★"),
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
