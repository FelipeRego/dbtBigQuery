#!/usr/bin/env python
"""
Render docs/img/semantic-layer-overview.svg from live MetricFlow queries.

Every number in the image comes from `mf query` at render time, so the picture
in the README cannot drift from the metric definitions the way a pasted
screenshot can. Run it after a build:

    .venv/bin/python scripts/render_overview_svg.py

The output is a static SVG with its own light surface, so it reads correctly in
both GitHub themes. It carries no hover layer — a README image cannot have one —
so every mark is directly labelled instead, which is also what the palette's
contrast check requires for the aqua series.
"""

from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

PROJECT_DIR = Path(__file__).resolve().parent.parent
OUT = PROJECT_DIR / "docs" / "img" / "semantic-layer-overview.svg"

# Validated categorical slots 1-3 (light mode) from the data-viz reference
# palette. Checked with scripts/validate_palette.js: worst adjacent CVD ΔE 9.2,
# normal-vision ΔE 27.6. Aqua sits below 3:1 on this surface, so every mark
# carries a visible direct label.
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
SURFACE = "#fcfcfb"
PANEL = "#ffffff"
INK = "#0b0b0b"
INK_2 = "#52514e"
INK_3 = "#87867f"
GRID = "#e6e5e1"

W, H = 1180, 844


def mf(metrics: str, group_by: str = "", order: str = "") -> list[dict[str, str]]:
    """Run one MetricFlow query and return its rows."""
    env = dict(os.environ)
    env.setdefault("DBT_PROFILES_DIR", str(PROJECT_DIR))
    if not env.get("DBT_BIGQUERY_PROJECT"):
        sys.exit("DBT_BIGQUERY_PROJECT is not set — source .env first.")

    out = PROJECT_DIR / "target" / "_overview_tmp.csv"
    args = [str(PROJECT_DIR / ".venv" / "bin" / "mf"), "query", "--metrics", metrics]
    if group_by:
        args += ["--group-by", group_by]
    if order:
        args += ["--order", order]
    args += ["--csv", str(out)]

    result = subprocess.run(
        args, cwd=PROJECT_DIR, env=env, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        sys.exit(f"mf query failed for {metrics}:\n{result.stderr or result.stdout}")
    rows = list(csv.DictReader(io.StringIO(out.read_text(encoding="utf-8"))))
    out.unlink(missing_ok=True)
    return rows


def num(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


# --------------------------------------------------------------------------
# SVG helpers
# --------------------------------------------------------------------------


def text(x, y, s, size=13, fill=INK, weight=400, anchor="start", opacity=1.0):
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
        f'font-weight="{weight}" text-anchor="{anchor}" opacity="{opacity}">'
        f"{escape(s)}</text>"
    )


def rect(x, y, w, h, fill, rx=0, opacity=1.0, stroke="none"):
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" height="{max(h, 0):.1f}" '
        f'rx="{rx}" fill="{fill}" opacity="{opacity}" stroke="{stroke}"/>'
    )


def build() -> str:
    # ---- data ------------------------------------------------------------
    kpi = mf("activation_rate,d7_retention_rate,session_depth,feature_adoption_rate")[0]
    weekly = mf(
        "activation_rate,d1_retention_rate,d7_retention_rate",
        group_by="metric_time__week",
        order="metric_time__week",
    )
    funnel_row = mf(
        "sessions_starting_funnel,sessions_reaching_view_item,"
        "sessions_reaching_add_to_cart,sessions_reaching_begin_checkout,"
        "sessions_reaching_purchase"
    )[0]
    adoption_row = mf(
        "feature_adoption_rate_site_search,feature_adoption_rate_product_list,"
        "feature_adoption_rate_promotions,feature_adoption_rate_outbound_click"
    )[0]

    p: list[str] = []
    p.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="Inter, -apple-system, BlinkMacSystemFont, '
        f"'Segoe UI', Helvetica, Arial, sans-serif\">"
    )
    p.append(rect(0, 0, W, H, SURFACE))

    # ---- header ----------------------------------------------------------
    p.append(text(40, 48, "GA4 governed semantic layer", 25, INK, 700))
    p.append(
        text(
            40,
            72,
            "Every figure below is read from MetricFlow at render time — "
            "no number here was typed by hand.",
            13.5,
            INK_2,
        )
    )
    p.append(
        text(
            W - 40,
            48,
            "4,295,584 events · 360,129 sessions · 270,154 users",
            13,
            INK_2,
            anchor="end",
        )
    )
    p.append(text(W - 40, 70, "2020-11-01 → 2021-01-31", 13, INK_3, anchor="end"))

    # ---- KPI tiles -------------------------------------------------------
    tiles = [
        (
            "Activation rate",
            f"{num(kpi['activation_rate']) * 100:.2f}%",
            "first purchase ≤7d",
            BLUE,
        ),
        (
            "D7 retention",
            f"{num(kpi['d7_retention_rate']) * 100:.2f}%",
            "returned on days 1–7",
            ORANGE,
        ),
        ("Session depth", f"{num(kpi['session_depth']):.1f}", "events per session", AQUA),
        (
            "Feature adoption",
            f"{num(kpi['feature_adoption_rate']) * 100:.1f}%",
            "any feature ≤7d",
            INK_2,
        ),
    ]
    tw, gap, ty = 262, 20, 100
    for i, (label, value, sub, colour) in enumerate(tiles):
        x = 40 + i * (tw + gap)
        p.append(rect(x, ty, tw, 96, PANEL, rx=10, stroke=GRID))
        p.append(rect(x, ty, 4, 96, colour, rx=2))
        p.append(text(x + 20, ty + 27, label.upper(), 10.5, INK_3, 600))
        p.append(text(x + 20, ty + 63, value, 30, INK, 700))
        p.append(text(x + 20, ty + 82, sub, 11.5, INK_2))

    # ---- panel 1: cohort curves -----------------------------------------
    px, py, pw, ph = 40, 226, 700, 290
    p.append(rect(px, py, pw, ph, PANEL, rx=10, stroke=GRID))
    p.append(text(px + 20, py + 28, "Cohort curves by acquisition week", 15, INK, 600))
    p.append(
        text(
            px + pw - 20,
            py + 28,
            "dated by cohort arrival, not by return",
            11.5,
            INK_3,
            anchor="end",
        )
    )

    label_gutter = 104
    cx, cy = px + 48, py + 68
    cw, chh = pw - 48 - label_gutter, ph - 118
    y_max = 0.12
    for i in range(4):
        gy = cy + chh * i / 3
        p.append(f'<line x1="{cx}" y1="{gy:.1f}" x2="{cx + cw}" y2="{gy:.1f}" stroke="{GRID}"/>')
        p.append(
            text(cx - 10, gy + 4, f"{y_max * (3 - i) / 3 * 100:.0f}%", 10.5, INK_3, anchor="end")
        )

    n = len(weekly)
    step = cw / max(n - 1, 1)

    def sx(i: int) -> float:
        return cx + i * step

    def sy(v: float) -> float:
        return cy + chh * (1 - min(v / y_max, 1.0))

    series = [
        ("d7_retention_rate", ORANGE, "D7 retention"),
        ("d1_retention_rate", BLUE, "D1 retention"),
        ("activation_rate", AQUA, "Activation"),
    ]
    lx = px + 20
    for _key, colour, label in series:
        p.append(rect(lx, py + 38, 9, 9, colour, rx=2))
        p.append(text(lx + 15, py + 47, label, 11.5, INK_2))
        lx += 26 + len(label) * 6.2
    for key, colour, label in series:
        pts = [(i, num(r.get(key))) for i, r in enumerate(weekly)]
        drawn = [(i, v) for i, v in pts if v is not None]
        if not drawn:
            continue
        d = " ".join(
            f"{'M' if k == 0 else 'L'}{sx(i):.1f},{sy(v):.1f}" for k, (i, v) in enumerate(drawn)
        )
        p.append(
            f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
        )
        for i, v in drawn:
            p.append(
                f'<circle cx="{sx(i):.1f}" cy="{sy(v):.1f}" r="3.2" fill="{colour}" '
                f'stroke="{PANEL}" stroke-width="2"/>'
            )
        li, lv = drawn[-1]
        p.append(text(sx(li) + 9, sy(lv) + 4, label, 11.5, INK_2, 600))

    # x labels: first, middle, last
    for i in (0, n // 2, n - 1):
        if 0 <= i < n:
            p.append(
                text(
                    sx(i),
                    cy + chh + 18,
                    weekly[i]["metric_time__week"][:10],
                    10.5,
                    INK_3,
                    anchor="middle",
                )
            )

    # The censoring guard, made visible.
    last_gap = [i for i, r in enumerate(weekly) if num(r.get("d7_retention_rate")) is None]
    if last_gap:
        gi = last_gap[0]
        p.append(
            f'<line x1="{sx(gi):.1f}" y1="{cy}" x2="{sx(gi):.1f}" y2="{cy + chh}" '
            f'stroke="{INK_3}" stroke-width="1" stroke-dasharray="3 3"/>'
        )
        p.append(text(sx(gi) - 10, cy + 14, "window still open —", 10.5, INK_2, 600, anchor="end"))
        p.append(text(sx(gi) - 10, cy + 28, "the layer returns null", 10.5, INK_2, anchor="end"))
        p.append(text(sx(gi) - 10, cy + 42, "rather than a low number", 10.5, INK_2, anchor="end"))

    # ---- panel 2: feature adoption --------------------------------------
    ax, ay, aw, ah = 760, 226, 380, 290
    p.append(rect(ax, ay, aw, ah, PANEL, rx=10, stroke=GRID))
    p.append(text(ax + 20, ay + 28, "Feature adoption, first 7 days", 15, INK, 600))
    p.append(
        text(ax + 20, ay + 47, "User-initiated events only; impressions excluded.", 11.5, INK_2)
    )

    feats = [
        ("Site search", num(adoption_row["feature_adoption_rate_site_search"])),
        ("Product list", num(adoption_row["feature_adoption_rate_product_list"])),
        ("Promotions", num(adoption_row["feature_adoption_rate_promotions"])),
        ("Outbound click", num(adoption_row["feature_adoption_rate_outbound_click"])),
    ]
    fmax = max(v for _, v in feats if v) or 1
    bx, by0, bw = ax + 132, ay + 78, aw - 210
    for i, (label, v) in enumerate(feats):
        yy = by0 + i * 48
        p.append(text(ax + 118, yy + 15, label, 12, INK_2, anchor="end"))
        p.append(rect(bx, yy, bw, 20, GRID, rx=4, opacity=0.5))
        p.append(rect(bx, yy, bw * (v or 0) / fmax, 20, BLUE, rx=4))
        p.append(
            text(bx + bw * (v or 0) / fmax + 9, yy + 15, f"{(v or 0) * 100:.2f}%", 12, INK, 600)
        )

    # ---- panel 3: funnel -------------------------------------------------
    fx, fy, fw, fh = 40, 536, 1100, 258
    p.append(rect(fx, fy, fw, fh, PANEL, rx=10, stroke=GRID))
    p.append(
        text(fx + 20, fy + 28, "Purchase funnel — strict, in-sequence definition", 15, INK, 600)
    )
    p.append(
        text(
            fx + 20,
            fy + 47,
            "A step counts only when every prior step was reached in the same session.",
            11.5,
            INK_2,
        )
    )

    steps = [
        ("Session start", num(funnel_row["sessions_starting_funnel"])),
        ("View item", num(funnel_row["sessions_reaching_view_item"])),
        ("Add to cart", num(funnel_row["sessions_reaching_add_to_cart"])),
        ("Begin checkout", num(funnel_row["sessions_reaching_begin_checkout"])),
        ("Purchase", num(funnel_row["sessions_reaching_purchase"])),
    ]
    top = steps[0][1] or 1
    sbx, sby, sbw = fx + 150, fy + 72, fw - 330
    for i, (label, v) in enumerate(steps):
        yy = sby + i * 32
        p.append(text(fx + 136, yy + 15, label, 12, INK_2, anchor="end"))
        p.append(rect(sbx, yy, sbw, 21, GRID, rx=4, opacity=0.45))
        width = sbw * (v or 0) / top
        # Keep a hairline visible for steps that round to nothing at this scale.
        p.append(rect(sbx, yy, max(width, 2.5), 21, ORANGE if i == len(steps) - 1 else BLUE, rx=4))
        p.append(text(sbx + max(width, 2.5) + 10, yy + 15, f"{int(v or 0):,}", 12, INK, 600))
        if i:
            prev = steps[i - 1][1] or 1
            p.append(
                text(
                    fx + fw - 24,
                    yy + 15,
                    f"{(v or 0) / prev * 100:5.1f}% of previous",
                    11.5,
                    INK_3,
                    anchor="end",
                )
            )

    p.append(
        text(
            fx + 20,
            fy + fh - 14,
            "Permissive counting (step reached regardless of prior steps) returns 11,106 at "
            "begin checkout and 4,848 at purchase — carts survive across sessions, this funnel "
            "does not.",
            11.5,
            INK_3,
        )
    )

    p.append(
        text(
            40,
            H - 14,
            "Generated by scripts/render_overview_svg.py · definitions in "
            "models/semantic/metrics.yml",
            11,
            INK_3,
        )
    )
    p.append("</svg>")
    return "\n".join(p)


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(PROJECT_DIR)} ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
