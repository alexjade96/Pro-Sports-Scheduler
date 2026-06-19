"""
Validate home/away window constraints for every team in a generated schedule.

Checks per team:
  SC13  any 5 consecutive fixtures: 2 or 3 home (Atos Golden Rule)
  SC14  opening 2 and closing 2 fixtures must not be HH or AA
  SC1   no run of more than 5 consecutive away games
  SC2   no run of more than 5 consecutive home games

Usage:
  python tools/validate_ha_windows.py [--csv output/schedule_cp_sat.csv]
  python tools/validate_ha_windows.py --team ARS
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).parent.parent
DEFAULT_CSV = ROOT / "output" / "schedule_cp_sat.csv"


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_schedule(path: Path) -> dict[str, list[tuple[str, str, str]]]:
    """Returns {team_id: [(date, H/A, opponent), ...]} sorted by date."""
    team_games: dict[str, list] = defaultdict(list)
    with open(path) as f:
        for row in csv.DictReader(f):
            team_games[row["home"]].append((row["date"], row["kickoff"], "H", row["away"]))
            team_games[row["away"]].append((row["date"], row["kickoff"], "A", row["home"]))
    return {
        tid: sorted(games, key=lambda g: (g[0], g[1]))
        for tid, games in team_games.items()
    }


# ---------------------------------------------------------------------------
# Per-team checks
# ---------------------------------------------------------------------------

def check_sc13(seq: list[str]) -> list[dict]:
    """Every 5-consecutive-fixture window must have 2 or 3 home games."""
    violations = []
    for i in range(len(seq) - 4):
        window = seq[i : i + 5]
        h = window.count("H")
        if h not in (2, 3):
            violations.append({
                "pos": i,
                "window": "".join(window),
                "home_count": h,
                "type": "excess_home" if h > 3 else "excess_away",
            })
    return violations


def check_sc14(seq: list[str]) -> list[dict]:
    """Opening 2 and closing 2 must not be HH or AA."""
    violations = []
    if len(seq) >= 2:
        if seq[0] == seq[1]:
            violations.append({"boundary": "opening", "pattern": seq[0] * 2})
        if seq[-1] == seq[-2]:
            violations.append({"boundary": "closing", "pattern": seq[-1] * 2})
    return violations


def check_runs(seq: list[str], max_run: int = 5) -> list[dict]:
    """SC1/SC2: no run of more than max_run consecutive home or away games."""
    violations = []
    i = 0
    while i < len(seq):
        j = i
        while j < len(seq) and seq[j] == seq[i]:
            j += 1
        run_len = j - i
        if run_len > max_run:
            violations.append({
                "pos": i,
                "ha": seq[i],
                "run_length": run_len,
                "constraint": "SC2" if seq[i] == "H" else "SC1",
            })
        i = j
    return violations


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _format_sequence_annotated(games: list[tuple], sc13_viols: list[dict]) -> str:
    """Return the H/A sequence with violation positions marked with brackets."""
    seq = [g[2] for g in games]
    viol_positions: set[int] = set()
    for v in sc13_viols:
        for p in range(v["pos"], v["pos"] + 5):
            viol_positions.add(p)
    chars = []
    for i, ha in enumerate(seq):
        chars.append(f"[{ha}]" if i in viol_positions else ha)
    return "".join(chars)


def report_team(team_id: str, games: list[tuple], *, verbose: bool = True) -> dict:
    seq = [g[2] for g in games]
    sc13 = check_sc13(seq)
    sc14 = check_sc14(seq)
    runs = check_runs(seq)

    result = {
        "team": team_id,
        "sequence": "".join(seq),
        "sc13_violations": sc13,
        "sc14_violations": sc14,
        "run_violations": runs,
        "total_violations": len(sc13) + len(sc14) + len(runs),
    }

    if verbose:
        annotated = _format_sequence_annotated(games, sc13)
        print(f"\n{'─' * 60}")
        print(f"  {team_id}  ({len(games)} games)")
        print(f"  Sequence : {annotated}")

        if not sc13 and not sc14 and not runs:
            print("  ✓ No H/A window violations")
        else:
            if sc13:
                print(f"  SC13 violations ({len(sc13)}):")
                for v in sc13:
                    games_in_window = games[v["pos"] : v["pos"] + 5]
                    detail = "  ".join(
                        f"{g[0]} {g[2]} vs {g[3]}" for g in games_in_window
                    )
                    print(f"    pos {v['pos']:2d}: [{v['window']}] {v['home_count']}H — {detail}")
            if sc14:
                print(f"  SC14 violations ({len(sc14)}):")
                for v in sc14:
                    print(f"    {v['boundary']}: {v['pattern']}")
            if runs:
                print(f"  Run violations:")
                for v in runs:
                    print(f"    {v['constraint']}: {v['run_length']} consecutive "
                          f"{'home' if v['ha']=='H' else 'away'} from pos {v['pos']}")

    return result


def summary_table(results: list[dict]) -> None:
    print(f"\n{'='*72}")
    print(f"  H/A WINDOW VALIDATION SUMMARY")
    print(f"{'='*72}")
    print(f"  {'Team':<6} {'Sequence':<42} {'SC13':>4} {'SC14':>4} {'Runs':>4} {'Total':>6}")
    print(f"  {'-'*6} {'-'*42} {'-'*4} {'-'*4} {'-'*4} {'-'*6}")
    for r in sorted(results, key=lambda x: -x["total_violations"]):
        sc13_n = len(r["sc13_violations"])
        sc14_n = len(r["sc14_violations"])
        run_n  = len(r["run_violations"])
        flag   = "" if r["total_violations"] == 0 else " !"
        print(f"  {r['team']:<6} {r['sequence']:<42} {sc13_n:>4} {sc14_n:>4} {run_n:>4} {r['total_violations']:>6}{flag}")
    total_sc13 = sum(len(r["sc13_violations"]) for r in results)
    total_sc14 = sum(len(r["sc14_violations"]) for r in results)
    total_runs = sum(len(r["run_violations"]) for r in results)
    print(f"  {'─'*64}")
    print(f"  {'TOTAL':<6} {'':<42} {total_sc13:>4} {total_sc14:>4} {total_runs:>4} "
          f"{total_sc13+total_sc14+total_runs:>6}")
    print(f"  {'Teams OK':<6} {sum(1 for r in results if r['total_violations']==0):>2} / {len(results)}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Validate H/A window constraints per team")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help="Schedule CSV path")
    parser.add_argument("--team", default=None, help="Single team ID (e.g. ARS)")
    parser.add_argument("--quiet", action="store_true", help="Summary table only, no per-team detail")
    args = parser.parse_args()

    schedule = load_schedule(Path(args.csv))

    teams = [args.team.upper()] if args.team else sorted(schedule.keys())
    unknown = [t for t in teams if t not in schedule]
    if unknown:
        print(f"Unknown team(s): {unknown}. Available: {sorted(schedule.keys())}")
        return

    results = []
    for team_id in teams:
        r = report_team(team_id, schedule[team_id], verbose=not args.quiet)
        results.append(r)

    summary_table(results)


if __name__ == "__main__":
    main()
