# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project scope

This is a **general pro sports scheduling engine**. The architecture is
designed to support any league that provides a
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

# Run a solver — any league via --league (default epl), dispatched through
# solvers/registry.py + solvers/runner.py; no league is hardcoded in the entry points.
python -m solvers.cp_sat.main [--league epl|nfl|nba] [--time-limit 600]   # Option A: CP-SAT
python -m solvers.ilp.main    [--league epl|nfl|nba] [--time-limit 1800]  # Option B: ILP / PuLP + CBC
python -m solvers.metaheuristic.main [--league epl|nfl|nba] [--time-limit 600]  # Option C: Simulated Annealing
# EPL CP-SAT reaches FEASIBLE with 0 hard violations within ~30-60s; the large penalty-term
# objective means OR-tools does not prove OPTIMAL even given a 300s+ budget. NFL/NBA: CP-SAT is
# the working MIP option (ILP/CBC not confirmed to converge at that scale — see below).
# The runner writes output/schedule_<solver>.csv for EPL, output/schedule_<league>_<solver>.csv
# for NFL/NBA (metaheuristic label is "metaheuristic"), and runs the per-league validator
# (core.validator dispatches to core/leagues/<league>/validator.py) for all three leagues.

# Run all three solvers and compare (any league)
python tools/run_solver_comparison.py [--league epl|nfl|nba] [--time-limit 90] [--skip-cp-sat] [--skip-ilp] [--skip-mh]

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

# Validate a schedule object — core.validator.validate() dispatches to the
# active (or given) league's validator and returns a dict with the same shape
# for every league (hard/soft violations, counts, penalty, feasible)
from core.validator import validate, print_report
report = validate(schedule, teams)            # infers active league
report = validate(schedule, teams, league="nfl")  # or pass explicitly
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

There are no automated tests. Post-solve validation runs via `core/validator.py` for all three leagues (it dispatches to `core/leagues/<league>/validator.py` for NFL/NBA, EPL inline); `tools/constraint_report.py` gives the per-constraint implementation matrix.

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
core/validator.py → dict (per-league dispatch; hard_violations, soft_violations, total_penalty_score, feasible)
analysis/metrics.py → MetricsReport (27 metrics)
        │
webapp/app.py → Flask dashboard (reads output/ CSVs + samples/calendars/ PNGs at startup)
```

### Core models (`core/models.py`)

- `Slot` has a computed `slot_id = f"{date}_{kickoff_no_colon}"` — used as the key in solver variable dicts.
- `ScheduledFixture` wraps `Fixture + Slot` and proxies `home_team_id`/`away_team_id` directly.
- `Schedule.fixtures_for_team(team_id)` returns both home and away fixtures for a team.

### League-aware data loader

`_ACTIVE_LEAGUE` is a module-level global in `core/data_loader.py`. Call `set_league("nfl")` before any load call; all subsequent calls read from `data/leagues/nfl/`. Default is `"epl"`. `get_active_league()` returns the current value — used by `analysis/metrics.py` and `analysis/historical_loader.py` to dispatch to the right per-league extension without threading an explicit `league` parameter through every call site. This global persists for the lifetime of the process — reset it explicitly when switching leagues in long-running scripts.

### Solver architecture: shared vs. league-scoped code

The three "generic" solver cores — `solvers/cp_sat/solver.py`, `solvers/ilp/solver.py`,
`solvers/metaheuristic/solver.py` — are **driven entirely by the `constraint_set` object each is given**.
Each one calls methods on that object
(`build_eligible_slots`/`add_hard_constraints`/`add_soft_constraints` for the
MIP solvers; `pre_assign`/`greedy_params`/`score` for the metaheuristic), per
the `Protocol` interfaces defined in `solvers/constraint_set.py`. Each league
provides its own constraint-set implementations under `solvers/leagues/<league>/`.

This means `solvers/cp_sat/constraints.py` and `solvers/ilp/constraints.py`
are **shared building-block libraries used by every league's constraint set**
— they must only contain functions that are genuinely reusable across leagues (fixture-once
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

The sparse structure comes from `solvers/slot_filter.py → build_eligible_slots()`, which restricts each fixture to slots within `±window_rounds` of its expected date window for that fixture's **natural round** — computed generically by `solvers/round_assignment.py → assign_natural_rounds()` (greedy earliest-available-round / graph-edge-coloring over the fixture list) rather than a hardcoded `n_rounds`/`_FIXTURES_PER_ROUND`. Run over EPL's fixture list (already round-interleaved by the circle-method generator) it exactly reconstructs EPL's existing 38 rounds of 10; for a league whose generator emits matchup-type blocks (all division games, then all conference games, ...) it still needs those blocks pre-merged into a round-interleaved order first, or a team's entire block of e.g. division games gets read as "these all happen in the season's first few weeks." NFL's and NBA's generators do this via `generators/interleave.py → interleave_blocks()` (weighted round-robin merge of each matchup-type block, proportional to block size) as the last step of `generate_fixtures()`. With both pieces in place, the underlying hard-constraint feasible region is confirmed non-empty for both leagues by direct testing — CP-SAT reaches OPTIMAL for NFL and NBA's hard-constraint-only models (272-fixture NFL model in well under 90s; 1,230-fixture NBA model in well under 150s). NBA additionally needed its `window_rounds` widened from EPL's 3–5 to `20` (`solvers/leagues/nba/{cp_sat,ilp}_constraint_set.py`) — NBA's ~94 natural rounds pack far less real time into each round than EPL's 38, so EPL's window size proved genuinely too narrow to satisfy HC5/HC6 (4-in-5, 8-in-12) even with correct round assignment; narrower windows are provably infeasible by direct testing, `window_rounds=20` is the smallest confirmed feasible. ILP/CBC, given the identical eligible-slot model, has not been confirmed to converge at this scale in practice — a direct feasibility-only test (no objective) left CBC still in presolve/branch-and-bound past a 280s budget for NBA's ~221K-variable model (consistent with EPL's own ILP already needing a documented ~1800s cap at a 10× smaller variable count); CP-SAT is the more scalable MIP option for NFL/NBA today. All three solver entry points (`solvers/{cp_sat,ilp,metaheuristic}/main.py`) are now league-parametrized via `--league` — they delegate to `solvers/runner.py → run_solver()`, which resolves the per-league generator and constraint set through `solvers/registry.py` (the one place that knows the concrete per-league class names). The metaheuristic solver bypasses this filter entirely and has worked for all three leagues since before this fix.

**PuLP/CBC's `timeLimit` is unreliable in this environment** — repeated direct testing (EPL, NFL, and NBA's full-objective ILP models) showed CBC running 8–40+ minutes past a stated 180–300s `timeLimit`, sometimes not even finishing the initial LP relaxation (NFL's full-objective model, 360K rows/99K columns: the continuous relaxation alone took ~2544s). Don't trust a short `timeLimit` to bound an ILP run's actual wall-clock time — if you need a hard bound, wrap the call with an external timeout and be prepared to kill the CBC subprocess directly (killing the PuLP-spawned `cbc` process cleanly surfaces as a `PulpSolverError` in the caller). Also avoid running multiple CP-SAT/ILP solves concurrently on this container — it has 4 cores, and CP-SAT's default `num_workers=8` alone oversubscribes that; three parallel league solves plus CBC dropped effective per-process CPU share to ~10% and made otherwise-fast solves (e.g. EPL CP-SAT reaching FEASIBLE at 60-90s alone) falsely appear to hang.

Constraint functions in `solvers/cp_sat/constraints.py` and `solvers/ilp/constraints.py` use a local `_fixture_slot_index(x, slots) → dict[fixture_id, list[(slot_id, Slot)]]` helper to iterate only over eligible pairs. Any new constraint function should use this pattern rather than iterating over all `slots`.

The CP-SAT solver passes `season_start`/`season_end` dates from the calendar through to `build_model()` and then to `build_eligible_slots()`. The ILP solver does the same. The metaheuristic works directly on `Schedule` objects, independent of the filter.

### Metaheuristic solver

`solvers/metaheuristic/solver.py` runs greedy initialisation followed by simulated annealing; it is league-agnostic (see "Solver architecture" above). The penalty score is computed by whatever `constraint_set.score()` the caller passes in — for EPL that's `solvers/leagues/epl/mh_objective.py → score(schedule, teams)`; NFL and NBA compute their own scores inline in `solvers/leagues/nfl/mh_constraint_set.py` / `nba/mh_constraint_set.py`. The SA temperature drops to near-zero within a few thousand iterations at the default `cooling_rate=0.995`, making it effectively a hill-climber for most of the 300s budget. To get more exploration, lower `cooling_rate` (e.g. 0.9995) or reduce `initial_temp`.

`greedy_initial_schedule()` allows multiple fixtures to share a slot (date + kickoff time) as long as no team repeats there, tracked via a `slot_teams: dict[str, set[str]]` occupancy map that keeps every slot available in the pool. This matters because a slot legitimately hosts several simultaneous games in most leagues (NFL Sunday 13:00 ET has ~8, NBA nights often have 10+, even EPL's Round 38 pins all 10 final-day fixtures to one slot) — treating slots as single-use (the previous behaviour) silently dropped any fixture that couldn't find an unused slot once the pool ran out, which is nearly guaranteed whenever `len(slots) < len(fixtures)` (true for NFL: 213 slots / 272 fixtures, and NBA: 464 slots / 1,230 fixtures). Each league's `mh_constraint_set.score()` independently penalises a team appearing twice on the same date (mirrors CP-SAT/ILP's `add_team_plays_at_most_once_per_slot`/`_day`), giving the SA a signal to fix any collision the greedy fallback introduces — this is done in all three leagues' `score()` functions. NBA's SA needs substantially longer to converge than NFL/EPL at equal time budgets — each iteration deep-copies and rescoring a 1,230-fixture `Schedule`, so plan for several minutes, not tens of seconds, to fully resolve initial-greedy collisions on NBA-scale schedules.

### Web dashboard (`webapp/`)

`webapp/app.py` is a Flask app launched via `python run_webapp.py`. It's league-aware: every route reads `?league=epl|nfl|nba` (via `active_league()`, default `epl`) and loads that league's data lazily on first request, cached per-league in `_cache: dict[str, dict]` (`_get_cache(league)`) rather than eagerly at startup — NFL/NBA may not have any generated `output/` CSVs yet, so there's no reason to pay that load cost before a request for that league arrives. The nav bar's league dropdown (`webapp/templates/base.html`) switches leagues by appending `?league=` to the current path; every client-side `fetch('/api/...')` call also carries the query param explicitly, since the API routes read `active_league()` from the request they receive, not from whatever page rendered the link.

| Route | Page |
|---|---|
| `/` | KPI dashboard — hard violations, rest days, day-of-week chart |
| `/schedule` | Filterable fixture table (by solver label and team) |
| `/accuracy` | Generated vs most-recent-historical-season metric delta table |
| `/solvers` | Side-by-side solver comparison |
| `/analysis` | Trends, quality radar, fixture-density heatmap, per-team scorecard, and server-side chart export |
| `/calendar` | Gallery of committed PNG calendars from `samples/calendars/` |

Calendar images are served from `samples/calendars/` (falling back to `output/`) via `/calendar-img/<filename>`, and generated-solver CSVs read from `_solver_csv_path(league, key)` — EPL keeps the unprefixed `output/schedule_<solver>.csv` convention for backward compatibility; NFL/NBA use `output/schedule_<league>_<solver>.csv`. The `/analysis` page's Atos-Golden-Rule-flavored charts (Boxing Day coverage, SC13 pattern violations, the Festive-Coverage/SC13-Compliance radar dimensions) are EPL-only — `core.validator` and those specific `MetricsReport` fields don't have an NFL/NBA equivalent, so the route passes `epl_only_charts=(league != "epl")` and the template shows a banner rather than presenting zeros as real compliance data; rest days, city clashes, and derby spacing on the same page are computed generically and stay meaningful for any league. The webapp also exposes `/api/schedule/<label>`, `/api/metrics`, `/api/team-details`, and `/api/export-analytics` (POST, runs `tools/export_analytics.py` server-side) JSON endpoints, all league-scoped by the same query param.

### Calendar PNG tool (`tools/calendar_png.py`)

League-agnostic: pass `--league nfl` / `--league nba` (or call `set_league()` before invoking `main()`/`render_season_png()` programmatically) to render a non-EPL calendar. Month range, team colors, and festive/blocked-window labels are all derived from the active league's data at render time:
- `render_season_png()` produces either a full-season or single-team PNG. In team mode, panel width is content-driven: `_list_panel_width_frac(fig_w_in)` derives purely from `_LIST_COLS` character counts × `_LIST_FONT_PT` × `_LIST_MONO_ADV`.
- `TEAM_COLORS_EPL` / `TEAM_COLORS_NFL` / `TEAM_COLORS_NBA` each map that league's club IDs to a primary brand hex color, selected via `team_colors_for_league(league)` — kept as separate dicts (rather than one merged dict) because team IDs collide across leagues (e.g. NFL "DAL" Cowboys vs NBA "DAL" Mavericks need different colors).
- `derive_months(calendar)` walks `calendar["start_date"]..calendar["end_date"]` to build the month grid for that league's actual season span.
- `build_festive_dates(calendar)` reads EPL's flat `festive_matchdays` list and NFL/NBA's `special_matchdays` dict (skipping nested non-date structures like NFL's `international_games` or NBA's `in_season_tournament`) to label festive/marquee cells for whichever season the active calendar describes.

Committed samples live in `samples/calendars/` (21 EPL PNGs: `calendar.png` + one per team). Generated output goes to `output/` (gitignored).

### Analysis architecture: shared vs. league-scoped code

`analysis/metrics.py` and `analysis/historical_loader.py` follow the same shared-vs-league-scoped split as the solver layer (see "Solver architecture" above) — the same test applies: *would this produce a correct or at least meaningful result for a league with a totally different season length, calendar, and team set?* If not, it's league-scoped and belongs under `analysis/leagues/<league>/`, not in the shared module.

- `analysis/metrics.py → compute(schedule, solver_meta=None) → MetricsReport` computes the metrics that generalize across any league — REST, RUNS, DISTRIBUTION, CITY clashes, DERBY/rivalry gaps (with the minimum-gap threshold read dynamically from that league's own rivalry-spread constraint via `_derby_gap_threshold_days()`), BALANCE, blocked-window compliance, and final-day team coverage. It reads the active league via `core.data_loader.get_active_league()` (call `set_league()` before `compute()` for a non-EPL schedule) and dispatches to that league's `analysis/leagues/<league>/metrics.py → extend(report, schedule, calendar, city_groups, all_team_ids)`, which fills in the `MetricsReport` fields specific to that league: EPL's Atos Golden Rules (SC13/SC14/SC15), Boxing Day/NYD/Easter festive coverage, and the London cluster cap (SC10) live in `analysis/leagues/epl/metrics.py`; NFL's Thanksgiving coverage/fixed-host check and primetime broadcast-slot share (derived from `calendar.json`'s `broadcast_windows`) live in `analysis/leagues/nfl/metrics.py`; NBA's back-to-back counts, 4-games-in-5-nights violations, and All-Star break compliance live in `analysis/leagues/nba/metrics.py`. `MetricsReport` keeps every league's fields on one dataclass (rather than per-league subclasses) for backward compatibility with existing EPL-focused callers (`analysis/comparator.py`, `analysis/cross_season.py`) — each schedule simply populates the fields its own league's extension fills in, leaving the rest at their zero/empty default.
- Date-based league-scoped metrics compute their reference date **per year present in the schedule** rather than reading a single date out of the active calendar.json, since a historical season's holiday falls on a different date than the currently-configured season's: EPL's `_festive_coverage()` matches Boxing Day/New Year's Day by month/day (both recur on the same civil date every year); `_easter_coverage()` computes Easter Sunday per year via the Anonymous Gregorian algorithm (`_easter_sunday()`) since Easter moves; NFL's `_thanksgiving()` computes the 4th Thursday of November per year (`_fourth_thursday_of_november()`); NBA's `_all_star_break()` uses a per-season-start-year lookup of real All-Star break windows (`_ALLSTAR_BREAKS`, same source as `generate_synthetic.py`'s table) since that break has no fixed formula, falling back to the active calendar's `blocked_windows` only for a season not in the lookup (i.e. the currently-configured/generated season). Reading a fixed active-calendar date instead of computing per-year was a real bug found while testing multi-season historical trend charts — it silently produced a flat 0 for 9 of 10 EPL historical seasons before this fix.
- `analysis/historical_loader.py → load_season(csv_path, season_label=None, league=None) / available_seasons(league=None)` is a thin dispatcher: it infers the league from the CSV path (`data/leagues/<league>/historical/...`) or falls back to the active league, then delegates row-parsing to `analysis/leagues/<league>/historical.py`. Each league's CSV schema is genuinely different — EPL is football-data.co.uk's name-mapped `DD/MM/YYYY` format (needs `team_name_map.json`); NFL/NBA use direct team IDs with an ISO date column (`gameday` / `game_date`) — so the row-parsing itself is league-scoped, not shared.
- `solver_meta` is an optional dict with keys `solve_time_seconds`, `penalty_score`, `hard_violations`, `soft_violations`; pass it to attach solver performance data to the report (league-agnostic).
- `analysis/comparator.py` and `analysis/cross_season.py` remain EPL-focused CLI/reporting tools today (their titles, notes, and historical CSV paths assume EPL) — extending them to NFL/NBA is a separate, dedicated pass. `tools/export_analytics.py` and `tools/solver_accuracy_viz.py` are league-aware via `--league epl|nfl|nba`: EPL keeps its Atos-Golden-Rule-flavored charts unchanged; NFL substitutes Thanksgiving-coverage/primetime-share panels and NBA substitutes back-to-back/4-in-5/All-Star-break panels for the charts that depend on EPL-only `MetricsReport` fields (see each file's `TREND_CONFIGS`/`RADAR_CONFIGS`/`CONSTRAINT_PANELS`). Both tools keep EPL's output filenames unprefixed for backward compatibility and prefix NFL/NBA output as `<prefix>_<league>_*` so all three leagues can export into the same directory without collisions.

### EPL constraint IDs

The EPL solver and validator share a consistent constraint ID scheme. The Atos Golden Rules are **SC13** (five-match H/A pattern), **SC14** (season boundary H/A), and **SC15** (Boxing Day / NYD pairing). SC7 was widened from same-day to a 4-day matchday window.

**Redundant-but-official rules carry an `implied_by` field rather than being deleted.** Some constraints are officially-named PL/Atos/broadcast rules that are nonetheless mathematically implied by another rule: HC6 (venue single-use) ⊂ HC5; SC1/SC2 (max-5-consecutive) ⊂ SC13 (2-3-in-5 forces ≤3 consecutive, so they never bind on an SC13-compliant EPL schedule — but the shared `add_soft_max_consecutive_home_away` mechanism is a genuine independent constraint for NFL/NBA, which have no SC13, so keep it); SC12 (opening balance) ⊂ SC13+SC14; PR2 (festive) ⊂ SC9. These keep their `constraints.json` entry (with `source`) plus an `implied_by`/`implied_note` pair so the official ruleset and its provenance stay documented and validator-reportable, while signalling the redundancy. The per-team single-day caps HC9/HC11/HC12/HC13 are officially-sourced Sky/PL slot allocations and stay named entries, but their *enforcement* is consolidated into one table (`_DAY_CAPS` in `cp_sat_constraint_set.py` and `ilp_constraint_set.py`) that calls the shared generic `add_max_single_day_games_per_team` / `add_max_games_on_day`, rather than five per-day wrapper functions (HC10 Tue+Wed stays a separate union call). Verified behavior-preserving: identical model size (ILP 9,160 hard / 46,392 soft terms) before and after.

When adding or modifying EPL constraints, update all relevant locations: `data/leagues/epl/constraints.json`, `core/validator.py`, `solvers/leagues/epl/mh_objective.py`, and (for MIP solvers) `solvers/leagues/epl/cp_sat_helpers.py` / `ilp_helpers.py` or the constraint-set files directly. Do not add EPL-specific logic to the shared `solvers/cp_sat/constraints.py` / `solvers/ilp/constraints.py` — see "Solver architecture: shared vs. league-scoped code" above.

**Constraint-reality reconciliation (all three leagues).** Several officially-defined hard constraints were calibrated against the real historical seasons so the solvers' feasible region actually contains real-world schedules. Two kinds of annotation record this on the `constraints.json` entries, always *preserving* the official `source`:
- `relaxed_from_official` + `relaxation_note` — the enforced value was **changed** because the original was over-strict / mis-specified vs. what real schedules do. These were changed: **NFL HC10** (Thursday-night rest `10 → 4` days: a standard TNF is a 4-day short week; the 10-day figure is the *post*-Thursday mini-bye, and a hard ≥10 made every real season infeasible at 36-40 violations); **EPL HC9** (Friday `3 → 6`), **HC11** (Monday `7 → 8`), **HC13** (Thursday `2 → 4`) — raised to the observed 10-season per-team maxima (driven by the COVID seasons 2019-20/2020-21). The EPL raises don't change generated output (its day mix is shaped by the objective's Sat-15:00/Monday floors, not by these ceilings binding); they only stop a real season being falsely rejected. Enforced values live in JSON `value` and are mirrored in the `_DAY_CAPS` tuple defaults (`cp_sat_constraint_set.py`/`ilp_constraint_set.py`) and `mh_objective.py`.
- `reality_note` — the value was **kept** but real data diverges for a reason that isn't over-strictness, so it's documented rather than encoded: **EPL HC1** (3-clear-days rest is real but the PL itself ignores it for the festive Boxing-Day/Dec-28 doubles — every real HC1 breach is in December; kept hard because it shapes whole-season rest); **NFL HC1** (16→17-game rule change pre-2021; 2022 Bills-Bengals cancellation) and **HC8** (SoFi didn't open until 2020, so LAC/LAR co-hosting flags in 2017/2019 are a retroactive-venue-map artifact); **NBA HC1** (COVID-shortened 2019-20/2020-21 seasons), **HC5/HC6** (genuine post-2017-CBA rules that modern seasons satisfy — only pre-2017 and the 2020 restart violate), **HC8** (real recent seasons reach 17 b2b/team above the official 16 ceiling, which is why it's soft-enforced via SC1's target of 14). After this pass the current-era real seasons validate clean (EPL 2022-25, NFL 2021+/current) and the residual "violations" are all rule-changes-over-time or documented festive/COVID breaches, not over-strict constraints.

**SC13 is enforced as a round-ordering term in the MIP solvers, not a home-count cap.** The circle-method generator (`generators/leagues/epl/generate_epl.py`) interleaves each first-half round with its H/A-swapped counterpart, so its natural-round fixture order is *already* a perfect 2-3/3-2 five-match pattern (SC13 = 0 pre-solve, deterministically), and home/away per fixture is fixed. The only way SC13 breaks is the solver scheduling a team's games out of round order within the eligible-slot window, so `add_soft_ha_window` (both `cp_sat_helpers.py` and `ilp_helpers.py`) penalises each adjacent out-of-order pair per team (CP-SAT: a reified boolean per inversion; ILP: a big-M boolean) rather than the old rolling 35-day home/away cap — that window held 8-9 games in festive congestion, where a simultaneous 3-home/3-away cap is unsatisfiable, so it penalised unavoidable congestion while never tracking the true 5-game metric. The metaheuristic (`mh_objective.py`) already scored the true 5-consecutive-fixture metric directly.

**Boxing Day / New Year's Day coverage is weighted above the other festive dates.** `add_soft_festive_coverage` gives Dec 26 and Jan 1 a marquee weight (1.5× the SC9 base) so the solver concentrates a near-full round on each, the way real EPL does; Dec 28 is weighted low (base ÷ 5) because it sits only 2 days after Boxing Day and HC1's 3-day minimum rest forbids a team playing both, so weighting it equally would split teams across the two dates and cap Boxing Day coverage. The MH gained a Boxing Day/NYD coverage term it previously lacked. These two fixes are in tension — concentrating a full round on Boxing Day pulls fixtures across natural rounds, which the SC13 round-ordering term resists — so SC13 weight (40) and the marquee multiplier (1.5×) are balanced to land both inside real-EPL ranges. Validated against the 10 real historical seasons: CP-SAT SC13 78→25, Boxing Day 2→14, SC14 15→8, all now inside the historical envelope (real ranges 3-41, 10-20, 0-9). The metaheuristic changes are correct by construction but the SA does not reliably reach feasibility on the 380-fixture EPL model within a practical budget (~23K iterations in 600s at ~17ms/score, still clearing its ~100-hard-violation greedy start), so the MH improvement is not empirically confirmed — CP-SAT is the solver to trust for EPL SC13/festive quality.

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
- **Constraint coverage**: run `python tools/constraint_report.py` for the current per-constraint, per-solver implementation matrix. Most hard constraints that describe the fixture-generation *formula itself* (e.g. NFL HC2-HC6, NBA HC2-HC4) show as "implied" — they're guaranteed by the generator rather than enforced by the solver. Most unimplemented soft constraints are waiting on external data the project hasn't collected yet (broadcast slots, arena coordinates, travel distances, IST/marquee-game designations).
- **Historical data**: EPL has real 10-season CSVs (2015-16 through 2024-25) in `data/leagues/epl/historical/`, fetched by `download_seasons.py` from the openfootball public-domain dataset (mirrored on GitHub raw content) — `football-data.co.uk`, the originally-intended source, is blocked by the sandbox proxy, so this reads from that reachable mirror and writes the fixtures back out in football-data.co.uk CSV format so the loader is unchanged. NBA has 9 real seasons (2015-16 through 2023-24) in `data/leagues/nba/historical/`, fetched by its own `download_seasons.py` from an MIT-licensed GitHub mirror of NBA Stats API data (`stats.nba.com` itself is blocked by the sandbox proxy). Each league keeps a `generate_synthetic.py` as a fallback generator in case its mirror ever becomes unavailable; neither is what's committed under `historical/` anymore. The synthetic generators assign approximate dates that don't preserve real match-day structure, so date-derived metrics (rest, consecutive runs, festive coverage) are only trustworthy on the real data — the EPL set was synthetic until this pass and was actively distorting the historical-accuracy baselines (e.g. Boxing Day coverage, SC13 counts). The NBA mirror doesn't yet cover 2024-25, so NBA has 9 real seasons vs. EPL/NFL's 10.
- **CP-SAT**: hard-constraint feasibility confirmed for both leagues (see "MIP solvers (CP-SAT and ILP)" above for the round-assignment + block-interleaving fix and NBA's widened `window_rounds`) — CP-SAT reaches OPTIMAL on the hard-constraint-only model for both. **ILP/CBC** shares the same eligible-slot model but hasn't been confirmed to converge at NFL/NBA's scale in practice; treat CP-SAT as the working MIP option for these two leagues for now. Full end-to-end solves (soft-constraint objective included, run to a real time budget) haven't been benchmarked yet, only hard-constraint feasibility.
- **All three solver entry points are wired for every league** (`python -m solvers.{cp_sat,ilp,metaheuristic}.main --league nfl|nba`), dispatched through `solvers/registry.py` (the `(league, solver) → generator + constraint-set-class` factory) and `solvers/runner.py` (the shared load→generate→build→solve→validate→export flow). The runner runs the per-league validator and uses the `schedule_<league>_<solver>.csv` output convention. `tools/run_solver_comparison.py --league` runs all three solvers for any league. The metaheuristic works for all three leagues (verified end-to-end via the runner).
- **`core/validator.py` validates all three leagues via a dispatcher.** `validate(schedule, teams, league=None)` infers the active league (or takes an explicit one) and delegates: EPL inline, NFL/NBA to `core/leagues/<league>/validator.py`. Every league returns the same report shape, so `print_report` and downstream callers (the runner, `run_solver_comparison.py`, the webapp's `_validate_generated`) are league-agnostic. The NFL/NBA validators check the schedule-verifiable constraints and reuse `analysis.metrics.compute()` for the per-team/league-specific counts it already derives (back-to-backs, 4-in-5, All-Star break, Thanksgiving hosts, consecutive runs); structural constraints guaranteed by the generator (division/conference rotation, bye weeks) and those needing external data the project doesn't collect (travel miles, arena windows, broadcast slots) are not re-checked — see each validator's module docstring. On the CP-SAT output all three leagues validate with 0 hard violations, i.e. every hard-modellable rule is enforced in the model. The one officially-hard rule that is *not* hard-modelled is NBA **HC8** (per-team back-to-back ceiling of 16): a season-global b2b cap couples every fixture and makes the 221K-variable model intractable (no feasible solution in 300s of direct testing, vs OPTIMAL in <150s without it), so it is enforced by the soft SC1 term (target 14, below the ceiling) and the NBA validator reports overages as *soft*, not as a false infeasibility — the `enforced_as`/`enforced_note` fields on HC8 in `data/leagues/nba/constraints.json` record this.

### Adding a new league

1. Create `data/leagues/<league>/teams.json`, `calendar.json`, `constraints.json`
2. Create `generators/leagues/<league>/generate_<league>.py`. If the league is round-based like EPL, return fixtures in strict round order. If it's formula-based like NFL/NBA (fixtures built as matchup-type blocks — division, conference, ...), merge the blocks via `generators/interleave.py → interleave_blocks()` as the last step before returning — see "MIP solvers" above for why, and check whether `window_rounds` in your `{cp_sat,ilp}_constraint_set.py` needs widening from EPL's 3–5 default once you know your league's natural round count.
3. Create `solvers/leagues/<league>/{cp_sat,ilp,mh}_constraint_set.py` implementing the `CpSatConstraintSet`/`ILPConstraintSet`/`MHConstraintSet` protocols from `solvers/constraint_set.py`. Only import genuinely generic helpers from `solvers/cp_sat/constraints.py` / `solvers/ilp/constraints.py` — write league-specific logic locally in the constraint-set file or a sibling `*_helpers.py` module, never in the shared files (see "Solver architecture" above).
4. Call `set_league("<league>")` before any data loader or solver call.
5. Run `python tools/constraint_report.py` after wiring up constraints to check documented coverage against actual implementation status.
6. Optionally add `analysis/leagues/<league>/metrics.py` (an `extend()` hook — see "Analysis architecture" above) and `analysis/leagues/<league>/historical.py` (a `load_season()` row-parser) if the league has metrics or historical data that don't fit the generic `analysis/metrics.py` core / `analysis/historical_loader.py` dispatcher.

### Output

Generated CSVs and reports go to `output/` (gitignored). The metaheuristic `main.py` incorrectly writes to `solvers/output/` instead — use `tools/run_solver_comparison.py` which normalises output to `output/`.
