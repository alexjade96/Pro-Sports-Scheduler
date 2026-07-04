"""
NFL post-solve validator. Returns the same report dict shape as
core/validator.py's EPL validator (hard_violations / soft_violations /
counts / total_penalty_score / feasible) so core.validator.print_report and
every downstream caller work unchanged.

Checks the constraints that are verifiable from a completed schedule and reuses
analysis.metrics.compute() for the per-team and NFL-specific counts it already
derives. Structural constraints guaranteed by the fixture generator (HC2-HC6
division/conference rotation, HC7 bye week) and those needing external data the
project does not collect (travel distances, broadcast-slot designations) are not
re-checked here — see the per-check comments.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from core.models import Schedule
from core.data_loader import load_constraints
from analysis.metrics import compute


def validate(schedule: Schedule, teams: dict) -> dict:
    constraints = load_constraints()
    hard = {c["id"]: c for c in constraints.get("hard", [])}
    soft = {c["id"]: c for c in constraints.get("soft", [])}
    m = compute(schedule)  # active league must be nfl (set by caller/dispatcher)

    hard_v: list[dict] = []
    soft_v: list[dict] = []
    penalty = 0

    team_ids = list(teams.keys())

    # ── HC1: exactly 17 games per team ────────────────────────────────────
    target_games = hard.get("HC1", {}).get("value", 17)
    games_per_team: dict[str, int] = defaultdict(int)
    for sf in schedule.fixtures:
        games_per_team[sf.home_team_id] += 1
        games_per_team[sf.away_team_id] += 1
    for tid in team_ids:
        if games_per_team[tid] != target_games:
            hard_v.append({"constraint": "HC1", "team": tid,
                           "games": games_per_team[tid], "expected": target_games})

    # ── HC8: shared-venue tenants cannot both host on the same date ────────
    home_by_date: dict[date, set[str]] = defaultdict(set)
    for sf in schedule.fixtures:
        home_by_date[sf.slot.date].add(sf.home_team_id)
    for venue in hard.get("HC8", {}).get("venues", []):
        tenants = set(venue.get("tenants", []))
        for d, hosts in home_by_date.items():
            both = tenants & hosts
            if len(both) > 1:
                hard_v.append({"constraint": "HC8", "stadium": venue.get("stadium"),
                               "date": str(d), "co_hosting": sorted(both)})

    # ── HC9: DAL & DET must host on Thanksgiving (from metrics) ────────────
    if m.thanksgiving_fixed_host_violations:
        hard_v.append({"constraint": "HC9",
                       "note": f"{m.thanksgiving_fixed_host_violations} mandatory Thanksgiving host(s) not at home"})

    # ── HC10: Thursday games need >= min rest since previous game ──────────
    tnf_min = hard.get("HC10", {}).get("min_days_since_last_game", 10)
    for tid in team_ids:
        fx = sorted(schedule.fixtures_for_team(tid), key=lambda s: s.slot.date)
        for i in range(1, len(fx)):
            if fx[i].slot.day_of_week == "Thursday":
                gap = (fx[i].slot.date - fx[i - 1].slot.date).days
                if gap < tnf_min:
                    hard_v.append({"constraint": "HC10", "team": tid,
                                   "date": str(fx[i].slot.date), "rest_days": gap, "min": tnf_min})

    # ── HC11: no team plays Dec 24 and Dec 25 ─────────────────────────────
    for tid in team_ids:
        dates = {sf.slot.date for sf in schedule.fixtures_for_team(tid)}
        if any(d.month == 12 and d.day == 24 for d in dates) and \
           any(d.month == 12 and d.day == 25 for d in dates):
            hard_v.append({"constraint": "HC11", "team": tid,
                           "note": "plays both Christmas Eve and Christmas Day"})

    # ── HC12: regular season spans <= 18 weeks ────────────────────────────
    weeks = hard.get("HC12", {}).get("weeks", 18)
    all_dates = [sf.slot.date for sf in schedule.fixtures]
    if all_dates:
        span_days = (max(all_dates) - min(all_dates)).days
        if span_days > weeks * 7 + 6:  # allow a few days slack for the final weekend
            hard_v.append({"constraint": "HC12", "span_days": span_days, "max": weeks * 7})

    # ── SC1: max 3 consecutive road games ─────────────────────────────────
    sc1 = soft.get("SC1", {}); road_cap = sc1.get("value", 3); road_pen = sc1.get("penalty_per_violation", 25)
    for tid, run in m.max_consec_away_per_team.items():
        if run > road_cap:
            soft_v.append({"constraint": "SC1", "team": tid, "max_consec_road": run, "cap": road_cap})
            penalty += road_pen

    # ── SC2: max 4 consecutive home games ─────────────────────────────────
    sc2 = soft.get("SC2", {}); home_cap = sc2.get("value", 4); home_pen = sc2.get("penalty_per_violation", 20)
    for tid, run in m.max_consec_home_per_team.items():
        if run > home_cap:
            soft_v.append({"constraint": "SC2", "team": tid, "max_consec_home": run, "cap": home_cap})
            penalty += home_pen

    # ── SC11: division rivalry legs spread out (generic derby-gap metric) ──
    sc11_pen = soft.get("SC11", {}).get("penalty_per_violation", 20)
    for pair in m.derbies_under_56d:
        soft_v.append({"constraint": "SC11", "rivalry": pair,
                       "note": f"legs closer than the {m.derby_gap_threshold_days}-day rivalry-spread threshold"})
        penalty += sc11_pen

    return {
        "hard_violations":      hard_v,
        "hard_violation_count": len(hard_v),
        "soft_violations":      soft_v,
        "soft_violation_count": len(soft_v),
        "total_penalty_score":  penalty,
        "feasible":             len(hard_v) == 0,
    }
