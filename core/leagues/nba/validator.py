"""
NBA post-solve validator. Returns the same report dict shape as
core/validator.py's EPL validator so core.validator.print_report and every
downstream caller work unchanged.

Checks the constraints verifiable from a completed schedule and reuses
analysis.metrics.compute() for the per-team and NBA-specific counts it already
derives (back-to-backs, 4-in-5, All-Star-break). Structural constraints
guaranteed by the fixture generator (HC2-HC4 game distribution) and those
needing external data the project does not collect (HC7 travel miles, HC9/HC11
host-city, HC12 IST, arena windows) are not re-checked here.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from core.models import Schedule
from core.data_loader import load_constraints
from analysis.metrics import compute


def _eight_in_twelve(schedule: Schedule, team_ids) -> dict[str, int]:
    """Count windows where a team plays >= 8 games in 12 consecutive nights."""
    dates_by_team: dict[str, list[date]] = defaultdict(list)
    for sf in schedule.fixtures:
        dates_by_team[sf.home_team_id].append(sf.slot.date)
        dates_by_team[sf.away_team_id].append(sf.slot.date)
    out: dict[str, int] = {}
    for tid in team_ids:
        ds = sorted(dates_by_team.get(tid, []))
        count = 0
        for i, d in enumerate(ds):
            window_end = d + timedelta(days=11)
            if sum(1 for wd in ds[i:] if wd <= window_end) >= 8:
                count += 1
        if count:
            out[tid] = count
    return out


def _road_back_to_backs(schedule: Schedule, team_ids) -> dict[str, int]:
    """Count road-road back-to-backs (consecutive away games one night apart)."""
    fx_by_team: dict[str, list] = defaultdict(list)
    for sf in schedule.fixtures:
        fx_by_team[sf.home_team_id].append(sf)
        fx_by_team[sf.away_team_id].append(sf)
    out: dict[str, int] = {}
    for tid in team_ids:
        fx = sorted(fx_by_team.get(tid, []), key=lambda s: s.slot.date)
        count = 0
        for a, b in zip(fx, fx[1:]):
            if (b.slot.date - a.slot.date).days == 1 \
               and a.away_team_id == tid and b.away_team_id == tid:
                count += 1
        if count:
            out[tid] = count
    return out


def validate(schedule: Schedule, teams: dict) -> dict:
    constraints = load_constraints()
    hard = {c["id"]: c for c in constraints.get("hard", [])}
    soft = {c["id"]: c for c in constraints.get("soft", [])}
    m = compute(schedule)  # active league must be nba (set by caller/dispatcher)

    hard_v: list[dict] = []
    soft_v: list[dict] = []
    penalty = 0
    team_ids = list(teams.keys())

    # ── HC1: exactly 82 games per team ────────────────────────────────────
    target_games = hard.get("HC1", {}).get("value", 82)
    games_per_team: dict[str, int] = defaultdict(int)
    for sf in schedule.fixtures:
        games_per_team[sf.home_team_id] += 1
        games_per_team[sf.away_team_id] += 1
    for tid in team_ids:
        if games_per_team[tid] != target_games:
            hard_v.append({"constraint": "HC1", "team": tid,
                           "games": games_per_team[tid], "expected": target_games})

    # ── HC5: no 4 games in 5 nights (from metrics) ────────────────────────
    if m.four_in_five_violations:
        hard_v.append({"constraint": "HC5",
                       "note": f"{m.four_in_five_violations} four-in-five-nights window(s)"})

    # ── HC6: no 8 games in 12 nights ──────────────────────────────────────
    for tid, cnt in _eight_in_twelve(schedule, team_ids).items():
        hard_v.append({"constraint": "HC6", "team": tid, "windows": cnt})

    # ── HC8: back-to-backs must not exceed the hard ceiling ───────────────
    ceiling = hard.get("HC8", {}).get("hard_ceiling", 16)
    for tid, b2b in m.back_to_back_counts.items():
        if b2b > ceiling:
            hard_v.append({"constraint": "HC8", "team": tid, "back_to_backs": b2b, "ceiling": ceiling})

    # ── HC10: no games during the All-Star break (from metrics) ───────────
    if m.all_star_break_violations:
        hard_v.append({"constraint": "HC10",
                       "note": f"{m.all_star_break_violations} game(s) during the All-Star break window"})

    # ── HC13: all teams play on the final calendar day ────────────────────
    if m.final_day_team_coverage and m.final_day_team_coverage < len(team_ids):
        hard_v.append({"constraint": "HC13", "teams_on_final_day": m.final_day_team_coverage,
                       "expected": len(team_ids)})

    # ── SC1: back-to-backs above target per team ──────────────────────────
    sc1 = soft.get("SC1", {}); target = sc1.get("target_per_team", 14)
    sc1_pen = sc1.get("penalty_per_occurrence_above_target", 10)
    for tid, b2b in m.back_to_back_counts.items():
        if b2b > target:
            soft_v.append({"constraint": "SC1", "team": tid, "back_to_backs": b2b, "target": target})
            penalty += sc1_pen * (b2b - target)

    # ── SC2: road-road back-to-backs ──────────────────────────────────────
    sc2_pen = soft.get("SC2", {}).get("penalty_per_violation", 30)
    for tid, cnt in _road_back_to_backs(schedule, team_ids).items():
        soft_v.append({"constraint": "SC2", "team": tid, "road_back_to_backs": cnt})
        penalty += sc2_pen * cnt

    # ── SC3: road trips longer than the cap ───────────────────────────────
    sc3 = soft.get("SC3", {}); trip_cap = sc3.get("max_consecutive_road_games", 6)
    sc3_pen = sc3.get("penalty_per_violation", 20)
    for tid, run in m.max_consec_away_per_team.items():
        if run > trip_cap:
            soft_v.append({"constraint": "SC3", "team": tid, "max_road_trip": run, "cap": trip_cap})
            penalty += sc3_pen

    # ── SC5: Christmas Day marquee-game coverage ──────────────────────────
    sc5 = soft.get("SC5", {}); target_games_xmas = sc5.get("target_games", 5)
    sc5_pen = sc5.get("penalty_per_missing_game", 50)
    xmas_games = sum(1 for sf in schedule.fixtures
                     if sf.slot.date.month == 12 and sf.slot.date.day == 25)
    if xmas_games < target_games_xmas:
        missing = target_games_xmas - xmas_games
        soft_v.append({"constraint": "SC5", "christmas_games": xmas_games, "target": target_games_xmas})
        penalty += sc5_pen * missing

    return {
        "hard_violations":      hard_v,
        "hard_violation_count": len(hard_v),
        "soft_violations":      soft_v,
        "soft_violation_count": len(soft_v),
        "total_penalty_score":  penalty,
        "feasible":             len(hard_v) == 0,
    }
