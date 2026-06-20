"""
Report renderer: turns comparator output dicts into human-readable
plain-text tables and a self-contained HTML file.

Plain text is written to stdout and to output/report_*.txt.
HTML is written to output/report_*.html — open in any browser.
"""
from __future__ import annotations

import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any

from analysis.metrics import MetricsReport

OUTPUT_DIR = Path(__file__).parent.parent / "output"


# ---------------------------------------------------------------------------
# Plain-text renderer
# ---------------------------------------------------------------------------

def _pad(value: Any, width: int, align: str = "<") -> str:
    s = "—" if value is None else str(value)
    return f"{s:{align}{width}}"


def render_text_accuracy(comparison: dict) -> str:
    rows  = comparison["rows"]
    gen   = comparison["generated"]
    hist  = comparison["historical"]

    col_w = [40, 14, 14, 10, 30]
    header = (
        f"{'Metric':<{col_w[0]}} "
        f"{'Generated':>{col_w[1]}} "
        f"{'Historical':>{col_w[2]}} "
        f"{'Delta':>{col_w[3]}} "
        f"{'Note':<{col_w[4]}}"
    )
    sep = "-" * sum(col_w + [len(col_w)])

    lines = [
        "",
        "=" * len(sep),
        f"ACCURACY REPORT:  {gen}  vs  {hist}",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M}",
        "=" * len(sep),
        header,
        sep,
    ]
    for row in rows:
        delta = row.get("delta")
        if delta is None:
            delta_str = "—"
        elif isinstance(delta, float):
            delta_str = f"{delta:+.1f}"
        else:
            delta_str = f"{delta:+d}" if delta != 0 else "0"

        lines.append(
            f"{_pad(row['metric'], col_w[0])} "
            f"{_pad(row['generated'], col_w[1], '>')} "
            f"{_pad(row['historical'], col_w[2], '>')} "
            f"{_pad(delta_str, col_w[3], '>')} "
            f"{_pad(row.get('note',''), col_w[4])}"
        )

    lines += ["=" * len(sep), ""]
    return "\n".join(lines)


def render_text_solver_comparison(comparison: dict) -> str:
    labels = comparison["labels"]
    rows   = comparison["rows"]

    col_w  = 22
    metric_w = 36

    header = f"{'Metric':<{metric_w}}" + "".join(f"{lbl:>{col_w}}" for lbl in labels) + f"  {'Note'}"
    sep    = "-" * (metric_w + col_w * len(labels) + 30)

    lines = [
        "",
        "=" * len(sep),
        "SOLVER COMPARISON REPORT",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M}",
        "=" * len(sep),
        header,
        sep,
    ]
    for row in rows:
        metric = row["metric"]
        if metric.startswith("──"):
            lines.append(sep)
            lines.append(f"{metric}")
            lines.append(sep)
            continue
        vals = "".join(
            f"{_pad(row['values'].get(lbl), col_w, '>'):>{col_w}}"
            for lbl in labels
        )
        lines.append(f"{_pad(metric, metric_w)}{vals}  {row.get('note','')}")

    lines += ["=" * len(sep), ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

def _html_table_accuracy(comparison: dict) -> str:
    rows = comparison["rows"]
    gen  = comparison["generated"]
    hist = comparison["historical"]

    def cell_class(delta):
        if delta is None or delta == 0:
            return ""
        return "positive" if delta > 0 else "negative"

    thead = f"""
    <thead>
      <tr>
        <th>Metric</th>
        <th>{gen}</th>
        <th>{hist} (historical)</th>
        <th>Delta</th>
        <th>Note</th>
      </tr>
    </thead>"""

    tbody_rows = []
    for row in rows:
        delta = row.get("delta")
        cls   = cell_class(delta)
        delta_str = "—" if delta is None else (f"{delta:+.1f}" if isinstance(delta, float) else f"{delta:+d}")
        tbody_rows.append(
            f"<tr>"
            f"<td>{row['metric']}</td>"
            f"<td>{row.get('generated','—')}</td>"
            f"<td>{row.get('historical','—')}</td>"
            f"<td class='{cls}'>{delta_str}</td>"
            f"<td class='note'>{row.get('note','')}</td>"
            f"</tr>"
        )

    return f"""
    <h2>Accuracy: {gen} vs {hist} (historical)</h2>
    <table>
      {thead}
      <tbody>{''.join(tbody_rows)}</tbody>
    </table>"""


def _html_table_solver(comparison: dict) -> str:
    labels = comparison["labels"]
    rows   = comparison["rows"]

    header_cells = "".join(f"<th>{lbl}</th>" for lbl in labels)
    thead = f"<thead><tr><th>Metric</th>{header_cells}<th>Note</th></tr></thead>"

    tbody_rows = []
    for row in rows:
        metric = row["metric"]
        if metric.startswith("──"):
            # Section separator row
            span = len(labels) + 2
            tbody_rows.append(
                f"<tr class='section-header'><td colspan='{span}'>{metric}</td></tr>"
            )
            continue
        vals = "".join(
            f"<td>{'—' if row['values'].get(lbl) is None else row['values'].get(lbl, '—')}</td>"
            for lbl in labels
        )
        tbody_rows.append(f"<tr><td>{metric}</td>{vals}<td class='note'>{row.get('note','')}</td></tr>")

    return f"""
    <h2>Solver Comparison</h2>
    <table>
      {thead}
      <tbody>{''.join(tbody_rows)}</tbody>
    </table>"""


_HTML_STYLE = """
<style>
  body { font-family: monospace; margin: 2em; background: #fafafa; color: #222; }
  h1   { border-bottom: 2px solid #333; padding-bottom: 6px; }
  h2   { margin-top: 2em; color: #444; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 1.5em; }
  th, td { border: 1px solid #ccc; padding: 6px 10px; text-align: right; }
  th { background: #2c3e50; color: #fff; text-align: center; }
  td:first-child { text-align: left; }
  tr:nth-child(even) { background: #f0f0f0; }
  .positive { color: #b5451b; font-weight: bold; }
  .negative { color: #27ae60; font-weight: bold; }
  .note { color: #666; font-size: 0.9em; text-align: left; }
  .timestamp { font-size: 0.85em; color: #888; }
  .section-header td { background: #34495e; color: #ecf0f1; font-weight: bold;
                       text-align: left; padding: 4px 10px; font-size: 0.85em; }
</style>
"""


def render_html(
    accuracy_comparison:       dict | None = None,
    solver_comparison:         dict | None = None,
    per_team_table:            str  | None = None,
) -> str:
    sections = []
    if accuracy_comparison:
        sections.append(_html_table_accuracy(accuracy_comparison))
    if solver_comparison:
        sections.append(_html_table_solver(solver_comparison))
    if per_team_table:
        sections.append(f"<h2>Per-Team Detail</h2><pre>{per_team_table}</pre>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>EPL Scheduler — Analysis Report</title>
  {_HTML_STYLE}
</head>
<body>
  <h1>EPL Scheduler — Analysis Report</h1>
  <p class="timestamp">Generated: {datetime.now():%Y-%m-%d %H:%M:%S}</p>
  {''.join(sections)}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Per-team detail table (plain text)
# ---------------------------------------------------------------------------

def render_per_team_table(reports: list[MetricsReport]) -> str:
    """
    One row per team, columns for each report: min rest, max consec H, max consec A.
    Useful for spotting which teams are treated worse by each solver.
    """
    all_teams = sorted(set(t for r in reports for t in r.teams_seen))
    col_w = 10
    label_w = 6

    header = f"{'Team':<{label_w}}"
    for r in reports:
        lbl = r.label[:col_w]
        header += f" {lbl:>{col_w}}{'':>{col_w}}{'':>{col_w}}"
    subheader = f"{'':>{label_w}}"
    for r in reports:
        subheader += f" {'minRst':>{col_w}}{'maxH':>{col_w}}{'maxA':>{col_w}}"

    sep = "-" * (label_w + len(reports) * 3 * col_w + len(reports))
    lines = [header, subheader, sep]

    for team in all_teams:
        row = f"{team:<{label_w}}"
        for r in reports:
            min_rest  = r.rest_min_per_team.get(team, "—")
            max_home  = r.max_consec_home_per_team.get(team, "—")
            max_away  = r.max_consec_away_per_team.get(team, "—")
            row += f" {str(min_rest):>{col_w}}{str(max_home):>{col_w}}{str(max_away):>{col_w}}"
        lines.append(row)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Save to disk
# ---------------------------------------------------------------------------

def save(text: str, filename: str) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / filename
    path.write_text(text)
    return path
