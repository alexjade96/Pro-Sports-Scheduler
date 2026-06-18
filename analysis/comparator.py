"""
Comparator: takes two or more MetricsReports and produces a structured
comparison dict that the report renderer can display as tables.

Two modes:
  1. generated vs historical  — accuracy check against real schedule
  2. option A vs B vs C       — solver performance comparison

Accuracy deltas are signed: positive = generated is higher than historical.
"""
from __future__ import annotations
import statistics
from analysis.metrics import MetricsReport


# ---------------------------------------------------------------------------
# Accuracy comparison: one generated schedule vs one historical baseline
# ---------------------------------------------------------------------------

def compare_to_historical(
    generated: MetricsReport,
    historical: MetricsReport,
) -> dict:
    """
    Returns a structured dict of per-metric accuracy comparisons.
    Delta = generated_value - historical_value.
    """

    def delta(a, b):
        if a is None or b is None:
            return None
        return round(a - b, 2)

    def pct_delta(a, b):
        """Percentage-point delta."""
        return delta(a, b)

    def _hist_consec_home(r: MetricsReport) -> float:
        vals = list(r.max_consec_home_per_team.values())
        return round(statistics.mean(vals), 2) if vals else 0.0

    def _hist_consec_away(r: MetricsReport) -> float:
        vals = list(r.max_consec_away_per_team.values())
        return round(statistics.mean(vals), 2) if vals else 0.0

    def _avg_home_pct(r: MetricsReport) -> float:
        vals = list(r.home_pct_first_half.values()) + list(r.home_pct_second_half.values())
        return round(statistics.mean(vals), 2) if vals else 0.0

    rows = []

    rows.append({
        "metric":      "Total fixtures",
        "generated":   generated.total_fixtures,
        "historical":  historical.total_fixtures,
        "delta":       delta(generated.total_fixtures, historical.total_fixtures),
        "note":        "Should be 380 for a full EPL season",
    })

    rows.append({
        "metric":      "Mean rest days (league)",
        "generated":   generated.rest_mean,
        "historical":  historical.rest_mean,
        "delta":       delta(generated.rest_mean, historical.rest_mean),
        "note":        "Higher = more recovery time",
    })

    rows.append({
        "metric":      "Minimum rest days (global)",
        "generated":   generated.rest_min_global,
        "historical":  historical.rest_min_global,
        "delta":       delta(generated.rest_min_global, historical.rest_min_global),
        "note":        "Hard constraint requires ≥3",
    })

    rows.append({
        "metric":      "City home clashes",
        "generated":   generated.city_clash_count,
        "historical":  historical.city_clash_count,
        "delta":       delta(generated.city_clash_count, historical.city_clash_count),
        "note":        "Hard constraint: target = 0",
    })

    rows.append({
        "metric":      "Intl break violations",
        "generated":   generated.intl_break_violation_count,
        "historical":  historical.intl_break_violation_count,
        "delta":       delta(generated.intl_break_violation_count, historical.intl_break_violation_count),
        "note":        "Hard constraint: target = 0",
    })

    rows.append({
        "metric":      "League max consec. home (any team)",
        "generated":   generated.league_max_consec_home,
        "historical":  historical.league_max_consec_home,
        "delta":       delta(generated.league_max_consec_home, historical.league_max_consec_home),
        "note":        "Soft constraint: target ≤3",
    })

    rows.append({
        "metric":      "League max consec. away (any team)",
        "generated":   generated.league_max_consec_away,
        "historical":  historical.league_max_consec_away,
        "delta":       delta(generated.league_max_consec_away, historical.league_max_consec_away),
        "note":        "Soft constraint: target ≤3",
    })

    rows.append({
        "metric":      "Avg max consec. home across teams",
        "generated":   _hist_consec_home(generated),
        "historical":  _hist_consec_home(historical),
        "delta":       delta(_hist_consec_home(generated), _hist_consec_home(historical)),
        "note":        "",
    })

    rows.append({
        "metric":      "Teams exceeding 3 consec. away",
        "generated":   len(generated.teams_over_3_away),
        "historical":  len(historical.teams_over_3_away),
        "delta":       delta(len(generated.teams_over_3_away), len(historical.teams_over_3_away)),
        "note":        "Soft constraint SC1",
    })

    rows.append({
        "metric":      "Derbies with gap < 56 days",
        "generated":   len(generated.derbies_under_56d),
        "historical":  len(historical.derbies_under_56d),
        "delta":       delta(len(generated.derbies_under_56d), len(historical.derbies_under_56d)),
        "note":        "Soft constraint SC3",
    })

    rows.append({
        "metric":      "Boxing Day coverage (teams)",
        "generated":   generated.boxing_day_coverage,
        "historical":  historical.boxing_day_coverage,
        "delta":       delta(generated.boxing_day_coverage, historical.boxing_day_coverage),
        "note":        "Preference: target = 20",
    })

    rows.append({
        "metric":      "New Year's Day coverage (teams)",
        "generated":   generated.new_years_day_coverage,
        "historical":  historical.new_years_day_coverage,
        "delta":       delta(generated.new_years_day_coverage, historical.new_years_day_coverage),
        "note":        "Preference: target = 20",
    })

    rows.append({
        "metric":      "Avg home% across halves",
        "generated":   _avg_home_pct(generated),
        "historical":  _avg_home_pct(historical),
        "delta":       delta(_avg_home_pct(generated), _avg_home_pct(historical)),
        "note":        "Balance: target ≈50%",
    })

    # Day-of-week distribution comparison
    all_days = sorted(set(
        list(generated.day_of_week_counts.keys()) +
        list(historical.day_of_week_counts.keys())
    ))
    for day in all_days:
        rows.append({
            "metric":     f"  % fixtures on {day}",
            "generated":  generated.day_of_week_pct.get(day, 0.0),
            "historical": historical.day_of_week_pct.get(day, 0.0),
            "delta":      pct_delta(
                generated.day_of_week_pct.get(day, 0.0),
                historical.day_of_week_pct.get(day, 0.0),
            ),
            "note": "Distribution",
        })

    return {
        "type":       "historical_accuracy",
        "generated":  generated.label,
        "historical": historical.label,
        "rows":       rows,
    }


# ---------------------------------------------------------------------------
# Solver comparison: multiple generated MetricsReports side by side
# ---------------------------------------------------------------------------

def compare_solvers(reports: list[MetricsReport]) -> dict:
    """
    Produces a side-by-side comparison table for multiple generated
    schedules (one per solver option).
    """
    labels = [r.label for r in reports]

    def _row(metric: str, extractor, note: str = "") -> dict:
        return {
            "metric": metric,
            "values": {r.label: extractor(r) for r in reports},
            "note":   note,
        }

    rows = [
        _row("Penalty score",              lambda r: r.penalty_score,        "Lower is better"),
        _row("Hard violations",            lambda r: r.hard_violations,       "Must be 0"),
        _row("Soft violations",            lambda r: r.soft_violations,       "Lower is better"),
        _row("Solve time (s)",             lambda r: r.solve_time_seconds,    ""),
        _row("Mean rest days",             lambda r: r.rest_mean,             ""),
        _row("Min rest days (global)",     lambda r: r.rest_min_global,       "Hard constraint ≥3"),
        _row("City home clashes",          lambda r: r.city_clash_count,      "Hard constraint = 0"),
        _row("Intl break violations",      lambda r: r.intl_break_violation_count, "Hard constraint = 0"),
        _row("Max consec. home (league)",  lambda r: r.league_max_consec_home,"Soft ≤3"),
        _row("Max consec. away (league)",  lambda r: r.league_max_consec_away,"Soft ≤3"),
        _row("Teams >3 consec. away",      lambda r: len(r.teams_over_3_away),""),
        _row("Derbies gap <56 days",       lambda r: len(r.derbies_under_56d),""),
        _row("Boxing Day coverage",        lambda r: r.boxing_day_coverage,   "Target=20"),
        _row("New Year's Day coverage",    lambda r: r.new_years_day_coverage,"Target=20"),
    ]

    return {
        "type":   "solver_comparison",
        "labels": labels,
        "rows":   rows,
    }
