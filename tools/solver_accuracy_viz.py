"""
Solver accuracy visualization — all three solvers vs. historical EPL 2024-25.

Produces a multi-panel figure covering:
  1. Solver performance (penalty score, hard/soft violations)
  2. Schedule quality vs historical (rest days, consecutive runs, city clashes)
  3. Constraint violations (SC7, SC10, SC13, SC14)
  4. Day-of-week fixture distribution
  5. Festive & special-event coverage

Usage:
    python tools/solver_accuracy_viz.py
    python tools/solver_accuracy_viz.py --out output/solver_accuracy.png
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
import numpy as np

from analysis.historical_loader import load_season
from analysis.main import _load_generated_csv, _validate_generated
from analysis.metrics import compute, MetricsReport
from core.data_loader import load_teams


# ---------------------------------------------------------------------------
# Colour palette
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


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_reports() -> dict[str, MetricsReport]:
    teams = load_teams()
    reports: dict[str, MetricsReport] = {}

    hist = compute(load_season(str(ROOT / "data/leagues/epl/historical/2024-25.csv")))
    hist.label = "Historical"
    reports["Historical"] = hist

    for key, label in [("cp_sat", "CP-SAT"), ("ilp", "ILP"), ("metaheuristic", "Metaheuristic")]:
        p = ROOT / "output" / f"schedule_{key}.csv"
        if not p.exists():
            print(f"  [skip] {p.name} not found — run the solver first")
            continue
        sched = _load_generated_csv(str(p))
        meta  = _validate_generated(sched, teams)
        rep   = compute(sched, solver_meta=meta)
        rep.label = label
        reports[label] = rep

    return reports


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
    log_scale: bool = False,
    ref_line: float | None = None,
    ref_label: str = "",
) -> None:
    n_groups  = len(group_labels)
    n_series  = len(labels)
    bar_w     = 0.7 / n_series
    x         = np.arange(n_groups)

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
            if v is None or np.isnan(v):
                continue
            top = bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                top + (top * 0.04 if log_scale else max(top * 0.02, 0.3)),
                f"{v:,.0f}" if v >= 100 else f"{v:.1f}" if v < 10 else f"{int(v)}",
                ha="center", va="bottom", fontsize=6.0, color="#374151", zorder=4,
            )

    ax.set_xticks(x)
    ax.set_xticklabels(group_labels, fontsize=7.5)
    ax.set_ylabel(ylabel, fontsize=7.5)
    ax.set_title(title, fontsize=9, fontweight="bold", pad=5)
    ax.tick_params(axis="y", labelsize=7)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, linewidth=0.4, alpha=0.5)

    if log_scale:
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(matplotlib.ticker.ScalarFormatter())

    if ref_line is not None:
        ax.axhline(ref_line, color="#f59e0b", linewidth=1.2, linestyle="--", zorder=2)
        ax.text(
            n_groups - 0.5, ref_line,
            f" {ref_label}",
            va="bottom", ha="right", fontsize=6.5, color="#92400e",
        )


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(reports: dict[str, MetricsReport], out_path: Path) -> None:
    solvers   = [k for k in reports if k != "Historical"]
    all_keys  = ["Historical"] + solvers
    present   = [k for k in all_keys if k in reports]

    fig = plt.figure(figsize=(22, 26), facecolor="white")
    fig.suptitle(
        "EPL Solver Accuracy vs. Historical 2024-25 Season",
        fontsize=16, fontweight="bold", color="#1e3a5f", y=0.995,
    )

    # Grid: 5 rows, 3 cols
    from matplotlib.gridspec import GridSpec
    gs = GridSpec(
        5, 3, figure=fig,
        hspace=0.55, wspace=0.38,
        left=0.06, right=0.97, top=0.97, bottom=0.04,
    )

    # ── Legend strip (row 0, spanning all cols) ─────────────────────────────
    legend_ax = fig.add_subplot(gs[0, :])
    legend_ax.axis("off")
    handles = [
        mpatches.Patch(
            facecolor=COLORS[k], hatch=HATCH[k],
            edgecolor="white", label=k, alpha=0.88,
        )
        for k in present
    ]
    legend_ax.legend(
        handles=handles,
        loc="center", ncol=len(present),
        fontsize=10, frameon=False,
        handlelength=2.0, handleheight=1.4,
    )
    legend_ax.set_title(
        "Colour & hatch key — same across all panels below",
        fontsize=8.5, color="#6b7280", pad=2,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Panel A — Solver performance (penalty score, violations)
    # ─────────────────────────────────────────────────────────────────────────
    ax_pen  = fig.add_subplot(gs[1, 0])
    ax_viol = fig.add_subplot(gs[1, 1])
    ax_rest = fig.add_subplot(gs[1, 2])

    # Penalty score (solvers only — historical has no penalty)
    pen_keys  = [k for k in solvers if k in reports]
    pen_vals  = {k: [reports[k].penalty_score or 0] for k in pen_keys}
    grouped_bars(
        ax_pen, pen_keys,
        {k: [v[0]] for k, v in pen_vals.items()},
        ["Penalty Score"],
        ylabel="Score (log scale)",
        title="A. Optimisation Penalty Score\n(solver objective — lower is better)",
        log_scale=True,
    )

    # Hard + soft violations
    viol_groups = ["Hard Violations", "Soft Violations"]
    viol_data   = {
        k: [
            reports[k].hard_violations or 0,
            reports[k].soft_violations or 0,
        ]
        for k in pen_keys
    }
    grouped_bars(
        ax_viol, pen_keys, viol_data, viol_groups,
        ylabel="Count",
        title="B. Hard & Soft Constraint Violations\n(0 hard = feasible schedule)",
        ref_line=0, ref_label="hard target = 0",
    )
    ax_viol.set_ylim(bottom=-5)

    # Rest days
    rest_groups = ["Mean rest days", "Min rest days"]
    rest_data   = {
        k: [
            reports[k].rest_mean,
            reports[k].rest_min_global,
        ]
        for k in present
    }
    grouped_bars(
        ax_rest, present, rest_data, rest_groups,
        ylabel="Days",
        title="C. Rest Days vs. Historical\n(min ≥ 3 is the hard constraint)",
        ref_line=3, ref_label="HC1 min = 3",
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Panel B — Consecutive runs & city clashes
    # ─────────────────────────────────────────────────────────────────────────
    ax_consec = fig.add_subplot(gs[2, 0])
    ax_city   = fig.add_subplot(gs[2, 1])
    ax_derby  = fig.add_subplot(gs[2, 2])

    # Consecutive home/away
    consec_groups = ["Max consec. home", "Max consec. away", "Teams >3 consec. away"]
    consec_data   = {
        k: [
            reports[k].league_max_consec_home,
            reports[k].league_max_consec_away,
            len(reports[k].teams_over_3_away),
        ]
        for k in present
    }
    grouped_bars(
        ax_consec, present, consec_data, consec_groups,
        ylabel="Count",
        title="D. Consecutive Fixture Runs\n(SC1/SC2 soft target ≤ 3/5)",
        ref_line=3, ref_label="soft target = 3",
    )

    # City clashes — constraint_violations is a dict for solvers, empty list for historical
    def _cv(r: MetricsReport, key: str) -> int:
        cv = r.constraint_violations
        if isinstance(cv, dict):
            return cv.get(key, 0)
        return 0

    city_groups = ["Same-day clashes", "4-day window (SC7)"]
    city_data   = {
        k: [
            reports[k].city_clash_count,
            _cv(reports[k], "SC7"),
        ]
        for k in present
    }
    # Fill historical SC7 from the city_weekend_clash_count field
    if "Historical" in reports:
        city_data["Historical"][1] = reports["Historical"].city_weekend_clash_count
    grouped_bars(
        ax_city, present, city_data, city_groups,
        ylabel="Clash count",
        title="E. City Home Clashes\n(SC7: same-city clubs both home within 4 days)",
    )

    # Derby spacing
    derby_groups = ["Derbies gap <56 days"]
    derby_data   = {
        k: [len(reports[k].derbies_under_56d)]
        for k in present
    }
    grouped_bars(
        ax_derby, present, derby_data, derby_groups,
        ylabel="Derby pairs",
        title="F. Derby Leg Spacing (SC3)\n(gap <56 days between legs — lower is better)",
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Panel C — Atos Golden Rule violations (SC13, SC14, SC15)
    # ─────────────────────────────────────────────────────────────────────────
    ax_golden  = fig.add_subplot(gs[3, 0])
    ax_london  = fig.add_subplot(gs[3, 1])
    ax_festive = fig.add_subplot(gs[3, 2])

    golden_groups = ["SC13\n5-match H/A", "SC14\nSeason boundary", "SC15\nBoxDay/NYD pair"]
    golden_data   = {
        k: [
            reports[k].five_match_pattern_violations,
            reports[k].season_boundary_violations,
            _cv(reports[k], "SC15") if isinstance(reports[k].constraint_violations, dict)
                else reports[k].boxing_day_nyd_violations,
        ]
        for k in present
    }
    # Historical golden rule values come from the report fields directly
    if "Historical" in reports:
        h = reports["Historical"]
        golden_data["Historical"] = [
            h.five_match_pattern_violations,
            h.season_boundary_violations,
            h.boxing_day_nyd_violations,
        ]
    grouped_bars(
        ax_golden, present, golden_data, golden_groups,
        ylabel="Violations",
        title="G. Atos Golden Rules\n(SC13/SC14/SC15 — target = 0, but historical ≠ 0)",
    )
    # Annotate historical baseline callout
    ax_golden.text(
        0.02, 0.97,
        "Note: Real EPL averages 244 SC13 violations/season",
        transform=ax_golden.transAxes,
        fontsize=6.5, color="#7c3aed", va="top", fontstyle="italic",
    )

    # London cluster
    london_groups = ["SC10 violations\n(>3 London clubs home same day)"]
    london_data   = {
        k: [reports[k].london_cluster_violations]
        for k in present
    }
    grouped_bars(
        ax_london, present, london_data, london_groups,
        ylabel="Days with violation",
        title="H. London Cluster (SC10)\n(>3 London clubs home on same day)",
        ref_line=0, ref_label="target = 0",
    )
    ax_london.set_ylim(bottom=-0.3)

    # Festive coverage
    festive_groups = ["Boxing Day", "New Year's Day", "Good Friday", "Easter Monday"]
    festive_data   = {
        k: [
            reports[k].boxing_day_coverage,
            reports[k].new_years_day_coverage,
            reports[k].good_friday_coverage,
            reports[k].easter_monday_coverage,
        ]
        for k in present
    }
    grouped_bars(
        ax_festive, present, festive_data, festive_groups,
        ylabel="Teams playing",
        title="I. Festive Date Coverage\n(target = all 20 teams; historical CSVs lack festive data)",
        ref_line=20, ref_label="target = 20",
    )
    ax_festive.text(
        0.02, 0.97,
        "* Historical = 0 due to data-source gap, not actual absence of fixtures",
        transform=ax_festive.transAxes,
        fontsize=6.0, color="#6b7280", va="top", fontstyle="italic",
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Panel D — Day-of-week distribution
    # ─────────────────────────────────────────────────────────────────────────
    ax_dow = fig.add_subplot(gs[4, :])

    x      = np.arange(len(DAYS_ORDER))
    n_ser  = len(present)
    bar_w  = 0.7 / n_ser

    for i, lbl in enumerate(present):
        vals    = [reports[lbl].day_of_week_pct.get(d, 0) for d in DAYS_ORDER]
        offsets = (i - (n_ser - 1) / 2) * bar_w
        bars = ax_dow.bar(
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
            if v > 1:
                ax_dow.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 0.3,
                    f"{v:.1f}%",
                    ha="center", va="bottom", fontsize=5.8, color="#374151", zorder=4,
                )

    ax_dow.set_xticks(x)
    ax_dow.set_xticklabels(DAY_ABBR, fontsize=9)
    ax_dow.set_ylabel("% of fixtures", fontsize=8)
    ax_dow.set_title(
        "J. Day-of-Week Fixture Distribution vs. Historical\n"
        "(solvers under-weight Monday; ILP/MH over-weight midweek)",
        fontsize=9, fontweight="bold", pad=6,
    )
    ax_dow.tick_params(axis="y", labelsize=7.5)
    ax_dow.spines[["top", "right"]].set_visible(False)
    ax_dow.set_axisbelow(True)
    ax_dow.yaxis.grid(True, linewidth=0.4, alpha=0.5)
    ax_dow.legend(fontsize=8.5, frameon=False, loc="upper right")

    # Annotate Monday gap
    mon_idx = DAYS_ORDER.index("Monday")
    ax_dow.annotate(
        "Monday gap:\nReal EPL ~19.5%\nCP-SAT only 12.6%",
        xy=(mon_idx, 19.5), xytext=(mon_idx + 0.8, 25),
        fontsize=7, color="#92400e",
        arrowprops=dict(arrowstyle="->", color="#92400e", lw=1.0),
    )

    out_path.parent.mkdir(exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Saved: {out_path}  ({out_path.stat().st_size // 1024} KB)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Solver accuracy visualization")
    parser.add_argument(
        "--out", default=str(ROOT / "output" / "solver_accuracy.png"),
        help="Output PNG path",
    )
    args = parser.parse_args()

    print("Loading reports…")
    reports = load_reports()
    print(f"  Loaded: {list(reports.keys())}")

    render(reports, Path(args.out))


if __name__ == "__main__":
    main()
