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
) -> Schedule:
    """
    Assigns fixtures to slots sequentially, skipping a slot if the team
    is already playing that day. Not guaranteed feasible but provides a
    warm start for the metaheuristic.
    """
    available_slots = list(slots)
    random.shuffle(available_slots)
    slot_iter = iter(available_slots)

    assigned: list[ScheduledFixture] = []
    team_dates: dict[str, set] = {}

    for fixture in fixtures:
        home, away = fixture.home_team_id, fixture.away_team_id
        for slot in available_slots:
            date_str = str(slot.date)
            if date_str not in team_dates.get(home, set()) and \
               date_str not in team_dates.get(away, set()):
                assigned.append(ScheduledFixture(fixture=fixture, slot=slot))
                team_dates.setdefault(home, set()).add(date_str)
                team_dates.setdefault(away, set()).add(date_str)
                available_slots.remove(slot)
                break
        else:
            # Fallback: assign to first remaining slot (may violate constraints)
            if available_slots:
                slot = available_slots.pop(0)
                assigned.append(ScheduledFixture(fixture=fixture, slot=slot))

    return Schedule(season=season, fixtures=assigned)


# ---------------------------------------------------------------------------
# Simulated Annealing
# ---------------------------------------------------------------------------

def simulated_annealing(
    initial: Schedule,
    slots: list[Slot],
    teams: dict[str, Team],
    initial_temp: float = 5000.0,
    cooling_rate: float = 0.995,
    max_iterations: int = 50_000,
    tabu_size: int = 100,
    time_limit_seconds: int = 300,
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
    cooling_rate: float = 0.995,
    max_iterations: int = 50_000,
    tabu_size: int = 100,
    time_limit_seconds: int = 300,
) -> Schedule:
    print("[Metaheuristic] Building greedy initial solution ...")
    initial = greedy_initial_schedule(fixtures, slots, season)
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
