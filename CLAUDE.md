# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Activate the venv (required — ortools, pulp, flask are not in system Python)
source .venv/bin/activate

# Run a solver
python -m solvers.cp_sat.main           # Option A: CP-SAT (~7s, reaches OPTIMAL)
python -m solvers.ilp.main              # Option B: ILP / PuLP + CBC (~1800s cap)
python -m solvers.metaheuristic.main    # Option C: Simulated Annealing (~300s)

# Run all three solvers and compare
python tools/run_solver_comparison.py [--time-limit 90] [--skip-cp-sat] [--skip-ilp] [--skip-mh]

# Web dashboard (requires solved output/ CSVs)
python run_webapp.py                    # serves at http://127.0.0.1:5000
python run_webapp.py --port 8080 --debug

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

# Sample schedule output (matchday grid, team card, derbies, festive)
python tools/sample_schedule.py [--csv output/schedule_cp_sat.csv] [--team LIV] [--section derbies]

# Validate H/A window constraints per team
python tools/validate_ha_windows.py [--csv output/schedule_cp_sat.csv] [--team ARS]

# Generate calendar PNGs
python tools/calendar_png.py                    # full-season PNG → output/calendar.png
python tools/calendar_png.py --team LIV         # team PNG → output/calendar_liv.png
python tools/calendar_png.py --team ARS --month 11
```

There are no automated tests. Validation is done via `core/validator.py` post-solve.

## Git conventions

Always set the commit author to the repo owner; committer stays as Claude:

```bash
git config user.name Claude
git config user.email noreply@anthropic.com
# then on every commit:
git commit --author="alexjade96 <3687389+alexjade96@users.noreply.github.com>" ...
```

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
                │                               (NFL/NBA generators are stubs — NotImplementedError)
solvers/<solver>/solver.py
        └─ solve(...) → Schedule | None
                │
core/validator.py → dict (hard_violations, soft_violations, total_penalty_score, feasible)
analysis/metrics.py → MetricsReport (27 metrics)
        │
webapp/app.py → Flask dashboard (reads output/ CSVs + samples/calendars/ PNGs at startup)
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

### Web dashboard (`webapp/`)

`webapp/app.py` is a Flask app launched via `python run_webapp.py`. It loads all solver CSVs from `output/` at startup (cached in `_cache` dict) and exposes five pages:

| Route | Page |
|---|---|
| `/` | KPI dashboard — hard violations, rest days, day-of-week chart |
| `/schedule` | Filterable fixture table (by solver label and team) |
| `/accuracy` | Generated vs historical EPL 2024-25 metric delta table |
| `/solvers` | Side-by-side solver comparison |
| `/calendar` | Gallery of committed PNG calendars from `samples/calendars/` |

Calendar images are served from `samples/calendars/` (falling back to `output/`) via `/calendar-img/<filename>`. The webapp also exposes `/api/schedule/<label>` and `/api/metrics` JSON endpoints.

### Calendar PNG tool (`tools/calendar_png.py`)

`render_season_png()` produces either a full-season or single-team PNG. In team mode:
- Panel width is content-driven: `_list_panel_width_frac(fig_w_in)` derives from `_LIST_COLS` character counts × `_LIST_FONT_PT` × `_LIST_MONO_ADV` — no hardcoded pixel values.
- `TEAM_COLORS` dict maps each of the 20 EPL club IDs to a primary brand hex color. When adding/changing a team color, update only `TEAM_COLORS` — the rendering functions consume it via the `team_color` parameter.

Committed samples live in `samples/calendars/` (21 PNGs: `calendar.png` + one per team). Generated output goes to `output/` (gitignored).

### Analysis framework

`analysis/metrics.py → compute(schedule, solver_meta=None) → MetricsReport` is the single source of truth for all 27 metrics. It loads the active league's calendar and constraint data at call time — if `set_league()` was used before solving, metrics will use the same league's calendar.

`solver_meta` is an optional dict with keys `solve_time_seconds`, `penalty_score`, `hard_violations`, `soft_violations`; pass it to attach solver performance data to the report.

`analysis/comparator.py` produces structured dicts consumed by `analysis/report.py`. The two modes are `compare_to_historical(gen, hist)` (delta table) and `compare_solvers(reports)` (side-by-side table).

### EPL constraint IDs

The EPL solver and validator share a consistent constraint ID scheme. The Atos Golden Rules are **SC13** (five-match H/A pattern), **SC14** (season boundary H/A), and **SC15** (Boxing Day / NYD pairing). SC7 was widened from same-day to a 4-day matchday window.

When adding or modifying EPL constraints, update all three locations: `data/leagues/epl/constraints.json`, `core/validator.py`, and `solvers/metaheuristic/objective.py`.

**Constraint implementation matrix** (current state):

| ID | Description | CP-SAT | ILP | MH | Validator |
|----|-------------|--------|-----|----|-----------|
| HC1 | Min 3 rest days | ✅ | ✅ | ✅ | ✅ |
| HC3 | Blocked windows | ✅ | ✅ | ✅ | ✅ |
| HC4 | Double round-robin | ✅ | ✅ | ✅ | implied |
| HC5 | Team once per day | ✅ | ✅ | ✅ | — |
| HC7 | Christmas Day blackout | ✅ | ✅ | ✅ | ✅ |
| HC8 | Round 38 simultaneous | ✅ | ✅ | ✅ | ✅ |
| HC9 | Max 3 Friday games/team | ✅ | ✅ | ✅ | ✅ |
| HC10 | Max 10 Tue/Wed games/team | ✅ | ✅ | ✅ | ✅ |
| HC11 | Max 7 Monday games/team | ✅ | ✅ | ✅ | ✅ |
| HC12 | Max 6 Wednesday games/team | ✅ | ✅ | ✅ | ✅ |
| HC13 | Max 2 Thursday games/team | ✅ | ✅ | ✅ | ✅ |
| SC1/SC2 | Max 5 consecutive H or A | ✅ | ✅ | ✅ | ✅ |
| SC3 | Derby gap ≥8 rounds | ✅ | ✅ | ✅ | ✅ |
| SC5 | Half-season H/A balance | ✅ | ✅ | ✅ | ✅ |
| SC7 | Same-city home clash (4-day) | ✅ | ✅ | ✅ | ✅ |
| SC9 | Easter coverage | ✅ | ✅ | ✅ | ✅ |
| SC10 | London cluster cap (≤3/day) | ✅ | ✅ | ✅ | ✅ |
| SC12 | Opening balance (rounds 1-5) | — | — | ✅ | ✅ |
| SC13 | 5-match H/A pattern (Atos) | ✅ | ✅ | ✅ | ✅ |
| SC14 | Season boundary H/A (Atos) | ✅ | ✅ | ✅ | ✅ |
| SC15 | Boxing Day / NYD pairing (Atos) | ✅ | ✅ | ✅ | ✅ |
| SC16 | Spare rescheduling window | — | — | ✅ | — |
| SC17 | Min 5 Saturday 15:00/team | ✅ | ✅ | ✅ | ✅ |
| SC18 | Min 3 Monday games/team | ✅ | ✅ | ✅ | ✅ |
| SC4 | European Tue/Wed rest (≥3 days) | ❌ needs CL/EL match dates | | | |
| SC6 | Cup+league same opponent window | ❌ needs FA Cup/Carabao draw | | | |
| SC8 | UEFA Thursday 5-day rest | ❌ needs EL/ECL match dates | | | |
| SC11 | Promoted team separation | ❌ needs `promoted` flag in teams.json | | | |

HC2 (hard same-city ban) was demoted to SC7 (soft): it is incompatible with HC8 because all Round 38 home teams are pinned to the same final day, making same-city clashes unavoidable on that date.

### NFL / NBA support

Data files exist (`data/leagues/nfl/` and `data/leagues/nba/`) and are used by `analysis/cross_league.py` for constraint comparison. The fixture generators (`generators/leagues/nfl/generate_nfl.py` and `generators/leagues/nba/generate_nba.py`) are stubs that raise `NotImplementedError` — the full generation algorithms are not yet implemented.

### Adding a new league

1. Create `data/leagues/<league>/teams.json`, `calendar.json`, `constraints.json`
2. Create `generators/leagues/<league>/generate_<league>.py` — return fixtures in round order if using MIP solvers
3. Call `set_league("<league>")` before any data loader or solver call
4. The three solvers are league-agnostic at the data level but EPL-specific in some constraint implementations — new league constraints need to be wired into the objective/validator separately

### Output

Generated CSVs and reports go to `output/` (gitignored). The metaheuristic `main.py` incorrectly writes to `solvers/output/` instead — use `tools/run_solver_comparison.py` which normalises output to `output/`.
