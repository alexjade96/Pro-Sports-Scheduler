"""
Generates standalone, human-readable constraint reports for each league.

Reads data/leagues/<league>/constraints.json for the full constraint text
(ID, description, source, values/penalties) and cross-references it against
a manually-verified implementation matrix (curated by reading the actual
solver/validator source, not by naive string grep) so the ✅/❌ flags are
accurate rather than "the ID string appears somewhere in the file."

Run:  python tools/constraint_report.py
Output: output/constraints_<league>.txt  (one per league)
"""
from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_OUT_DIR = _ROOT / "output"

# ---------------------------------------------------------------------------
# Implementation matrices — curated by manual code review, not grep.
# Status values: "yes", "no", "implied", "partial"
#   yes     — explicit constraint logic present and correctly tied to this ID
#   implied — enforced indirectly (e.g. by fixture generation), not a
#             standalone solver constraint
#   partial — logic exists but is mislabeled, approximated, or folded into
#             another constraint's implementation
#   no      — not implemented; note explains why (usually missing data)
# ---------------------------------------------------------------------------

EPL_STATUS: dict[str, dict] = {
    "HC1":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "HC3":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "HC4":  {"cp_sat": "implied", "ilp": "implied", "mh": "implied", "validator": "implied", "note": "Guaranteed by fixture generator producing 380 fixtures; solver assigns each exactly once."},
    "HC5":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "HC6":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "HC7":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "HC8":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "HC9":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "HC10": {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "HC11": {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "HC12": {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "HC13": {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "SC1":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "SC2":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "SC3":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "SC4":  {"cp_sat": "no", "ilp": "no", "mh": "no", "validator": "no", "note": "Needs Champions/Europa League match dates (not in data model)."},
    "SC5":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "SC6":  {"cp_sat": "no", "ilp": "no", "mh": "no", "validator": "no", "note": "Needs FA Cup / Carabao Cup draw data (not in data model)."},
    "SC7":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "SC8":  {"cp_sat": "no", "ilp": "no", "mh": "no", "validator": "no", "note": "Needs Europa/Conference League Thursday match dates (not in data model)."},
    "SC9":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "SC10": {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "SC11": {"cp_sat": "no", "ilp": "no", "mh": "no", "validator": "no", "note": "Needs a `promoted` flag on teams.json (not present)."},
    "SC12": {"cp_sat": "no", "ilp": "no", "mh": "yes", "validator": "yes", "note": "CP-SAT/ILP: not wired in (metaheuristic + validator only)."},
    "SC13": {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "SC14": {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "SC15": {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "SC16": {"cp_sat": "no", "ilp": "no", "mh": "yes", "validator": "no", "note": "Metaheuristic only; not wired into CP-SAT/ILP or validator."},
    "SC17": {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "SC18": {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "validator": "yes"},
    "PR1":  {"cp_sat": "no", "ilp": "no", "mh": "no", "validator": "no", "note": "Not implemented — no primetime-slot assignment model for derbies."},
    "PR2":  {"cp_sat": "partial", "ilp": "partial", "mh": "partial", "validator": "no", "note": "Folded into the SC9 festive-coverage soft constraint (Boxing Day / Dec 28); not tracked as a standalone preference."},
    "PR3":  {"cp_sat": "no", "ilp": "no", "mh": "no", "validator": "no", "note": "Not implemented — no opening-day marquee-fixture selection logic."},
    "PR4":  {"cp_sat": "no", "ilp": "no", "mh": "no", "validator": "no", "note": "Not implemented — narrative-only in the cross-league report, no solver logic."},
    "PR5":  {"cp_sat": "no", "ilp": "no", "mh": "no", "validator": "no", "note": "Not implemented — narrative-only in the cross-league report, no solver logic."},
}

NFL_STATUS: dict[str, dict] = {
    "HC1":  {"cp_sat": "implied", "ilp": "implied", "mh": "implied", "note": "Generator produces exactly 17 fixtures/team; solver assigns each exactly once."},
    "HC2":  {"cp_sat": "implied", "ilp": "implied", "mh": "implied", "note": "Division pairing (6 games) is fixed at fixture-generation time."},
    "HC3":  {"cp_sat": "implied", "ilp": "implied", "mh": "implied", "note": "Intra-conference rotation (4 games) is fixed at fixture-generation time."},
    "HC4":  {"cp_sat": "implied", "ilp": "implied", "mh": "implied", "note": "Inter-conference rotation (4 games) is fixed at fixture-generation time."},
    "HC5":  {"cp_sat": "implied", "ilp": "implied", "mh": "implied", "note": "Standings-crossover pairing (2 games) is fixed at fixture-generation time using standings_2024.json."},
    "HC6":  {"cp_sat": "implied", "ilp": "implied", "mh": "implied", "note": "17th-game pairing (1 game) is fixed at fixture-generation time."},
    "HC7":  {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "GAP: no solver constraint pins each team's bye week inside the weeks 6-14 window, or caps it at exactly one. Slot assignment can currently place a team's open week anywhere in the season."},
    "HC8":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes"},
    "HC9":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes"},
    "HC10": {"cp_sat": "yes", "ilp": "yes", "mh": "yes"},
    "HC11": {"cp_sat": "yes", "ilp": "yes", "mh": "yes"},
    "HC12": {"cp_sat": "implied", "ilp": "implied", "mh": "implied", "note": "Enforced by calendar.json start_date/end_date/matchday_slots, not a solver constraint."},
    "SC1":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes"},
    "SC2":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes"},
    "SC3":  {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Not implemented — distinct from HC10's flat 10-day TNF rest rule; this soft signal (short-week Thu game after a Sunday road trip specifically) isn't separately penalised."},
    "SC4":  {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Needs a broadcast-slot (SNF/MNF/TNF) assignment model — not in the data model."},
    "SC5":  {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Needs a broadcast-slot assignment model — not in the data model."},
    "SC6":  {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Not implemented as a solver check; largely satisfied implicitly by the fixed rotation formula at generation time, but no explicit verification."},
    "SC7":  {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Needs stadium geo-coordinates / travel-distance data — not in the data model."},
    "SC8":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes"},
    "SC9":  {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "calendar.json has an `international_games` window field but it is unused by any solver."},
    "SC10": {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Needs a broadcast-slot assignment model — not in the data model."},
    "SC11": {"cp_sat": "yes", "ilp": "yes", "mh": "yes"},
    "SC12": {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Only the mandatory DAL/DET hosts (HC9) are enforced; the rotating third Thanksgiving host is not modelled."},
}

NBA_STATUS: dict[str, dict] = {
    "HC1":  {"cp_sat": "implied", "ilp": "implied", "mh": "implied", "note": "Generator produces exactly 82 fixtures/team."},
    "HC2":  {"cp_sat": "implied", "ilp": "implied", "mh": "implied", "note": "Division pairing (16 games) fixed at fixture-generation time."},
    "HC3":  {"cp_sat": "implied", "ilp": "implied", "mh": "implied", "note": "Conference non-division split (36 games, symmetric 6x4+4x3) fixed at fixture-generation time."},
    "HC4":  {"cp_sat": "implied", "ilp": "implied", "mh": "implied", "note": "Inter-conference pairing (30 games) fixed at fixture-generation time."},
    "HC5":  {"cp_sat": "yes", "ilp": "yes", "mh": "partial", "note": "MH scores 4-in-5 as a large penalty (soft-enforced) rather than an unbreakable hard rule."},
    "HC6":  {"cp_sat": "yes", "ilp": "yes", "mh": "partial", "note": "MH scores 8-in-12 as a large penalty (soft-enforced) rather than an unbreakable hard rule."},
    "HC7":  {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Needs arena geo-coordinates / travel-distance data — not in the data model."},
    "HC8":  {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "No explicit ceiling-of-16 back-to-back cap anywhere; only indirectly bounded by HC5/HC6 windows. MH tracks the target=14 as a soft SC1 penalty, not a 16 hard ceiling."},
    "HC9":  {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Needs per-game marquee designation (Christmas/Opening Night/IST Finals) — not in the data model."},
    "HC10": {"cp_sat": "yes", "ilp": "yes", "mh": "yes"},
    "HC11": {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Only the break-window blackout (HC10) is implemented; the host-city-specific ±3/2-day arena blackout is not."},
    "HC12": {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Needs In-Season Tournament game designation data — not in the data model."},
    "HC13": {"cp_sat": "yes", "ilp": "yes", "mh": "partial", "note": "MH scores missing final-day appearances as a large penalty (soft-enforced) rather than an unbreakable hard rule."},
    "SC1":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "note": "CP-SAT/ILP penalise a team's total back-to-back count beyond target_per_team (14) via a per-date-pair boolean detector."},
    "SC2":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes"},
    "SC3":  {"cp_sat": "yes", "ilp": "yes", "mh": "yes", "note": "CP-SAT/ILP approximate road-trip length with a rolling date window sized from this model's actual game cadence (avg days/game × max_away), not a hardcoded 42-day window borrowed from EPL's weekly cadence — still date-window-based rather than literal consecutive-game counting, so may over/under-penalise vs. the true metric."},
    "SC4":  {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Not implemented — no NBA-specific half-season H/A balance term exists (EPL's SC5 equivalent was not ported)."},
    "SC5":  {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Needs Christmas Day game designation/market data — not in the data model."},
    "SC6":  {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Needs prior-season champion + Opening Night designation — not in the data model."},
    "SC7":  {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Needs MLK Day designation — not in the data model."},
    "SC8":  {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "FTE rest-equity is a comparative opponent-rest metric — not implemented anywhere."},
    "SC9":  {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Needs arena timezone data — not in the data model."},
    "SC10": {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Not implemented — no weekend-home preference term."},
    "SC11": {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Depends on HC12 (IST designation), which is also unimplemented."},
    "SC12": {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Needs arena-unavailability calendar data — not in the data model."},
    "SC13": {"cp_sat": "no", "ilp": "no", "mh": "no", "note": "Needs NFL-market + NFL-season-calendar cross-reference — not in the data model."},
}

_STATUS_SYMBOL = {"yes": "YES", "implied": "IMPLIED", "partial": "PARTIAL", "no": "NO"}


def _load_constraints(league: str) -> dict:
    with open(_ROOT / "data" / "leagues" / league / "constraints.json") as f:
        return json.load(f)


# Annotation fields rendered on their own dedicated lines (not dumped into Params).
_ANNOTATION_FIELDS = {
    "relaxed_from_official", "relaxation_note",
    "reality_note",
    "enforced_as", "enforced_note",
    "implied_by", "implied_note",
}


def _fmt_value_fields(item: dict) -> str:
    skip = {"id", "type", "source", "description"} | _ANNOTATION_FIELDS
    parts = []
    for k, v in item.items():
        if k in skip:
            continue
        parts.append(f"{k}={v}")
    return "; ".join(parts)


def _wrap(label: str, text: str, width: int = 92) -> list[str]:
    """Wrap a long annotation body under a 'Label : ...' header with hanging indent."""
    import textwrap
    indent = " " * 6 + " " * (len(label) + 3)  # align continuation under the text column
    first = " " * 6 + f"{label} : "
    wrapped = textwrap.wrap(text, width=width) or [""]
    out = [first + wrapped[0]]
    out.extend(indent + line for line in wrapped[1:])
    return out


def _reconciliation_status(item: dict) -> str:
    """One-word tag for the historical-reconciliation column."""
    if "relaxed_from_official" in item:
        return "RELAXED"
    if item.get("enforced_as"):
        return "SOFT-ENFORCED"
    if "reality_note" in item:
        return "KEPT (reality documented)"
    if "implied_by" in item:
        return "IMPLIED (redundant)"
    return "AS OFFICIAL"


def _render_section(title: str, items: list[dict], status_map: dict[str, dict], solver_cols: list[str]) -> str:
    lines = [f"\n{'─' * 100}", f"{title} ({len(items)})", "─" * 100]
    for item in items:
        cid = item["id"]
        st = status_map.get(cid, {})
        lines.append(f"\n[{cid}] {item['description']}")
        details = _fmt_value_fields(item)
        if details:
            lines.append(f"      Params : {details}")
        lines.append(f"      Source : {item.get('source', '—')}")
        flags = "  ".join(f"{col.upper()}={_STATUS_SYMBOL.get(st.get(col, 'no'), 'NO')}" for col in solver_cols)
        lines.append(f"      Status : {flags}")

        # Historical-reconciliation column: when a rule was relaxed/changed/kept
        # relative to its official definition for historical-scheduling reasons.
        lines.append(f"      Reality: {_reconciliation_status(item)}")
        if "relaxed_from_official" in item:
            lines += _wrap("Relaxed", f"from official {item['relaxed_from_official']}. "
                           f"{item.get('relaxation_note', '')}".strip())
        elif item.get("reality_note"):
            lines += _wrap("Note", item["reality_note"])
        if item.get("enforced_as"):
            lines += _wrap("Enforced", f"as {item['enforced_as']}. {item.get('enforced_note', '')}".strip())
        if item.get("implied_by"):
            lines += _wrap("Implied", f"by {item['implied_by']}. {item.get('implied_note', '')}".strip())

        if st.get("note"):
            lines += _wrap("Impl", st["note"])
    return "\n".join(lines)


def _reconciliation_summary(data: dict) -> str:
    """List every constraint whose enforcement diverges from its official
    definition for historical-scheduling reasons (relaxed / soft-enforced /
    kept-but-documented / redundant)."""
    all_items = data.get("hard", []) + data.get("soft", []) + data.get("preferences", [])
    relaxed, soft_enf, kept, implied = [], [], [], []
    for it in all_items:
        if "relaxed_from_official" in it:
            relaxed.append((it["id"], it["relaxed_from_official"]))
        elif it.get("enforced_as"):
            soft_enf.append((it["id"], it["enforced_as"]))
        elif "reality_note" in it:
            kept.append(it["id"])
        elif "implied_by" in it:
            implied.append((it["id"], it["implied_by"]))
    lines = [f"\n{'─' * 100}", "HISTORICAL-RECONCILIATION SUMMARY", "─" * 100,
             "  How each rule's enforcement relates to its official definition, after calibrating",
             "  against the real historical seasons (see data/leagues/<league>/constraints.json).", ""]
    if relaxed:
        lines.append("  RELAXED / CHANGED from official (enforced value differs from the rulebook):")
        for cid, orig in relaxed:
            lines.append(f"    - {cid}: original official {orig} -> relaxed to current `value` (see Relaxed note)")
    if soft_enf:
        lines.append("  SOFT-ENFORCED (officially hard, enforced as soft for tractability/reality):")
        for cid, how in soft_enf:
            lines.append(f"    - {cid}: enforced {how}")
    if kept:
        lines.append("  KEPT as official, real-world divergence documented (rule-change-over-time / festive / COVID):")
        lines.append(f"    - {', '.join(kept)}")
    if implied:
        lines.append("  IMPLIED / redundant (kept for provenance, enforced via another rule):")
        for cid, by in implied:
            lines.append(f"    - {cid}: implied by {by}")
    if not (relaxed or soft_enf or kept or implied):
        lines.append("  (none — every constraint is enforced exactly as officially defined)")
    return "\n".join(lines)


def _coverage_stats(status_map: dict[str, dict], solver_cols: list[str]) -> str:
    lines = [f"\n{'─' * 100}", "COVERAGE SUMMARY", "─" * 100]
    total = len(status_map)
    for col in solver_cols:
        counts = {"yes": 0, "implied": 0, "partial": 0, "no": 0}
        for st in status_map.values():
            counts[st.get(col, "no")] += 1
        fully = counts["yes"] + counts["implied"]
        lines.append(
            f"  {col.upper():10s}: {fully}/{total} covered "
            f"(yes={counts['yes']} implied={counts['implied']} partial={counts['partial']} no={counts['no']})"
        )
    return "\n".join(lines)


_INFRA_WARNING = (
    "\n  *** KNOWN INFRASTRUCTURE ISSUE ***\n"
    "  solvers/slot_filter.py hardcodes EPL's structure (n_rounds=38, 10 fixtures/round,\n"
    "  and assumes fixtures are returned in round-interleaved order). This league's fixture\n"
    "  generator groups fixtures by matchup-type block instead (e.g. all division games\n"
    "  first), so the filter's per-fixture date windows do not correspond to spread-out\n"
    "  weekly play — a team's division rivals, for example, all get windowed into the same\n"
    "  few weeks. Confirmed by direct testing: CP-SAT hard constraints alone are INFEASIBLE\n"
    "  for this league at solve time (min-rest ≥3 days already breaks it). The metaheuristic\n"
    "  solver is unaffected — it does not use this filter. CP-SAT/ILP will need either a\n"
    "  per-league-parameterized slot filter or a fixture generator that round-interleaves\n"
    "  output before this pipeline is usable end-to-end.\n"
)


def generate_report(league: str, solver_cols: list[str], status_map: dict[str, dict]) -> str:
    data = _load_constraints(league)
    lines = [
        "=" * 100,
        f"  {league.upper()} — STANDALONE CONSTRAINT REFERENCE".ljust(99) + "=",
        "=" * 100,
    ]
    for note in data.get("_notes", []):
        lines.append(f"  {note}")
    if league in ("nfl", "nba"):
        lines.append(_INFRA_WARNING)

    if data.get("hard"):
        lines.append(_render_section("HARD CONSTRAINTS", data["hard"], status_map, solver_cols))
    if data.get("soft"):
        lines.append(_render_section("SOFT CONSTRAINTS", data["soft"], status_map, solver_cols))
    if data.get("preferences"):
        lines.append(_render_section("PREFERENCE CONSTRAINTS", data["preferences"], status_map, solver_cols))

    lines.append(_reconciliation_summary(data))
    lines.append(_coverage_stats(status_map, solver_cols))
    lines.append("\n" + "=" * 100)
    lines.append("  Status legend: YES = fully implemented and correctly tied to this ID")
    lines.append("          IMPLIED = enforced indirectly (e.g. by fixture generation), not a standalone solver check")
    lines.append("          PARTIAL = logic exists but is mislabeled, approximated, or folded into another constraint")
    lines.append("          NO = not implemented (see Impl note for reason, usually missing external data)")
    lines.append("  Reality legend (relation to official definition, per historical reconciliation):")
    lines.append("          RELAXED = enforced value was changed because the official was over-strict/mis-specified vs real schedules")
    lines.append("          SOFT-ENFORCED = officially hard, enforced as soft for tractability/reality")
    lines.append("          KEPT (reality documented) = value unchanged, but real seasons diverge (rule-change-over-time / festive / COVID)")
    lines.append("          IMPLIED (redundant) = kept for provenance, enforced via another rule")
    lines.append("          AS OFFICIAL = enforced exactly as the rulebook defines it")
    lines.append("=" * 100)
    return "\n".join(lines)


def main() -> None:
    _OUT_DIR.mkdir(exist_ok=True)

    reports = {
        "epl": (["cp_sat", "ilp", "mh", "validator"], EPL_STATUS),
        "nfl": (["cp_sat", "ilp", "mh"], NFL_STATUS),
        "nba": (["cp_sat", "ilp", "mh"], NBA_STATUS),
    }

    for league, (cols, status_map) in reports.items():
        report = generate_report(league, cols, status_map)
        out_path = _OUT_DIR / f"constraints_{league}.txt"
        with open(out_path, "w") as f:
            f.write(report)
        print(f"  {league.upper()}: {out_path}")


if __name__ == "__main__":
    main()
