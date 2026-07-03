"""
Solver accuracy visualization — all three solvers vs. historical data, for
any of the three leagues via --league.

Produces four separate PNGs (EPL keeps unprefixed filenames for backward
compatibility; NFL/NBA get a viz_<league>_ prefix):
  viz[_<league>]_performance.png   — penalty score, hard/soft violations, rest days
  viz[_<league>]_quality.png       — consecutive runs, city clashes, derby spacing
  viz[_<league>]_constraints.png   — league-specific rules (see CONSTRAINT_PANELS)
  viz[_<league>]_dow.png           — day-of-week fixture distribution

EPL's constraints panel (Atos Golden Rules, London cluster, festive coverage)
and penalty/violations panel (core.validator-derived) stay EPL-only — the
validator hardcodes EPL constraint IDs (see CLAUDE.md's "Solver architecture"
section) and NFL/NBA have no equivalent Golden Rules. NFL/NBA substitute
their own signature metrics (Thanksgiving/primetime, back-to-backs/4-in-5/
All-Star) — see CONSTRAINT_PANELS below.

Usage:
    python tools/solver_accuracy_viz.py [--league epl|nfl|nba]
    python tools/solver_accuracy_viz.py --out-dir output/
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker
import numpy as np

from analysis.historical_loader import available_seasons, load_season
from analysis.main import _load_generated_csv, _validate_generated
from analysis.metrics import compute, MetricsReport
from core.data_loader import load_teams, set_league

LEAGUE_LABELS = {"epl": "EPL", "nfl": "NFL", "nba": "NBA"}


# ---------------------------------------------------------------------------
# Shared style
# ---------------------------------------------------------------------------

COLORS = {
    "Historical":    "#374151",
    "CP-SAT":        "#2563eb",
    "ILP":           "#16a34a",
    "Metaheuristic": "#dc2626",
}
HATCH = {
    "Historical":    "",
    "CP-SAT":        "",
    "ILP":           "//",
    "Metaheuristic": "xx",
}

DAYS_ORDER = ["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
DAY_ABBR   = ["Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]

SUBTITLE_COLOR = "#6b7280"
NOTE_COLOR     = "#7c3aed"


def _legend_handles(present: list[str]) -> list[mpatches.Patch]:
    return [
        mpatches.Patch(
            facecolor=COLORS[k], hatch=HATCH[k],
            edgecolor="white", label=k, alpha=0.88,
        )
        for k in present
    ]


def _polish(ax: plt.Axes) -> None:
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, linewidth=0.4, alpha=0.5)
    ax.tick_params(axis="both", labelsize=8)


def _save(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Saved: {path}  ({path.stat().st_size // 1024} KB)")


# ---------------------------------------------------------------------------
# Grouped bar helper
# ---------------------------------------------------------------------------

def grouped_bars(
    ax: plt.Axes,
    labels: list[str],
    groups: dict[str, list[float]],
    group_labels: list[str],
    ylabel: str = "",
    title: str = "",
    subtitle: str = "",
    log_scale: bool = False,
    ref_line: float | None = None,
    ref_label: str = "",
    note: str = "",
) -> None:
    n_groups = len(group_labels)
    n_series = len(labels)
    bar_w    = 0.7 / n_series
    x        = np.arange(n_groups)

    for i, lbl in enumerate(labels):
        vals    = groups[lbl]
        offsets = (i - (n_series - 1) / 2) * bar_w
        bars = ax.bar(
            x + offsets, vals, bar_w,
            label=lbl,
            color=COLORS[lbl],
            hatch=HATCH[lbl],
            edgecolor="white",
            linewidth=0.5,
            alpha=0.88,
            zorder=3,
        )
        for bar, v in zip(bars, vals):
            if v is None or (isinstance(v, float) and np.isnan(v)):
                continue
            top = bar.get_height()
            label_text = (
                f"{v:,.0f}" if v >= 1000
                else f"{v:.1f}" if v < 10
                else f"{int(v)}"
            )
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                top + (top * 0.04 if log_scale else max(top * 0.02, 0.2)),
                label_text,
                ha="center", va="bottom", fontsize=7, color="#374151", zorder=4,
            )

    ax.set_xticks(x)
    ax.set_xticklabels(group_labels, fontsize=8.5)
    ax.set_ylabel(ylabel, fontsize=8.5)
    ax.set_title(title, fontsize=11, fontweight="bold", pad=6)
    if subtitle:
        ax.text(0.5, 1.01, subtitle, transform=ax.transAxes,
                ha="center", va="bottom", fontsize=8, color=SUBTITLE_COLOR, style="italic")
    _polish(ax)

    if log_scale:
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(matplotlib.ticker.ScalarFormatter())

    if ref_line is not None:
        ax.axhline(ref_line, color="#f59e0b", linewidth=1.4, linestyle="--", zorder=2)
        ax.text(
            n_groups - 0.5, ref_line, f"  {ref_label}",
            va="bottom", ha="right", fontsize=7.5, color="#92400e",
        )

    if note:
        ax.text(0.02, 0.97, note, transform=ax.transAxes,
                fontsize=7, color=NOTE_COLOR, va="top", style="italic")


# ---------------------------------------------------------------------------
# Helpers for pulling values safely
# ---------------------------------------------------------------------------

def _cv(r: MetricsReport, key: str) -> int:
    cv = r.constraint_violations
    if isinstance(cv, dict):
        return cv.get(key, 0)
    return 0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _solver_csv_path(league: str, key: str) -> Path:
    """EPL keeps its established output/schedule_<solver>.csv convention
    (unprefixed, for backward compatibility); NFL/NBA use
    output/schedule_<league>_<solver>.csv — matches webapp/app.py and
    tools/export_analytics.py."""
    if league == "epl":
        return ROOT / "output" / f"schedule_{key}.csv"
    return ROOT / "output" / f"schedule_{league}_{key}.csv"


def _out_filename(league: str, base: str) -> str:
    prefix = "viz" if league == "epl" else f"viz_{league}"
    return f"{prefix}_{base}"


def load_reports(league: str = "epl") -> dict[str, MetricsReport]:
    set_league(league)
    teams = load_teams()
    reports: dict[str, MetricsReport] = {}

    seasons = available_seasons(league=league)
    if seasons:
        hist = compute(load_season(str(seasons[-1]), league=league))
        hist.label = "Historical"
        reports["Historical"] = hist

    for key, label in [("cp_sat", "CP-SAT"), ("ilp", "ILP"), ("metaheuristic", "Metaheuristic")]:
        p = _solver_csv_path(league, key)
        if not p.exists():
            print(f"  [skip] {p.name} not found — run the solver first")
            continue
        sched = _load_generated_csv(str(p))
        # core.validator hardcodes EPL constraint IDs — only meaningful for EPL.
        meta = _validate_generated(sched, teams) if league == "epl" else None
        rep  = compute(sched, solver_meta=meta)
        rep.label = label
        reports[label] = rep

    return reports


# ---------------------------------------------------------------------------
# Figure 1 — Solver performance
# ---------------------------------------------------------------------------

def render_performance(reports: dict[str, MetricsReport], out: Path, league: str = "epl") -> None:
    solvers = [k for k in ["CP-SAT", "ILP", "Metaheuristic"] if k in reports]
    present = [k for k in ["Historical"] + solvers if k in reports]
    label = LEAGUE_LABELS.get(league, league.upper())

    # Penalty score / hard+soft violations come from core.validator, which
    # hardcodes EPL constraint IDs — only render those two panels for EPL;
    # rest days is generic and works for any league.
    n_panels = 3 if league == "epl" else 1
    fig, axes = plt.subplots(1, n_panels, figsize=(15 if n_panels == 3 else 6, 5.5), facecolor="white")
    axes = [axes] if n_panels == 1 else axes
    fig.suptitle(
        f"Solver Performance vs. Historical — {label}",
        fontsize=14, fontweight="bold", color="#1e3a5f", y=1.01,
    )

    if league == "epl":
        # Penalty score (solvers only)
        grouped_bars(
            axes[0], solvers,
            {k: [reports[k].penalty_score or 0] for k in solvers},
            ["Penalty Score"],
            ylabel="Score (log scale)",
            title="Optimisation Penalty Score",
            subtitle="Solver objective — lower is better",
            log_scale=True,
        )

        # Hard + soft violations (solvers only)
        grouped_bars(
            axes[1], solvers,
            {k: [reports[k].hard_violations or 0, reports[k].soft_violations or 0] for k in solvers},
            ["Hard Violations", "Soft Violations"],
            ylabel="Count",
            title="Constraint Violations",
            subtitle="0 hard violations = feasible schedule",
            ref_line=0, ref_label="hard target = 0",
        )
        axes[1].set_ylim(bottom=-8)
        rest_ax = axes[2]
    else:
        rest_ax = axes[0]

    # Rest days (all incl. historical) — generic across leagues
    grouped_bars(
        rest_ax, present,
        {k: [reports[k].rest_mean, reports[k].rest_min_global] for k in present},
        ["Mean rest days", "Min rest days"],
        ylabel="Days",
        title="Rest Days vs. Historical",
        subtitle="Higher min = fewer back-to-backs" if league != "epl" else "Min ≥ 3 is the hard constraint (HC1)",
        ref_line=3 if league == "epl" else None,
        ref_label="HC1 min = 3" if league == "epl" else "",
    )

    fig.legend(
        handles=_legend_handles(present), loc="lower center",
        ncol=len(present), fontsize=9.5, frameon=False,
        bbox_to_anchor=(0.5, -0.06),
    )
    fig.tight_layout()
    _save(fig, out)


# ---------------------------------------------------------------------------
# Figure 2 — Schedule quality
# ---------------------------------------------------------------------------

def render_quality(reports: dict[str, MetricsReport], out: Path, league: str = "epl") -> None:
    present = [k for k in ["Historical", "CP-SAT", "ILP", "Metaheuristic"] if k in reports]
    label = LEAGUE_LABELS.get(league, league.upper())

    fig, axes = plt.subplots(1, 3, figsize=(15, 5.5), facecolor="white")
    fig.suptitle(
        f"Schedule Quality vs. Historical — {label}",
        fontsize=14, fontweight="bold", color="#1e3a5f", y=1.01,
    )

    # Consecutive runs
    grouped_bars(
        axes[0], present,
        {k: [
            reports[k].league_max_consec_home,
            reports[k].league_max_consec_away,
            len(reports[k].teams_over_3_away),
        ] for k in present},
        ["Max consec.\nhome", "Max consec.\naway", "Teams >3\nconsec. away"],
        ylabel="Count",
        title="Consecutive Fixture Runs",
        subtitle="SC1/SC2 soft target ≤ 3; real EPL routinely exceeds this" if league == "epl"
                  else "Soft target ≤ 3 consecutive home/away games",
        ref_line=3, ref_label="soft target = 3",
    )

    # City clashes — city_weekend_clash_count is computed generically by
    # analysis/metrics.py for every league; the second bar only differs from
    # the first for EPL, where core.validator's own SC7 count (constraint_
    # violations) is available as a cross-check against solver output.
    if league == "epl":
        city_data = {
            k: [reports[k].city_clash_count, _cv(reports[k], "SC7")]
            for k in present
        }
        if "Historical" in reports:
            city_data["Historical"][1] = reports["Historical"].city_weekend_clash_count
        city_subtitle = "SC7: same-city clubs both home within 4 days"
    else:
        city_data = {
            k: [reports[k].city_clash_count, reports[k].city_weekend_clash_count]
            for k in present
        }
        city_subtitle = "Same-city clubs both home within 4 days"
    grouped_bars(
        axes[1], present, city_data,
        ["Same-day\nclashes", "4-day window"],
        ylabel="Clash count",
        title="City Home Clashes",
        subtitle=city_subtitle,
    )

    # Derby spacing — threshold is read from the active league's own
    # rivalry-spread constraint (see _derby_gap_threshold_days() in
    # analysis/metrics.py), not a hardcoded 56-day EPL window.
    threshold = next((r.derby_gap_threshold_days for r in reports.values() if r.derby_gap_threshold_days), 0)
    grouped_bars(
        axes[2], present,
        {k: [len(reports[k].derbies_under_56d)] for k in present},
        [f"Derby pairs gap <{threshold}d"],
        ylabel="Count",
        title="Derby Leg Spacing" + (" (SC3)" if league == "epl" else ""),
        subtitle=f"Fewer than {threshold} days between legs — lower is better",
    )

    fig.legend(
        handles=_legend_handles(present), loc="lower center",
        ncol=len(present), fontsize=9.5, frameon=False,
        bbox_to_anchor=(0.5, -0.06),
    )
    fig.tight_layout()
    _save(fig, out)


# ---------------------------------------------------------------------------
# Figure 3 — Constraint violations
# ---------------------------------------------------------------------------

def _render_constraints_epl(reports: dict[str, MetricsReport], axes, present: list[str]) -> None:
    # Atos Golden Rules
    golden_data = {
        k: [
            reports[k].five_match_pattern_violations,
            reports[k].season_boundary_violations,
            _cv(reports[k], "SC15") if isinstance(reports[k].constraint_violations, dict)
                else reports[k].boxing_day_nyd_violations,
        ]
        for k in present
    }
    if "Historical" in reports:
        h = reports["Historical"]
        golden_data["Historical"] = [
            h.five_match_pattern_violations,
            h.season_boundary_violations,
            h.boxing_day_nyd_violations,
        ]
    grouped_bars(
        axes[0], present, golden_data,
        ["SC13\n5-match H/A", "SC14\nSeason boundary", "SC15\nBoxDay/NYD pair"],
        ylabel="Violations",
        title="Atos Golden Rules",
        subtitle="Target = 0 for all; real EPL averages 244 SC13 violations/season",
        note="Real EPL avg: SC13=244, SC14=19, SC15=0.3",
    )

    # London cluster
    grouped_bars(
        axes[1], present,
        {k: [reports[k].london_cluster_violations] for k in present},
        [">3 London clubs\nhome same day"],
        ylabel="Days with violation",
        title="London Cluster (SC10)",
        subtitle="More than 3 London clubs scheduled at home on the same day",
        ref_line=0, ref_label="target = 0",
    )
    axes[1].set_ylim(bottom=-0.4)

    # Festive coverage
    grouped_bars(
        axes[2], present,
        {k: [
            reports[k].boxing_day_coverage,
            reports[k].new_years_day_coverage,
            reports[k].good_friday_coverage,
            reports[k].easter_monday_coverage,
        ] for k in present},
        ["Boxing\nDay", "New Year's\nDay", "Good\nFriday", "Easter\nMonday"],
        ylabel="Teams playing",
        title="Festive Date Coverage",
        subtitle="Target = all 20 teams",
        ref_line=20, ref_label="target = 20",
    )


def _render_constraints_nfl(reports: dict[str, MetricsReport], axes, present: list[str]) -> None:
    grouped_bars(
        axes[0], present,
        {k: [reports[k].thanksgiving_coverage, reports[k].thanksgiving_fixed_host_violations] for k in present},
        ["Teams playing\nThanksgiving", "Fixed-host\nviolations (DAL/DET)"],
        ylabel="Count",
        title="Thanksgiving Coverage",
        subtitle="Normally 6 teams (3 games); DAL/DET must host",
        ref_line=6, ref_label="target = 6",
    )
    grouped_bars(
        axes[1], present,
        {k: [reports[k].primetime_game_pct] for k in present},
        ["Primetime\nbroadcast share"],
        ylabel="% of season fixtures",
        title="Primetime Broadcast Share",
        subtitle="TNF/SNF/MNF designated slots, from calendar.json broadcast_windows",
    )


def _render_constraints_nba(reports: dict[str, MetricsReport], axes, present: list[str]) -> None:
    grouped_bars(
        axes[0], present,
        {k: [reports[k].league_back_to_backs] for k in present},
        ["League\nback-to-backs"],
        ylabel="Count",
        title="Back-to-Back Games",
        subtitle="1-day gap between consecutive fixtures for a team",
    )
    grouped_bars(
        axes[1], present,
        {k: [reports[k].four_in_five_violations] for k in present},
        ["4+ games in any\n5-night window"],
        ylabel="Violations",
        title="4-in-5 Violations (HC5)",
        subtitle="No team should play 4+ games in 5 consecutive nights",
        ref_line=0, ref_label="target = 0",
    )
    grouped_bars(
        axes[2], present,
        {k: [reports[k].all_star_break_violations] for k in present},
        ["Games during\nAll-Star break"],
        ylabel="Violations",
        title="All-Star Break Compliance (HC10)",
        subtitle="No games during that season's All-Star break window",
        ref_line=0, ref_label="target = 0",
    )


# Per-league constraint panel renderers — each takes (reports, axes, present)
# and fills in as many axes as it needs (EPL/NBA: 3, NFL: 2).
CONSTRAINT_PANELS = {
    "epl": (3, _render_constraints_epl),
    "nfl": (2, _render_constraints_nfl),
    "nba": (3, _render_constraints_nba),
}


def render_constraints(reports: dict[str, MetricsReport], out: Path, league: str = "epl") -> None:
    present = [k for k in ["Historical", "CP-SAT", "ILP", "Metaheuristic"] if k in reports]
    label = LEAGUE_LABELS.get(league, league.upper())
    n_panels, render_fn = CONSTRAINT_PANELS.get(league, CONSTRAINT_PANELS["epl"])

    fig, axes = plt.subplots(1, n_panels, figsize=(5 * n_panels, 5.5), facecolor="white")
    if n_panels == 1:
        axes = [axes]
    fig.suptitle(
        f"Constraint Violations vs. Historical — {label}",
        fontsize=14, fontweight="bold", color="#1e3a5f", y=1.01,
    )

    render_fn(reports, axes, present)

    fig.legend(
        handles=_legend_handles(present), loc="lower center",
        ncol=len(present), fontsize=9.5, frameon=False,
        bbox_to_anchor=(0.5, -0.06),
    )
    fig.tight_layout()
    _save(fig, out)


# ---------------------------------------------------------------------------
# Figure 4 — Day-of-week distribution
# ---------------------------------------------------------------------------

def render_dow(reports: dict[str, MetricsReport], out: Path, league: str = "epl") -> None:
    present = [k for k in ["Historical", "CP-SAT", "ILP", "Metaheuristic"] if k in reports]
    label = LEAGUE_LABELS.get(league, league.upper())

    fig, ax = plt.subplots(figsize=(13, 5.5), facecolor="white")
    fig.suptitle(
        f"Day-of-Week Fixture Distribution vs. Historical — {label}",
        fontsize=14, fontweight="bold", color="#1e3a5f", y=1.01,
    )

    x     = np.arange(len(DAYS_ORDER))
    n_ser = len(present)
    bar_w = 0.7 / n_ser

    for i, lbl in enumerate(present):
        vals    = [reports[lbl].day_of_week_pct.get(d, 0) for d in DAYS_ORDER]
        offsets = (i - (n_ser - 1) / 2) * bar_w
        bars = ax.bar(
            x + offsets, vals, bar_w,
            label=lbl,
            color=COLORS[lbl],
            hatch=HATCH[lbl],
            edgecolor="white",
            linewidth=0.5,
            alpha=0.88,
            zorder=3,
        )
        for bar, v in zip(bars, vals):
            if v > 0.5:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 0.3,
                    f"{v:.1f}%",
                    ha="center", va="bottom", fontsize=6.5, color="#374151", zorder=4,
                )

    ax.set_xticks(x)
    ax.set_xticklabels(DAY_ABBR, fontsize=11)
    ax.set_ylabel("% of season fixtures", fontsize=9)
    if league == "epl":
        ax.set_title(
            "Solvers under-weight Monday (needs TV rights logic); ILP/MH over-weight midweek",
            fontsize=8.5, color=SUBTITLE_COLOR, style="italic", pad=4,
        )
    _polish(ax)
    ax.legend(fontsize=9.5, frameon=False, loc="upper right")

    # Monday annotation — only meaningful when both Historical and CP-SAT
    # are present, which isn't guaranteed for NFL/NBA (no wired main.py
    # CP-SAT entry point yet — see CLAUDE.md's "NFL / NBA support" section).
    if "Historical" in reports and "CP-SAT" in reports:
        mon_idx = DAYS_ORDER.index("Monday")
        hist_mon = reports["Historical"].day_of_week_pct.get("Monday", 0)
        ax.annotate(
            f"Historical: {hist_mon:.1f}%\nCP-SAT: {reports['CP-SAT'].day_of_week_pct.get('Monday', 0):.1f}%",
            xy=(mon_idx, hist_mon),
            xytext=(mon_idx + 1.1, hist_mon + 5),
            fontsize=8, color="#92400e",
            arrowprops=dict(arrowstyle="->", color="#92400e", lw=1.0),
        )

    fig.tight_layout()
    _save(fig, out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Solver accuracy visualizations")
    parser.add_argument(
        "--out-dir", default=str(ROOT / "output"),
        help="Directory to write PNG files (default: output/)",
    )
    parser.add_argument(
        "--league", default="epl", choices=["epl", "nfl", "nba"],
        help="League to visualize (default: epl)",
    )
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    league = args.league

    print(f"Loading reports ({league})…")
    reports = load_reports(league)
    print(f"  Loaded: {list(reports.keys())}\n")

    render_performance(reports, out_dir / _out_filename(league, "performance.png"), league)
    render_quality(reports,     out_dir / _out_filename(league, "quality.png"), league)
    render_constraints(reports, out_dir / _out_filename(league, "constraints.png"), league)
    render_dow(reports,         out_dir / _out_filename(league, "dow.png"), league)


if __name__ == "__main__":
    main()
