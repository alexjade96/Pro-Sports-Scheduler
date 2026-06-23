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
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

from analysis.historical_loader import available_seasons, load_season
from analysis.metrics import compute

# ── Style constants ────────────────────────────────────────────────────────────
SOLVER_COLORS = ["#0d6efd", "#198754", "#ffc107"]
HIST_COLOR    = "#6c757d"
BG_COLOR      = "#f4f6f9"
DAYS_FULL  = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAYS_SHORT = [d[:3] for d in DAYS_FULL]


def _set_style() -> None:
    plt.rcParams.update({
        "font.family":       "DejaVu Sans",
        "axes.spines.top":   False,
        "axes.spines.right": False,
        "axes.grid":         True,
        "grid.alpha":        0.3,
        "figure.facecolor":  BG_COLOR,
        "axes.facecolor":    BG_COLOR,
    })


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

    ax.plot(x_nums, values, color=HIST_COLOR, linewidth=2, linestyle="--",
            marker="o", markersize=4, label="Historical", zorder=2)

    if values:
        mean_val = sum(values) / len(values)
        ax.axhline(mean_val, color=HIST_COLOR, linewidth=0.8, linestyle=":", alpha=0.6)
        ax.text(len(x_nums) * 0.05, mean_val * 1.01, f"avg {mean_val:.1f}",
                fontsize=7, color=HIST_COLOR, va="bottom")

    for i, r in enumerate(gen_reports):
        val = _gen_value(r, metric_key)
        if val is not None:
            ax.scatter(gen_x, val, color=SOLVER_COLORS[i % len(SOLVER_COLORS)],
                       s=140, marker="^", zorder=5, label=r.label)

    ax.set_xticks(list(range(len(all_x))))
    ax.set_xticklabels(all_x, rotation=28, ha="right", fontsize=8)
    ax.set_ylabel(ylabel, fontsize=9)
    if ymin is not None:
        current_min = ax.get_ylim()[0]
        ax.set_ylim(bottom=min(current_min, ymin))
    ax.legend(fontsize=8, loc="upper left")


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
        ax.set_title(f"{ylabel} — EPL 10-season trend (2015–2025)",
                     fontsize=11, fontweight="bold", pad=10)
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

    fig, ax = plt.subplots(figsize=(7, 7), subplot_kw={"projection": "polar"})
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    legend_handles = []

    if avg:
        vals = _radar_score(avg) + [_radar_score(avg)[0]]
        ax.plot(angles, vals, color=HIST_COLOR, linewidth=2, linestyle="dashed")
        ax.fill(angles, vals, color=HIST_COLOR, alpha=0.12)
        legend_handles.append(mpatches.Patch(color=HIST_COLOR, alpha=0.7, label="10-Season Avg"))

    for i, r in enumerate(gen_reports):
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
        c = SOLVER_COLORS[i % len(SOLVER_COLORS)]
        ax.plot(angles, vals, color=c, linewidth=2)
        ax.fill(angles, vals, color=c, alpha=0.12)
        legend_handles.append(mpatches.Patch(color=c, label=r.label))

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(RLABELS, size=9)
    ax.set_ylim(0, 100)
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_yticklabels(["20", "40", "60", "80", "100"], size=7)
    ax.set_title("Schedule Quality Radar (100 = perfect)", pad=24, fontsize=12, fontweight="bold")
    ax.legend(handles=legend_handles, loc="lower right",
              bbox_to_anchor=(1.35, -0.12), fontsize=9)
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
    cmap = LinearSegmentedColormap.from_list("epl", [BG_COLOR, "#0d6efd"])
    ax.imshow(data, cmap=cmap, aspect="auto", vmin=0)
    ax.set_xticks(range(7))
    ax.set_xticklabels(DAYS_SHORT, fontsize=9)
    ax.set_yticks(range(len(kickoffs)))
    ax.set_yticklabels(kickoffs, fontsize=9)
    ax.set_title(title, fontsize=10, fontweight="bold", pad=8)
    mx = data.max() if data.max() > 0 else 1
    for ki in range(len(kickoffs)):
        for di in range(7):
            v = int(data[ki, di])
            if v > 0:
                col = "white" if data[ki, di] / mx > 0.5 else "#212529"
                ax.text(di, ki, str(v), ha="center", va="center", fontsize=8, color=col)


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

    _draw_heatmap_ax(axes[0], gen_matrix, gen_kickoffs, f"Generated ({gen_reports[0].label})")
    if has_hist:
        _draw_heatmap_ax(axes[1], hist_matrix, hist_kickoffs, "2024-25 Historical")

    fig.suptitle("Fixture Density: Day × Kickoff Time", fontsize=12, fontweight="bold", y=1.02)
    fig.tight_layout()
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

    col_labels = ["Team", "Min Rest", "Max\nConsec H", "Max\nConsec A", "H1 Home%", "H2 Home%"]
    rows_data, cell_colors = [], []

    for tid in teams:
        mr   = r.rest_min_per_team.get(tid, 0)
        mch  = r.max_consec_home_per_team.get(tid, 0)
        mca  = r.max_consec_away_per_team.get(tid, 0)
        h1   = round(r.home_pct_first_half.get(tid, 0), 1)
        h2   = round(r.home_pct_second_half.get(tid, 0), 1)
        rows_data.append([tid, mr, mch, mca, f"{h1}%", f"{h2}%"])

        def _c(ok, warn):
            if ok:   return "#d4edda"
            if warn: return "#fff3cd"
            return "#f8d7da"

        cell_colors.append([
            BG_COLOR,
            _c(mr >= 3, mr == 3),
            _c(mch <= 5, mch == 5),
            _c(mca <= 5, mca == 5),
            _c(abs(h1 - 50) <= 10, abs(h1 - 50) <= 15),
            _c(abs(h2 - 50) <= 10, abs(h2 - 50) <= 15),
        ])

    fig, ax = plt.subplots(figsize=(10, 0.45 * len(teams) + 1.5))
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    ax.axis("off")

    tbl = ax.table(cellText=rows_data, colLabels=col_labels,
                   cellColours=cell_colors, loc="center", cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1, 1.35)
    for (row, col), cell in tbl.get_celld().items():
        if row == 0:
            cell.set_facecolor("#1a1f2e")
            cell.set_text_props(color="white", fontweight="bold")
        cell.set_linewidth(0.5)

    ax.set_title(f"Per-Team Compliance Scorecard — {r.label}",
                 fontsize=11, fontweight="bold", pad=16)
    fig.tight_layout()
    dest = out_dir / "analytics_scorecard.png"
    fig.savefig(dest, dpi=150, bbox_inches="tight", facecolor=BG_COLOR)
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
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    fig.patch.set_facecolor(BG_COLOR)
    fig.suptitle("EPL Schedule Analytics — 10-Season Overview (2015–2025)",
                 fontsize=13, fontweight="bold", y=1.01)

    for ax, (mk, label, ymin) in zip(axes.flat, configs):
        ax.set_facecolor(BG_COLOR)
        _plot_trend(ax, hist_all, gen_reports, mk, label, ymin)
        ax.set_title(label, fontsize=10, fontweight="bold")

    fig.tight_layout()
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
