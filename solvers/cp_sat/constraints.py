"""
Option A — CP-SAT: Constraint builder modules.

Decision variable layout (sparse):
    x[(fixture_id, slot_id)] ∈ {0, 1}  — only exists for eligible pairs
    = 1 if fixture fixture_id is assigned to slot slot_id

All functions must guard against missing keys; use `if (fid, sid) in x`.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ortools.sat.python import cp_model

from core.models import Fixture, Slot, Team
from core.data_loader import load_city_groups, load_high_profile_derbies, load_calendar


def _fixture_slot_index(x: dict, slots: list[Slot]) -> dict[str, list[tuple[str, Slot]]]:
    """Build {fixture_id: [(slot_id, Slot), ...]} from the sparse x dict."""
    slot_map: dict[str, Slot] = {s.slot_id: s for s in slots}
    idx: dict[str, list[tuple[str, Slot]]] = defaultdict(list)
    for fid, sid in x:
        idx[fid].append((sid, slot_map[sid]))
    return dict(idx)


# ---------------------------------------------------------------------------
# Hard constraints
# ---------------------------------------------------------------------------

def add_each_fixture_assigned_exactly_once(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
) -> None:
    """HC4 — every fixture gets exactly one slot (from its eligible set)."""
    fsi = _fixture_slot_index(x, slots)
    for fixture in fixtures:
        eligible_vars = [x[(fixture.fixture_id, sid)] for sid, _ in fsi.get(fixture.fixture_id, [])]
        if eligible_vars:
            model.add_exactly_one(eligible_vars)


def add_team_plays_at_most_once_per_slot(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
) -> None:
    """HC5 — a team cannot appear in two fixtures on the same calendar day."""
    fsi = _fixture_slot_index(x, slots)

    # Group eligible (fixture, slot) by team and date
    team_date_vars: dict[tuple[str, str], list] = defaultdict(list)
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            date_str = str(slot.date)
            for team_id in (fixture.home_team_id, fixture.away_team_id):
                team_date_vars[(team_id, date_str)].append(x[(fixture.fixture_id, sid)])

    for vars_on_date in team_date_vars.values():
        if len(vars_on_date) >= 2:
            model.add(sum(vars_on_date) <= 1)


def add_min_rest_days(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    min_days: int = 3,
) -> None:
    """HC1 — minimum gap between consecutive fixtures for each team.

    Sliding-window formulation: for each team and each date d, at most one
    assignment variable may be active in the window [d, d + min_days - 1].
    Produces O(teams × dates) ≈ 7,460 constraints vs the naive O(pairs²) ≈ 563K.
    """
    fsi = _fixture_slot_index(x, slots)

    team_date_vars: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            for team_id in (fixture.home_team_id, fixture.away_team_id):
                team_date_vars[team_id][slot.date].append((fixture.fixture_id, sid))

    for team_id, date_map in team_date_vars.items():
        for d in sorted(date_map.keys()):
            window_vars = []
            for offset in range(min_days):
                wd = d + timedelta(days=offset)
                for fid, sid in date_map.get(wd, []):
                    window_vars.append(x[(fid, sid)])
            if len(window_vars) >= 2:
                model.add(sum(window_vars) <= 1)


def add_no_same_city_home_clash(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
) -> None:
    """HC2 — no two same-city teams can have home games on the same day."""
    city_groups = load_city_groups()
    fsi = _fixture_slot_index(x, slots)

    # Build {team_id: {date_str: [vars]}} for home fixtures only
    home_date_vars: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            home_date_vars[fixture.home_team_id][str(slot.date)].append(
                x[(fixture.fixture_id, sid)]
            )

    for city, members in city_groups.items():
        if len(members) < 2:
            continue
        # Collect all dates where any city member has eligible home slots
        all_dates: set[str] = set()
        for team_id in members:
            all_dates |= home_date_vars[team_id].keys()
        for date_str in all_dates:
            city_vars = []
            for team_id in members:
                city_vars.extend(home_date_vars[team_id].get(date_str, []))
            if len(city_vars) >= 2:
                model.add(sum(city_vars) <= 1)


def add_max_midweek_games_per_team(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_midweek: int = 10,
) -> None:
    """HC10 — each team plays at most max_midweek games on Tuesday or Wednesday."""
    fsi = _fixture_slot_index(x, slots)
    midweek_sids = {s.slot_id for s in slots if s.day_of_week in ("Tuesday", "Wednesday")}

    for team_id in teams:
        mw_vars = []
        for fixture in fixtures:
            if fixture.home_team_id != team_id and fixture.away_team_id != team_id:
                continue
            for sid, _ in fsi.get(fixture.fixture_id, []):
                if sid in midweek_sids:
                    mw_vars.append(x[(fixture.fixture_id, sid)])
        if len(mw_vars) > max_midweek:
            model.add(sum(mw_vars) <= max_midweek)


def add_max_single_day_games_per_team(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    day_of_week: str,
    max_games: int,
) -> None:
    """Generic per-team cap on games falling on a specific day of week."""
    fsi = _fixture_slot_index(x, slots)
    target_sids = {s.slot_id for s in slots if s.day_of_week == day_of_week}

    for team_id in teams:
        team_vars = []
        for fixture in fixtures:
            if fixture.home_team_id != team_id and fixture.away_team_id != team_id:
                continue
            for sid, _ in fsi.get(fixture.fixture_id, []):
                if sid in target_sids:
                    team_vars.append(x[(fixture.fixture_id, sid)])
        if len(team_vars) > max_games:
            model.add(sum(team_vars) <= max_games)


def add_max_monday_games_per_team(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_monday: int = 3,
) -> None:
    """HC11 — each team plays at most max_monday games on Monday."""
    add_max_single_day_games_per_team(model, x, fixtures, slots, teams, "Monday", max_monday)


def add_max_friday_games_per_team(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_friday: int = 3,
) -> None:
    """HC9 — each team plays at most max_friday games on a Friday."""
    add_max_single_day_games_per_team(model, x, fixtures, slots, teams, "Friday", max_friday)


def add_max_wednesday_games_per_team(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_wednesday: int = 6,
) -> None:
    """HC12 — each team plays at most max_wednesday games on Wednesday."""
    add_max_single_day_games_per_team(model, x, fixtures, slots, teams, "Wednesday", max_wednesday)


def add_max_thursday_games_per_team(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_thursday: int = 2,
) -> None:
    """HC13 — each team plays at most max_thursday games on Thursday."""
    add_max_single_day_games_per_team(model, x, fixtures, slots, teams, "Thursday", max_thursday)


# ---------------------------------------------------------------------------
# Soft constraints (returned as (weight, bool_var) penalty terms)
# ---------------------------------------------------------------------------

def add_soft_max_consecutive_home_away(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    max_run: int = 3,
    penalty: int = 20,
) -> list:
    """SC1/SC2 — penalise runs of more than max_run consecutive home or away.
    Skeleton; full run-tracking requires auxiliary sequencing variables."""
    return []


def add_soft_ha_window(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    window: int = 5,
    min_home: int = 2,
    max_home: int = 3,
    penalty: int = 25,
) -> list:
    """SC13 (Atos Golden Rule) — penalise home-game clusters within date windows.

    For each team and each rolling date window, penalise excess home or away
    games assigned within that span.

    The primary constraint for SC13 correctness is the slot filter in
    build_eligible_slots (window_rounds=2): by limiting each fixture to dates
    within ±2 natural rounds, the solver cannot reorder fixtures by more than
    2 rounds, keeping consecutive-fixture H/A patterns close to the natural
    ordering (which has 0 SC13 violations by construction from generate_epl.py).

    This date-window penalty acts as a secondary soft push to discourage the
    residual reordering violations that can still occur within the ±2 window.
    """
    fsi = _fixture_slot_index(x, slots)
    penalty_terms = []

    team_home_date: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    team_away_date: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            team_home_date[fixture.home_team_id][slot.date].append(
                x[(fixture.fixture_id, sid)]
            )
            team_away_date[fixture.away_team_id][slot.date].append(
                x[(fixture.fixture_id, sid)]
            )

    all_dates = sorted({slot.date for slot in slots})
    if not all_dates:
        return penalty_terms

    # 35-day window: covers ~5 EPL fixtures in normal periods.
    # The slot filter (window_rounds=2) prevents fixtures from spanning
    # more than ±2 rounds (~15 days) from their natural position, so 5
    # consecutive games now fit comfortably within 35 days in most cases.
    window_days = 35

    for team_id in teams:
        home_by_date = team_home_date[team_id]
        away_by_date = team_away_date[team_id]

        for d in all_dates:
            end_d = d + timedelta(days=window_days)

            home_in_win: list = []
            away_in_win: list = []
            for wd in all_dates:
                if wd < d or wd > end_d:
                    continue
                home_in_win.extend(home_by_date.get(wd, []))
                away_in_win.extend(away_by_date.get(wd, []))

            if len(home_in_win) > max_home:
                hc = model.new_int_var(0, len(home_in_win), f"hc_{team_id}_{d}")
                model.add(hc == sum(home_in_win))
                exc = model.new_int_var(0, len(home_in_win) - max_home,
                                        f"exc_{team_id}_{d}")
                model.add(exc >= hc - max_home)
                penalty_terms.append((penalty, exc))

            max_away = window - min_home
            if len(away_in_win) > max_away:
                ac = model.new_int_var(0, len(away_in_win), f"ac_{team_id}_{d}")
                model.add(ac == sum(away_in_win))
                dfc = model.new_int_var(0, len(away_in_win) - max_away,
                                        f"dfc_{team_id}_{d}")
                model.add(dfc >= ac - max_away)
                penalty_terms.append((penalty, dfc))

    return penalty_terms


def add_soft_same_city_home_clash(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    window_days: int = 4,
    penalty: int = 40,
) -> list:
    """SC7 — penalise same-city teams both at home within a window_days window.

    HC2 was demoted to SC7 because 14–34 same-city home clashes occur per
    real EPL season, making a hard ban infeasible. The 4-day window captures
    the matchday-weekend planning horizon used by police.
    """
    city_groups = load_city_groups()
    fsi = _fixture_slot_index(x, slots)
    penalty_terms = []

    home_date_vars: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            home_date_vars[fixture.home_team_id][slot.date].append(
                x[(fixture.fixture_id, sid)]
            )

    all_dates = sorted({slot.date for slot in slots})
    if not all_dates:
        return penalty_terms

    for city, members in city_groups.items():
        if len(members) < 2:
            continue
        for i, d in enumerate(all_dates):
            end_d = d + timedelta(days=window_days - 1)
            city_home_in_win: list = []
            for wd in all_dates[i:]:
                if wd > end_d:
                    break
                for team_id in members:
                    city_home_in_win.extend(home_date_vars[team_id].get(wd, []))

            if len(city_home_in_win) <= 1:
                continue

            hc = model.new_int_var(0, len(city_home_in_win), f"sc7h_{city}_{d}")
            model.add(hc == sum(city_home_in_win))
            exc = model.new_int_var(0, len(city_home_in_win) - 1, f"sc7e_{city}_{d}")
            model.add(exc >= hc - 1)
            penalty_terms.append((penalty, exc))

    return penalty_terms


def add_soft_derby_gap(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    min_gap_days: int = 56,
    penalty: int = 30,
) -> list:
    """SC3 — penalise derby legs scheduled fewer than min_gap_days apart."""
    derbies = load_high_profile_derbies()
    fsi = _fixture_slot_index(x, slots)
    penalty_terms = []

    for team_a, team_b in derbies:
        leg1 = next(
            (f for f in fixtures if f.home_team_id == team_a and f.away_team_id == team_b), None
        )
        leg2 = next(
            (f for f in fixtures if f.home_team_id == team_b and f.away_team_id == team_a), None
        )
        if not (leg1 and leg2):
            continue

        for sid1, s1 in fsi.get(leg1.fixture_id, []):
            for sid2, s2 in fsi.get(leg2.fixture_id, []):
                gap = abs((s2.date - s1.date).days)
                if 0 < gap < min_gap_days:
                    viol = model.new_bool_var(
                        f"derby_{leg1.fixture_id}_{leg2.fixture_id}_{sid1}_{sid2}"
                    )
                    model.add_bool_and([
                        x[(leg1.fixture_id, sid1)],
                        x[(leg2.fixture_id, sid2)],
                    ]).only_enforce_if(viol)
                    penalty_terms.append((penalty, viol))

    return penalty_terms


def add_soft_london_cluster(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    max_per_day: int = 3,
    penalty: int = 30,
) -> list:
    """SC10 — penalise >max_per_day London teams hosting on the same calendar day."""
    city_groups = load_city_groups()
    london_teams = set(city_groups.get("London", []))
    if not london_teams:
        return []

    fsi = _fixture_slot_index(x, slots)
    penalty_terms = []

    home_date_vars: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        if fixture.home_team_id in london_teams:
            for sid, slot in fsi.get(fixture.fixture_id, []):
                home_date_vars[fixture.home_team_id][slot.date].append(
                    x[(fixture.fixture_id, sid)]
                )

    all_dates = sorted({slot.date for slot in slots})
    for d in all_dates:
        day_vars = []
        for team_id in london_teams:
            day_vars.extend(home_date_vars[team_id].get(d, []))

        if len(day_vars) <= max_per_day:
            continue

        total = model.new_int_var(0, len(day_vars), f"sc10_{d}")
        model.add(total == sum(day_vars))
        exc = model.new_int_var(0, len(day_vars) - max_per_day, f"sc10e_{d}")
        model.add(exc >= total - max_per_day)
        penalty_terms.append((penalty, exc))

    return penalty_terms


def add_soft_festive_coverage(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    penalty: int = 20,
) -> list:
    """SC9/PR2 — penalise missing team coverage on Boxing Day, Dec 28, Good Friday, Easter Monday.

    Only fires for festive dates that actually have eligible slots in the pool.
    NYD (Jan 1) is currently a Thursday with no slots defined — it will be
    skipped here until Thursday slots are added to calendar.json.
    """
    from datetime import date as _date

    fsi = _fixture_slot_index(x, slots)
    calendar = load_calendar()
    penalty_terms = []
    all_team_ids = set(teams.keys())

    festive_dates: set[_date] = set()
    for d in calendar.get("festive_matchdays", []):
        festive_dates.add(_date.fromisoformat(d))
    easter = calendar.get("easter_matchdays", {})
    for key in ("good_friday", "easter_monday"):
        if key in easter:
            festive_dates.add(_date.fromisoformat(easter[key]))

    slot_dates = {slot.date for slot in slots}
    active_festive = sorted(festive_dates & slot_dates)

    for fest_date in active_festive:
        for team_id in all_team_ids:
            team_vars = [
                x[(f.fixture_id, sid)]
                for f in fixtures
                if f.home_team_id == team_id or f.away_team_id == team_id
                for sid, slot in fsi.get(f.fixture_id, [])
                if slot.date == fest_date
            ]
            if not team_vars:
                continue
            plays = model.new_bool_var(f"fst_{team_id}_{fest_date}")
            model.add_max_equality(plays, team_vars)
            penalty_terms.append((penalty, plays.Not()))

    return penalty_terms


def add_soft_sc14_season_boundary(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    penalty: int = 30,
) -> list:
    """SC14 — penalise HH or AA in the opening/closing 14-day boundary window.

    Proxy for the Atos Golden Rule: teams must not start or finish with two
    consecutive home (HH) or two consecutive away (AA) fixtures. Uses a 14-day
    window from season start/end to approximate the first/last two rounds.

    Direct sum inequalities (no intermediate aggregation var) for solver speed:
    - hh_exc >= sum(home_vars) - 1: fires when ≥2 home in window
    - aa_def >= 1 - sum(home_vars): fires when 0 home in window (all away)
    """
    from datetime import date as _date

    calendar = load_calendar()
    season_start = _date.fromisoformat(calendar["start_date"])
    season_end = _date.fromisoformat(calendar["end_date"])
    window = timedelta(days=14)
    open_cutoff = season_start + window
    close_cutoff = season_end - window

    fsi = _fixture_slot_index(x, slots)
    penalty_terms = []

    for team_id in teams:
        for label, lo, hi in [
            ("open", season_start, open_cutoff),
            ("close", close_cutoff, season_end),
        ]:
            home_vars: list = []
            total_vars: list = []
            for fixture in fixtures:
                is_home = (fixture.home_team_id == team_id)
                is_away = (fixture.away_team_id == team_id)
                if not (is_home or is_away):
                    continue
                for sid, slot in fsi.get(fixture.fixture_id, []):
                    if lo <= slot.date <= hi:
                        v = x[(fixture.fixture_id, sid)]
                        total_vars.append(v)
                        if is_home:
                            home_vars.append(v)

            if len(total_vars) < 2 or not home_vars:
                continue

            home_sum = sum(home_vars)

            if len(home_vars) >= 2:
                hh_exc = model.new_int_var(0, len(home_vars) - 1, f"sc14_hx_{team_id}_{label}")
                model.add(hh_exc >= home_sum - 1)
                penalty_terms.append((penalty, hh_exc))

            aa_def = model.new_int_var(0, 1, f"sc14_ad_{team_id}_{label}")
            model.add(aa_def >= 1 - home_sum)
            penalty_terms.append((penalty, aa_def))

    return penalty_terms


def add_soft_sc15_boxing_day_nyd(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    penalty: int = 35,
) -> list:
    """SC15 — penalise matching H/A designation on Boxing Day and New Year's Day.

    Atos Golden Rule: if a team is home on Dec 26 they must be away on Jan 1,
    and vice versa. Penalises both HH (both home) and AA (both away).

    Linear sum equality replaces add_max_equality — valid since HC5 ensures each
    team has at most one fixture per date (sum ≡ max for binary vars). Each sum
    becomes a single linear constraint vs n+1 OR-clause constraints from
    add_max_equality, reducing model complexity significantly.
    Linear AND encoding (no conditional constraints) for solver speed:
    - same_hh = h_bd AND h_nyd (standard 3-inequality linearisation)
    - same_aa = away_bd AND away_nyd, where away_x = plays_x − home_x
    """
    from datetime import date as _date

    calendar = load_calendar()
    yr = _date.fromisoformat(calendar["start_date"]).year
    boxing_day = _date(yr, 12, 26)
    nyd = _date(yr + 1, 1, 1)

    fsi = _fixture_slot_index(x, slots)
    penalty_terms = []

    for team_id in teams:
        home_bd = [
            x[(f.fixture_id, sid)]
            for f in fixtures if f.home_team_id == team_id
            for sid, slot in fsi.get(f.fixture_id, []) if slot.date == boxing_day
        ]
        home_nyd = [
            x[(f.fixture_id, sid)]
            for f in fixtures if f.home_team_id == team_id
            for sid, slot in fsi.get(f.fixture_id, []) if slot.date == nyd
        ]
        all_bd = [
            x[(f.fixture_id, sid)]
            for f in fixtures
            if f.home_team_id == team_id or f.away_team_id == team_id
            for sid, slot in fsi.get(f.fixture_id, []) if slot.date == boxing_day
        ]
        all_nyd = [
            x[(f.fixture_id, sid)]
            for f in fixtures
            if f.home_team_id == team_id or f.away_team_id == team_id
            for sid, slot in fsi.get(f.fixture_id, []) if slot.date == nyd
        ]

        if not all_bd or not all_nyd:
            continue

        # sum() ≡ max() here because HC5 ensures at most one fixture per team per date
        h_bd = model.new_bool_var(f"sc15_hbd_{team_id}")
        model.add(h_bd == sum(home_bd))

        h_nyd = model.new_bool_var(f"sc15_hnyd_{team_id}")
        model.add(h_nyd == sum(home_nyd))

        plays_bd = model.new_bool_var(f"sc15_pbd_{team_id}")
        model.add(plays_bd == sum(all_bd))

        plays_nyd = model.new_bool_var(f"sc15_pnyd_{team_id}")
        model.add(plays_nyd == sum(all_nyd))

        # HH: home on BD AND home on NYD (linear AND of two binary vars)
        same_hh = model.new_bool_var(f"sc15_hh_{team_id}")
        model.add(same_hh <= h_bd)
        model.add(same_hh <= h_nyd)
        model.add(same_hh >= h_bd + h_nyd - 1)

        # AA: away on BD AND away on NYD (away_x = plays_x − home_x)
        same_aa = model.new_bool_var(f"sc15_aa_{team_id}")
        model.add(same_aa <= plays_bd - h_bd)
        model.add(same_aa <= plays_nyd - h_nyd)
        model.add(same_aa >= plays_bd - h_bd + plays_nyd - h_nyd - 1)

        penalty_terms.append((penalty, same_hh))
        penalty_terms.append((penalty, same_aa))

    return penalty_terms


def add_soft_min_sat_1500(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    min_per_team: int = 5,
    penalty: int = 10,
) -> list:
    """SC17 — penalise fewer than min_per_team Saturday 15:00 appearances per team.

    The unbroadcast Saturday 3pm slot accounts for ~47% of all EPL fixtures;
    historically each team plays ~9 home and ~9 away games at 15:00. A soft
    floor of 5 total pushes the solver toward realistic usage of this slot pool.

    Per-slot aggregation: group eligible (fixture, slot) pairs by slot, build a
    BoolVar per slot ("does team play at this slot?"), then sum over ~35 slot-level
    BoolVars instead of ~133 raw decision variables. Reduces LP row density ~4×.
    sum() == max() for binary slot-appears vars because HC5 ensures at most one
    fixture per team per slot.
    """
    fsi = _fixture_slot_index(x, slots)
    sat15_sids = {
        s.slot_id for s in slots
        if s.day_of_week == "Saturday" and s.kickoff == "15:00"
    }
    if not sat15_sids:
        return []

    penalty_terms = []
    for team_id in teams:
        slot_to_fvars: dict[str, list] = defaultdict(list)
        for f in fixtures:
            if f.home_team_id == team_id or f.away_team_id == team_id:
                for sid, _ in fsi.get(f.fixture_id, []):
                    if sid in sat15_sids:
                        slot_to_fvars[sid].append(x[(f.fixture_id, sid)])

        if not slot_to_fvars:
            continue

        slot_appears = []
        for slot_id, fvars in slot_to_fvars.items():
            if len(fvars) == 1:
                slot_appears.append(fvars[0])
            else:
                # Multiple fixtures eligible for same slot; sum() == max() (HC5)
                appear = model.new_bool_var(f"sc17_ap_{team_id}_{slot_id}")
                model.add(appear == sum(fvars))
                slot_appears.append(appear)

        if len(slot_appears) < min_per_team:
            continue

        deficit = model.new_int_var(0, min_per_team, f"sc17_def_{team_id}")
        model.add(deficit >= min_per_team - sum(slot_appears))
        penalty_terms.append((penalty, deficit))

    return penalty_terms


def add_soft_min_monday(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    min_per_team: int = 3,
    penalty: int = 12,
) -> list:
    """SC18 — penalise fewer than min_per_team Monday appearances per team.

    Historical data shows teams average ~3.7 Monday Night Football appearances
    per season. A soft floor of 3 corrects the solver's tendency to avoid Monday
    slots (which have only one kickoff time vs Saturday's three).

    Per-slot aggregation: same pattern as SC17 — groups by Monday slot to
    reduce LP row density from ~64 terms to ~17 terms per team.
    """
    fsi = _fixture_slot_index(x, slots)
    mon_sids = {s.slot_id for s in slots if s.day_of_week == "Monday"}
    if not mon_sids:
        return []

    penalty_terms = []
    for team_id in teams:
        slot_to_fvars: dict[str, list] = defaultdict(list)
        for f in fixtures:
            if f.home_team_id == team_id or f.away_team_id == team_id:
                for sid, _ in fsi.get(f.fixture_id, []):
                    if sid in mon_sids:
                        slot_to_fvars[sid].append(x[(f.fixture_id, sid)])

        if not slot_to_fvars:
            continue

        slot_appears = []
        for slot_id, fvars in slot_to_fvars.items():
            if len(fvars) == 1:
                slot_appears.append(fvars[0])
            else:
                # Multiple fixtures eligible for same slot; sum() == max() (HC5)
                appear = model.new_bool_var(f"sc18_ap_{team_id}_{slot_id}")
                model.add(appear == sum(fvars))
                slot_appears.append(appear)

        if len(slot_appears) < min_per_team:
            continue

        deficit = model.new_int_var(0, min_per_team, f"sc18_def_{team_id}")
        model.add(deficit >= min_per_team - sum(slot_appears))
        penalty_terms.append((penalty, deficit))

    return penalty_terms
