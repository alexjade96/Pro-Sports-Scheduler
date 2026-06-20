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
from analysis.historical_loader import load_season
from analysis.main import _load_generated_csv, _validate_generated
from analysis.metrics import compute
from core.data_loader import load_teams

app = Flask(__name__)
OUTPUT_DIR  = ROOT / "output"
SAMPLES_DIR = ROOT / "samples" / "calendars"  # preferred; falls back to OUTPUT_DIR

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
    _cache["gen_rows"]    = gen_rows  # label → list[row_dict]

    if gen_reports:
        _cache["accuracy"] = compare_to_historical(gen_reports[0], hist_report)
    if len(gen_reports) > 1:
        _cache["solvers"] = compare_solvers(gen_reports)

    # Team list for calendar / filter dropdowns
    _cache["teams"] = sorted(teams.keys())
    _cache["team_names"] = {t: teams[t].name for t in teams}


_load_all()


# ---------------------------------------------------------------------------
# Helper: calendar PNG availability
# ---------------------------------------------------------------------------

def _calendar_images() -> list[dict]:
    """Return a list of {key, label, filename} for full season + every team."""
    images = [{"key": "season", "label": "Full Season", "filename": "calendar.png"}]
    for tid in _cache["teams"]:
        fname = f"calendar_{tid.lower()}.png"
        images.append({
            "key":      tid,
            "label":    _cache["team_names"].get(tid, tid),
            "filename": fname,
        })
    return images


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

    # Day-of-week distribution for chart
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

    # Build a display name list sorted by full name
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


# Serve calendar PNGs from samples/calendars/ (falling back to output/)
@app.route("/calendar-img/<filename>")
def calendar_img(filename: str):
    # Try samples/calendars first, then output/
    if (SAMPLES_DIR / filename).exists():
        return send_from_directory(str(SAMPLES_DIR), filename)
    return send_from_directory(str(OUTPUT_DIR), filename)


# ---------------------------------------------------------------------------
# JSON APIs (for dynamic updates without page reload)
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
            "label":               r.label,
            "total_fixtures":      r.total_fixtures,
            "hard_violations":     r.hard_violations,
            "soft_violations":     r.soft_violations,
            "penalty_score":       r.penalty_score,
            "rest_mean":           r.rest_mean,
            "rest_min_global":     r.rest_min_global,
            "league_max_consec_home": r.league_max_consec_home,
            "league_max_consec_away": r.league_max_consec_away,
            "city_clash_count":    r.city_clash_count,
            "constraint_violations": r.constraint_violations,
        })
    return jsonify(out)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5000)
