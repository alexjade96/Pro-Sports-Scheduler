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

from flask import Flask, jsonify, render_template, send_from_directory

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from analysis.comparator import compare_solvers, compare_to_historical
from analysis.historical_loader import load_season, available_seasons
from analysis.main import _load_generated_csv, _validate_generated
from analysis.metrics import compute
from core.data_loader import load_teams

app = Flask(__name__)
OUTPUT_DIR          = ROOT / "output"
SAMPLES_DIR         = ROOT / "samples" / "calendars"
ANALYTICS_SAMPLES_DIR = ROOT / "samples" / "analytics"

# ---------------------------------------------------------------------------
# Startup data load (cached in-process)
# ---------------------------------------------------------------------------

_cache: dict = {}

SOLVER_LABELS = {
    "cp_sat":        "CP-SAT",
    "ilp":           "ILP",
    "metaheuristic": "Metaheuristic",
}


def _read_csv_rows(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _date_from_hist_str(date_str: str):
    """Parse DD/MM/YYYY or DD/MM/YY date strings from historical CSVs."""
    from datetime import datetime as _dt2
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return _dt2.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {date_str!r}")


def _gen_heatmap(rows: list[dict]) -> dict:
    """Day × kickoff fixture counts from generated schedule rows."""
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


def _hist_heatmap(hist_path: Path) -> dict:
    """Day × kickoff fixture counts from a football-data.co.uk CSV."""
    DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    counts: dict[str, dict[str, int]] = {d: {} for d in DAYS}
    with open(hist_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if not row.get("Date"):
                continue
            try:
                d = _date_from_hist_str(row["Date"])
            except ValueError:
                continue
            day_name = d.strftime("%A")
            ko = (row.get("Time") or "15:00").strip() or "15:00"
            if day_name in counts and ko:
                counts[day_name][ko] = counts[day_name].get(ko, 0) + 1
    kickoffs = sorted({ko for dc in counts.values() for ko in dc})
    matrix   = [[counts[day].get(ko, 0) for ko in kickoffs] for day in DAYS]
    return {"days": [d[:3] for d in DAYS], "kickoffs": kickoffs, "matrix": matrix}


def _load_all() -> None:
    teams = load_teams()

    # Historical baseline
    hist_path = ROOT / "data/leagues/epl/historical/2024-25.csv"
    hist_schedule = load_season(str(hist_path))
    hist_report = compute(hist_schedule)
    _cache["hist"] = hist_report

    # Generated schedules
    gen_reports = []
    gen_rows: dict[str, list[dict]] = {}
    for key in ("cp_sat", "ilp", "metaheuristic"):
        csv_path = OUTPUT_DIR / f"schedule_{key}.csv"
        if not csv_path.exists():
            continue
        gen_schedule = _load_generated_csv(str(csv_path))
        solver_meta  = _validate_generated(gen_schedule, teams)
        report       = compute(gen_schedule, solver_meta=solver_meta)
        report.label = SOLVER_LABELS[key]
        gen_reports.append(report)
        gen_rows[SOLVER_LABELS[key]] = _read_csv_rows(csv_path)

    _cache["gen_reports"] = gen_reports
    _cache["gen_rows"]    = gen_rows

    if gen_reports:
        _cache["accuracy"] = compare_to_historical(gen_reports[0], hist_report)
    if len(gen_reports) > 1:
        _cache["solvers"] = compare_solvers(gen_reports)

    _cache["teams"]      = sorted(teams.keys())
    _cache["team_names"] = {t: teams[t].name for t in teams}

    # ── Multi-season historical metrics ──────────────────────────────────────
    hist_all: list[dict] = []
    for _spath in sorted(available_seasons()):
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
    _cache["hist_all"] = hist_all

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
        _cache["team_scorecard"] = _score

    # ── Fixture density heatmaps ──────────────────────────────────────────────
    if gen_reports:
        _best_rows = gen_rows.get(gen_reports[0].label, [])
        _cache["heatmap_gen"]  = _gen_heatmap(_best_rows)
        _hist24 = ROOT / "data/leagues/epl/historical/2024-25.csv"
        if _hist24.exists():
            _cache["heatmap_hist"] = _hist_heatmap(_hist24)


_load_all()


# ---------------------------------------------------------------------------
# Helper: calendar / analytics PNG availability
# ---------------------------------------------------------------------------

def _calendar_images() -> list[dict]:
    images = [{"key": "season", "label": "Full Season", "filename": "calendar.png"}]
    for tid in _cache["teams"]:
        fname = f"calendar_{tid.lower()}.png"
        images.append({
            "key":      tid,
            "label":    _cache["team_names"].get(tid, tid),
            "filename": fname,
        })
    return images


def _analytics_sample_files() -> list[str]:
    if not ANALYTICS_SAMPLES_DIR.exists():
        return []
    return sorted(f.name for f in ANALYTICS_SAMPLES_DIR.glob("analytics_*.png"))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    gen = _cache.get("gen_reports", [])
    best = gen[0] if gen else None
    hist = _cache.get("hist")

    kpis = []
    if best:
        kpis = [
            {"label": "Total Fixtures",   "value": best.total_fixtures,        "sub": "380 target"},
            {"label": "Hard Violations",  "value": best.hard_violations or 0,  "sub": "must be 0",    "ok": (best.hard_violations or 0) == 0},
            {"label": "Soft Violations",  "value": best.soft_violations or 0,  "sub": "lower is better"},
            {"label": "Penalty Score",    "value": best.penalty_score or 0,    "sub": "lower is better"},
            {"label": "Mean Rest Days",   "value": best.rest_mean,             "sub": f"hist {hist.rest_mean if hist else '—'}"},
            {"label": "Min Rest Days",    "value": best.rest_min_global,       "sub": "≥3 required",  "ok": best.rest_min_global >= 3},
        ]

    solvers_available = len(gen)
    has_accuracy = "accuracy" in _cache
    has_solvers  = "solvers" in _cache

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
    )


@app.route("/schedule")
def schedule():
    gen_rows = _cache.get("gen_rows", {})
    labels   = list(gen_rows.keys())
    team_names = _cache.get("team_names", {})
    teams    = _cache.get("teams", [])

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
    )


@app.route("/accuracy")
def accuracy():
    cmp = _cache.get("accuracy")
    if not cmp:
        return render_template("accuracy.html", rows=[], gen="—", hist="—")
    return render_template(
        "accuracy.html",
        rows=cmp["rows"],
        gen=cmp["generated"],
        hist=cmp["historical"],
    )


@app.route("/solvers")
def solvers():
    cmp = _cache.get("solvers")
    if not cmp:
        return render_template("solvers.html", labels=[], rows=[])
    return render_template(
        "solvers.html",
        labels=cmp["labels"],
        rows=cmp["rows"],
    )


@app.route("/calendar")
def calendar():
    images = _calendar_images()
    return render_template("calendar.html", images=images)


@app.route("/calendar-img/<filename>")
def calendar_img(filename: str):
    if (SAMPLES_DIR / filename).exists():
        return send_from_directory(str(SAMPLES_DIR), filename)
    return send_from_directory(str(OUTPUT_DIR), filename)


@app.route("/analysis")
def analysis():
    hist_all    = _cache.get("hist_all", [])
    gen_reports = _cache.get("gen_reports", [])

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

    sample_files = _analytics_sample_files()

    return render_template(
        "analysis.html",
        trend_json=json.dumps(trend),
        gen_trend_json=json.dumps(gen_trend),
        radar_json=json.dumps(radar_datasets),
        heatmap_gen_json=json.dumps(_cache.get("heatmap_gen", {})),
        heatmap_hist_json=json.dumps(_cache.get("heatmap_hist", {})),
        team_scorecard_json=json.dumps(_cache.get("team_scorecard", [])),
        sample_files_json=json.dumps(sample_files),
        best_label=gen_reports[0].label if gen_reports else "—",
        has_gen=bool(gen_reports),
        hist_count=len(hist_all),
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
    try:
        from tools.export_analytics import main as _export_main
        out_dir = ANALYTICS_SAMPLES_DIR
        files = _export_main(out_dir)
        return jsonify({"ok": True, "files": [f.name for f in sorted(files)]})
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/team-details")
def api_team_details():
    return jsonify(_cache.get("team_scorecard", []))


# ---------------------------------------------------------------------------
# JSON APIs
# ---------------------------------------------------------------------------

@app.route("/api/schedule/<label>")
def api_schedule(label: str):
    rows = _cache.get("gen_rows", {}).get(label, [])
    return jsonify(rows)


@app.route("/api/metrics")
def api_metrics():
    out = []
    for r in _cache.get("gen_reports", []):
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
