# Pro Sports Scheduler

A research-grade fixture scheduling engine for professional sports leagues, implemented in Python. The system generates full-season schedules that satisfy league-specific hard and soft constraints, validates them against official rules, and compares them against historical data using a 27-metric analysis framework.

Currently supports the **English Premier League (EPL)**, **NFL**, and **NBA**, with a league-agnostic data layer designed for future expansion.

---

## Solvers

Three independent solver implementations tackle the scheduling problem from different algorithmic directions, all operating on the same constraint definitions and producing comparable `Schedule` objects.

| | Option A — CP-SAT | Option B — ILP | Option C — Metaheuristic |
|---|---|---|---|
| **Library** | Google OR-Tools | PuLP + CBC | Pure Python |
| **Method** | Constraint programming | Integer linear programming | Simulated annealing + Tabu |
| **Feasibility** | Guaranteed if optimal/feasible | Guaranteed if solved | Heuristic (no guarantee) |
| **EPL solve time** | ~7s (OPTIMAL) | ~90s (feasible) | ~300s |
| **Warm start** | No | No | Greedy initialisation |

The MIP solvers (A and B) use a temporal slot-filter that restricts each fixture to slots within its natural round window, reducing decision variables from ~142K to ~22K (6.5× reduction).

---

## Constraint Frameworks

Constraints are stored as JSON per league under `data/leagues/<league>/constraints.json` and are loaded at runtime. The system distinguishes hard constraints (inviolable) from soft constraints (penalty-weighted) and, for the EPL, broadcasters' preferences.

| League | Hard | Soft | Preferences | Sources |
|---|---|---|---|---|
| **EPL** | 7 | 15 | 5 | PL Handbook, Atos/Glenn Thompson algorithm, FIFA regulations |
| **NFL** | 12 | 12 | — | NFL Constitution, NFLPA CBA 2020, broadcast contracts |
| **NBA** | 13 | 13 | — | NBA CBA 2023, Fastbreak.ai, broadcast contracts |

### EPL — Key Constraints
- **HC1** Min 3 days between fixtures; **HC7** Christmas Day blackout; **HC8** Round 38 simultaneous kickoff
- **SC13** (Atos Golden Rule) Any 5-fixture window must be exactly 2H+3A or 3H+2A
- **SC14** (Atos Golden Rule) Cannot start or end season with HH or AA
- **SC15** (Atos Golden Rule) Boxing Day and New Year's Day must be opposite H/A designations
- **SC7** Same-city pairs (e.g. LIV+EVE, ARS+TOT) cannot both be home within a 4-day matchday window
- **SC10** London cluster cap: ≤3 London clubs at home on the same day

### NFL — Key Constraints
- **HC2–HC6** Full 17-game formula (6 division + 4 intra-conf + 4 inter-conf + 2 standings crossover + 1 17th-game)
- **HC7** One bye week per team, weeks 6–14 (NFLPA CBA 2020)
- **HC8** Shared stadium single-use: MetLife (NYJ/NYG), SoFi (LAC/LAR)
- **HC9** Thanksgiving fixed hosts: Dallas Cowboys + Detroit Lions
- **HC10** Thursday Night Football minimum 10-day rest (NFLPA CBA 2020)
- **SC10** SNF flex-scheduling eligibility maintained weeks 5–18

### NBA — Key Constraints
- **HC2–HC4** 82-game distribution formula (div×4, conf-nondiv×3–4, opposite-conf×2)
- **HC5** No 4 games in 5 nights; **HC6** No 8 games in 12 nights (CBA 2023)
- **HC7** No back-to-back where second leg requires >1,000-mile travel (CBA 2023)
- **HC8** Max 16 back-to-backs per team; **HC13** All 30 teams play on final day
- **SC5** Christmas Day 5-game slate (near-contractual, ABC/ESPN)
- **SC8** FTE (Fresh-Tired-Even) rest equity factor ±5

---

## Project Structure

```
Pro-Sports-Scheduler/
│
├── core/
│   ├── models.py            # Fixture, Slot, ScheduledFixture, Schedule, Team
│   ├── data_loader.py       # League-aware loader (set_league("nfl") etc.)
│   └── validator.py         # Hard + soft constraint checker; returns violation report
│
├── data/
│   └── leagues/
│       ├── epl/
│       │   ├── teams.json           # 20 clubs, city groups, high-profile derbies
│       │   ├── calendar.json        # Season dates, blocked windows, festive matchdays
│       │   ├── constraints.json     # 7 HC + 15 SC + 5 preferences
│       │   └── historical/          # football-data.co.uk CSVs (2015-16 → 2024-25)
│       ├── nfl/
│       │   ├── teams.json / calendar.json / constraints.json   # 12 HC + 12 SC
│       └── nba/
│           ├── teams.json / calendar.json / constraints.json   # 13 HC + 13 SC
│
├── generators/
│   └── leagues/
│       ├── epl/generate_epl.py    # Double round-robin via circle method (380 fixtures)
│       ├── nfl/generate_nfl.py    # Stub — 17-game formula (NotImplementedError)
│       └── nba/generate_nba.py    # Stub — 82-game distribution (NotImplementedError)
│
├── solvers/
│   ├── slot_filter.py             # Temporal pre-filter: 142K → 22K MIP variables
│   ├── cp_sat/                    # Option A: OR-Tools CP-SAT
│   │   ├── solver.py
│   │   ├── constraints.py
│   │   └── main.py
│   ├── ilp/                       # Option B: PuLP + CBC
│   │   ├── solver.py
│   │   ├── constraints.py
│   │   └── main.py
│   └── metaheuristic/             # Option C: Simulated Annealing + Tabu Search
│       ├── solver.py
│       ├── objective.py
│       ├── neighborhood.py
│       └── main.py
│
├── analysis/
│   ├── metrics.py             # 27-metric MetricsReport (rest, runs, derby, festive, …)
│   ├── comparator.py          # compare_to_historical(), compare_solvers()
│   ├── report.py              # Text + HTML report renderers
│   ├── cross_season.py        # 10-season EPL historical trend analysis
│   ├── cross_league.py        # EPL / NFL / NBA constraint comparison
│   ├── historical_loader.py   # Loads football-data.co.uk CSV → Schedule
│   └── main.py                # CLI entry point for all analysis modes
│
├── tools/
│   └── run_solver_comparison.py   # Runs all 3 solvers + produces comparison reports
│
├── output/                    # Generated schedules and reports (gitignored)
├── requirements.txt
└── Guide.txt                  # Original 2018 project milestones (historical reference)
```

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` pulls `ortools` (Option A) and `pulp` (Option B). Option C has no dependencies beyond the standard library.

---

## Usage

### Run a single solver

```bash
# Option A — CP-SAT (recommended: ~7s to OPTIMAL for EPL)
python -m solvers.cp_sat.main

# Option B — ILP / PuLP + CBC
python -m solvers.ilp.main

# Option C — Simulated Annealing (300s, no optimality guarantee)
python -m solvers.metaheuristic.main
```

Each solver writes its schedule to `output/schedule_<solver>.csv`.

### Run all three solvers and compare

```bash
python tools/run_solver_comparison.py [--time-limit 90]
```

Runs CP-SAT → ILP → Metaheuristic sequentially, validates each, computes metrics, and writes:
- `output/report_solvers.txt` — side-by-side solver comparison
- `output/report_accuracy.txt` — generated vs historical EPL baseline
- `output/report_per_team.txt` — per-team metric breakdown

### Cross-season historical analysis (EPL, 2015–2025)

```bash
python -m analysis.cross_season
# output/report_cross_season.txt
```

Analyses 10 EPL seasons across 27 metrics covering rest days, consecutive runs, city clashes, derby gaps, festive coverage, and the Atos Golden Rules (SC13/SC14/SC15).

### Cross-league constraint comparison

```bash
python -m analysis.cross_league
```

Prints a structured comparison of EPL, NFL, and NBA constraint frameworks across 8 pillars: rest/fatigue, consecutive runs, home/away balance, calendar blackouts, venue conflicts, broadcast requirements, rivalry spread, and travel management.

### Analysis against historical data

```bash
python -m analysis.main \
  --generated output/schedule_cp_sat.csv \
  --historical data/leagues/epl/historical/2024-25.csv

# Full solver comparison mode
python -m analysis.main \
  --solver-compare \
    output/schedule_cp_sat.csv \
    output/schedule_ilp.csv \
    output/schedule_metaheuristic.csv \
  --historical data/leagues/epl/historical/2024-25.csv
```

### Switching leagues

```python
from core.data_loader import set_league, load_teams, load_calendar, load_constraints

set_league("nfl")   # or "nba", "epl" (default)
teams       = load_teams()
calendar    = load_calendar()
constraints = load_constraints()
```

---

## Analysis Metrics (EPL)

The `MetricsReport` produced by `analysis/metrics.py` covers:

| Category | Metrics |
|---|---|
| **Rest** | Mean/min/max inter-game gap per team; global min |
| **Runs** | Max consecutive home/away per team; league-wide max; teams over 3/5 |
| **Distribution** | Day-of-week %; kickoff time % |
| **City** | Same-day clashes; 4-day window clashes (SC7); London cluster violations (SC10) |
| **Derby** | Gap in days between legs; pairs under 56 days |
| **Festive** | Boxing Day, NYD, Good Friday, Easter Monday team coverage |
| **Golden Rules** | SC13 five-match pattern violations; SC14 season boundary violations; SC15 Boxing Day/NYD pair violations |
| **Compliance** | International break violations; Christmas Day violations |
| **Balance** | Home % in first half vs second half per team |
| **Solver** | Solve time; penalty score; hard/soft violation counts |

---

## Real-World Scheduling Platforms

| League | Platform | Method |
|---|---|---|
| EPL | Atos (Glenn Thompson algorithm) | Custom ILP + metaheuristic |
| NFL | Recentive Analytics + Gurobi + AWS | MIP at scale |
| NBA | Fastbreak.ai (since 2024) | CP/MIP hybrid, 1M+ constraints |

---

## Extending to a New League

1. Add `data/leagues/<league>/teams.json`, `calendar.json`, `constraints.json`
2. Create `generators/leagues/<league>/generate_<league>.py` implementing the fixture generation formula
3. Call `set_league("<league>")` before any data loader calls
4. Implement solver wiring in the existing three solver frameworks or add a new one under `solvers/`
