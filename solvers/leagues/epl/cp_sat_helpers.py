"""
EPL-specific constraint builders for the CP-SAT solver.

These encode EPL-only rules and Atos Golden Rule concepts (Boxing Day / New
Year's Day pairing, the London cluster cap, the Saturday 15:00 blackout slot
floor, derby-gap spacing, same-city clash windows, festive coverage) that
have no equivalent in other leagues. They live here — scoped to
``solvers/leagues/epl/`` — rather than in ``solvers/cp_sat/constraints.py``,
which is shared across all leagues and must stay league-agnostic.

Moved out of solvers/cp_sat/constraints.py with zero behaviour changes.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from ortools.sat.python import cp_model

from core.models import Fixture, Slot, Team
from core.data_loader import load_city_groups, load_high_profile_derbies, load_calendar
from solvers.cp_sat.constraints import _fixture_slot_index


def add_no_same_city_home_clash(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
) -> None:
    """HC2 (demoted to SC7, kept for reference) — no two same-city teams can
    have home games on the same day."""
    city_groups = load_city_groups()
    fsi = _fixture_slot_index(x, slots)

    home_date_vars: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        for sid, slot in fsi.get(fixture.fixture_id, []):
            home_date_vars[fixture.home_team_id][str(slot.date)].append(
                x[(fixture.fixture_id, sid)]
            )

    for city, members in city_groups.items():
        if len(members) < 2:
            continue
        all_dates: set[str] = set()
        for team_id in members:
            all_dates |= home_date_vars[team_id].keys()
        for date_str in all_dates:
            city_vars = []
            for team_id in members:
                city_vars.extend(home_date_vars[team_id].get(date_str, []))
            if len(city_vars) >= 2:
                model.add(sum(city_vars) <= 1)


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
    """SC13 (Atos Golden Rule) — penalise home-game clusters within date windows."""
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

    window_days = 35  # covers ~5 EPL fixtures in normal periods

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
    """SC7 — penalise same-city teams both at home within a window_days window."""
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


def add_soft_city_cluster(
    model: cp_model.CpModel,
    x: dict,
    fixtures: list[Fixture],
    slots: list[Slot],
    city_name: str = "London",
    max_per_day: int = 3,
    penalty: int = 30,
) -> list:
    """SC10 — penalise >max_per_day teams from city_name hosting on the same day."""
    city_groups = load_city_groups()
    cluster_teams = set(city_groups.get(city_name, []))
    if not cluster_teams:
        return []

    fsi = _fixture_slot_index(x, slots)
    penalty_terms = []

    home_date_vars: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for fixture in fixtures:
        if fixture.home_team_id in cluster_teams:
            for sid, slot in fsi.get(fixture.fixture_id, []):
                home_date_vars[fixture.home_team_id][slot.date].append(
                    x[(fixture.fixture_id, sid)]
                )

    all_dates = sorted({slot.date for slot in slots})
    for d in all_dates:
        day_vars = []
        for team_id in cluster_teams:
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
    """SC9/PR2 — penalise missing team coverage on Boxing Day, Dec 28, Good Friday, Easter Monday."""
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
    """SC14 (Atos Golden Rule) — penalise HH or AA in the opening/closing
    14-day boundary window."""
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
    """SC15 (Atos Golden Rule) — penalise matching H/A designation on Boxing
    Day and New Year's Day."""
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

        h_bd = model.new_bool_var(f"sc15_hbd_{team_id}")
        model.add(h_bd == sum(home_bd))

        h_nyd = model.new_bool_var(f"sc15_hnyd_{team_id}")
        model.add(h_nyd == sum(home_nyd))

        plays_bd = model.new_bool_var(f"sc15_pbd_{team_id}")
        model.add(plays_bd == sum(all_bd))

        plays_nyd = model.new_bool_var(f"sc15_pnyd_{team_id}")
        model.add(plays_nyd == sum(all_nyd))

        same_hh = model.new_bool_var(f"sc15_hh_{team_id}")
        model.add(same_hh <= h_bd)
        model.add(same_hh <= h_nyd)
        model.add(same_hh >= h_bd + h_nyd - 1)

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
    """SC17 — penalise fewer than min_per_team Saturday 15:00 appearances per team."""
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
    """SC18 — penalise fewer than min_per_team Monday appearances per team."""
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
                appear = model.new_bool_var(f"sc18_ap_{team_id}_{slot_id}")
                model.add(appear == sum(fvars))
                slot_appears.append(appear)

        if len(slot_appears) < min_per_team:
            continue

        deficit = model.new_int_var(0, min_per_team, f"sc18_def_{team_id}")
        model.add(deficit >= min_per_team - sum(slot_appears))
        penalty_terms.append((penalty, deficit))

    return penalty_terms
