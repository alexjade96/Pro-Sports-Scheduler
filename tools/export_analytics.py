#!/usr/bin/env python3
"""
Export analytics charts as PNG files using matplotlib.

Generates in <out-dir> (default: samples/analytics/):
  analytics_trend_rest.png      — 10-season rest days trend + solver points
  analytics_trend_clashes.png   — 10-season SC7 city-clash trend
  analytics_trend_boxing.png    — Boxing Day coverage trend
  analytics_trend_sc13.png      — SC13 violations trend
  analytics_radar.png           — Quality radar (6 dimensions)
  analytics_heatmap.png         — Fixture density heatmap (gen vs historical)
  analytics_scorecard.png       — Per-team compliance table
  analytics_overview.png        — 2×2 combined trend overview

Usage:
    python tools/export_analytics.py [--out-dir samples/analytics]
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime as _dt
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

from analysis.historical_loader import available_seasons, load_season
from analysis.metrics import compute

# ── Style constants ─────────────────────────────────────────────────────────
# Validated categorical palette (dataviz skill reference instance, light mode).
# Colors are assigned to solver IDENTITY, not position — a chart missing one
# solver never repaints the others (run tools/... with any subset present and
# CP-SAT is always blue, ILP always aqua, Metaheuristic always yellow).
SOLVER_COLOR_MAP = {
    "CP-SAT":        "#2a78d6",   # categorical slot 1 — blue
    "ILP":           "#1baf7a",   # categorical slot 2 — aqua
    "Metaheuristic": "#eda100",   # categorical slot 3 — yellow
}
SOLVER_ORDER = ["CP-SAT", "ILP", "Metaheuristic"]

# Chart chrome & ink (reference palette, light surface)
BG_COLOR      = "#fcfcfb"   # chart surface
INK_PRIMARY   = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED     = "#898781"
GRIDLINE      = "#e1e0d9"
BASELINE      = "#c3c2b7"
HIST_COLOR    = INK_SECONDARY   # historical is a reference line, not an identity

# Status palette (fixed — scorecard cell states only, never a series color)
STATUS_GOOD     = "#0ca30c"
STATUS_WARNING  = "#fab219"
STATUS_CRITICAL = "#d03b3b"

# Sequential ramp anchors for the heatmap (blue, one hue light→dark)
SEQ_MID  = "#2a78d6"   # step 450
SEQ_HIGH = "#0d366b"   # step 700

DAYS_FULL  = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAYS_SHORT = [d[:3] for d in DAYS_FULL]


def _solver_color(label: str) -> str:
    return SOLVER_COLOR_MAP.get(label, INK_MUTED)


def _tint(hex_color: str, toward: str = "#ffffff", amount: float = 0.82) -> str:
    """Blend hex_color toward `toward` by `amount` (0=no change, 1=pure `toward`)."""
    c = hex_color.lstrip("#")
    t = toward.lstrip("#")
    cr, cg, cb = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    tr, tg, tb = int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)
    r = round(cr + (tr - cr) * amount)
    g = round(cg + (tg - cg) * amount)
    b = round(cb + (tb - cb) * amount)
    return f"#{r:02x}{g:02x}{b:02x}"


def _set_style() -> None:
    plt.rcParams.update({
        "font.family":        "DejaVu Sans",
        "text.color":         INK_PRIMARY,
        "axes.edgecolor":     BASELINE,
        "axes.labelcolor":    INK_SECONDARY,
        "axes.spines.top":    False,
        "axes.spines.right":  False,
        "axes.spines.left":   False,
        "axes.grid":          True,
        "axes.grid.axis":     "y",
        "axes.axisbelow":     True,
        "grid.color":         GRIDLINE,
        "grid.linewidth":     0.8,
        "grid.alpha":         1.0,
        "xtick.color":        INK_MUTED,
        "ytick.color":        INK_MUTED,
        "xtick.labelsize":    8.5,
        "ytick.labelsize":    8.5,
        "figure.facecolor":   BG_COLOR,
        "axes.facecolor":     BG_COLOR,
        "legend.frameon":     False,
        "legend.labelcolor":  INK_SECONDARY,
    })


def _title(ax, text: str, fontsize: float = 11, pad: float = 18,
           accent: str = SEQ_MID, rule: bool = True):
    """Left-aligned bold title with a short accent rule at the axes' top edge,
    riding *below* the title text (which floats above via `pad`). `rule=False`
    for off-axis (table) layouts, where the axes box isn't a reliable anchor.
    Returns the title Text artist (callers may need its rendered bbox)."""
    title_artist = ax.set_title(text, fontsize=fontsize, fontweight="bold",
                                color=INK_PRIMARY, loc="left", pad=pad)
    if rule:
        ax.plot([0, 0.045], [1.006, 1.006], transform=ax.transAxes,
                color=accent, linewidth=3, solid_capstyle="round", clip_on=False, zorder=10)
    return title_artist


# ── Data loaders ───────────────────────────────────────────────────────────────

def load_hist_all() -> list[dict]:
    results = []
    for path in sorted(available_seasons()):
        if path.suffix != ".csv":
            continue
        try:
            sched = load_season(str(path))
            r = compute(sched)
            results.append({
                "season":                       r.label,
                "rest_mean":                    r.rest_mean,
                "city_weekend_clash_count":     r.city_weekend_clash_count,
                "league_max_consec_away":       r.league_max_consec_away,
                "five_match_pattern_violations":r.five_match_pattern_violations,
                "season_boundary_violations":   r.season_boundary_violations,
                "boxing_day_coverage":          r.boxing_day_coverage,
                "new_years_day_coverage":       r.new_years_day_coverage,
                "derbies_under_56d":            len(r.derbies_under_56d),
                "teams_over_5_home":            len(r.teams_over_5_home),
                "teams_over_5_away":            len(r.teams_over_5_away),
            })
        except Exception as e:
            print(f"  [warn] hist {path.stem}: {e}")
    return results


def load_gen_reports() -> list:
    from analysis.main import _load_generated_csv, _validate_generated
    from core.data_loader import load_teams
    teams = load_teams()
    output_dir = ROOT / "output"
    reports = []
    for key, label in [("cp_sat", "CP-SAT"), ("ilp", "ILP"), ("metaheuristic", "Metaheuristic")]:
        p = output_dir / f"schedule_{key}.csv"
        if not p.exists():
            continue
        try:
            sched = _load_generated_csv(str(p))
            meta  = _validate_generated(sched, teams)
            r     = compute(sched, solver_meta=meta)
            r.label = label
            reports.append(r)
        except Exception as e:
            print(f"  [warn] gen {key}: {e}")
    return reports


def _gen_value(r, key: str):
    """Retrieve a metric from a MetricsReport using the hist_all key names."""
    mapping = {
        "rest_mean":                    r.rest_mean,
        "city_weekend_clash_count":     r.city_weekend_clash_count,
        "league_max_consec_away":       r.league_max_consec_away,
        "five_match_pattern_violations":r.five_match_pattern_violations,
        "season_boundary_violations":   r.season_boundary_violations,
        "boxing_day_coverage":          r.boxing_day_coverage,
    }
    return mapping.get(key)


def _date_from_str(s: str):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return _dt.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(s)


# ── Individual trend chart ─────────────────────────────────────────────────────

def _plot_trend(ax, hist_all, gen_reports, metric_key, ylabel, ymin=None) -> None:
    seasons = [h["season"] for h in hist_all]
    values  = [h[metric_key] for h in hist_all]
    x_nums  = list(range(len(seasons)))
    gen_x   = len(seasons)
    all_x   = seasons + ["2025/26"]

    ax.plot(x_nums, values, color=HIST_COLOR, linewidth=1.75, linestyle="--",
            dashes=(4, 2), solid_capstyle="round", marker="o", markersize=4.5,
            markerfacecolor=HIST_COLOR, markeredgewidth=0, label="Historical", zorder=2)

    if values:
        mean_val = sum(values) / len(values)
        ax.axhline(mean_val, color=INK_MUTED, linewidth=0.8, linestyle=":", zorder=1)
        # Anchored on the *last historical point* (not the legend's upper-left
        # corner) so it never collides with the legend regardless of scale.
        ax.text(len(x_nums) - 1, mean_val, f"avg {mean_val:.1f}  ", fontsize=7.5,
                color=INK_MUTED, va="bottom", ha="right", style="italic")

    # Plot in fixed solver order so the legend never reshuffles by value; stack
    # direct labels by value RANK (not plot order) so close points don't collide.
    present = [(r, _gen_value(r, metric_key)) for r in gen_reports]
    present = [(r, v) for r, v in present if v is not None]
    rank_of = {
        id(r): i for i, (r, _) in enumerate(sorted(present, key=lambda p: p[1]))
    }
    mid_rank = (len(present) - 1) / 2 if present else 0

    for r, val in sorted(present, key=lambda p: SOLVER_ORDER.index(p[0].label)
                          if p[0].label in SOLVER_ORDER else 99):
        color = _solver_color(r.label)
        ax.scatter(gen_x, val, color=color, s=110, marker="o", zorder=5,
                   edgecolors=BG_COLOR, linewidths=1.5, label=r.label)
        # Direct label — required relief for the sub-3:1-contrast palette slots.
        # Vertically staggered by value rank so close values stack legibly
        # instead of overprinting each other.
        y_off = (rank_of[id(r)] - mid_rank) * 12
        ax.annotate(f"{val:.3g}", (gen_x, val), xytext=(10, y_off),
                    textcoords="offset points", fontsize=7.8, color=color,
                    fontweight="bold", va="center", ha="left")

    ax.set_xticks(list(range(len(all_x))))
    ax.set_xticklabels(all_x, rotation=28, ha="right", fontsize=8)
    ax.set_ylabel(ylabel, fontsize=9, color=INK_SECONDARY)
    ax.set_xlim(right=len(all_x) - 1 + 0.55)
    if ymin is not None:
        current_min = ax.get_ylim()[0]
        ax.set_ylim(bottom=min(current_min, ymin))
    # "best" (not a hardcoded corner) — a fixed corner can land squarely on
    # the historical line itself when a metric's early seasons happen to
    # cluster near that corner's data range (e.g. SC13's 2015-19 seasons
    # sit right at the top-left on that chart's scale).
    ax.legend(fontsize=8, loc="best", handletextpad=0.5)


def export_trend_charts(hist_all, gen_reports, out_dir: Path) -> list[Path]:
    _set_style()
    configs = [
        ("rest_mean",                   "analytics_trend_rest.png",    "Mean rest days (all teams)",     10),
        ("city_weekend_clash_count",    "analytics_trend_clashes.png", "SC7 same-city clashes (4-day)",  0),
        ("boxing_day_coverage",         "analytics_trend_boxing.png",  "Boxing Day coverage (# teams)",  0),
        ("five_match_pattern_violations","analytics_trend_sc13.png",   "SC13 five-match H/A violations", 0),
    ]
    saved = []
    for metric_key, filename, ylabel, ymin in configs:
        fig, ax = plt.subplots(figsize=(9, 4.5))
        fig.patch.set_facecolor(BG_COLOR)
        ax.set_facecolor(BG_COLOR)
        _plot_trend(ax, hist_all, gen_reports, metric_key, ylabel, ymin)
        _title(ax, f"{ylabel} — EPL 10-season trend (2015–2025)", fontsize=12, pad=16)
        fig.tight_layout()
        dest = out_dir / filename
        fig.savefig(dest, dpi=150, bbox_inches="tight", facecolor=BG_COLOR)
        plt.close(fig)
        saved.append(dest)
        print(f"  → {filename}")
    return saved


# ── Radar chart ────────────────────────────────────────────────────────────────

def _radar_score(d: dict) -> list[float]:
    return [
        round(min(d.get("rest_mean", 0) * 5.0, 100), 1),
        round(max(0, 100 - d.get("city_weekend_clash_count", 0) * 1.2), 1),
        round(max(0, 100 - (d.get("teams_over_5_home", 0) + d.get("teams_over_5_away", 0)) * 5), 1),
        round((d.get("boxing_day_coverage", 0) + d.get("new_years_day_coverage", 0)) / 40 * 100, 1),
        round(max(0, 100 - d.get("five_match_pattern_violations", 0) * 0.3), 1),
        round(max(0, 100 - d.get("derbies_under_56d", 0) * 12), 1),
    ]


def export_radar(hist_all, gen_reports, out_dir: Path) -> Path:
    _set_style()
    RLABELS = ["Rest\nQuality", "City\nSeparation", "Run\nControl",
               "Festive\nCoverage", "SC13\nCompliance", "Derby\nSpacing"]
    N = len(RLABELS)
    angles = [n / N * 2 * np.pi for n in range(N)] + [0]

    n = len(hist_all)
    avg = {}
    if n:
        for k in ["rest_mean", "city_weekend_clash_count", "teams_over_5_home", "teams_over_5_away",
                  "boxing_day_coverage", "new_years_day_coverage", "five_match_pattern_violations", "derbies_under_56d"]:
            avg[k] = sum(h.get(k, 0) for h in hist_all) / n

    fig, ax = plt.subplots(figsize=(7.5, 7.5), subplot_kw={"projection": "polar"})
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.spines["polar"].set_color(BASELINE)
    ax.spines["polar"].set_linewidth(0.8)
    ax.grid(color=GRIDLINE, linewidth=0.8)
    ax.tick_params(colors=INK_MUTED)

    legend_handles = []

    if avg:
        vals = _radar_score(avg) + [_radar_score(avg)[0]]
        ax.plot(angles, vals, color=HIST_COLOR, linewidth=1.75, linestyle="dashed",
                dashes=(4, 2), solid_capstyle="round", zorder=3)
        ax.fill(angles, vals, color=HIST_COLOR, alpha=0.08, zorder=1)
        legend_handles.append(mpatches.Patch(facecolor=HIST_COLOR, alpha=0.6, label="10-Season Avg"))

    ordered_reports = sorted(
        gen_reports,
        key=lambda r: SOLVER_ORDER.index(r.label) if r.label in SOLVER_ORDER else 99,
    )
    for r in ordered_reports:
        rd = {
            "rest_mean":                    r.rest_mean,
            "city_weekend_clash_count":     r.city_weekend_clash_count,
            "teams_over_5_home":            len(r.teams_over_5_home),
            "teams_over_5_away":            len(r.teams_over_5_away),
            "boxing_day_coverage":          r.boxing_day_coverage,
            "new_years_day_coverage":       r.new_years_day_coverage,
            "five_match_pattern_violations":r.five_match_pattern_violations,
            "derbies_under_56d":            len(r.derbies_under_56d),
        }
        vals = _radar_score(rd) + [_radar_score(rd)[0]]
        c = _solver_color(r.label)
        ax.plot(angles, vals, color=c, linewidth=2.25, solid_capstyle="round", zorder=4)
        ax.plot(angles, vals, "o", color=c, markersize=5,
                markerfacecolor=BG_COLOR, markeredgewidth=1.75, zorder=5)
        ax.fill(angles, vals, color=c, alpha=0.10, zorder=2)
        legend_handles.append(mpatches.Patch(facecolor=c, label=r.label))

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(RLABELS, size=9.5, color=INK_SECONDARY)
    ax.set_ylim(0, 100)
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_yticklabels(["20", "40", "60", "80", "100"], size=7, color=INK_MUTED)
    ax.set_title("Schedule Quality Radar", loc="left", pad=28, fontsize=13,
                 fontweight="bold", color=INK_PRIMARY)
    ax.text(0.0, 1.065, "100 = perfect across all six dimensions", transform=ax.transAxes,
            fontsize=9, color=INK_MUTED, ha="left")
    ax.plot([0.0, 0.09], [1.10, 1.10], transform=ax.transAxes, color=SEQ_MID,
            linewidth=3, solid_capstyle="round", clip_on=False, zorder=10)
    ax.legend(handles=legend_handles, loc="lower right",
              bbox_to_anchor=(1.38, -0.08), fontsize=9.5)
    fig.tight_layout()
    dest = out_dir / "analytics_radar.png"
    fig.savefig(dest, dpi=150, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    print("  → analytics_radar.png")
    return dest


# ── Fixture density heatmap ────────────────────────────────────────────────────

def _build_matrix(rows_iter, day_col="day", ko_col="kickoff") -> tuple[list, list]:
    counts: dict[str, dict[str, int]] = {d: {} for d in DAYS_FULL}
    for row in rows_iter:
        day = row.get(day_col, "")
        ko  = row.get(ko_col, "")
        if day in counts and ko:
            counts[day][ko] = counts[day].get(ko, 0) + 1
    kickoffs = sorted({ko for dc in counts.values() for ko in dc})
    matrix   = [[counts[day].get(ko, 0) for ko in kickoffs] for day in DAYS_FULL]
    return matrix, kickoffs


def _build_hist_matrix(hist_path: Path) -> tuple[list, list]:
    counts: dict[str, dict[str, int]] = {d: {} for d in DAYS_FULL}
    with open(hist_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if not row.get("Date"):
                continue
            try:
                d = _date_from_str(row["Date"])
            except ValueError:
                continue
            day_name = d.strftime("%A")
            ko = (row.get("Time") or "15:00").strip() or "15:00"
            if day_name in counts and ko:
                counts[day_name][ko] = counts[day_name].get(ko, 0) + 1
    kickoffs = sorted({ko for dc in counts.values() for ko in dc})
    matrix   = [[counts[day].get(ko, 0) for ko in kickoffs] for day in DAYS_FULL]
    return matrix, kickoffs


def _draw_heatmap_ax(ax, matrix, kickoffs, title):
    data = np.array(matrix, dtype=float).T  # (n_kickoffs, 7)
    # Sequential ramp: one hue, light → dark (surface fades to "near zero").
    cmap = LinearSegmentedColormap.from_list("seq_blue", [BG_COLOR, SEQ_MID, SEQ_HIGH])
    im = ax.imshow(data, cmap=cmap, aspect="auto", vmin=0, interpolation="nearest")

    # 2px surface-colored gap between cells (mark spec) via a minor-tick grid.
    ax.set_xticks(np.arange(-0.5, 7, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(kickoffs), 1), minor=True)
    ax.grid(which="minor", color=BG_COLOR, linewidth=2.5)
    ax.grid(which="major", visible=False)
    ax.tick_params(which="minor", length=0)

    ax.set_xticks(range(7))
    ax.set_xticklabels(DAYS_SHORT, fontsize=9, color=INK_SECONDARY)
    ax.set_yticks(range(len(kickoffs)))
    ax.set_yticklabels(kickoffs, fontsize=9, color=INK_SECONDARY)
    ax.tick_params(which="major", length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title(title, fontsize=10.5, fontweight="bold", color=INK_PRIMARY,
                 loc="left", pad=10)

    mx = data.max() if data.max() > 0 else 1
    for ki in range(len(kickoffs)):
        for di in range(7):
            v = int(data[ki, di])
            if v > 0:
                col = BG_COLOR if data[ki, di] / mx > 0.55 else INK_PRIMARY
                ax.text(di, ki, str(v), ha="center", va="center", fontsize=8, color=col)
    return im


def export_heatmap(gen_reports, out_dir: Path) -> Path | None:
    _set_style()
    if not gen_reports:
        return None

    label_map = {"CP-SAT": "cp_sat", "ILP": "ilp", "Metaheuristic": "metaheuristic"}
    key = label_map.get(gen_reports[0].label)
    if not key:
        return None
    gen_csv = ROOT / "output" / f"schedule_{key}.csv"
    if not gen_csv.exists():
        return None

    with open(gen_csv, newline="") as f:
        gen_rows = list(csv.DictReader(f))
    gen_matrix, gen_kickoffs = _build_matrix(gen_rows)

    hist24 = ROOT / "data/leagues/epl/historical/2024-25.csv"
    has_hist = hist24.exists()
    if has_hist:
        hist_matrix, hist_kickoffs = _build_hist_matrix(hist24)

    ncols = 2 if has_hist else 1
    fig, axes = plt.subplots(1, ncols, figsize=(7 * ncols, 5.5))
    fig.patch.set_facecolor(BG_COLOR)
    if ncols == 1:
        axes = [axes]
    for ax in axes:
        ax.set_facecolor(BG_COLOR)

    im = _draw_heatmap_ax(axes[0], gen_matrix, gen_kickoffs, f"Generated ({gen_reports[0].label})")
    if has_hist:
        _draw_heatmap_ax(axes[1], hist_matrix, hist_kickoffs, "2024-25 Historical")

    cbar = fig.colorbar(im, ax=axes, orientation="horizontal", fraction=0.04,
                        pad=0.14, aspect=40, shrink=0.5)
    cbar.set_label("Fixtures", fontsize=8.5, color=INK_MUTED)
    cbar.ax.tick_params(labelsize=7.5, colors=INK_MUTED, length=0)
    cbar.outline.set_visible(False)

    fig.suptitle("Fixture Density: Day × Kickoff Time", fontsize=13, fontweight="bold",
                 color=INK_PRIMARY, x=0.02, ha="left", y=1.04)
    fig.text(0.02, 0.985, "Where each solver clusters kickoffs vs. the real 2024-25 slate",
              fontsize=9, color=INK_MUTED, ha="left")
    dest = out_dir / "analytics_heatmap.png"
    fig.savefig(dest, dpi=150, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    print("  → analytics_heatmap.png")
    return dest


# ── Per-team compliance scorecard ──────────────────────────────────────────────

def export_scorecard(gen_reports, out_dir: Path) -> Path | None:
    if not gen_reports:
        return None
    _set_style()
    r      = gen_reports[0]
    teams  = sorted(r.rest_min_per_team.keys())

    TINT_GOOD     = _tint(STATUS_GOOD, BG_COLOR, 0.85)
    TINT_WARNING  = _tint(STATUS_WARNING, BG_COLOR, 0.78)
    TINT_CRITICAL = _tint(STATUS_CRITICAL, BG_COLOR, 0.85)
    STRIPE        = _tint(INK_MUTED, BG_COLOR, 0.94)

    col_labels = ["Team", "Min Rest", "Max\nConsec H", "Max\nConsec A", "H1 Home%", "H2 Home%"]
    rows_data, cell_colors = [], []

    for i, tid in enumerate(teams):
        mr   = r.rest_min_per_team.get(tid, 0)
        mch  = r.max_consec_home_per_team.get(tid, 0)
        mca  = r.max_consec_away_per_team.get(tid, 0)
        h1   = round(r.home_pct_first_half.get(tid, 0), 1)
        h2   = round(r.home_pct_second_half.get(tid, 0), 1)
        rows_data.append([tid, mr, mch, mca, f"{h1}%", f"{h2}%"])

        def _c(ok, warn):
            if ok:   return TINT_GOOD
            if warn: return TINT_WARNING
            return TINT_CRITICAL

        team_cell = STRIPE if i % 2 else BG_COLOR
        cell_colors.append([
            team_cell,
            _c(mr >= 3, mr == 3),
            _c(mch <= 5, mch == 5),
            _c(mca <= 5, mca == 5),
            _c(abs(h1 - 50) <= 10, abs(h1 - 50) <= 15),
            _c(abs(h2 - 50) <= 10, abs(h2 - 50) <= 15),
        ])

    # Figure height only needs to be "tall enough" — bbox_inches="tight" crops
    # the saved PNG to the real content extent, so we don't need to predict
    # matplotlib's table row height analytically.
    fig = plt.figure(figsize=(10, 0.4 * len(teams) + 3))
    fig.patch.set_facecolor(BG_COLOR)
    # Full-bleed axes (no default subplot margins) so the later inches-based
    # crop lines up with the real content instead of matplotlib's default
    # ~12%/11% left/bottom margins.
    ax = fig.add_axes((0.015, 0.02, 0.97, 0.96))
    ax.axis("off")
    ax.patch.set_alpha(0)

    tbl = ax.table(cellText=rows_data, colLabels=col_labels,
                   cellColours=cell_colors, loc="upper center", cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1, 1.4)
    for (row, col), cell in tbl.get_celld().items():
        cell.set_edgecolor(GRIDLINE)
        cell.set_linewidth(0.8)
        if row == 0:
            cell.set_facecolor(INK_PRIMARY)
            cell.set_text_props(color=BG_COLOR, fontweight="bold")
        else:
            cell.set_text_props(color=INK_PRIMARY)
            if col == 0:
                cell.set_text_props(color=INK_PRIMARY, fontweight="bold")

    title_artist = _title(ax, f"Per-Team Compliance Scorecard — {r.label}",
                          fontsize=13, pad=6, rule=False)

    # Measure the title's top edge and the table's bottom edge so the crop
    # (below) hugs the real content on both ends regardless of row count.
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    tbl_bbox_in   = tbl.get_window_extent(renderer).transformed(fig.dpi_scale_trans.inverted())
    title_bbox_in = title_artist.get_window_extent(renderer).transformed(fig.dpi_scale_trans.inverted())
    fig_h_in = fig.get_size_inches()[1]
    cap_y = (tbl_bbox_in.y0 - 0.32) / fig_h_in

    # Status-key caption — color is never the only signal (the value is
    # always visible), but a legend still removes any doubt about what each
    # tint means.
    for i, (color, text) in enumerate([
        (TINT_GOOD, "within target"), (TINT_WARNING, "borderline"), (TINT_CRITICAL, "needs attention"),
    ]):
        x0 = 0.30 + i * 0.16
        fig.patches.append(mpatches.Rectangle((x0, cap_y - 0.011), 0.018, 0.022,
                                              transform=fig.transFigure, facecolor=color,
                                              edgecolor=GRIDLINE, linewidth=0.6, figure=fig))
        fig.text(x0 + 0.026, cap_y, text, fontsize=8.5, color=INK_SECONDARY, va="center", ha="left")

    # `bbox_inches="tight"` auto-detection includes the (invisible) axes'
    # full default extent, not just the drawn content, so crop explicitly to
    # the real content span instead: title top .. caption bottom.
    from matplotlib.transforms import Bbox
    fig_w_in = fig.get_size_inches()[0]
    crop = Bbox.from_extents(
        0.0, cap_y * fig_h_in - 0.30,
        fig_w_in, title_bbox_in.y1 + 0.18,
    )
    dest = out_dir / "analytics_scorecard.png"
    fig.savefig(dest, dpi=150, bbox_inches=crop, facecolor=BG_COLOR)
    plt.close(fig)
    print("  → analytics_scorecard.png")
    return dest


# ── 2×2 combined overview ──────────────────────────────────────────────────────

def export_overview(hist_all, gen_reports, out_dir: Path) -> Path:
    _set_style()
    configs = [
        ("rest_mean",                   "Mean Rest Days",             10),
        ("city_weekend_clash_count",    "SC7 City Clashes (4-day)",   0),
        ("boxing_day_coverage",         "Boxing Day Coverage",        0),
        ("five_match_pattern_violations","SC13 H/A Violations",       0),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(14, 9.5))
    fig.patch.set_facecolor(BG_COLOR)
    fig.suptitle("EPL Schedule Analytics — 10-Season Overview (2015–2025)",
                 fontsize=15, fontweight="bold", color=INK_PRIMARY, x=0.015, ha="left", y=1.015)
    fig.text(0.015, 0.975, "Historical baseline vs. each solver's 2025/26 output",
             fontsize=10, color=INK_MUTED, ha="left")

    for ax, (mk, label, ymin) in zip(axes.flat, configs):
        ax.set_facecolor(BG_COLOR)
        _plot_trend(ax, hist_all, gen_reports, mk, label, ymin)
        _title(ax, label, fontsize=11, pad=14)

    fig.tight_layout(rect=(0, 0, 1, 0.955))
    dest = out_dir / "analytics_overview.png"
    fig.savefig(dest, dpi=150, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close(fig)
    print("  → analytics_overview.png")
    return dest


# ── Main entry point ───────────────────────────────────────────────────────────

def main(out_dir: Path | None = None) -> list[Path]:
    if out_dir is None:
        out_dir = ROOT / "samples" / "analytics"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("[export] Loading 10 historical seasons…")
    hist_all = load_hist_all()
    print(f"[export] Loaded {len(hist_all)} seasons")

    print("[export] Loading generated solver reports…")
    gen_reports = load_gen_reports()
    print(f"[export] Found {len(gen_reports)} solver outputs")

    print("[export] Generating charts…")
    saved: list[Path] = []
    saved += export_trend_charts(hist_all, gen_reports, out_dir)
    saved.append(export_radar(hist_all, gen_reports, out_dir))
    hm = export_heatmap(gen_reports, out_dir)
    if hm:
        saved.append(hm)
    sc = export_scorecard(gen_reports, out_dir)
    if sc:
        saved.append(sc)
    saved.append(export_overview(hist_all, gen_reports, out_dir))

    saved = [p for p in saved if p is not None]
    print(f"[export] Done — {len(saved)} PNGs in {out_dir}/")
    return saved


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=str(ROOT / "samples" / "analytics"),
                    help="Output directory (default: samples/analytics/)")
    args = ap.parse_args()
    files = main(Path(args.out_dir))
    print(f"\nGenerated {len(files)} PNG files in {args.out_dir}")
