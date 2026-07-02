# Pro Sports Scheduler

A general-purpose fixture scheduling engine for professional sports leagues, implemented in Python. Give it a league's teams, calendar, and constraint rules as JSON, and it generates a full-season schedule, checks it against those rules, and compares it against real historical seasons — using three interchangeable solving algorithms and a shared analysis framework.

**This is not an EPL tool that grew a couple of extra leagues bolted on.** The engine has no built-in notion of "20 teams" or "38 rounds" or any other league-specific assumption baked into its core — every genuinely shared module (data loading, the two MIP solvers' constraint-building libraries, the metaheuristic engine, the metrics framework) operates purely on whatever a league's `data/leagues/<league>/` directory and fixture generator hand it. Anything that only makes sense for one league — the Premier League's Atos Golden Rules, the NFL's Thanksgiving hosts, the NBA's back-to-back limits — lives in that league's own `leagues/<league>/` subpackage, never in the shared code. Adding a new league means adding data and a small amount of league-scoped code, not modifying the engine.

Three leagues are implemented today:

| League | Teams | Fixtures/season | Status |
|---|---|---|---|
| **EPL** | 20 | 380 | Reference implementation — all 3 solvers, full validator, web dashboard, 10 seasons of real historical data |
| **NFL** | 32 | 272 | Fixture generator + all 3 constraint sets implemented; CP-SAT/ILP solving currently blocked (see Known Limitations) |
| **NBA** | 30 | 1,230 | Fixture generator + all 3 constraint sets implemented; CP-SAT/ILP solving currently blocked (see Known Limitations) |

EPL is simply the most mature of the three because it was built first and has had the most iteration — it is not architecturally privileged over NFL or NBA in any way.

---

## How it generalizes

```
data/leagues/<league>/*.json                    ← per-league config (teams, calendar, constraints)
        │
core/data_loader.py                              ← league-aware loader (set_league("nfl") switches everything)
        │
generators/leagues/<league>/generate_<league>.py ← per-league fixture generator
        │
solvers/{cp_sat,ilp,metaheuristic}/solver.py     ← 3 generic solver engines, zero league-specific logic
        + solvers/leagues/<league>/*_constraint_set.py   ← per-league rules, plugged into the generic solvers
        │
analysis/metrics.py                              ← generic metrics core (rest, runs, distribution, balance, …)
        + analysis/leagues/<league>/metrics.py           ← per-league metrics (Golden Rules, Thanksgiving, B2Bs, …)
```

The three solver engines (`solvers/cp_sat/solver.py`, `solvers/ilp/solver.py`, `solvers/metaheuristic/solver.py`) never import anything league-specific — they only call methods on whatever `constraint_set` object they're handed, per the `Protocol` interfaces in `solvers/constraint_set.py`. `analysis/metrics.py` follows the same split: it computes only metrics that are meaningful for any league (rest days, consecutive-run limits, city clashes, rivalry gaps, home/away balance) and dispatches to a per-league extension module for anything else.

---

## Solvers

| | Option A — CP-SAT | Option B — ILP | Option C — Metaheuristic |
|---|---|---|---|
| **Library** | Google OR-Tools | PuLP + CBC | Pure Python (no dependencies) |
| **Method** | Constraint programming | Integer linear programming | Simulated annealing + tabu search |
| **Feasibility** | Guaranteed if proven feasible | Guaranteed if solved | Heuristic — no guarantee |
| **EPL runtime** | Reaches FEASIBLE (0 hard violations) in ~30–60s; the current objective's size means OR-Tools doesn't prove OPTIMAL even with a 300s+ budget | ~1800s cap | ~300s |
| **NFL / NBA** | Currently infeasible — see Known Limitations | Currently infeasible — see Known Limitations | Works (see caveats below) |

The two MIP solvers (A and B) use a temporal slot-filter (`solvers/slot_filter.py`) that restricts each fixture to slots within a window of its natural round, cutting decision variables substantially. That filter currently assumes EPL's round-interleaved fixture order — see **Known Limitations**.

---

## Constraint frameworks

Constraints are stored as JSON per league under `data/leagues/<league>/constraints.json`, loaded at runtime, and split into hard (inviolable), soft (penalty-weighted), and — for EPL only — broadcaster preferences.

| League | Hard | Soft | Preferences | Sources |
|---|---|---|---|---|
| **EPL** | 12 | 18 | 5 | PL Handbook, Atos/Glenn Thompson algorithm, FIFA regulations |
| **NFL** | 12 | 12 | — | NFL Constitution, NFLPA CBA 2020, broadcast contracts |
| **NBA** | 13 | 13 | — | NBA CBA 2023, Fastbreak.ai, broadcast contracts |

Run `python tools/constraint_report.py` for a full, current, per-constraint implementation-status report for every league (`output/constraints_epl.txt`, `_nfl.txt`, `_nba.txt`).

### EPL — representative constraints
- **HC1** Min 3 days between fixtures · **HC7** Christmas Day blackout · **HC8** Final-round simultaneous kickoff
- **SC13/SC14/SC15** (Atos Golden Rules) 5-fixture H/A pattern, season-boundary H/A, Boxing Day ↔ New Year's Day opposite H/A
- **SC7** Same-city home clash within a 4-day window · **SC10** London cluster cap (≤3 London clubs at home per day)

### NFL — representative constraints
- **HC2–HC6** Full 17-game rotation formula (division / intra-conf / inter-conf / standings-crossover / 17th game)
- **HC9** Thanksgiving fixed hosts: Dallas Cowboys + Detroit Lions · **HC10** Thursday Night Football min 10-day rest
- **HC8** Shared-stadium single-use (MetLife: NYJ/NYG, SoFi: LAC/LAR) · **SC11** Division rivalry legs ≥5 weeks apart

### NBA — representative constraints
- **HC2–HC4** 82-game distribution formula (division ×4, conf-non-division, inter-conference)
- **HC5/HC6** No 4-in-5-nights / no 8-in-12-nights · **HC7** No back-to-back with >1,000-mile second-leg travel
- **HC10** All-Star break blackout · **HC13** All 30 teams play on the final regular-season day

---

## Project structure

```
Pro-Sports-Scheduler/
│
├── core/
│   ├── models.py                  # Team, Slot, Fixture, ScheduledFixture, Schedule — no league-specific fields
│   ├── data_loader.py             # League-aware loader: set_league("nfl"), get_active_league(), generate_slots()
│   └── validator.py               # EPL-only constraint validator (hardcoded EPL constraint IDs, by design)
│
├── data/leagues/{epl,nfl,nba}/
│   ├── teams.json                 # Teams, city groups, high-profile derbies
│   ├── calendar.json              # Season dates, blocked windows, festive/special matchdays
│   ├── constraints.json           # Hard / soft / preference constraint definitions
│   └── historical/                # Real (EPL, NFL) or synthetic (NBA) historical season CSVs — 10 seasons each
│
├── generators/leagues/{epl,nfl,nba}/generate_<league>.py   # Full fixture-generation formula per league
│
├── solvers/
│   ├── constraint_set.py          # Protocol interfaces every league's constraint sets implement
│   ├── slot_filter.py             # Temporal pre-filter for the MIP solvers (currently EPL-round-structure only)
│   ├── cp_sat/ · ilp/ · metaheuristic/    # 3 generic solver engines — zero league-specific logic
│   └── leagues/{epl,nfl,nba}/     # Per-league constraint-set implementations plugged into the 3 engines
│
├── analysis/
│   ├── metrics.py                 # Generic MetricsReport core (rest, runs, distribution, city, derby, balance, …)
│   ├── historical_loader.py       # Dispatches to each league's CSV row-parser
│   ├── leagues/{epl,nfl,nba}/     # Per-league metrics extensions + historical CSV parsers
│   ├── comparator.py · report.py  # Structured comparison + text/HTML report rendering (EPL-focused today)
│   ├── cross_season.py            # 10-season EPL historical trend analysis
│   ├── cross_league.py            # EPL / NFL / NBA constraint-framework comparison
│   └── main.py                    # CLI entry point for all analysis modes
│
├── tools/
│   ├── run_solver_comparison.py   # Runs all 3 solvers + produces comparison reports
│   ├── constraint_report.py       # Per-league, per-constraint implementation-status report
│   ├── calendar_png.py            # League-agnostic PNG calendar renderer (--league nfl/nba/epl)
│   ├── calendar_view.py           # Terminal/text calendar renderer
│   ├── export_analytics.py        # Styled analytics chart set (EPL-focused today)
│   ├── solver_accuracy_viz.py     # Solver-vs-historical accuracy charts (EPL-focused today)
│   ├── sample_schedule.py         # Prints matchday grids / team cards / derby lists from a schedule CSV
│   └── validate_ha_windows.py     # H/A window constraint validator
│
├── webapp/                        # Flask dashboard (EPL only today — see Known Limitations)
├── samples/                       # Committed reference output: 21 EPL calendar PNGs, 8 analytics charts
├── output/                        # Generated schedules and reports (gitignored)
└── requirements.txt
```

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` pulls `ortools` (Option A), `pulp` (Option B), `flask` (web dashboard), and `matplotlib` (charts/calendars). Option C (metaheuristic) has no dependencies beyond the standard library.

---

## Usage

All commands below default to EPL. Switch leagues by passing `--league nfl`/`--league nba` where supported, or by calling `set_league()` first in a script (see **Switching leagues** below).

### Run a single solver (EPL — the only league with wired-up `main.py` entry points so far)

```bash
python -m solvers.cp_sat.main           # Option A — CP-SAT
python -m solvers.ilp.main              # Option B — ILP / PuLP + CBC
python -m solvers.metaheuristic.main    # Option C — Simulated Annealing
```

Each solver writes its schedule to `output/schedule_<solver>.csv`. For NFL/NBA, construct fixtures/slots/constraint-set manually and call `solvers.metaheuristic.solver.solve()` directly — see any `solvers/leagues/{nfl,nba}/*_constraint_set.py` for the exact pattern.

### Run all three solvers and compare

```bash
python tools/run_solver_comparison.py [--time-limit 90]
```

### Generate a PNG season calendar (any league)

```bash
python tools/calendar_png.py                                # EPL, full season
python tools/calendar_png.py --team LIV                      # EPL, one team
python tools/calendar_png.py --league nfl --team KC --month 12
```

### Constraint implementation status (any league)

```bash
python tools/constraint_report.py
# output/constraints_epl.txt, _nfl.txt, _nba.txt
```

### Cross-season historical analysis (EPL, 10 seasons)

```bash
python -m analysis.cross_season
```

### Cross-league constraint comparison

```bash
python -m analysis.cross_league
```

### Analysis against historical data

```bash
python -m analysis.main \
  --generated output/schedule_cp_sat.csv \
  --historical data/leagues/epl/historical/2024-25.csv
```

### Web dashboard (EPL only today)

```bash
python run_webapp.py
# http://127.0.0.1:5000
```

### Switching leagues

```python
from core.data_loader import set_league, get_active_league, load_teams, load_calendar, load_constraints

set_league("nfl")            # or "nba", "epl" (default)
teams       = load_teams()
calendar    = load_calendar()
constraints = load_constraints()
get_active_league()          # "nfl" — used by analysis/ to dispatch without an explicit league param
```

---

## Analysis metrics

`analysis/metrics.py → compute(schedule)` returns a `MetricsReport` whose generic core works identically for any league:

| Category | Metrics |
|---|---|
| **Rest** | Mean/min/max inter-game gap per team; global min |
| **Runs** | Max consecutive home/away per team; league-wide max; teams over 3/5 |
| **Distribution** | Day-of-week %; kickoff-time % |
| **City** | Same-day clashes; 4-day-window clashes |
| **Derby/Rivalry** | Gap in days between legs; pairs under that league's own configured gap threshold |
| **Balance** | Home % in first half vs second half per team |
| **Coverage** | Team participation on the schedule's final calendar date |
| **Solver** | Solve time; penalty score; hard/soft violation counts (generated schedules only) |

Each league then fills in its own extension via `analysis/leagues/<league>/metrics.py`:

- **EPL**: Atos Golden Rule violations (SC13/SC14/SC15), Boxing Day / New Year's Day / Good Friday / Easter Monday coverage, London cluster cap
- **NFL**: Thanksgiving coverage + fixed-host compliance, primetime broadcast-slot share
- **NBA**: back-to-back counts, 4-games-in-5-nights violations, All-Star break compliance

---

## Real-world scheduling platforms

| League | Platform | Method |
|---|---|---|
| EPL | Atos (Glenn Thompson algorithm) | Custom ILP + metaheuristic |
| NFL | Recentive Analytics + Gurobi + AWS | MIP at scale |
| NBA | Fastbreak.ai (since 2024) | CP/MIP hybrid, 1M+ constraints |

---

## Extending to a new league

1. Create `data/leagues/<league>/{teams,calendar,constraints}.json`
2. Create `generators/leagues/<league>/generate_<league>.py`. Round-based leagues (like EPL) should return fixtures in strict round order, required by `solvers/slot_filter.py`; formula-based leagues (like NFL/NBA) should be aware of the slot-filter caveat below.
3. Implement `solvers/leagues/<league>/{cp_sat,ilp,mh}_constraint_set.py` against the `Protocol`s in `solvers/constraint_set.py`. Pull genuinely generic building blocks from `solvers/cp_sat/constraints.py` / `solvers/ilp/constraints.py`; write anything league-specific locally.
4. Call `set_league("<league>")` before any data-loader or solver call.
5. Run `python tools/constraint_report.py` to check implementation coverage.
6. Optionally add `analysis/leagues/<league>/metrics.py` and `historical.py` for league-specific metrics or historical-data support.

See `CLAUDE.md` for the full architectural rules this repo enforces (what belongs in shared code vs. a league's own subpackage) and more detail on every module above.

---

## Known limitations

- **CP-SAT/ILP solving is currently infeasible for NFL and NBA.** `solvers/slot_filter.py` restricts each fixture to slots near its "natural round," but that logic assumes EPL's round-interleaved fixture order; NFL/NBA generators emit fixtures grouped by matchup type instead. The metaheuristic solver is unaffected and works for all three leagues.
- **The web dashboard and several `tools/` scripts (`export_analytics.py`, `solver_accuracy_viz.py`) are EPL-only today** — they haven't been given a league selector yet, though the data layer underneath them (`analysis/metrics.py`, `analysis/historical_loader.py`, `tools/calendar_png.py`) already supports all three leagues.
- **`core/validator.py` is EPL-only by design** — it hardcodes EPL's constraint IDs and should not be called on NFL/NBA schedules.
- **NBA's historical data is synthetic** (`data/leagues/nba/historical/generate_synthetic.py`) pending real historical data collection; EPL and NFL historical data is real.

`Guide.txt` in the repo root is the original 2018 milestone list from when this project was EPL-only — kept as a historical artifact, not a description of current scope.
