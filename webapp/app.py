"""
EPL Scheduler — Web Dashboard
Run from project root (with .venv active):
    python -m webapp.app
or:
    flask --app webapp/app.py run
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from analysis.comparator import compare_solvers, compare_to_historical
from analysis.historical_loader import load_season, available_seasons
from analysis.main import _load_generated_csv, _validate_generated
from analysis.metrics import compute
from core.data_loader import load_teams, set_league

app = Flask(__name__)
OUTPUT_DIR          = ROOT / "output"
SAMPLES_DIR         = ROOT / "samples" / "calendars"
ANALYTICS_SAMPLES_DIR = ROOT / "samples" / "analytics"

# ---------------------------------------------------------------------------
# Per-league data load, cached in-process on first request for that league.
#
# EPL is the only league with a wired-up validator (core/validator.py
# hardcodes EPL constraint IDs) and EPL-specific comparator rows (Golden
# Rules, festive coverage) — those fields simply stay at their MetricsReport
# defaults (0/None) for NFL/NBA rather than being computed, since no
# equivalent exists for those leagues yet. Every other page (dashboard,
# schedule, calendar) works identically for all three leagues.
# ---------------------------------------------------------------------------

LEAGUES = ["epl", "nfl", "nba"]
LEAGUE_LABELS = {"epl": "EPL", "nfl": "NFL", "nba": "NBA"}

_cache: dict[str, dict] = {}

SOLVER_LABELS = {
    "cp_sat":        "CP-SAT",
    "ilp":           "ILP",
    "metaheuristic": "Metaheuristic",
}


def active_league() -> str:
    lg = request.args.get("league", "epl")
    return lg if lg in LEAGUES else "epl"


def _read_csv_rows(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _heatmap_from_rows(rows: list[dict]) -> dict:
    """Day × kickoff fixture counts from generated schedule CSV rows."""
    DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    counts: dict[str, dict[str, int]] = {d: {} for d in DAYS}
    for row in rows:
        day = row.get("day", "")
        ko  = row.get("kickoff", "")
        if day in counts and ko:
            counts[day][ko] = counts[day].get(ko, 0) + 1
    kickoffs = sorted({ko for dc in counts.values() for ko in dc})
    matrix   = [[counts[day].get(ko, 0) for ko in kickoffs] for day in DAYS]
    return {"days": [d[:3] for d in DAYS], "kickoffs": kickoffs, "matrix": matrix}


def _heatmap_from_schedule(schedule) -> dict:
    """Day × kickoff fixture counts from a Schedule object — works for any
    league's historical data via the league-aware historical_loader, unlike
    the old version which hand-parsed EPL's football-data.co.uk CSV columns
    directly."""
    DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    counts: dict[str, dict[str, int]] = {d: {} for d in DAYS}
    for sf in schedule.fixtures:
        day, ko = sf.slot.day_of_week, sf.slot.kickoff
        if day in counts and ko:
            counts[day][ko] = counts[day].get(ko, 0) + 1
    kickoffs = sorted({ko for dc in counts.values() for ko in dc})
    matrix   = [[counts[day].get(ko, 0) for ko in kickoffs] for day in DAYS]
    return {"days": [d[:3] for d in DAYS], "kickoffs": kickoffs, "matrix": matrix}


def _solver_csv_path(league: str, key: str) -> Path:
    """EPL keeps its established output/schedule_<solver>.csv convention
    (unprefixed, for backward compatibility with existing tooling); NFL/NBA
    use output/schedule_<league>_<solver>.csv."""
    if league == "epl":
        return OUTPUT_DIR / f"schedule_{key}.csv"
    return OUTPUT_DIR / f"schedule_{league}_{key}.csv"


def _load_league_data(league: str) -> dict:
    set_league(league)
    cache: dict = {}
    teams = load_teams()

    # Historical baseline — most recent available season for this league
    seasons = sorted(available_seasons())
    hist_report = None
    if seasons:
        hist_schedule = load_season(str(seasons[-1]))
        hist_report = compute(hist_schedule)
    cache["hist"] = hist_report

    # Generated schedules
    gen_reports = []
    gen_rows: dict[str, list[dict]] = {}
    for key in ("cp_sat", "ilp", "metaheuristic"):
        csv_path = _solver_csv_path(league, key)
        if not csv_path.exists():
            continue
        gen_schedule = _load_generated_csv(str(csv_path))
        # core.validator dispatches to the active league's validator (set_league
        # was called above), so all three leagues get real solver_meta now.
        solver_meta = _validate_generated(gen_schedule, teams)
        report      = compute(gen_schedule, solver_meta=solver_meta)
        report.label = SOLVER_LABELS[key]
        gen_reports.append(report)
        gen_rows[SOLVER_LABELS[key]] = _read_csv_rows(csv_path)

    cache["gen_reports"] = gen_reports
    cache["gen_rows"]    = gen_rows

    if gen_reports and hist_report:
        cache["accuracy"] = compare_to_historical(gen_reports[0], hist_report)
    if len(gen_reports) > 1:
        cache["solvers"] = compare_solvers(gen_reports)

    cache["teams"]      = sorted(teams.keys())
    cache["team_names"] = {t: teams[t].name for t in teams}

    # ── Multi-season historical metrics ──────────────────────────────────────
    hist_all: list[dict] = []
    for _spath in seasons:
        if _spath.suffix != ".csv":
            continue
        try:
            _sched = load_season(str(_spath))
            _r     = compute(_sched)
            hist_all.append({
                "season":                       _r.label,
                "rest_mean":                    _r.rest_mean,
                "rest_min":                     _r.rest_min_global,
                "city_weekend_clash_count":     _r.city_weekend_clash_count,
                "london_cluster_violations":    _r.london_cluster_violations,
                "league_max_consec_home":       _r.league_max_consec_home,
                "league_max_consec_away":       _r.league_max_consec_away,
                "teams_over_5_home":            len(_r.teams_over_5_home),
                "teams_over_5_away":            len(_r.teams_over_5_away),
                "five_match_pattern_violations":_r.five_match_pattern_violations,
                "season_boundary_violations":   _r.season_boundary_violations,
                "boxing_day_coverage":          _r.boxing_day_coverage,
                "new_years_day_coverage":       _r.new_years_day_coverage,
                "derbies_under_56d":            len(_r.derbies_under_56d),
            })
        except Exception as _e:
            print(f"[analysis] hist {_spath.stem}: {_e}")
    cache["hist_all"] = hist_all

    # ── Per-team scorecard for best generated solver ──────────────────────────
    if gen_reports:
        _best = gen_reports[0]
        _score = []
        for _tid in sorted(teams.keys()):
            _score.append({
                "id":              _tid,
                "name":            teams[_tid].name,
                "min_rest":        _best.rest_min_per_team.get(_tid, 0),
                "max_consec_home": _best.max_consec_home_per_team.get(_tid, 0),
                "max_consec_away": _best.max_consec_away_per_team.get(_tid, 0),
                "h1_home_pct":     round(_best.home_pct_first_half.get(_tid, 0), 1),
                "h2_home_pct":     round(_best.home_pct_second_half.get(_tid, 0), 1),
                "solver":          _best.label,
            })
        cache["team_scorecard"] = _score

    # ── Fixture density heatmaps ──────────────────────────────────────────────
    if gen_reports:
        _best_rows = gen_rows.get(gen_reports[0].label, [])
        cache["heatmap_gen"] = _heatmap_from_rows(_best_rows)
        if seasons:
            cache["heatmap_hist"] = _heatmap_from_schedule(load_season(str(seasons[-1])))

    return cache


def _get_cache(league: str) -> dict:
    if league not in _cache:
        _cache[league] = _load_league_data(league)
    return _cache[league]


# League data loads lazily on first request for that league (see _get_cache)
# rather than eagerly for all three at startup — NFL/NBA may not have any
# generated output/ CSVs yet, and there's no need to pay that load cost
# before a request for that league actually arrives.


# ---------------------------------------------------------------------------
# Helper: calendar / analytics PNG availability
# ---------------------------------------------------------------------------

def _calendar_images(league: str) -> list[dict]:
    cache = _get_cache(league)
    prefix = "calendar" if league == "epl" else f"calendar_{league}"
    images = [{"key": "season", "label": "Full Season", "filename": f"{prefix}.png"}]
    for tid in cache["teams"]:
        fname = f"{prefix}_{tid.lower()}.png"
        images.append({
            "key":      tid,
            "label":    cache["team_names"].get(tid, tid),
            "filename": fname,
        })
    return images


def _analytics_sample_files(league: str) -> list[str]:
    """EPL's exports are unprefixed (analytics_*.png); NFL/NBA use an
    analytics_<league>_ prefix (see tools/export_analytics.py's
    _out_filename()) — filter so one league's gallery never shows another
    league's charts."""
    if not ANALYTICS_SAMPLES_DIR.exists():
        return []
    pattern = "analytics_*.png" if league == "epl" else f"analytics_{league}_*.png"
    files = sorted(f.name for f in ANALYTICS_SAMPLES_DIR.glob(pattern))
    if league == "epl":
        files = [f for f in files if not f.startswith(("analytics_nfl_", "analytics_nba_"))]
    return files


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _nav_context(league: str) -> dict:
    """Common template variables for the league switcher nav, present on every page."""
    return {
        "leagues":        LEAGUES,
        "league_labels":  LEAGUE_LABELS,
        "active_league":  league,
        "active_league_label": LEAGUE_LABELS[league],
    }


@app.route("/")
def index():
    league = active_league()
    cache = _get_cache(league)
    gen = cache.get("gen_reports", [])
    best = gen[0] if gen else None
    hist = cache.get("hist")

    kpis = []
    if best:
        kpis = [
            {"label": "Total Fixtures",   "value": best.total_fixtures,        "sub": f"{hist.total_fixtures if hist else best.total_fixtures} target"},
            {"label": "Hard Violations",  "value": best.hard_violations if best.hard_violations is not None else "—",  "sub": "must be 0", "ok": (best.hard_violations or 0) == 0 if best.hard_violations is not None else None},
            {"label": "Soft Violations",  "value": best.soft_violations if best.soft_violations is not None else "—",  "sub": "lower is better"},
            {"label": "Penalty Score",    "value": best.penalty_score if best.penalty_score is not None else "—",    "sub": "lower is better"},
            {"label": "Mean Rest Days",   "value": best.rest_mean,             "sub": f"hist {hist.rest_mean if hist else '—'}"},
            {"label": "Min Rest Days",    "value": best.rest_min_global,       "sub": "≥3 required",  "ok": best.rest_min_global >= 3},
        ]

    solvers_available = len(gen)
    has_accuracy = "accuracy" in cache
    has_solvers  = "solvers" in cache

    dow_labels, dow_gen, dow_hist = [], [], []
    if best and hist:
        all_days = sorted(set(list(best.day_of_week_counts.keys()) + list(hist.day_of_week_counts.keys())),
                          key=lambda d: ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"].index(d)
                          if d in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"] else 99)
        for d in all_days:
            dow_labels.append(d[:3])
            dow_gen.append(best.day_of_week_pct.get(d, 0))
            dow_hist.append(hist.day_of_week_pct.get(d, 0))

    return render_template(
        "index.html",
        kpis=kpis,
        solvers_available=solvers_available,
        has_accuracy=has_accuracy,
        has_solvers=has_solvers,
        dow_labels=json.dumps(dow_labels),
        dow_gen=json.dumps(dow_gen),
        dow_hist=json.dumps(dow_hist),
        best_label=best.label if best else "—",
        total_fixtures=best.total_fixtures if best else (hist.total_fixtures if hist else "—"),
        **_nav_context(league),
    )


@app.route("/schedule")
def schedule():
    league = active_league()
    cache = _get_cache(league)
    gen_rows = cache.get("gen_rows", {})
    labels   = list(gen_rows.keys())
    team_names = cache.get("team_names", {})
    teams    = cache.get("teams", [])

    team_display = sorted(
        [{"id": t, "name": team_names.get(t, t)} for t in teams],
        key=lambda x: x["name"],
    )

    return render_template(
        "schedule.html",
        labels=labels,
        default_label=labels[0] if labels else "",
        team_display=team_display,
        rows_json={lbl: rows for lbl, rows in gen_rows.items()},
        **_nav_context(league),
    )


@app.route("/accuracy")
def accuracy():
    league = active_league()
    cmp = _get_cache(league).get("accuracy")
    if not cmp:
        return render_template("accuracy.html", rows=[], gen="—", hist="—", **_nav_context(league))
    return render_template(
        "accuracy.html",
        rows=cmp["rows"],
        gen=cmp["generated"],
        hist=cmp["historical"],
        **_nav_context(league),
    )


@app.route("/solvers")
def solvers():
    league = active_league()
    cmp = _get_cache(league).get("solvers")
    if not cmp:
        return render_template("solvers.html", labels=[], rows=[], **_nav_context(league))
    return render_template(
        "solvers.html",
        labels=cmp["labels"],
        rows=cmp["rows"],
        **_nav_context(league),
    )


@app.route("/calendar")
def calendar():
    league = active_league()
    images = _calendar_images(league)
    return render_template("calendar.html", images=images, **_nav_context(league))


@app.route("/calendar-img/<filename>")
def calendar_img(filename: str):
    if (SAMPLES_DIR / filename).exists():
        return send_from_directory(str(SAMPLES_DIR), filename)
    return send_from_directory(str(OUTPUT_DIR), filename)


@app.route("/analysis")
def analysis():
    league = active_league()
    cache = _get_cache(league)
    hist_all    = cache.get("hist_all", [])
    gen_reports = cache.get("gen_reports", [])

    def _series(key: str) -> list:
        return [h[key] for h in hist_all]

    trend = {
        "seasons":              [h["season"] for h in hist_all],
        "rest_mean":            _series("rest_mean"),
        "city_weekend_clashes": _series("city_weekend_clash_count"),
        "boxing_coverage":      _series("boxing_day_coverage"),
        "max_consec_away":      _series("league_max_consec_away"),
        "sc13_violations":      _series("five_match_pattern_violations"),
        "sc14_violations":      _series("season_boundary_violations"),
    }

    gen_trend = [
        {
            "label":                r.label,
            "rest_mean":            r.rest_mean,
            "city_weekend_clashes": r.city_weekend_clash_count,
            "boxing_coverage":      r.boxing_day_coverage,
            "max_consec_away":      r.league_max_consec_away,
            "sc13_violations":      r.five_match_pattern_violations,
            "sc14_violations":      r.season_boundary_violations,
        }
        for r in gen_reports
    ]

    def _radar(d: dict) -> list[float]:
        rest_q  = round(min(d.get("rest_mean", 0) * 5.0, 100), 1)
        city_q  = round(max(0.0, 100 - d.get("city_weekend_clash_count", 0) * 1.2), 1)
        run_q   = round(max(0.0, 100 - (d.get("teams_over_5_home", 0) + d.get("teams_over_5_away", 0)) * 5), 1)
        fest_q  = round((d.get("boxing_day_coverage", 0) + d.get("new_years_day_coverage", 0)) / 40 * 100, 1)
        sc13_q  = round(max(0.0, 100 - d.get("five_match_pattern_violations", 0) * 0.3), 1)
        derby_q = round(max(0.0, 100 - d.get("derbies_under_56d", 0) * 12), 1)
        return [rest_q, city_q, run_q, fest_q, sc13_q, derby_q]

    n = len(hist_all)
    if n:
        _avg_keys = ["rest_mean", "city_weekend_clash_count", "teams_over_5_home",
                     "teams_over_5_away", "boxing_day_coverage", "new_years_day_coverage",
                     "five_match_pattern_violations", "derbies_under_56d"]
        hist_avg = {k: round(sum(h.get(k, 0) for h in hist_all) / n, 2) for k in _avg_keys}
    else:
        hist_avg = {}

    radar_datasets = [{"label": "10-Season Avg", "data": _radar(hist_avg), "ci": 3}]
    for i, r in enumerate(gen_reports):
        radar_datasets.append({
            "label": r.label,
            "ci":    i,
            "data":  _radar({
                "rest_mean":                    r.rest_mean,
                "city_weekend_clash_count":     r.city_weekend_clash_count,
                "teams_over_5_home":            len(r.teams_over_5_home),
                "teams_over_5_away":            len(r.teams_over_5_away),
                "boxing_day_coverage":          r.boxing_day_coverage,
                "new_years_day_coverage":       r.new_years_day_coverage,
                "five_match_pattern_violations":r.five_match_pattern_violations,
                "derbies_under_56d":            len(r.derbies_under_56d),
            }),
        })

    sample_files = _analytics_sample_files(league)

    return render_template(
        "analysis.html",
        trend_json=json.dumps(trend),
        gen_trend_json=json.dumps(gen_trend),
        radar_json=json.dumps(radar_datasets),
        heatmap_gen_json=json.dumps(cache.get("heatmap_gen", {})),
        heatmap_hist_json=json.dumps(cache.get("heatmap_hist", {})),
        team_scorecard_json=json.dumps(cache.get("team_scorecard", [])),
        sample_files_json=json.dumps(sample_files),
        best_label=gen_reports[0].label if gen_reports else "—",
        has_gen=bool(gen_reports),
        hist_count=len(hist_all),
        # This page's Golden-Rule/festive dimensions (Boxing Day, SC13, SC14,
        # derby spacing) are EPL-only — analysis/leagues/nfl/nba don't
        # populate those MetricsReport fields, so they'd show as a flat 0
        # for other leagues. Flag it so the template can show a banner
        # instead of presenting zeros as if they were real compliance data.
        epl_only_charts=(league != "epl"),
        **_nav_context(league),
    )


@app.route("/analytics-img/<filename>")
def analytics_img(filename: str):
    """Serve exported analytics PNGs from samples/analytics/ or output/analytics/."""
    for directory in (ANALYTICS_SAMPLES_DIR, OUTPUT_DIR / "analytics"):
        if directory.exists() and (directory / filename).exists():
            return send_from_directory(str(directory), filename)
    return "Not found", 404


@app.route("/api/export-analytics", methods=["POST"])
def api_export_analytics():
    """Server-side: run matplotlib export, return list of generated filenames."""
    league = active_league()
    try:
        from tools.export_analytics import main as _export_main
        out_dir = ANALYTICS_SAMPLES_DIR
        files = _export_main(out_dir, league=league)
        return jsonify({"ok": True, "files": [f.name for f in sorted(files)]})
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/team-details")
def api_team_details():
    return jsonify(_get_cache(active_league()).get("team_scorecard", []))


# ---------------------------------------------------------------------------
# JSON APIs
# ---------------------------------------------------------------------------

@app.route("/api/schedule/<label>")
def api_schedule(label: str):
    rows = _get_cache(active_league()).get("gen_rows", {}).get(label, [])
    return jsonify(rows)


@app.route("/api/metrics")
def api_metrics():
    out = []
    for r in _get_cache(active_league()).get("gen_reports", []):
        out.append({
            "label":                  r.label,
            "total_fixtures":         r.total_fixtures,
            "hard_violations":        r.hard_violations,
            "soft_violations":        r.soft_violations,
            "penalty_score":          r.penalty_score,
            "rest_mean":              r.rest_mean,
            "rest_min_global":        r.rest_min_global,
            "league_max_consec_home": r.league_max_consec_home,
            "league_max_consec_away": r.league_max_consec_away,
            "city_clash_count":       r.city_clash_count,
            "constraint_violations":  r.constraint_violations,
        })
    return jsonify(out)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5000)
