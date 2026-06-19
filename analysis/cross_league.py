"""
Cross-league constraint comparison: EPL vs NFL vs NBA.

Loads each league's constraints.json, categorises constraints by
shared thematic pillars, and prints a side-by-side comparison report.

Run:
    python -m analysis.cross_league
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DATA_ROOT = Path(__file__).parent.parent / "data" / "leagues"

LEAGUES = ["epl", "nfl", "nba"]


LEAGUE_META = {
    "epl": {
        "name": "English Premier League",
        "teams": 20,
        "games_per_team": 38,
        "season_weeks": 38,
        "fixture_format": "Double round-robin (380 total fixtures)",
        "solver_platform": "Atos / custom ILP + metaheuristic (Glenn Thompson algorithm)",
    },
    "nfl": {
        "name": "NFL (National Football League)",
        "teams": 32,
        "games_per_team": 17,
        "season_weeks": 18,
        "fixture_format": "Formula-based rotation (272 total games)",
        "solver_platform": "Recentive Analytics + Gurobi (MIP) + AWS optimisation",
    },
    "nba": {
        "name": "NBA (National Basketball Association)",
        "teams": 30,
        "games_per_team": 82,
        "season_weeks": 25,
        "fixture_format": "Weighted round-robin (1230 total games)",
        "solver_platform": "Fastbreak.ai (CP/MIP hybrid, 1M+ constraints, since 2024)",
    },
}

PILLAR_MAP: dict[str, list[str]] = {
    "Rest / Fatigue": [
        "min_rest_days", "thursday_night_minimum_rest", "no_four_in_five_nights",
        "no_eight_in_twelve_nights", "no_back_to_back_long_travel", "max_back_to_backs",
        "marquee_game_minimum_rest", "european_team_rest_days", "fte_rest_equity",
    ],
    "Consecutive Runs": [
        "max_consecutive_away", "max_consecutive_home",
        "max_consecutive_road_games", "max_consecutive_home_games",
        "max_road_trip_length",
    ],
    "Home/Away Balance": [
        "balanced_home_away_split", "home_away_half_season_balance",
        "home_away_balance", "opening_home_away_balance",
    ],
    "Calendar / Blackouts": [
        "blocked_window", "christmas_day_blackout", "all_star_break_blackout",
        "all_star_host_city_blackout", "no_christmas_back_to_back",
        "in_season_tournament_game_days", "arena_unavailability_windows",
    ],
    "Venue / Shared Stadiums": [
        "venue_single_use", "shared_venue_single_use", "single_fixture_per_slot",
        "london_cluster_cap", "same_city_home_clash",
    ],
    "Fixture Format / Coverage": [
        "double_round_robin", "games_per_team", "division_game_distribution",
        "conference_nondivision_distribution", "inter_conference_distribution",
        "intra_division_games", "intra_conference_rotation",
        "inter_conference_rotation", "standings_crossover_games",
        "seventeenth_game", "season_length", "game_distribution",
        "all_teams_play_final_day", "final_day_simultaneous",
    ],
    "Broadcast / Primetime": [
        "primetime_minimum", "primetime_maximum", "primetime_distribution",
        "christmas_day_coverage", "snf_flex_eligibility", "opening_night_champion",
        "weekend_home_game_preference", "avoid_monday_home_in_nfl_markets",
    ],
    "Rivalry / Derby": [
        "derby_min_gap_rounds", "division_rivalry_spread", "rivalry_spread",
        "avoid_same_opponent_cup_league_window",
    ],
    "Travel": [
        "travel_fairness", "travel_timezone_fairness", "international_series_rest",
        "no_road_back_to_back", "tnf_after_road_game",
    ],
    "Special Events": [
        "thanksgiving_home_teams", "thanksgiving_rotation",
        "bye_week", "all_star_break_blackout",
        "mlk_day_preferred_hosts", "ist_no_b2b_second_night",
        "in_season_tournament_balance",
    ],
    "Season Structure": [
        "season_boundary_ha", "boxing_day_nyd_pairing", "five_match_ha_pattern",
        "easter_coverage", "promoted_team_separation", "opening_home_away_balance",
        "no_consecutive_byes", "strength_of_schedule_balance",
    ],
}


@dataclass
class ConstraintSummary:
    league: str
    hard_count: int = 0
    soft_count: int = 0
    pref_count: int = 0
    hard: list[dict] = field(default_factory=list)
    soft: list[dict] = field(default_factory=list)
    prefs: list[dict] = field(default_factory=list)
    pillar_hard: dict[str, list[str]] = field(default_factory=dict)
    pillar_soft: dict[str, list[str]] = field(default_factory=dict)
    avg_soft_penalty: float = 0.0
    max_soft_penalty: float = 0.0


def load_constraints(league: str) -> dict[str, Any]:
    path = DATA_ROOT / league / "constraints.json"
    with open(path) as f:
        return json.load(f)


def classify_constraint(c: dict) -> str:
    ctype = c.get("type", "")
    for pillar, types in PILLAR_MAP.items():
        if ctype in types:
            return pillar
    return "Other"


def summarise(league: str, raw: dict) -> ConstraintSummary:
    s = ConstraintSummary(league=league)
    s.hard  = raw.get("hard", [])
    s.soft  = raw.get("soft", [])
    s.prefs = raw.get("preferences", [])
    s.hard_count = len(s.hard)
    s.soft_count = len(s.soft)
    s.pref_count = len(s.prefs)

    for pillar in PILLAR_MAP:
        s.pillar_hard[pillar] = []
        s.pillar_soft[pillar] = []

    for c in s.hard:
        pillar = classify_constraint(c)
        s.pillar_hard.setdefault(pillar, []).append(c["id"])
    for c in s.soft:
        pillar = classify_constraint(c)
        s.pillar_soft.setdefault(pillar, []).append(c["id"])

    penalties = [
        v for c in s.soft
        for k, v in c.items()
        if "penalty" in k and isinstance(v, (int, float))
    ]
    if penalties:
        s.avg_soft_penalty = round(sum(penalties) / len(penalties), 1)
        s.max_soft_penalty = max(penalties)

    return s


def _bar(n: int, scale: int = 2, char: str = "█") -> str:
    return char * (n * scale)


def print_report(summaries: dict[str, ConstraintSummary]) -> None:
    w = 80
    sep = "─" * w

    print()
    print("=" * w)
    print("  CROSS-LEAGUE CONSTRAINT COMPARISON: EPL | NFL | NBA")
    print("=" * w)

    # League metadata
    print()
    print("LEAGUE OVERVIEW")
    print(sep)
    row_fmt = f"  {{:<30}} {{:<20}} {{:<20}} {{:<20}}"
    print(row_fmt.format("", "EPL", "NFL", "NBA"))
    print(row_fmt.format("-" * 28, "-" * 18, "-" * 18, "-" * 18))
    for field_name, keys in [
        ("Teams",           ["teams"]),
        ("Games / team",    ["games_per_team"]),
        ("Season weeks",    ["season_weeks"]),
    ]:
        vals = [str(LEAGUE_META[lg][keys[0]]) for lg in LEAGUES]
        print(row_fmt.format(field_name, *vals))
    for lg in LEAGUES:
        meta = LEAGUE_META[lg]
        print()
        print(f"  {meta['name']}")
        print(f"    Format  : {meta['fixture_format']}")
        print(f"    Platform: {meta['solver_platform']}")

    # Constraint counts
    print()
    print(sep)
    print("CONSTRAINT INVENTORY")
    print(sep)
    cnt_fmt = f"  {{:<30}} {{:>8}} {{:>8}} {{:>8}}"
    print(cnt_fmt.format("", "EPL", "NFL", "NBA"))
    print(cnt_fmt.format("-" * 28, "-" * 6, "-" * 6, "-" * 6))
    for label, attr in [
        ("Hard constraints",  "hard_count"),
        ("Soft constraints",  "soft_count"),
        ("Preferences",       "pref_count"),
    ]:
        vals = [str(getattr(summaries[lg], attr)) for lg in LEAGUES]
        print(cnt_fmt.format(label, *vals))
    totals = [str(summaries[lg].hard_count + summaries[lg].soft_count + summaries[lg].pref_count)
              for lg in LEAGUES]
    print(cnt_fmt.format("TOTAL", *totals))
    print()
    for label, attr in [
        ("Avg soft penalty weight", "avg_soft_penalty"),
        ("Max soft penalty weight", "max_soft_penalty"),
    ]:
        vals = [str(getattr(summaries[lg], attr)) for lg in LEAGUES]
        print(cnt_fmt.format(label, *vals))

    # Pillar breakdown
    print()
    print(sep)
    print("CONSTRAINT COVERAGE BY PILLAR")
    print(sep)
    all_pillars = list(PILLAR_MAP.keys()) + ["Other"]
    hdr_fmt = f"  {{:<32}} {{:<14}} {{:<14}} {{:<14}}"
    print(hdr_fmt.format("Pillar", "EPL", "NFL", "NBA"))
    print(hdr_fmt.format("-" * 30, "-" * 12, "-" * 12, "-" * 12))
    for pillar in all_pillars:
        row_parts: list[str] = []
        for lg in LEAGUES:
            s = summaries[lg]
            hc_ids = s.pillar_hard.get(pillar, [])
            sc_ids = s.pillar_soft.get(pillar, [])
            parts = []
            if hc_ids:
                parts.append(f"HC:{','.join(hc_ids)}")
            if sc_ids:
                parts.append(f"SC:{','.join(sc_ids)}")
            row_parts.append(", ".join(parts) if parts else "—")
        print(hdr_fmt.format(pillar, row_parts[0], row_parts[1], row_parts[2]))

    # Key structural differences
    print()
    print(sep)
    print("KEY STRUCTURAL COMPARISONS")
    print(sep)

    comparisons = [
        (
            "Player Rest Protection",
            {
                "EPL": "Min 3 days between fixtures (HC1). European teams get 5 days after\n"
                       "            Thursday UEFA games (SC8). No explicit fatigue model.",
                "NFL": "10-day minimum before TNF (HC10). CBA mandates bye week weeks 6-14.\n"
                       "            No back-to-back games possible (weekly schedule).",
                "NBA": "No 4-in-5 nights (HC5), no 8-in-12 nights (HC6), no B2B >1000mi (HC7).\n"
                       "            Max 16 B2Bs/team (HC8). FTE rest equity factor ±5 (SC8).",
            },
        ),
        (
            "Shared Venue / Ground Conflicts",
            {
                "EPL": "One ground per slot (HC6). City-pair clashes avoided in same matchday\n"
                       "            weekend — 4-day window (SC7). London cap: ≤3 home games/day (SC10).",
                "NFL": "MetLife (NYJ/NYG) and SoFi (LAC/LAR) tenants cannot share same date (HC8).\n"
                       "            Stadium ops constraint only — 2 pairs.",
                "NBA": "Arena unavailability windows for NHL, concerts etc. (SC12 — near-hard).\n"
                       "            All-Star host arena blacked out ±3 days (HC11).",
            },
        ),
        (
            "Broadcast / Commercial",
            {
                "EPL": "Top-6 primetime ≥5 slots (PR5). High-risk derbies → early kickoffs (PR4).\n"
                       "            All 380 fixtures TV-produced; slot allocation is primary revenue driver.",
                "NFL": "Every team ≥1 primetime slot (SC4). Max 8 primetime/team (SC5). SNF flex\n"
                       "            eligibility weeks 5-18 (SC10). $110B+ broadcast deals across 4 networks.",
                "NBA": "Christmas Day 5 marquee games — near-contractual (SC5). Opening night\n"
                       "            defending champion (SC6). Top-market teams ≥10 national appearances (SC6).",
            },
        ),
        (
            "Special Calendar Events",
            {
                "EPL": "Boxing Day (Dec 26) + NYD (Jan 1) all-teams coverage (PR2). Opposite\n"
                       "            H/A on Boxing Day vs NYD (SC15, Golden Rule). Easter all-teams (SC9).\n"
                       "            Christmas Day blackout (HC7).",
                "NFL": "Thanksgiving: DAL + DET home (HC9). Third Thanksgiving game rotation (SC12).\n"
                       "            Christmas Day games (no blackout — contractual game required). Bye week\n"
                       "            distribution across weeks 6-14.",
                "NBA": "All-Star break blackout (HC10). IST group stage games on designated\n"
                       "            Tue/Fri in November (HC12). All 30 teams play final regular season day (HC13).\n"
                       "            MLK Day preferred markets (SC7).",
            },
        ),
        (
            "Consecutive Home/Away Limits",
            {
                "EPL": "Soft: ≤5 consecutive home or away (SC1/SC2). Historical mean max is\n"
                       "            7-8; constraint is aspirational. Five-match H/A pattern enforced (SC13).",
                "NFL": "Soft: ≤3 consecutive road games (SC1), ≤4 consecutive home (SC2).\n"
                       "            Stricter than EPL due to weekly cadence and fan revenue dependence.",
                "NBA": "Soft: ≤6 consecutive road games (SC3). Back-to-back road games avoided\n"
                       "            (SC2). Road trip minimisation is primary operational constraint.",
            },
        ),
        (
            "Rivalry / Derby Spread",
            {
                "EPL": "Derby legs ≥8 rounds apart (SC3). High-risk derbies → early kickoffs (PR4).\n"
                       "            Promoted teams avoid each other in rounds 1-3 (SC11).",
                "NFL": "Division rivalry legs ≥5 weeks apart (SC11). Formula ensures home/away\n"
                       "            split is fixed — no rotation needed within division.",
                "NBA": "High-profile rivalry legs ≥20 games apart in team schedule (SC7).\n"
                       "            Division games (x4) spread across season implicitly.",
            },
        ),
        (
            "Home/Away Balance",
            {
                "EPL": "Tolerance ±2 per 19-game half-season (SC5). SC13 (5-match window) and\n"
                       "            SC14 (season boundary) enforce structural balance globally.",
                "NFL": "Tolerance ±1 game per 9-week half-season (SC8). Formula structure mostly\n"
                       "            determines balance automatically (8H + 9A or 9H + 8A per team).",
                "NBA": "Exact 41H / 41A total. Tolerance ±3 per half-season (SC4).\n"
                       "            Weekend home preference to maximise gate revenue (SC10).",
            },
        ),
        (
            "Travel Management",
            {
                "EPL": "European team rest buffer (SC4/SC8). City-pair clash avoidance reduces\n"
                       "            same-day travel clashes. No explicit cross-country travel constraint.",
                "NFL": "Max 2 consecutive cross-country trips (SC7). International series: ≥7 days\n"
                       "            rest before/after London, Munich, Brazil games (SC9).",
                "NBA": "No B2B second leg if travel >1000 miles (HC7). No timezone change >2\n"
                       "            zones on B2B second night (SC9). Road trip max 6 games (SC3).",
            },
        ),
    ]

    for title, league_texts in comparisons:
        print()
        print(f"  ▶ {title}")
        for lg_key, text in league_texts.items():
            lg_name = {"EPL": "EPL", "NFL": "NFL", "NBA": "NBA"}[lg_key]
            print(f"      [{lg_name}] {text}")

    # Unique constraints per league
    print()
    print(sep)
    print("LEAGUE-UNIQUE CONSTRAINTS (no direct equivalent in the other two)")
    print(sep)
    unique = {
        "EPL": [
            "SC13 — Five-match H/A pattern (Atos Golden Rule): any 5-game window must be 2H+3A or 3H+2A",
            "SC14 — Season boundary H/A (Atos Golden Rule): cannot start or finish season with HH or AA",
            "SC15 — Boxing Day / New Year's Day pairing (Atos Golden Rule): opposite H/A on each date",
            "SC10 — London cluster cap: ≤3 London clubs at home on any single day (police/TfL capacity)",
            "HC8  — Final day simultaneous kickoff (all Round 38 games at 16:00 — anti-collusion since 1994)",
            "SC11 — Promoted team separation: the 3 promoted clubs avoid each other in rounds 1-3",
            "SC9  — Easter all-teams coverage: all 20 clubs play on Good Friday AND Easter Monday",
        ],
        "NFL": [
            "HC6  — 17th game formula: inter-conference opponent matched by same prior-year division finish",
            "HC5  — Standings crossover games: 2 games vs teams with same prior-year finish in other conf. divisions",
            "HC9  — Thanksgiving fixed hosts: Dallas and Detroit always play home on Thanksgiving",
            "SC10 — SNF flex scheduling: weeks 5-18 must maintain flex-eligible matchup pool for NBC",
            "SC6  — Strength-of-schedule parity: division rivals face same opponents in rotation slots",
            "HC10 — TNF 10-day rest minimum: hardest per-game rest protection in any major US sport",
            "SC9  — International Series rest: ≥7 days before/after London, Munich, Brazil games",
        ],
        "NBA": [
            "HC5  — No 4-in-5-nights (CBA hard limit — triggers league fines if violated)",
            "HC6  — No 8-in-12-nights (CBA extended rest protection)",
            "HC7  — No B2B with second leg >1000 miles from first venue (CBA 2023)",
            "HC13 — All 30 teams play on the final day of the regular season (seeding integrity)",
            "HC12 — In-Season Tournament: group stage games on designated Tue/Fri in November",
            "SC8  — FTE (Fresh-Tired-Even) rest equity factor: competitive fairness metric ±5",
            "SC5  — Christmas Day 5-game slate: near-contractual with ABC/ESPN ($76B deal)",
            "SC13 — Avoid Monday home in NFL markets during NFL season (cross-sport broadcast conflict)",
        ],
    }
    for lg in LEAGUES:
        lg_key = lg.upper()
        print(f"\n  {LEAGUE_META[lg]['name']}")
        for item in unique[lg_key]:
            print(f"    • {item}")

    # Summary comparison table
    print()
    print(sep)
    print("QUANTITATIVE SUMMARY")
    print(sep)
    quant_fmt = f"  {{:<38}} {{:>10}} {{:>10}} {{:>10}}"
    print(quant_fmt.format("Metric", "EPL", "NFL", "NBA"))
    print(quant_fmt.format("-" * 36, "-" * 8, "-" * 8, "-" * 8))
    rows = [
        ("Teams",                       "20",       "32",       "30"),
        ("Games per team / season",      "38",       "17",       "82"),
        ("Total fixtures",               "380",      "272",      "1,230"),
        ("Season span (weeks)",          "38",       "18",       "~25"),
        ("Hard constraints",
            str(summaries["epl"].hard_count),
            str(summaries["nfl"].hard_count),
            str(summaries["nba"].hard_count)),
        ("Soft constraints",
            str(summaries["epl"].soft_count),
            str(summaries["nfl"].soft_count),
            str(summaries["nba"].soft_count)),
        ("Preference constraints (EPL)", str(summaries["epl"].pref_count), "—", "—"),
        ("Avg soft penalty weight",
            str(summaries["epl"].avg_soft_penalty),
            str(summaries["nfl"].avg_soft_penalty),
            str(summaries["nba"].avg_soft_penalty)),
        ("Max soft penalty weight",
            str(summaries["epl"].max_soft_penalty),
            str(summaries["nfl"].max_soft_penalty),
            str(summaries["nba"].max_soft_penalty)),
        ("Min inter-game rest (hard)",   "3 days",   "10 days",  "—"),
        ("Back-to-backs allowed",        "N/A",      "N/A",      "Yes (≤16)"),
        ("Shared venue pairs",           "N/A",      "2",        "varies"),
        ("Festive special matchdays",    "3",        "2 (Thx,Xmas)", "3 (Xmas,OPN,IST)"),
    ]
    for row in rows:
        print(quant_fmt.format(*row))

    print()
    print("=" * w)
    print("  END OF CROSS-LEAGUE COMPARISON REPORT")
    print("=" * w)
    print()


def main() -> None:
    summaries: dict[str, ConstraintSummary] = {}
    for lg in LEAGUES:
        raw = load_constraints(lg)
        summaries[lg] = summarise(lg, raw)
    print_report(summaries)


if __name__ == "__main__":
    main()
