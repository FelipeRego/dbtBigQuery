#!/usr/bin/env python
"""
Regenerate the README's metric dictionary from the compiled dbt manifest.

The definitions live in `models/semantic/metrics.yml`. dbt compiles them into
`target/semantic_manifest.json`. This script renders them into README.md
between two marker comments, and the MCP server serves the same text to agents.
One source, three consumers, no drift.

    python scripts/render_metric_docs.py            # rewrite the README section
    python scripts/render_metric_docs.py --check    # exit 1 if it is stale (CI)

The split between "headline" and "reference" metrics below is the one editorial
decision in this file. Ratio metrics that a stakeholder would actually ask for
get the full four-part treatment; the component metrics they are built from get
a compact row, because repeating "see activation_rate" twelve times helps
nobody. Both halves are generated, so neither can go stale.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
MANIFEST = PROJECT_DIR / "target" / "semantic_manifest.json"
README = PROJECT_DIR / "README.md"

BEGIN = "<!-- BEGIN GENERATED METRIC DICTIONARY -->"
END = "<!-- END GENERATED METRIC DICTIONARY -->"

# Metrics a person would ask for by name, in the order the README argues them.
HEADLINE: list[tuple[str, list[str]]] = [
    (
        "Activation",
        ["activation_rate", "avg_days_to_first_purchase"],
    ),
    (
        "Retention",
        ["d7_retention_rate", "d1_retention_rate", "d30_retention_rate"],
    ),
    (
        "Feature adoption",
        [
            "feature_adoption_rate",
            "feature_adoption_rate_site_search",
            "feature_adoption_rate_promotions",
            "feature_adoption_rate_product_list",
            "feature_adoption_rate_outbound_click",
        ],
    ),
    (
        "Session depth and engagement",
        [
            "session_depth",
            "engagement_rate",
            "engagement_rate_derived",
            "sessionisation_disagreement_rate",
        ],
    ),
    (
        "Funnel",
        ["purchase_conversion_rate", "checkout_completion_rate"],
    ),
    (
        "Commercial",
        ["revenue", "transactions", "average_order_value", "revenue_per_session"],
    ),
    (
        "Population",
        ["new_users", "sessions"],
    ),
]


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def load_metrics() -> dict[str, dict]:
    if not MANIFEST.exists():
        sys.exit(f"No semantic manifest at {MANIFEST}. Run `dbt parse` first.")
    manifest = json.loads(MANIFEST.read_text())
    out = {}
    for metric in manifest.get("metrics", []):
        meta = (metric.get("config") or {}).get("meta") or {}
        out[metric["name"]] = {
            "name": metric["name"],
            "label": metric.get("label") or metric["name"],
            "type": metric.get("type"),
            "description": _clean(metric.get("description")),
            "grain": _clean(meta.get("grain")),
            "definition": _clean(meta.get("definition")),
            "assumption": _clean(meta.get("assumption")),
            "tradeoff": _clean(meta.get("tradeoff")),
            "breaks_if": _clean(meta.get("breaks_if")),
        }
    return out


def render(metrics: dict[str, dict]) -> str:
    lines: list[str] = [
        BEGIN,
        "",
        (
            "> Generated from `models/semantic/metrics.yml` by "
            "`scripts/render_metric_docs.py`. Do not edit by hand — edit the "
            "YAML and re-run `dbt parse`. CI fails if this section drifts."
        ),
        "",
    ]

    seen: set[str] = set()
    for section, names in HEADLINE:
        present = [n for n in names if n in metrics]
        if not present:
            continue
        lines.append(f"### {section}")
        lines.append("")
        for name in present:
            m = metrics[name]
            seen.add(name)
            lines.append(f"#### `{name}` — {m['label']}")
            lines.append("")
            lines.append(f"*{m['type']} metric, {m['grain'] or 'unspecified'} grain.*")
            lines.append("")
            lines.append(f"**Definition.** {m['definition'] or m['description']}")
            lines.append("")
            if m["assumption"]:
                lines.append(f"**Assumption.** {m['assumption']}")
                lines.append("")
            if m["tradeoff"]:
                lines.append(f"**Trade-off.** {m['tradeoff']}")
                lines.append("")
            if m["breaks_if"]:
                lines.append(f"**Breaks if.** {m['breaks_if']}")
                lines.append("")

    remaining = sorted(n for n in metrics if n not in seen)
    if remaining:
        lines.append("### Component metrics")
        lines.append("")
        lines.append(
            "These exist so that the headline ratios above have inspectable "
            "numerators and denominators. Each carries the same four-part "
            "governance block in the manifest; the MCP server and "
            "`dbt docs` serve it in full."
        )
        lines.append("")
        lines.append("| Metric | Type | Grain | Definition |")
        lines.append("| --- | --- | --- | --- |")
        for name in remaining:
            m = metrics[name]
            definition = (m["definition"] or m["description"]).replace("|", "\\|")
            if len(definition) > 190:
                definition = definition[:187].rstrip() + "…"
            lines.append(f"| `{name}` | {m['type']} | {m['grain'] or '—'} | {definition} |")
        lines.append("")

    lines.append(END)
    return "\n".join(lines)


def splice(readme: str, block: str) -> str:
    if BEGIN not in readme or END not in readme:
        sys.exit(f"README.md is missing the {BEGIN} / {END} markers.")
    head = readme.split(BEGIN)[0]
    tail = readme.split(END, 1)[1]
    return head + block + tail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the README section is out of date, without rewriting it.",
    )
    args = parser.parse_args()

    metrics = load_metrics()
    block = render(metrics)
    current = README.read_text()
    updated = splice(current, block)

    if args.check:
        if updated != current:
            print(
                "README metric dictionary is out of date.\n"
                "Run: python scripts/render_metric_docs.py",
                file=sys.stderr,
            )
            return 1
        print(f"README metric dictionary is current ({len(metrics)} metrics).")
        return 0

    README.write_text(updated)
    print(f"Wrote {len(metrics)} metrics into README.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
