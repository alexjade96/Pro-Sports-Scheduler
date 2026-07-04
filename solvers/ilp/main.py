"""
ILP / PuLP solver entry point. League-agnostic via the shared runner/registry.
Usage:
    python -m solvers.ilp.main [--league epl|nfl|nba] [--time-limit 1800]

Note: PuLP/CBC's timeLimit is unreliable in this environment and CBC has not
been confirmed to converge at NFL/NBA scale — see CLAUDE.md. CP-SAT is the
recommended MIP option for NFL/NBA.
"""
import argparse

from solvers.runner import run_solver
from solvers.registry import LEAGUES


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--league", default="epl", choices=LEAGUES)
    ap.add_argument("--time-limit", type=int, default=None,
                    help="Solver time limit in seconds (default: 1800)")
    args = ap.parse_args()
    run_solver("ilp", league=args.league, time_limit=args.time_limit)


if __name__ == "__main__":
    main()
