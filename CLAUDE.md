# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Activate the venv (required — ortools and pulp are not in system Python)
source .venv/bin/activate

# Run a solver
python -m solvers.cp_sat.main           # Option A: CP-SAT (~7s, reaches OPTIMAL)
python -m solvers.ilp.main              # Option B: ILP / PuLP + CBC (~90s)
python -m solvers.metaheuristic.main    # Option C: Simulated Annealing (~300s)

# Run all three solvers and compare (90s cap each by default)
python tools/run_solver_comparison.py [--time-limit 90] [--skip-cp-sat] [--skip-ilp] [--skip-mh]

# Cross-season historical analysis (10 EPL seasons, 27 metrics)
python -m analysis.cross_season

# Cross-league constraint comparison (EPL / NFL / NBA)
python -m analysis.cross_league

# Analysis against historical data
python -m analysis.main \
  --solver-compare output/schedule_cp_sat.csv output/schedule_ilp.csv \
  --historical data/leagues/epl/historical/2024-25.csv

# Validate a schedule object (from any solver) — returns a dict
from core.validator import validate, print_report
report = validate(schedule, teams)
print_report(report)
```

There are no automated tests. Validation is done via `core/validator.py` post-solve.

## Architecture

### Data flow

```
data/leagues/<league>/*.json
        │
        ▼
core/data_loader.py          ← league-aware; call set_league("nfl") to switch
        │
        ├─ load_teams()       → dict[str, Team]
        ├─ load_calendar()    → dict  (start/end dates, blocked windows, festive matchdays)
        ├─ load_constraints() → dict  (hard: [...], soft: [...], preferences: [...])
        └─ generate_slots()   → list[Slot]  (excludes blocked windows)
                │
generators/leagues/<league>/generate_<league>.py
        └─ generate_fixtures() → list[Fixture]  (ordered by round — 10/round for EPL)
                │
solvers/<solver>/solver.py
        └─ solve(...) → Schedule | None
                │
core/validator.py → dict (hard_violations, soft_violations, total_penalty_score, feasible)
analysis/metrics.py → MetricsReport (27 metrics)
```

### Core models (`core/models.py`)

- `Slot` has a computed `slot_id = f"{date}_{kickoff_no_colon}"` — used as the key in solver variable dicts.
- `ScheduledFixture` wraps `Fixture + Slot` and proxies `home_team_id`/`away_team_id` directly.
- `Schedule.fixtures_for_team(team_id)` returns both home and away fixtures for a team.

### League-aware data loader

`_ACTIVE_LEAGUE` is a module-level global in `core/data_loader.py`. Call `set_league("nfl")` before any load call; all subsequent calls read from `data/leagues/nfl/`. Default is `"epl"`. This global persists for the lifetime of the process — reset it explicitly when switching leagues in long-running scripts.

### MIP solvers (CP-SAT and ILP)

Both build a **sparse decision variable dict** `x: dict[(fixture_id, slot_id), BoolVar/LpVar]`. Only eligible pairs exist in `x` — never assume `x[(fid, sid)]` exists; always guard with `if (fid, sid) in x`.

The sparse structure comes from `solvers/slot_filter.py → build_eligible_slots()`, which partitions fixtures by natural round (EPL: 10 fixtures/round, 38 rounds) and restricts each fixture to slots within ±3 rounds of its expected date window. This cuts variables from ~142K to ~22K. The filter relies on `generate_fixtures()` returning fixtures in strict round order — do not reorder the fixture list before passing it to a MIP solver.

Constraint functions in `solvers/cp_sat/constraints.py` and `solvers/ilp/constraints.py` use a local `_fixture_slot_index(x, slots) → dict[fixture_id, list[(slot_id, Slot)]]` helper to iterate only over eligible pairs. Any new constraint function should use this pattern rather than iterating over all `slots`.

The CP-SAT solver passes `season_start`/`season_end` dates from the calendar through to `build_model()` and then to `build_eligible_slots()`. The ILP solver does the same. The metaheuristic does not use the filter — it works directly on `Schedule` objects.

### Metaheuristic solver

`solvers/metaheuristic/solver.py` runs greedy initialisation followed by simulated annealing. The penalty score is computed by `solvers/metaheuristic/objective.py → score(schedule, teams)`. The SA temperature drops to near-zero within a few thousand iterations at the default `cooling_rate=0.995`, making it effectively a hill-climber for most of the 300s budget. To get more exploration, lower `cooling_rate` (e.g. 0.9995) or reduce `initial_temp`.

### Analysis framework

`analysis/metrics.py → compute(schedule, solver_meta=None) → MetricsReport` is the single source of truth for all 27 metrics. It loads the active league's calendar and constraint data at call time — if `set_league()` was used before solving, metrics will use the same league's calendar.

`solver_meta` is an optional dict with keys `solve_time_seconds`, `penalty_score`, `hard_violations`, `soft_violations`; pass it to attach solver performance data to the report.

`analysis/comparator.py` produces structured dicts consumed by `analysis/report.py`. The two modes are `compare_to_historical(gen, hist)` (delta table) and `compare_solvers(reports)` (side-by-side table).

### EPL constraint IDs

The EPL solver and validator share a consistent constraint ID scheme. The Atos Golden Rules are **SC13** (five-match H/A pattern), **SC14** (season boundary H/A), and **SC15** (Boxing Day / NYD pairing). SC7 was widened from same-day to a 4-day matchday window. When adding or modifying EPL constraints, update all three locations: `data/leagues/epl/constraints.json`, `core/validator.py`, and `solvers/metaheuristic/objective.py`.

### Adding a new league

1. Create `data/leagues/<league>/teams.json`, `calendar.json`, `constraints.json`
2. Create `generators/leagues/<league>/generate_<league>.py` — return fixtures in round order if using MIP solvers
3. Call `set_league("<league>")` before any data loader or solver call
4. The three solvers are league-agnostic at the data level but EPL-specific in some constraint implementations — new league constraints need to be wired into the objective/validator separately

### Output

Generated CSVs and reports go to `output/` (gitignored). The metaheuristic `main.py` incorrectly writes to `solvers/output/` instead — use `tools/run_solver_comparison.py` which normalises output to `output/`.
