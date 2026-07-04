"""
League / solver registry: maps a (league, solver) pair to the right fixture
generator and constraint-set class, so entry points can run any league through
any solver without hardcoding EPL. Imports stay lazy — only the requested
league's generator and the requested constraint-set class are imported.

This is the single place that knows the concrete per-league class names (which
are not perfectly uniform, e.g. NBAcpSatConstraintSet); everything else works
through these two factory functions plus the Protocol interfaces in
solvers/constraint_set.py.
"""
from __future__ import annotations

import inspect
from datetime import date

LEAGUES = ("epl", "nfl", "nba")
SOLVERS = ("cp_sat", "ilp", "mh")

# (league, solver) -> "module_path:ClassName"
_CONSTRAINT_SETS = {
    ("epl", "cp_sat"): "solvers.leagues.epl.cp_sat_constraint_set:EPLCpSatConstraintSet",
    ("epl", "ilp"):    "solvers.leagues.epl.ilp_constraint_set:EPLILPConstraintSet",
    ("epl", "mh"):     "solvers.leagues.epl.mh_constraint_set:EPLMHConstraintSet",
    ("nfl", "cp_sat"): "solvers.leagues.nfl.cp_sat_constraint_set:NFLCpSatConstraintSet",
    ("nfl", "ilp"):    "solvers.leagues.nfl.ilp_constraint_set:NFLILPConstraintSet",
    ("nfl", "mh"):     "solvers.leagues.nfl.mh_constraint_set:NFLMHConstraintSet",
    ("nba", "cp_sat"): "solvers.leagues.nba.cp_sat_constraint_set:NBAcpSatConstraintSet",
    ("nba", "ilp"):    "solvers.leagues.nba.ilp_constraint_set:NBAILPConstraintSet",
    ("nba", "mh"):     "solvers.leagues.nba.mh_constraint_set:NBAMHConstraintSet",
}

_GENERATORS = {
    "epl": "generators.leagues.epl.generate_epl:generate_fixtures",
    "nfl": "generators.leagues.nfl.generate_nfl:generate_fixtures",
    "nba": "generators.leagues.nba.generate_nba:generate_fixtures",
}


def _resolve(spec: str):
    module_path, name = spec.split(":")
    mod = __import__(module_path, fromlist=[name])
    return getattr(mod, name)


def generate_fixtures(league: str, teams: dict):
    """Build the fixture list for a league via its own generator."""
    if league not in _GENERATORS:
        raise ValueError(f"Unknown league {league!r} (expected one of {LEAGUES})")
    return _resolve(_GENERATORS[league])(teams)


def build_constraint_set(
    league: str,
    solver: str,
    constraint_config: dict,
    season_start: date,
    season_end: date,
    calendar: dict,
):
    """Construct the (league, solver) constraint set. Passes final_day only to
    constructors that accept it (EPL's, for HC8 Round-38 pinning); NFL/NBA
    constructors take just (config, season_start, season_end)."""
    key = (league, solver)
    if key not in _CONSTRAINT_SETS:
        raise ValueError(f"No constraint set for league={league!r} solver={solver!r}")
    cls = _resolve(_CONSTRAINT_SETS[key])
    kwargs = {}
    params = inspect.signature(cls.__init__).parameters
    if "final_day" in params:
        kwargs["final_day"] = calendar.get("final_day")
    return cls(constraint_config, season_start, season_end, **kwargs)
