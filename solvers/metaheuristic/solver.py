"""
Option C — Metaheuristic solver: Simulated Annealing with optional Tabu list.

Algorithm:
  1. Build an initial greedy schedule (assign fixtures to slots in order)
  2. Run Simulated Annealing:
     - Sample a random neighbour via a move operator
     - Accept if better, or with probability exp(-Δ/T) if worse
     - Cool temperature by factor α each iteration
  3. Tabu list prevents revisiting recently seen slot assignments

No external solver library required — pure Python.

Tune: initial_temp, cooling_rate, max_iterations, tabu_size
"""
import bisect
import math
import random
import time
from collections import deque
from copy import deepcopy

from core.models import Fixture, Slot, Schedule, ScheduledFixture, Team
from solvers.metaheuristic.objective import score
from solvers.metaheuristic.neighborhood import random_move


# ---------------------------------------------------------------------------
# Initial solution
# ---------------------------------------------------------------------------

def greedy_initial_schedule(
    fixtures: list[Fixture],
    slots: list[Slot],
    season: str,
    min_rest_days: int = 3,
    max_thursday: int = 2,
    max_friday: int = 3,
    final_day: dict | None = None,
    fixtures_per_round: int = 10,
) -> Schedule:
    """
    Assigns fixtures to slots in round order, choosing the earliest available
    slot that satisfies:
      - HC1: minimum rest days (3) for both teams
      - HC13: at most max_thursday Thursday games per team
      - HC9: at most max_friday Friday games per team
      - HC8: if final_day provided, Round 38 (last fixtures_per_round) is
             pinned to the final-day slot; all final-day date slots are
             excluded from consideration for earlier rounds.
    Uses bisect for O(log n) HC1 checks.  Falls back to first remaining slot
    only when no compliant slot is found (rare with a well-formed calendar).
    """
    available_slots = sorted(slots, key=lambda s: (s.date, s.kickoff))

    assigned: list[ScheduledFixture] = []
    team_ords: dict[str, list[int]] = {}   # team -> sorted ordinals of assigned dates
    thu_counts: dict[str, int] = {}        # team -> Thursday game count
    fri_counts: dict[str, int] = {}        # team -> Friday game count

    def _hc1_ok(team_id: str, ord_: int) -> bool:
        ords = team_ords.get(team_id)
        if not ords:
            return True
        pos = bisect.bisect_left(ords, ord_)
        if pos > 0 and ord_ - ords[pos - 1] < min_rest_days:
            return False
        if pos < len(ords) and ords[pos] - ord_ < min_rest_days:
            return False
        return True

    def _day_cap_ok(slot: Slot, home: str, away: str) -> bool:
        dow = slot.day_of_week
        if dow == "Thursday":
            if thu_counts.get(home, 0) >= max_thursday:
                return False
            if thu_counts.get(away, 0) >= max_thursday:
                return False
        elif dow == "Friday":
            if fri_counts.get(home, 0) >= max_friday:
                return False
            if fri_counts.get(away, 0) >= max_friday:
                return False
        return True

    def _record(slot: Slot, home: str, away: str, ord_: int) -> None:
        bisect.insort(team_ords.setdefault(home, []), ord_)
        bisect.insort(team_ords.setdefault(away, []), ord_)
        if slot.day_of_week == "Thursday":
            thu_counts[home] = thu_counts.get(home, 0) + 1
            thu_counts[away] = thu_counts.get(away, 0) + 1
        elif slot.day_of_week == "Friday":
            fri_counts[home] = fri_counts.get(home, 0) + 1
            fri_counts[away] = fri_counts.get(away, 0) + 1

    # ── HC8: pre-assign Round 38 to the final-day slot ───────────────────
    r38_fixture_ids: set[str] = set()
    if final_day:
        from datetime import date as _date
        fd_date = _date.fromisoformat(final_day["date"])
        fd_ko   = final_day["kickoff"]
        fd_slot = next(
            (s for s in available_slots if s.date == fd_date and s.kickoff == fd_ko),
            None,
        )
        if fd_slot:
            r38 = fixtures[-fixtures_per_round:]
            r38_fixture_ids = {f.fixture_id for f in r38}
            for f in r38:
                assigned.append(ScheduledFixture(fixture=f, slot=fd_slot))
                _record(fd_slot, f.home_team_id, f.away_team_id, fd_slot.date.toordinal())
            # Remove ALL final-day slots so earlier rounds can't land there
            available_slots = [s for s in available_slots if s.date != fd_date]
            print(f"[Greedy] Round 38 pinned to {fd_slot.slot_id}; final-day slots removed from pool")
        else:
            print(f"[Greedy] WARNING: final-day slot ({fd_date} {fd_ko}) not found — HC8 not enforced")

    fallbacks = 0
    for fixture in fixtures:
        if fixture.fixture_id in r38_fixture_ids:
            continue  # already assigned above

        home, away = fixture.home_team_id, fixture.away_team_id
        placed = False

        for slot in available_slots:
            ord_ = slot.date.toordinal()
            if _hc1_ok(home, ord_) and _hc1_ok(away, ord_) and _day_cap_ok(slot, home, away):
                assigned.append(ScheduledFixture(fixture=fixture, slot=slot))
                _record(slot, home, away, ord_)
                available_slots.remove(slot)
                placed = True
                break

        if not placed:
            fallbacks += 1
            if available_slots:
                slot = available_slots.pop(0)
                assigned.append(ScheduledFixture(fixture=fixture, slot=slot))
                _record(slot, home, away, slot.date.toordinal())

    if fallbacks:
        print(f"[Greedy] {fallbacks} fixture(s) fell back (no HC1/HC13/HC9-compliant slot found)")

    return Schedule(season=season, fixtures=assigned)


# ---------------------------------------------------------------------------
# Simulated Annealing
# ---------------------------------------------------------------------------

def simulated_annealing(
    initial: Schedule,
    slots: list[Slot],
    teams: dict[str, Team],
    initial_temp: float = 5000.0,
    cooling_rate: float = 0.9997,
    max_iterations: int = 5_000_000,
    tabu_size: int = 100,
    time_limit_seconds: int = 600,
) -> Schedule:
    current = deepcopy(initial)
    current_score = score(current, teams)

    best = deepcopy(current)
    best_score = current_score

    temperature = initial_temp
    tabu: deque[float] = deque(maxlen=tabu_size)  # stores recent scores as a lightweight proxy

    start_time = time.time()
    iteration = 0

    print(f"[SA] Initial penalty score: {current_score:.1f}")

    while iteration < max_iterations:
        if time.time() - start_time > time_limit_seconds:
            print(f"[SA] Time limit reached at iteration {iteration}")
            break

        neighbour = random_move(current, slots)
        neighbour_score = score(neighbour, teams)
        delta = neighbour_score - current_score

        # Accept if better, or probabilistically if worse
        if delta < 0 or random.random() < math.exp(-delta / max(temperature, 1e-9)):
            if neighbour_score not in tabu:
                current = neighbour
                current_score = neighbour_score
                tabu.append(neighbour_score)

                if current_score < best_score:
                    best = deepcopy(current)
                    best_score = current_score

        temperature *= cooling_rate
        iteration += 1

        if iteration % 5000 == 0:
            elapsed = time.time() - start_time
            print(f"[SA] iter={iteration:6d} | T={temperature:8.2f} | "
                  f"current={current_score:.1f} | best={best_score:.1f} | {elapsed:.0f}s")

        if best_score == 0:
            print(f"[SA] Optimal solution found at iteration {iteration}")
            break

    print(f"[SA] Final best penalty: {best_score:.1f}")
    return best


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def solve(
    fixtures: list[Fixture],
    slots: list[Slot],
    teams: dict[str, Team],
    season: str,
    initial_temp: float = 5000.0,
    cooling_rate: float = 0.9997,
    max_iterations: int = 5_000_000,
    tabu_size: int = 100,
    time_limit_seconds: int = 600,
    final_day: dict | None = None,
    fixtures_per_round: int = 10,
) -> Schedule:
    from solvers.metaheuristic.objective import set_r38_fixture_ids
    if final_day:
        r38_ids = frozenset(f.fixture_id for f in fixtures[-fixtures_per_round:])
        set_r38_fixture_ids(r38_ids)
    else:
        set_r38_fixture_ids(frozenset())

    print("[Metaheuristic] Building greedy initial solution ...")
    initial = greedy_initial_schedule(
        fixtures, slots, season,
        final_day=final_day, fixtures_per_round=fixtures_per_round,
    )
    initial_penalty = score(initial, teams)
    print(f"[Metaheuristic] Greedy score: {initial_penalty:.1f}")

    print("[Metaheuristic] Running Simulated Annealing ...")
    best = simulated_annealing(
        initial=initial,
        slots=slots,
        teams=teams,
        initial_temp=initial_temp,
        cooling_rate=cooling_rate,
        max_iterations=max_iterations,
        tabu_size=tabu_size,
        time_limit_seconds=time_limit_seconds,
    )

    return best
