"""
CP-SAT solver entry point. League-agnostic via the shared runner/registry.
Usage:
    python -m solvers.cp_sat.main [--league epl|nfl|nba] [--time-limit 600]
"""
import argparse

from solvers.runner import run_solver
from solvers.registry import LEAGUES


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", default="epl", choices=LEAGUES)
    ap.add_argument("--time-limit", type=int, default=None,
                    help="Solver time limit in seconds (default: 600)")
    args = ap.parse_args()
    run_solver("cp_sat", league=args.league, time_limit=args.time_limit)


if __name__ == "__main__":
    main()
