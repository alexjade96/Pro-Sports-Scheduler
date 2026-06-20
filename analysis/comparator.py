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

    # --- Golden Rules / Soft Constraint Comparison ---
    rows.append({
        "metric":      "── Soft Constraint Violations ──",
        "generated":   "", "historical":  "", "delta": None, "note": "",
    })

    rows.append({
        "metric":      "SC7 same-city home clashes (4d)",
        "generated":   generated.city_weekend_clash_count,
        "historical":  historical.city_weekend_clash_count,
        "delta":       delta(generated.city_weekend_clash_count, historical.city_weekend_clash_count),
        "note":        "Soft SC7: lower is better",
    })

    rows.append({
        "metric":      "SC10 London cluster violations",
        "generated":   generated.london_cluster_violations,
        "historical":  historical.london_cluster_violations,
        "delta":       delta(generated.london_cluster_violations, historical.london_cluster_violations),
        "note":        "Soft SC10: >3 London home/day",
    })

    rows.append({
        "metric":      "SC13 five-match H/A violations",
        "generated":   generated.five_match_pattern_violations,
        "historical":  historical.five_match_pattern_violations,
        "delta":       delta(generated.five_match_pattern_violations, historical.five_match_pattern_violations),
        "note":        "Atos Golden Rule: 2-3 home in 5 consec.",
    })

    rows.append({
        "metric":      "SC14 season boundary violations",
        "generated":   generated.season_boundary_violations,
        "historical":  historical.season_boundary_violations,
        "delta":       delta(generated.season_boundary_violations, historical.season_boundary_violations),
        "note":        "Atos Golden Rule: no H+H or A+A at start/end",
    })

    rows.append({
        "metric":      "SC9 Good Friday coverage (teams)",
        "generated":   generated.good_friday_coverage,
        "historical":  historical.good_friday_coverage,
        "delta":       delta(generated.good_friday_coverage, historical.good_friday_coverage),
        "note":        "Soft SC9: target = 20",
    })

    rows.append({
        "metric":      "SC9 Easter Monday coverage (teams)",
        "generated":   generated.easter_monday_coverage,
        "historical":  historical.easter_monday_coverage,
        "delta":       delta(generated.easter_monday_coverage, historical.easter_monday_coverage),
        "note":        "Soft SC9: target = 20",
    })

    if generated.final_day_enforced is not None:
        rows.append({
            "metric":      "HC8 final day enforced",
            "generated":   "Yes" if generated.final_day_enforced else "No",
            "historical":  "—",
            "delta":       None,
            "note":        "Hard HC8: all Rd38 simultaneous 16:00",
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

_CONSTRAINT_NOTES: dict[str, str] = {
    "HC1":  "Hard: ≥3 days rest between fixtures",
    "HC3":  "Hard: no fixtures in intl breaks",
    "HC7":  "Hard: no fixtures on Christmas Day",
    "HC8":  "Hard: all Round 38 simultaneous 16:00",
    "SC1":  "Soft: max 5 consec. away",
    "SC2":  "Soft: max 5 consec. home",
    "SC3":  "Soft: derby legs ≥8 rounds apart",
    "SC5":  "Soft: H/A balance per half-season",
    "SC7":  "Soft: no same-city home clash within 4 days",
    "SC9":  "Soft: all 20 teams play Good Fri + Easter Mon",
    "SC10": "Soft: ≤3 London home games per day",
    "SC12": "Soft: ≤3 consec. H or A in rounds 1-5",
    "SC13": "Atos Golden Rule: 2-3 home in any 5 consec.",
    "SC14": "Atos Golden Rule: no H+H or A+A at start/end",
    "SC15": "Atos Golden Rule: Boxing Day ↔ NYD opposite H/A",
}


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

    def _sep(label: str) -> dict:
        return {"metric": f"── {label} ──", "values": {r.label: "" for r in reports}, "note": ""}

    rows = [
        _sep("Solver Performance"),
        _row("Penalty score",              lambda r: r.penalty_score,              "Lower is better"),
        _row("Hard violations",            lambda r: r.hard_violations,            "Must be 0"),
        _row("Soft violations",            lambda r: r.soft_violations,            "Lower is better"),
        _row("Solve time (s)",             lambda r: r.solve_time_seconds,         ""),
        _row("HC8 final day enforced",     lambda r: ("Yes" if r.final_day_enforced else "No")
                                                     if r.final_day_enforced is not None else None,
             "Hard: all Round 38 at 16:00"),
    ]

    # --- Per-constraint violation breakdown (from validator) ---
    # Collect all constraint IDs appearing in any report (sorted for consistency)
    all_cids = sorted(
        set(cid for r in reports for cid in r.constraint_violations),
        key=lambda c: (c[:2], c[2:].zfill(4))  # sort HC before SC, numeric within
    )
    if all_cids:
        rows.append(_sep("Constraint Violations (validator)"))
        for cid in all_cids:
            rows.append(
                _row(f"  {cid}",
                     lambda r, c=cid: r.constraint_violations.get(c),
                     _CONSTRAINT_NOTES.get(cid, ""))
            )

    # --- Schedule Quality Metrics ---
    rows += [
        _sep("Schedule Quality"),
        _row("Mean rest days",             lambda r: r.rest_mean,                  "Higher = more recovery"),
        _row("Min rest days (global)",     lambda r: r.rest_min_global,            "Hard constraint ≥3"),
        _row("Intl break violations",      lambda r: r.intl_break_violation_count, "Hard constraint = 0"),
        _row("City home clashes (same day)",lambda r: r.city_clash_count,          "Legacy same-day count"),
        _row("SC7 city clashes (4d window)",lambda r: r.city_weekend_clash_count,  "Soft SC7"),
        _row("SC10 London cluster viol.",  lambda r: r.london_cluster_violations,  "Soft SC10: >3 London/day"),
        _row("SC13 five-match H/A viol.",  lambda r: r.five_match_pattern_violations, "Atos Golden Rule"),
        _row("SC14 season boundary viol.", lambda r: r.season_boundary_violations, "Atos Golden Rule"),
        _row("Max consec. home (league)",  lambda r: r.league_max_consec_home,     "Soft ≤5"),
        _row("Max consec. away (league)",  lambda r: r.league_max_consec_away,     "Soft ≤5"),
        _row("Teams >3 consec. away",      lambda r: len(r.teams_over_3_away),     ""),
        _row("Derbies gap <56 days",       lambda r: len(r.derbies_under_56d),     "SC3"),
        _sep("Coverage"),
        _row("Boxing Day coverage",        lambda r: r.boxing_day_coverage,        "Target=20"),
        _row("New Year's Day coverage",    lambda r: r.new_years_day_coverage,     "Target=20"),
        _row("Good Friday coverage",       lambda r: r.good_friday_coverage,       "SC9 target=20"),
        _row("Easter Monday coverage",     lambda r: r.easter_monday_coverage,     "SC9 target=20"),
    ]

    return {
        "type":   "solver_comparison",
        "labels": labels,
        "rows":   rows,
    }
