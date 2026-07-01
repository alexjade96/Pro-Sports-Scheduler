# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project scope

This is a **general pro sports scheduling engine**, not an EPL-only tool. The
architecture is designed to support any league that provides a
`data/leagues/<league>/` data directory and a fixture generator: three
interchangeable solvers (CP-SAT, ILP, simulated-annealing metaheuristic), a
shared analysis/metrics framework, and a web dashboard, all driven by
per-league JSON config rather than hardcoded league assumptions.

**EPL** is the original, most complete reference implementation (all three
solvers, full validator, web dashboard, historical accuracy checks). **NFL**
and **NBA** are actively-developed additional leagues with their own data
files, fixture generators, and constraint sets under `solvers/leagues/nfl/`
and `solvers/leagues/nba/` — see "NFL / NBA support" below for current
coverage and known limitations. The intent is to keep adding leagues over
time, so shared code (`solvers/cp_sat/constraints.py`, `solvers/ilp/constraints.py`,
`core/data_loader.py`, `analysis/`) must stay league-agnostic — see
"Solver architecture: shared vs. league-scoped code" below for the rule and
how it's enforced.

## Commands

```bash
# Activate the venv (required — ortools, pulp, flask are not in system Python)
source .venv/bin/activate

# Run a solver (EPL — the only league with wired-up main.py entry points so far)
python -m solvers.cp_sat.main           # Option A: CP-SAT (reaches FEASIBLE with 0 hard violations within ~30-60s; the current 54K-penalty-term objective is large enough that OR-tools does not prove OPTIMAL even given a 300s+ budget — an earlier, much smaller constraint set reached OPTIMAL in ~7s, but that's no longer accurate)
python -m solvers.ilp.main              # Option B: ILP / PuLP + CBC (~1800s cap)
python -m solvers.metaheuristic.main    # Option C: Simulated Annealing (~300s)

# Run all three solvers and compare (EPL only)
python tools/run_solver_comparison.py [--time-limit 90] [--skip-cp-sat] [--skip-ilp] [--skip-mh]

# Generate a standalone constraint reference + implementation-status report
# for every league (output/constraints_epl.txt, _nfl.txt, _nba.txt)
python tools/constraint_report.py

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

# Validate a schedule object (EPL-only — core/validator.py hardcodes EPL
# constraint IDs; do not call it on NFL/NBA schedules) — returns a dict
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

There are no automated tests. Validation for EPL is done via `core/validator.py` post-solve; for NFL/NBA, use `tools/constraint_report.py` plus manual smoke-testing (see "NFL / NBA support").

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
        └─ generate_fixtures() → list[Fixture]
                │        EPL: strict round order, 10 fixtures/round (required by slot_filter.py)
                │        NFL: 272 fixtures via a rotation formula (division/conf/inter-conf/
                │             standings-crossover/17th-game blocks — NOT round-interleaved)
                │        NBA: 1,230 fixtures (division/conf-non-div/inter-conf blocks — NOT
                │             round-interleaved)
solvers/<solver>/solver.py  (generic — dispatches everything through constraint_set)
        └─ solve(...) → Schedule | None
                │
core/validator.py → dict (EPL-only; hard_violations, soft_violations, total_penalty_score, feasible)
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

### Solver architecture: shared vs. league-scoped code

The three "generic" solver cores — `solvers/cp_sat/solver.py`, `solvers/ilp/solver.py`,
`solvers/metaheuristic/solver.py` — contain **zero league-specific logic**.
Each one only calls methods on whatever `constraint_set` object it's given
(`build_eligible_slots`/`add_hard_constraints`/`add_soft_constraints` for the
MIP solvers; `pre_assign`/`greedy_params`/`score` for the metaheuristic), per
the `Protocol` interfaces defined in `solvers/constraint_set.py`. Each league
provides its own constraint-set implementations under `solvers/leagues/<league>/`.

This means `solvers/cp_sat/constraints.py` and `solvers/ilp/constraints.py`
are **shared building-block libraries**, not EPL modules — they must only
contain functions that are genuinely reusable across leagues (fixture-once
assignment, min-rest windows, day-of-week caps, consecutive-run limits,
half-season H/A balance with a *dynamically computed* target). Any function
whose correctness depends on a specific league's structure (a hardcoded
city name, a specific calendar date like Boxing Day, a home-game target
tuned to one league's season length) belongs in
`solvers/leagues/<league>/cp_sat_helpers.py` / `ilp_helpers.py` instead —
see `solvers/leagues/epl/cp_sat_helpers.py` and `ilp_helpers.py` for the
Atos Golden Rule / Boxing Day / London-cluster functions that were moved
out of the shared modules for this reason. The EPL metaheuristic objective
function lives at `solvers/leagues/epl/mh_objective.py` for the same
reason — it used to sit in `solvers/metaheuristic/` (the shared package)
but was moved so it's only reachable via `EPLMHConstraintSet.score()`.

When adding a constraint function, ask: "would this produce a correct or
at least meaningful result for a league with a totally different season
length, calendar, and team set?" If not, it's league-scoped — put it under
`solvers/leagues/<league>/`, not in the shared `constraints.py` files.
`tools/constraint_report.py` documents the current implementation status
per constraint ID per league and is a good place to check before reusing
an existing "shared" function for a new league.

### MIP solvers (CP-SAT and ILP)

Both build a **sparse decision variable dict** `x: dict[(fixture_id, slot_id), BoolVar/LpVar]`. Only eligible pairs exist in `x` — never assume `x[(fid, sid)]` exists; always guard with `if (fid, sid) in x`.

The sparse structure comes from `solvers/slot_filter.py → build_eligible_slots()`, which partitions fixtures by natural round and restricts each fixture to slots within `±window_rounds` of its expected date window. **This module currently hardcodes EPL's structure** (`n_rounds = 38`, `_FIXTURES_PER_ROUND = 10`) and assumes `generate_fixtures()` returns fixtures in strict round-interleaved order. That assumption holds for EPL's generator but not for NFL/NBA's, which emit fixtures grouped by matchup-type block (all division games first, then conference rotation, etc.) — the resulting per-fixture eligible-slot windows cluster a team's entire block of division/rival games into a few weeks instead of spreading them across the season. Confirmed by direct testing: **CP-SAT/ILP hard constraints alone are currently INFEASIBLE for both NFL and NBA** once realistic minimum-rest is enforced. This needs either a per-league-parameterized slot filter or round-interleaved fixture generation before NFL/NBA CP-SAT/ILP solving is usable end-to-end — the metaheuristic solver is unaffected since it doesn't use this filter.

Constraint functions in `solvers/cp_sat/constraints.py` and `solvers/ilp/constraints.py` use a local `_fixture_slot_index(x, slots) → dict[fixture_id, list[(slot_id, Slot)]]` helper to iterate only over eligible pairs. Any new constraint function should use this pattern rather than iterating over all `slots`.

The CP-SAT solver passes `season_start`/`season_end` dates from the calendar through to `build_model()` and then to `build_eligible_slots()`. The ILP solver does the same. The metaheuristic does not use the filter — it works directly on `Schedule` objects.

### Metaheuristic solver

`solvers/metaheuristic/solver.py` runs greedy initialisation followed by simulated annealing; it is league-agnostic (see "Solver architecture" above). The penalty score is computed by whatever `constraint_set.score()` the caller passes in — for EPL that's `solvers/leagues/epl/mh_objective.py → score(schedule, teams)`; NFL and NBA compute their own scores inline in `solvers/leagues/nfl/mh_constraint_set.py` / `nba/mh_constraint_set.py`. The SA temperature drops to near-zero within a few thousand iterations at the default `cooling_rate=0.995`, making it effectively a hill-climber for most of the 300s budget. To get more exploration, lower `cooling_rate` (e.g. 0.9995) or reduce `initial_temp`.

`greedy_initial_schedule()` allows multiple fixtures to share a slot (date + kickoff time) as long as no team repeats there — tracked via a `slot_teams: dict[str, set[str]]` occupancy map, never by removing slots from the pool. This matters because a slot legitimately hosts several simultaneous games in most leagues (NFL Sunday 13:00 ET has ~8, NBA nights often have 10+, even EPL's Round 38 pins all 10 final-day fixtures to one slot) — treating slots as single-use (the previous behaviour) silently dropped any fixture that couldn't find an unused slot once the pool ran out, which is nearly guaranteed whenever `len(slots) < len(fixtures)` (true for NFL: 213 slots / 272 fixtures, and NBA: 464 slots / 1,230 fixtures). Each league's `mh_constraint_set.score()` must independently penalise a team appearing twice on the same date (mirrors CP-SAT/ILP's `add_team_plays_at_most_once_per_slot`/`_day`) or the SA has no signal to fix a collision the greedy fallback introduces — this is done in all three leagues' `score()` functions. NBA's SA needs substantially longer to converge than NFL/EPL at equal time budgets — each iteration deep-copies and rescoring a 1,230-fixture `Schedule`, so plan for several minutes, not tens of seconds, to fully resolve initial-greedy collisions on NBA-scale schedules.

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

When adding or modifying EPL constraints, update all relevant locations: `data/leagues/epl/constraints.json`, `core/validator.py`, `solvers/leagues/epl/mh_objective.py`, and (for MIP solvers) `solvers/leagues/epl/cp_sat_helpers.py` / `ilp_helpers.py` or the constraint-set files directly. Do not add EPL-specific logic to the shared `solvers/cp_sat/constraints.py` / `solvers/ilp/constraints.py` — see "Solver architecture: shared vs. league-scoped code" above.

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

Data files (`data/leagues/nfl/`, `data/leagues/nba/`), fixture generators (`generators/leagues/nfl/generate_nfl.py`, `generators/leagues/nba/generate_nba.py`), and constraint sets (`solvers/leagues/nfl/`, `solvers/leagues/nba/`) are implemented for both leagues:

- **Fixture generators**: NFL produces all 272 games via the real rotation formula (division/intra-conf/inter-conf/standings-crossover/17th-game); NBA produces all 1,230 games (division/conference-non-division/inter-conference) with correct 82-game, 41H/41A-per-team balance.
- **Constraint coverage**: run `python tools/constraint_report.py` for the current per-constraint, per-solver implementation matrix. Most hard constraints that describe the fixture-generation *formula itself* (e.g. NFL HC2-HC6, NBA HC2-HC4) show as "implied" — they're guaranteed by the generator, not enforced by the solver. Most unimplemented soft constraints need external data the project doesn't have yet (broadcast slots, arena coordinates, travel distances, IST/marquee-game designations).
- **Historical data**: EPL has real 10-season CSVs in `data/leagues/epl/historical/`. NBA has 10 seasons of *synthetic* data (`data/leagues/nba/historical/generate_synthetic.py`) — `stats.nba.com` is blocked by the sandbox proxy, so real data collection is still pending.
- **Known blocker**: CP-SAT/ILP solving is currently non-functional for both leagues due to `solvers/slot_filter.py` hardcoding EPL's round structure — see "MIP solvers (CP-SAT and ILP)" above. The metaheuristic solver works for both (verified via `mh_constraint_set.score()` smoke tests), but there is no `solvers/metaheuristic/main.py`-equivalent entry point wired up for NFL/NBA yet — construct fixtures/slots/constraint_set manually (see any `solvers/leagues/{nfl,nba}/*_constraint_set.py` for the exact pattern) or add one.
- `core/validator.py` is EPL-only (hardcoded constraint IDs) — do not call it on NFL/NBA schedules.

### Adding a new league

1. Create `data/leagues/<league>/teams.json`, `calendar.json`, `constraints.json`
2. Create `generators/leagues/<league>/generate_<league>.py`. If the league is round-based like EPL, return fixtures in strict round order (required by `solvers/slot_filter.py`). If it's formula-based like NFL/NBA, be aware `slot_filter.py`'s round-windowing currently assumes EPL's structure — see "MIP solvers" above before relying on CP-SAT/ILP.
3. Create `solvers/leagues/<league>/{cp_sat,ilp,mh}_constraint_set.py` implementing the `CpSatConstraintSet`/`ILPConstraintSet`/`MHConstraintSet` protocols from `solvers/constraint_set.py`. Only import genuinely generic helpers from `solvers/cp_sat/constraints.py` / `solvers/ilp/constraints.py` — write league-specific logic locally in the constraint-set file or a sibling `*_helpers.py` module, never in the shared files (see "Solver architecture" above).
4. Call `set_league("<league>")` before any data loader or solver call.
5. Run `python tools/constraint_report.py` after wiring up constraints to check documented coverage against actual implementation status.

### Output

Generated CSVs and reports go to `output/` (gitignored). The metaheuristic `main.py` incorrectly writes to `solvers/output/` instead — use `tools/run_solver_comparison.py` which normalises output to `output/`.
