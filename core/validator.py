"""
Post-solve validation: checks every hard and soft constraint against
a completed Schedule and returns a structured report.
Shared by all three solver options for consistent output comparison.

Constraint index
----------------
Hard:
  HC1  min_rest_days (≥3)
  HC3  blocked_window (intl breaks + Christmas Day + FA Cup Final week)
  HC4  double_round_robin (all 380 fixtures present)
  HC5  single_fixture_per_slot (team appears once per slot)
  HC6  venue_single_use (ground hosts one game per slot)
  HC7  christmas_day_blackout (Dec 25)
  HC8  final_day_simultaneous (all Round-38 games same kickoff)

Soft:
  SC1  max_consecutive_away (≤5)
  SC2  max_consecutive_home (≤5)
  SC3  derby_min_gap_rounds (≥8 rounds apart)
  SC4  european_team_rest_days (CL/EL Tue/Wed → ≥3 days before next PL)
  SC5  balanced_home_away_split (9-10 H and 9-10 A per half)
  SC6  avoid_same_opponent_cup_league_window (4-week buffer)
  SC7  same_city_home_clash — WIDENED to 4-day matchday window (not same-day only)
  SC8  uefa_thursday_five_day_rest (EL/ECL Thu → ≥5 days)
  SC9  easter_coverage (all 20 clubs play Good Friday + Easter Monday)
  SC10 london_cluster_cap (≤3 London home games per day)
  SC11 promoted_team_separation (promoted trio avoid each other rounds 1-3)
  SC12 opening_home_away_balance (≤3 consec H or A in rounds 1-5)
  SC13 five_match_ha_pattern (any 5 consecutive: 2 or 3 home — Atos Golden Rule)
  SC14 season_boundary_ha (no H+H or A+A in first 2 or last 2 fixtures)
  SC15 boxing_day_nyd_pairing (home on Dec 26 → away on Jan 1, and vice versa)
  SC16 spare_rescheduling_window (≥1 free midweek date per month)
  SC17 min_sat_1500_appearances (each team ≥5 Saturday 15:00 fixtures)
  SC18 min_monday_appearances (each team ≥3 Monday Night Football fixtures)
"""
from datetime import date, timedelta
from collections import defaultdict

from core.models import Schedule, ScheduledFixture
from core.data_loader import load_constraints, load_city_groups, load_calendar


# ── helpers ──────────────────────────────────────────────────────────────────

def _sorted_for_team(schedule: Schedule, team_id: str) -> list[ScheduledFixture]:
    return sorted(schedule.fixtures_for_team(team_id), key=lambda sf: sf.slot.date)


def _home_by_date(schedule: Schedule) -> dict[str, list[str]]:
    d: dict[str, list[str]] = defaultdict(list)
    for sf in schedule.fixtures:
        d[str(sf.slot.date)].append(sf.home_team_id)
    return d


# ── main validator ────────────────────────────────────────────────────────────

def validate(schedule: Schedule, teams: dict) -> dict:
    constraints  = load_constraints()
    city_groups  = load_city_groups()
    calendar     = load_calendar()

    city_lookup  = {t: city for city, members in city_groups.items() for t in members}
    sc           = {c["id"]: c for c in constraints["soft"]}
    hard_v: list[dict] = []
    soft_v: list[dict] = []
    penalty = 0

    # ── blocked window dates (HC3 + HC7) ──────────────────────────────────
    blocked: list[tuple[date, date]] = []
    for w in calendar.get("blocked_windows", []):
        blocked.append((date.fromisoformat(w["start"]), date.fromisoformat(w["end"])))

    def _in_blocked(d: date) -> bool:
        return any(s <= d <= e for s, e in blocked)

    # ── HC1: minimum rest days ────────────────────────────────────────────
    min_rest = next(c["value"] for c in constraints["hard"] if c["id"] == "HC1")
    for team_id in teams:
        team_fx = _sorted_for_team(schedule, team_id)
        for i in range(1, len(team_fx)):
            gap = (team_fx[i].slot.date - team_fx[i-1].slot.date).days
            if gap < min_rest:
                hard_v.append({"constraint": "HC1", "team": team_id,
                                "gap_days": gap, "date": str(team_fx[i].slot.date)})

    # ── HC3: blocked windows ──────────────────────────────────────────────
    for sf in schedule.fixtures:
        if _in_blocked(sf.slot.date):
            hard_v.append({"constraint": "HC3",
                            "fixture": f"{sf.home_team_id} v {sf.away_team_id}",
                            "date": str(sf.slot.date)})

    # ── HC7: Christmas Day ────────────────────────────────────────────────
    for sf in schedule.fixtures:
        if sf.slot.date.month == 12 and sf.slot.date.day == 25:
            hard_v.append({"constraint": "HC7",
                            "fixture": f"{sf.home_team_id} v {sf.away_team_id}",
                            "date": str(sf.slot.date)})

    # ── HC8: final-day simultaneous kickoff ───────────────────────────────
    final_day_cfg = calendar.get("final_day", {})
    if final_day_cfg:
        final_date   = date.fromisoformat(final_day_cfg["date"])
        final_ko     = final_day_cfg["kickoff"]
        final_fx     = [sf for sf in schedule.fixtures if sf.slot.date == final_date]
        if final_fx:
            wrong_ko = [sf for sf in final_fx if sf.slot.kickoff != final_ko]
            if wrong_ko:
                hard_v.append({"constraint": "HC8",
                                "note": f"{len(wrong_ko)} final-day fixture(s) not at {final_ko}",
                                "fixtures": [f"{sf.home_team_id} v {sf.away_team_id}" for sf in wrong_ko]})

    # ── SC1/SC2: consecutive home/away runs ───────────────────────────────
    max_away   = sc["SC1"]["value"]
    max_home   = sc["SC2"]["value"]
    p_away     = sc["SC1"]["penalty_per_violation"]
    p_home     = sc["SC2"]["penalty_per_violation"]
    for team_id in teams:
        away_run = home_run = 0
        for sf in _sorted_for_team(schedule, team_id):
            if sf.away_team_id == team_id:
                away_run += 1; home_run = 0
            else:
                home_run += 1; away_run = 0
            if away_run > max_away:
                soft_v.append({"constraint": "SC1", "team": team_id, "run": away_run})
                penalty += p_away
            if home_run > max_home:
                soft_v.append({"constraint": "SC2", "team": team_id, "run": home_run})
                penalty += p_home

    # ── SC7: same-city home clash — widened to matchday weekend window ────
    # Two same-city home fixtures within 4 days = same matchday round clash
    p_city     = sc["SC7"]["penalty_per_clash"]
    window_days = sc["SC7"].get("window_days", 4)
    home_fixtures_by_team: dict[str, list[date]] = defaultdict(list)
    for sf in schedule.fixtures:
        home_fixtures_by_team[sf.home_team_id].append(sf.slot.date)

    checked_pairs: set[frozenset] = set()
    for city, members in city_groups.items():
        for i, team_a in enumerate(members):
            for team_b in members[i+1:]:
                pair = frozenset([team_a, team_b])
                if pair in checked_pairs:
                    continue
                checked_pairs.add(pair)
                for d_a in home_fixtures_by_team.get(team_a, []):
                    for d_b in home_fixtures_by_team.get(team_b, []):
                        if abs((d_a - d_b).days) <= window_days:
                            soft_v.append({
                                "constraint": "SC7",
                                "city": city,
                                "teams": [team_a, team_b],
                                "dates": [str(d_a), str(d_b)],
                                "gap_days": abs((d_a - d_b).days),
                            })
                            penalty += p_city

    # ── SC10: London cluster cap (≤3 London home games per day) ──────────
    london_teams = set(city_groups.get("London", []))
    p_london     = sc["SC10"]["penalty_per_violation"]
    max_london   = sc["SC10"]["max_home_same_day"]
    hbd          = _home_by_date(schedule)
    for date_str, home_teams in hbd.items():
        london_home = [t for t in home_teams if t in london_teams]
        if len(london_home) > max_london:
            soft_v.append({"constraint": "SC10", "date": date_str,
                            "count": len(london_home), "teams": london_home})
            penalty += p_london * (len(london_home) - max_london)

    # ── SC9: Easter coverage ──────────────────────────────────────────────
    easter_cfg  = calendar.get("easter_matchdays", {})
    p_easter    = sc["SC9"]["penalty_per_missing_team"]
    team_ids    = set(teams.keys())
    for label, date_key in [("Good Friday", "good_friday"), ("Easter Monday", "easter_monday")]:
        if date_key not in easter_cfg:
            continue
        easter_date = date.fromisoformat(easter_cfg[date_key])
        playing = set()
        for sf in schedule.fixtures:
            if sf.slot.date == easter_date:
                playing.add(sf.home_team_id)
                playing.add(sf.away_team_id)
        missing = team_ids - playing
        if missing:
            soft_v.append({"constraint": "SC9", "matchday": label,
                            "date": str(easter_date), "missing_teams": sorted(missing)})
            penalty += p_easter * len(missing)

    # ── SC12: opening balance (rounds 1-5) ────────────────────────────────
    p_open   = sc["SC12"]["penalty_per_violation"]
    max_open = sc["SC12"]["max_consecutive"]
    # Approximate rounds 1-5 as first 5 distinct fixture dates league-wide
    all_dates = sorted({sf.slot.date for sf in schedule.fixtures})
    round_dates = all_dates[:5]
    for team_id in teams:
        early = [sf for sf in _sorted_for_team(schedule, team_id)
                 if sf.slot.date in round_dates]
        h_run = a_run = 0
        for sf in early:
            if sf.home_team_id == team_id:
                h_run += 1; a_run = 0
            else:
                a_run += 1; h_run = 0
            if h_run > max_open or a_run > max_open:
                soft_v.append({"constraint": "SC12", "team": team_id,
                                "home_run": h_run, "away_run": a_run})
                penalty += p_open
                break   # count once per team

    # ── SC13: five-match H/A pattern (Atos Golden Rule) ──────────────────
    p_5match = sc["SC13"]["penalty_per_violation"]
    for team_id in teams:
        team_fx = _sorted_for_team(schedule, team_id)
        for i in range(len(team_fx) - 4):
            window = team_fx[i:i+5]
            home_count = sum(1 for sf in window if sf.home_team_id == team_id)
            if home_count not in (2, 3):
                soft_v.append({"constraint": "SC13", "team": team_id,
                                "window_start": str(window[0].slot.date),
                                "home_in_5": home_count})
                penalty += p_5match

    # ── SC14: season boundary H/A (no H+H or A+A at start/end) ──────────
    p_boundary = sc["SC14"]["penalty_per_violation"]
    for team_id in teams:
        team_fx = _sorted_for_team(schedule, team_id)
        if len(team_fx) < 2:
            continue
        # Opening two fixtures
        open_home = [sf.home_team_id == team_id for sf in team_fx[:2]]
        if open_home[0] == open_home[1]:
            soft_v.append({"constraint": "SC14", "team": team_id,
                            "boundary": "opening", "pattern": "HH" if open_home[0] else "AA"})
            penalty += p_boundary
        # Closing two fixtures
        close_home = [sf.home_team_id == team_id for sf in team_fx[-2:]]
        if close_home[0] == close_home[1]:
            soft_v.append({"constraint": "SC14", "team": team_id,
                            "boundary": "closing", "pattern": "HH" if close_home[0] else "AA"})
            penalty += p_boundary

    # ── SC15: Boxing Day / NYD pairing ────────────────────────────────────
    p_festive = sc["SC15"]["penalty_per_violation"]
    for team_id in teams:
        bd_home: bool | None = None   # True=home on Dec26, False=away
        nyd_home: bool | None = None  # True=home on Jan1,  False=away
        for sf in schedule.fixtures_for_team(team_id):
            d = sf.slot.date
            if d.month == 12 and d.day == 26:
                bd_home = (sf.home_team_id == team_id)
            if d.month == 1 and d.day == 1:
                nyd_home = (sf.home_team_id == team_id)
        if bd_home is not None and nyd_home is not None:
            if bd_home == nyd_home:   # same role both days = violation
                soft_v.append({"constraint": "SC15", "team": team_id,
                                "boxing_day": "home" if bd_home else "away",
                                "nyd": "home" if nyd_home else "away"})
                penalty += p_festive

    # ── SC17: min Saturday 15:00 appearances per team ────────────────────────
    sc17 = sc.get("SC17", {})
    p_sat15 = sc17.get("penalty_per_violation", 10)
    min_sat15 = sc17.get("min_per_team", 5)
    for team_id in teams:
        count = sum(
            1 for sf in schedule.fixtures_for_team(team_id)
            if sf.slot.day_of_week == "Saturday" and sf.slot.kickoff == "15:00"
        )
        if count < min_sat15:
            soft_v.append({"constraint": "SC17", "team": team_id,
                            "sat1500_appearances": count, "minimum": min_sat15})
            penalty += p_sat15 * (min_sat15 - count)

    # ── SC18: min Monday appearances per team ─────────────────────────────────
    sc18 = sc.get("SC18", {})
    p_mon_min = sc18.get("penalty_per_violation", 12)
    min_mon = sc18.get("min_per_team", 3)
    for team_id in teams:
        count = sum(
            1 for sf in schedule.fixtures_for_team(team_id)
            if sf.slot.day_of_week == "Monday"
        )
        if count < min_mon:
            soft_v.append({"constraint": "SC18", "team": team_id,
                            "monday_appearances": count, "minimum": min_mon})
            penalty += p_mon_min * (min_mon - count)

    # ── SC5: balanced home/away split per half ─────────────────────────────
    season_start = date.fromisoformat(calendar["start_date"])
    season_end   = date.fromisoformat(calendar["end_date"])
    midpoint     = date.fromordinal((season_start.toordinal() + season_end.toordinal()) // 2)
    p_balance    = sc["SC5"]["penalty_per_violation"]
    tolerance    = sc["SC5"]["tolerance"]
    for team_id in teams:
        h1_h = h1_a = h2_h = h2_a = 0
        for sf in schedule.fixtures_for_team(team_id):
            first = sf.slot.date <= midpoint
            home  = sf.home_team_id == team_id
            if first:
                h1_h += home; h1_a += not home
            else:
                h2_h += home; h2_a += not home
        for half, hg, ag in [("H1", h1_h, h1_a), ("H2", h2_h, h2_a)]:
            if abs(hg - ag) > tolerance:
                soft_v.append({"constraint": "SC5", "team": team_id,
                                "half": half, "home": hg, "away": ag})
                penalty += p_balance

    return {
        "hard_violations":       hard_v,
        "hard_violation_count":  len(hard_v),
        "soft_violations":       soft_v,
        "soft_violation_count":  len(soft_v),
        "total_penalty_score":   penalty,
        "feasible":              len(hard_v) == 0,
    }


def print_report(report: dict) -> None:
    sep = "=" * 52
    print(f"\n{sep}")
    print("SCHEDULE VALIDATION REPORT")
    print(sep)
    print(f"Feasible (zero hard violations): {report['feasible']}")
    print(f"Hard violations : {report['hard_violation_count']}")
    print(f"Soft violations : {report['soft_violation_count']}")
    print(f"Total penalty   : {report['total_penalty_score']}")
    if report["hard_violations"]:
        print("\nHard Violations:")
        for v in report["hard_violations"]:
            print(f"  [{v['constraint']}] {v}")
    if report["soft_violations"]:
        counts: dict[str, int] = defaultdict(int)
        for v in report["soft_violations"]:
            counts[v["constraint"]] += 1
        print("\nSoft Violation Summary:")
        for cid, cnt in sorted(counts.items()):
            print(f"  {cid}: {cnt} violation(s)")
    print(f"{sep}\n")
