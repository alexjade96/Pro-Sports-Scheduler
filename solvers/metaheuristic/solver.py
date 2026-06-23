"""
Generic metaheuristic solver: Simulated Annealing with optional Tabu list.

Algorithm:
  1. Build an initial greedy schedule (assign fixtures to slots in order)
  2. Run Simulated Annealing:
     - Sample a random neighbour via a move operator
     - Accept if better, or with probability exp(-Δ/T) if worse
     - Cool temperature by factor α each iteration
  3. Tabu list prevents revisiting recently seen slot assignments

League-specific logic (pre-assignments, day caps, scoring) is fully delegated
to the ``constraint_set`` argument, which must satisfy the ``MHConstraintSet``
protocol defined in ``solvers/constraint_set.py``.

No external solver library required — pure Python.
"""
import bisect
import math
import random
import time
from collections import deque
from copy import deepcopy

from core.models import Fixture, Slot, Schedule, ScheduledFixture, Team
from solvers.metaheuristic.neighborhood import random_move


# ---------------------------------------------------------------------------
# Initial solution
# ---------------------------------------------------------------------------

def greedy_initial_schedule(
    fixtures: list[Fixture],
    slots: list[Slot],
    season: str,
    constraint_set,
) -> Schedule:
    """
    Assigns fixtures to slots in round order using greedy HC1 / per-day-cap
    logic supplied by constraint_set.greedy_params().

    Pre-assigned fixtures (e.g. EPL Round 38, NFL Thanksgiving hosts) are
    handled by constraint_set.pre_assign(), which also returns the set of
    slot IDs to block from the general pool.
    """
    params = constraint_set.greedy_params()
    min_rest_days: int = params.get("min_rest_days", 3)
    day_caps: dict[str, int] = params.get("day_caps", {})

    pre_assigned_pairs, blocked_slot_ids = constraint_set.pre_assign(fixtures, slots)
    pre_assigned_ids = {f.fixture_id for f, _ in pre_assigned_pairs}

    available_slots = sorted(
        [s for s in slots if s.slot_id not in blocked_slot_ids],
        key=lambda s: (s.date, s.kickoff),
    )

    assigned: list[ScheduledFixture] = []
    team_ords: dict[str, list[int]] = {}
    day_counts: dict[tuple[str, str], int] = {}

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
        cap = day_caps.get(slot.day_of_week)
        if cap is None:
            return True
        if day_counts.get((home, slot.day_of_week), 0) >= cap:
            return False
        if day_counts.get((away, slot.day_of_week), 0) >= cap:
            return False
        return True

    def _record(slot: Slot, home: str, away: str, ord_: int) -> None:
        bisect.insort(team_ords.setdefault(home, []), ord_)
        bisect.insort(team_ords.setdefault(away, []), ord_)
        if slot.day_of_week in day_caps:
            key_h = (home, slot.day_of_week)
            key_a = (away, slot.day_of_week)
            day_counts[key_h] = day_counts.get(key_h, 0) + 1
            day_counts[key_a] = day_counts.get(key_a, 0) + 1

    for fixture, slot in pre_assigned_pairs:
        assigned.append(ScheduledFixture(fixture=fixture, slot=slot))
        _record(slot, fixture.home_team_id, fixture.away_team_id, slot.date.toordinal())

    fallbacks = 0
    for fixture in fixtures:
        if fixture.fixture_id in pre_assigned_ids:
            continue

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
        print(f"[Greedy] {fallbacks} fixture(s) fell back (no compliant slot found)")

    return Schedule(season=season, fixtures=assigned)


# ---------------------------------------------------------------------------
# Simulated Annealing
# ---------------------------------------------------------------------------

def simulated_annealing(
    initial: Schedule,
    slots: list[Slot],
    teams: dict[str, Team],
    constraint_set,
    initial_temp: float = 5000.0,
    cooling_rate: float = 0.9997,
    max_iterations: int = 5_000_000,
    tabu_size: int = 100,
    time_limit_seconds: int = 600,
) -> Schedule:
    current = deepcopy(initial)
    current_score = constraint_set.score(current, teams)

    best = deepcopy(current)
    best_score = current_score

    temperature = initial_temp
    tabu: deque[float] = deque(maxlen=tabu_size)

    start_time = time.time()
    iteration = 0

    print(f"[SA] Initial penalty score: {current_score:.1f}")

    while iteration < max_iterations:
        if time.time() - start_time > time_limit_seconds:
            print(f"[SA] Time limit reached at iteration {iteration}")
            break

        neighbour = random_move(current, slots)
        neighbour_score = constraint_set.score(neighbour, teams)
        delta = neighbour_score - current_score

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
    constraint_set,
    season: str,
    initial_temp: float = 5000.0,
    cooling_rate: float = 0.9997,
    max_iterations: int = 5_000_000,
    tabu_size: int = 100,
    time_limit_seconds: int = 600,
) -> Schedule:
    print("[Metaheuristic] Building greedy initial solution ...")
    initial = greedy_initial_schedule(fixtures, slots, season, constraint_set)
    initial_penalty = constraint_set.score(initial, teams)
    print(f"[Metaheuristic] Greedy score: {initial_penalty:.1f}")

    print("[Metaheuristic] Running Simulated Annealing ...")
    return simulated_annealing(
        initial=initial,
        slots=slots,
        teams=teams,
        constraint_set=constraint_set,
        initial_temp=initial_temp,
        cooling_rate=cooling_rate,
        max_iterations=max_iterations,
        tabu_size=tabu_size,
        time_limit_seconds=time_limit_seconds,
    )
