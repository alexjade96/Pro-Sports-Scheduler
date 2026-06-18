"""
Option C — Metaheuristic: Neighbourhood / move operators.

A "move" transforms one schedule into a neighbouring schedule.
Three move types are defined; the solver samples them stochastically.

All moves are non-destructive: they return a new Schedule (or mutate
a copy) without altering the original.
"""
import random
from copy import deepcopy

from core.models import Schedule, ScheduledFixture, Slot


def swap_slots(schedule: Schedule, slots: list[Slot]) -> Schedule:
    """
    Move type 1 — SWAP: pick two fixtures and exchange their assigned slots.
    Always legal (both fixtures remain scheduled); may introduce/remove
    constraint violations.
    """
    if len(schedule.fixtures) < 2:
        return schedule

    new_schedule = deepcopy(schedule)
    i, j = random.sample(range(len(new_schedule.fixtures)), 2)
    new_schedule.fixtures[i].slot, new_schedule.fixtures[j].slot = (
        new_schedule.fixtures[j].slot,
        new_schedule.fixtures[i].slot,
    )
    return new_schedule


def reassign_slot(schedule: Schedule, slots: list[Slot]) -> Schedule:
    """
    Move type 2 — REASSIGN: pick one fixture and move it to a randomly
    chosen available slot (slot not currently used by another fixture).
    """
    new_schedule = deepcopy(schedule)
    used_slots = {sf.slot.slot_id for sf in new_schedule.fixtures}
    available = [s for s in slots if s.slot_id not in used_slots]
    if not available:
        return new_schedule

    target_idx = random.randrange(len(new_schedule.fixtures))
    new_schedule.fixtures[target_idx] = ScheduledFixture(
        fixture=new_schedule.fixtures[target_idx].fixture,
        slot=random.choice(available),
    )
    return new_schedule


def swap_home_away(schedule: Schedule, slots: list[Slot]) -> Schedule:
    """
    Move type 3 — REVERSE: pick two fixtures involving the same pair of
    teams and swap which is the home leg and which is the away leg
    (while keeping their assigned slots).

    This helps escape local optima caused by home/away imbalances.
    """
    new_schedule = deepcopy(schedule)

    # Find a random fixture and look for its reverse counterpart
    idx = random.randrange(len(new_schedule.fixtures))
    sf = new_schedule.fixtures[idx]
    home, away = sf.fixture.home_team_id, sf.fixture.away_team_id

    reverse_idx = next(
        (i for i, s in enumerate(new_schedule.fixtures)
         if s.fixture.home_team_id == away and s.fixture.away_team_id == home),
        None
    )
    if reverse_idx is None:
        return new_schedule

    # Swap team roles while keeping slots intact
    f1 = new_schedule.fixtures[idx]
    f2 = new_schedule.fixtures[reverse_idx]
    f1.fixture.home_team_id, f1.fixture.away_team_id = (
        f1.fixture.away_team_id, f1.fixture.home_team_id
    )
    f2.fixture.home_team_id, f2.fixture.away_team_id = (
        f2.fixture.away_team_id, f2.fixture.home_team_id
    )
    return new_schedule


# Registry used by the solver to sample moves
MOVE_OPERATORS = [swap_slots, reassign_slot, swap_home_away]
MOVE_WEIGHTS   = [0.5, 0.3, 0.2]   # probability weights


def random_move(schedule: Schedule, slots: list[Slot]) -> Schedule:
    operator = random.choices(MOVE_OPERATORS, weights=MOVE_WEIGHTS, k=1)[0]
    return operator(schedule, slots)
