"""
Historical schedule loader: dispatches to the active league's per-league
CSV parser (analysis/leagues/<league>/historical.py) and returns a Schedule
object usable by the shared metrics engine.

Each league's CSV schema is different (EPL is football-data.co.uk's
name-mapped DD/MM/YYYY format; NFL/NBA use direct team IDs with an
ISO gameday column) so the row-parsing itself is league-scoped — this
module only resolves "which league" and delegates. See "Analysis
architecture" in CLAUDE.md.
"""
from __future__ import annotations

from pathlib import Path

from core.data_loader import get_active_league
from core.models import Schedule

DATA_ROOT = Path(__file__).parent.parent / "data" / "leagues"

_SUPPORTED_LEAGUES = ("epl", "nfl", "nba")


def _historical_dir(league: str) -> Path:
    return DATA_ROOT / league / "historical"


def _infer_league(csv_path: Path) -> str:
    """Infers the league from a CSV path like data/leagues/<league>/historical/...
    Falls back to the active league if the path doesn't follow that layout —
    this lets callers pass arbitrary CSV paths without an explicit league arg."""
    parts = csv_path.resolve().parts
    if "leagues" in parts:
        idx = parts.index("leagues")
        if idx + 1 < len(parts) and parts[idx + 1] in _SUPPORTED_LEAGUES:
            return parts[idx + 1]
    return get_active_league()


def _loader(league: str):
    if league == "epl":
        from analysis.leagues.epl.historical import load_season
        return load_season
    if league == "nfl":
        from analysis.leagues.nfl.historical import load_season
        return load_season
    if league == "nba":
        from analysis.leagues.nba.historical import load_season
        return load_season
    raise ValueError(f"No historical loader for league {league!r}")


def load_season(csv_path: Path | str, season_label: str | None = None, league: str | None = None) -> Schedule:
    """Reads a historical schedule CSV and returns a Schedule object.

    If `league` is not given, it's inferred from the path (data/leagues/<league>/historical/...)
    or falls back to the currently active league (core.data_loader.get_active_league()).
    """
    csv_path = Path(csv_path)
    league = league or _infer_league(csv_path)
    return _loader(league)(csv_path, season_label)


def available_seasons(league: str | None = None) -> list[Path]:
    """Returns all historical CSV files for the given (or active) league."""
    league = league or get_active_league()
    return sorted(_historical_dir(league).glob("*.csv"))
