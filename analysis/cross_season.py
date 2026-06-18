"""
Cross-season analysis: loads every CSV in data/historical/, computes metrics
for each, then produces a comparison table showing consistency across seasons
and a target-range baseline for evaluating generated schedules.

Usage:
    python -m analysis.cross_season
    python -m analysis.cross_season --generated output/schedule_cp_sat.csv
"""
from __future__ import annotations

import argparse
import statistics
from pathlib import Path
from datetime import datetime

from analysis.historical_loader import load_season, available_seasons
from analysis.metrics import compute, MetricsReport
from analysis.report import save, OUTPUT_DIR

# ── simple table helpers ────────────────────────────────────────────────────

def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


def _col(v, w: int, align: str = ">") -> str:
    return f"{_fmt(v):{align}{w}}"


# ── per-metric row builders ─────────────────────────────────────────────────

def _row_values(reports: list[MetricsReport], extractor) -> list:
    return [extractor(r) for r in reports]


def _stats(values: list) -> tuple:
    nums = [v for v in values if v is not None]
    if not nums:
        return None, None, None
    return round(min(nums), 1), round(statistics.mean(nums), 1), round(max(nums), 1)


# ── main render functions ───────────────────────────────────────────────────

METRICS = [
    # (label, extractor, note)
    ("Total fixtures",            lambda r: r.total_fixtures,                   "Full season = 380"),
    ("Mean rest days",            lambda r: r.rest_mean,                        "Higher = more recovery"),
    ("Min rest days (global)",    lambda r: r.rest_min_global,                  "HC1 target ≥ 3"),
    ("Max rest days (global)",    lambda r: r.rest_max_global,                  ""),
    ("City home clashes",         lambda r: r.city_clash_count,                 "SC7 (was HC2) — soft"),
    ("Intl break violations",     lambda r: r.intl_break_violation_count,       "HC3 target = 0"),
    ("League max consec. home",   lambda r: r.league_max_consec_home,           "SC2 target ≤ 3"),
    ("League max consec. away",   lambda r: r.league_max_consec_away,           "SC1 target ≤ 3"),
    ("Teams >3 consec. away",     lambda r: len(r.teams_over_3_away),           "historical baseline"),
    ("Teams >3 consec. home",     lambda r: len(r.teams_over_3_home),           "historical baseline"),
    ("Teams >5 consec. away",     lambda r: len(r.teams_over_5_away),           "SC1 target (relaxed)"),
    ("Teams >5 consec. home",     lambda r: len(r.teams_over_5_home),           "SC2 target (relaxed)"),
    ("Derbies gap < 56 days",     lambda r: len(r.derbies_under_56d),           "SC3 violations"),
    ("London cluster days >3",    lambda r: r.london_cluster_violations,        "SC10 target = 0"),
    ("Christmas Day fixtures",    lambda r: r.christmas_day_violations,         "HC7 target = 0"),
    ("Good Friday coverage",      lambda r: r.good_friday_coverage,             "SC9 target = 20"),
    ("Easter Monday coverage",    lambda r: r.easter_monday_coverage,           "SC9 target = 20"),
    ("Boxing Day coverage",       lambda r: r.boxing_day_coverage,              "PR2 target = 20"),
    ("New Year's Day coverage",   lambda r: r.new_years_day_coverage,           "PR2 target = 20"),
    ("% Saturday fixtures",       lambda r: r.day_of_week_pct.get("Saturday",0),""),
    ("% Sunday fixtures",         lambda r: r.day_of_week_pct.get("Sunday", 0),""),
    ("% Monday fixtures",         lambda r: r.day_of_week_pct.get("Monday", 0),""),
    ("% midweek (Tue+Wed)",       lambda r: round(
                                      r.day_of_week_pct.get("Tuesday",  0) +
                                      r.day_of_week_pct.get("Wednesday",0), 1), ""),
    ("% 15:00 kickoffs",          lambda r: r.kickoff_time_pct.get("15:00",0),  "Typical broadcast"),
]


def render_cross_season(
    reports: list[MetricsReport],
    generated: MetricsReport | None = None,
) -> str:
    labels   = [r.label for r in reports]
    col_w    = 9
    metric_w = 30
    note_w   = 24

    n_data_cols = len(labels) + 3   # seasons + min/mean/max
    if generated:
        n_data_cols += 2            # + generated + delta-to-mean

    # ── header ──
    header = f"{'Metric':<{metric_w}}"
    for lbl in labels:
        header += _col(lbl[:col_w], col_w)
    header += _col("MIN",  col_w)
    header += _col("MEAN", col_w)
    header += _col("MAX",  col_w)
    if generated:
        header += _col("GENERATED", col_w + 1)
        header += _col("Δ MEAN",    col_w)
    header += f"  {'Note':<{note_w}}"

    sep = "─" * (metric_w + n_data_cols * col_w + note_w + 4)

    lines = [
        "",
        "═" * len(sep),
        "CROSS-SEASON HISTORICAL ANALYSIS",
        f"Seasons: {', '.join(labels)}",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M}",
        "═" * len(sep),
        header,
        sep,
    ]

    target_ranges: list[dict] = []

    for label, extractor, note in METRICS:
        values  = _row_values(reports, extractor)
        mn, mean, mx = _stats(values)

        row = f"{label:<{metric_w}}"
        for v in values:
            row += _col(v, col_w)
        row += _col(mn,   col_w)
        row += _col(mean, col_w)
        row += _col(mx,   col_w)

        if generated:
            gen_val = extractor(generated)
            if gen_val is not None and mean is not None:
                delta = round(gen_val - mean, 1)
                delta_str = f"{delta:+.1f}" if isinstance(delta, float) else f"{delta:+d}"
            else:
                gen_val   = None
                delta_str = "—"
            row += _col(gen_val,   col_w + 1)
            row += _col(delta_str, col_w)

        row += f"  {note:<{note_w}}"
        lines.append(row)

        target_ranges.append({
            "metric": label,
            "min":    mn,
            "mean":   mean,
            "max":    mx,
            "note":   note,
        })

    lines.append("═" * len(sep))

    # ── target range summary ──
    lines += [
        "",
        "TARGET RANGE SUMMARY (derived from historical baseline)",
        "─" * 60,
        f"{'Metric':<30} {'Target / Range':<25} {'Note'}",
        "─" * 60,
    ]
    for t in target_ranges:
        if t["min"] is None:
            continue
        range_str = f"{t['min']} – {t['max']}  (mean {t['mean']})"
        lines.append(f"{t['metric']:<30} {range_str:<25} {t['note']}")
    lines.append("")

    return "\n".join(lines)


def render_consistency_note(reports: list[MetricsReport]) -> str:
    """Flags metrics that vary a lot across seasons (high coefficient of variation)."""
    lines = [
        "",
        "CONSISTENCY CHECK (metrics with high cross-season variance)",
        "─" * 60,
    ]
    for label, extractor, note in METRICS:
        values = [extractor(r) for r in reports if extractor(r) is not None]
        if len(values) < 2:
            continue
        try:
            mean = statistics.mean(values)
            stdev = statistics.stdev(values)
            cv = (stdev / mean * 100) if mean else 0
        except Exception:
            continue
        if cv > 20:
            lines.append(f"  ⚑  {label:<30} CV={cv:.0f}%  (stdev={stdev:.1f}, mean={mean:.1f})")
    lines.append("")
    return "\n".join(lines)


# ── entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Cross-season EPL analysis")
    parser.add_argument("--generated", type=str, default=None,
                        help="Path to a generated schedule CSV to compare against baseline")
    args = parser.parse_args()

    csv_files = available_seasons()
    if not csv_files:
        print("No historical CSVs found in data/historical/. Run generate_synthetic.py first.")
        return

    print(f"Loading {len(csv_files)} season(s) ...")
    reports: list[MetricsReport] = []
    for path in csv_files:
        schedule = load_season(path)
        report   = compute(schedule)
        reports.append(report)
        print(f"  {report.label:12s}  {report.total_fixtures:3d} fixtures  "
              f"rest_mean={report.rest_mean:.1f}  city_clashes={report.city_clash_count}")

    gen_report: MetricsReport | None = None
    if args.generated:
        from analysis.main import _load_generated_csv
        gen_schedule = _load_generated_csv(args.generated)
        gen_report   = compute(gen_schedule)
        print(f"\nGenerated schedule: {gen_report.label} ({gen_report.total_fixtures} fixtures)")

    table = render_cross_season(reports, gen_report)
    print(table)

    consistency = render_consistency_note(reports)
    print(consistency)

    full_text = table + consistency
    saved = save(full_text, "report_cross_season.txt")
    print(f"Report saved to {saved}")


if __name__ == "__main__":
    main()
