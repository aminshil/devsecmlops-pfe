#!/usr/bin/env python3
"""Fleet status snapshot: every machine at ONE moment in time, red if it is anomalous then,
green if it is normal, with its name and an icon for its equipment type. Writes a
self-contained SVG (no JavaScript, no CDN) that opens anywhere and embeds in a report.

    python3 scripts/fleet_status.py                       # auto-picks the worst moment
    python3 scripts/fleet_status.py --at "2026-01-07 16:25:30"
    python3 scripts/fleet_status.py --light               # white background, for printing

Why a single moment: over the whole dataset almost every machine has some anomalies, so
"red if it ever failed" would paint the entire fleet red. A snapshot is the picture that
actually separates healthy from failing - and when a router fails, its dependents light up
around it, which is the cascade this project keeps talking about.

Read-only: reads the CSV and models/dependency_graph.json, writes one SVG.
"""
import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "models" / "dependency_graph.json"

DARK = {"bg": "#0a0e16", "panel": "#121a28", "ink": "#e8eef8", "dim": "#93a4bd",
        "faint": "#5f7189", "line": "#26344a"}
LIGHT = {"bg": "#ffffff", "panel": "#f3f6fa", "ink": "#16202e", "dim": "#4a5a70",
         "faint": "#7b8a9e", "line": "#c7d2e0"}
RED, GREEN = "#ff6b6b", "#36e0ac"


def mtype(name: str) -> str:
    return str(name).split("-")[0]


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Equipment pictograms from Lucide (https://lucide.dev), ISC licence, Copyright (c) Lucide
# Contributors; portions from Feather (MIT), Copyright (c) Cole Bemis. The 24x24 path data is
# inlined rather than loaded from a CDN, so the picture still renders on an offline machine and
# embeds unchanged in a report or a PDF.
ICONS = {
    # globe
    "web": '<circle cx="12" cy="12" r="10" /> <path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" /> <path d="M2 12h20" />',
    # app-window
    "app": '<rect x="2" y="4" width="20" height="16" rx="2" /> <path d="M10 4v4" /> <path d="M2 8h20" /> <path d="M6 4v4" />',
    # database
    "db": '<ellipse cx="12" cy="5" rx="9" ry="3" /> <path d="M3 5V19A9 3 0 0 0 21 19V5" /> <path d="M3 12A9 3 0 0 0 21 12" />',
    # memory-stick
    "cache": '<path d="M6 19v-3" /> <path d="M10 19v-3" /> <path d="M14 19v-3" /> <path d="M18 19v-3" /> <path d="M8 11V9" /> <path d="M16 11V9" /> <path d="M12 11V9" /> <path d="M2 15h20" /> <path d="M2 7a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v1.1a2 2 0 0 0 0 3.837V17a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-5.1a2 2 0 0 0 0-3.837Z" />',
    # layers
    "queue": '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83z" /> <path d="M2 12a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 12" /> <path d="M2 17a1 1 0 0 0 .58.91l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9A1 1 0 0 0 22 17" />',
    # clock
    "batch": '<path d="M12 6v6l4 2" /> <circle cx="12" cy="12" r="10" />',
    # network
    "edge": '<rect x="16" y="16" width="6" height="6" rx="1" /> <rect x="2" y="16" width="6" height="6" rx="1" /> <rect x="9" y="2" width="6" height="6" rx="1" /> <path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3" /> <path d="M12 12V8" />',
    # phone
    "voip": '<path d="M13.832 16.568a1 1 0 0 0 1.213-.303l.355-.465A2 2 0 0 1 17 15h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2A18 18 0 0 1 2 4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-.8 1.6l-.468.351a1 1 0 0 0-.292 1.233 14 14 0 0 0 6.392 6.384" />',
    # router
    "router": '<rect width="20" height="8" x="2" y="14" rx="2" /> <path d="M6.01 18H6" /> <path d="M10.01 18H10" /> <path d="M15 10v4" /> <path d="M17.84 7.17a4 4 0 0 0-5.66 0" /> <path d="M20.66 4.34a8 8 0 0 0-11.31 0" />',
    # brick-wall
    "firewall": '<rect width="18" height="18" x="3" y="3" rx="2" /> <path d="M12 9v6" /> <path d="M16 15v6" /> <path d="M16 3v6" /> <path d="M3 15h18" /> <path d="M3 9h18" /> <path d="M8 15v6" /> <path d="M8 3v6" />',
    # signpost
    "dns": '<path d="M12 13v8" /> <path d="M12 3v3" /> <path d="M18 6a2 2 0 0 1 1.387.56l2.307 2.22a1 1 0 0 1 0 1.44l-2.307 2.22A2 2 0 0 1 18 13H6a2 2 0 0 1-1.387-.56l-2.306-2.22a1 1 0 0 1 0-1.44l2.306-2.22A2 2 0 0 1 6 6z" />',
}


def icon(t: str, x: float, y: float, s: float, color: str, disc: bool = True) -> str:
    """One pictogram for an equipment type, centred on (x, y) and s px across, drawn in `color`."""
    body = ICONS.get(t) or ICONS["app"]
    k = s / 24.0
    halo = (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{s * 0.62:.1f}" fill="{color}" opacity="0.15"/>'
            if disc else "")
    return (f'{halo}<g transform="translate({x - s / 2:.1f},{y - s / 2:.1f}) scale({k:.4f})" fill="none" '
            f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{body}</g>')


def worst_moment(path: Path):
    """The timestamp with the most anomalous machines (the most informative snapshot)."""
    import pandas as pd
    counts = {}
    for chunk in pd.read_csv(path, usecols=["timestamp", "label"], chunksize=1_000_000):
        chunk["label"] = chunk["label"].astype(int)
        for ts, n in chunk.groupby("timestamp")["label"].sum().items():
            counts[ts] = counts.get(ts, 0) + int(n)
    if not counts:
        return None, 0
    ts = max(counts, key=counts.get)
    return ts, counts[ts]


def snapshot(path: Path, at: str) -> dict:
    """machine -> 0/1 at that timestamp."""
    import pandas as pd
    state = {}
    for chunk in pd.read_csv(path, usecols=["timestamp", "machine", "label"], chunksize=1_000_000):
        hit = chunk[chunk["timestamp"] == at]
        for _, r in hit.iterrows():
            state[r["machine"]] = int(r["label"])
    return state


def build_svg(graph, state, theme, at, extra, data_name) -> str:
    c = theme
    routers = list(graph["routers"])
    down = graph["downstream_of"]
    extra = sorted(extra)
    COLS, CW, CH = 6, 58.0, 54.0            # machine cell
    CARD_W, PAD, HEAD = COLS * CW + 28, 24.0, 42.0
    rows_for = lambda n: max(1, (n + COLS - 1) // COLS)
    card_h = HEAD + rows_for(max(len(down.get(r, [])) for r in routers)) * CH + 16
    X0, Y0 = 40.0, 168.0
    W = int(X0 * 2 + 3 * CARD_W + 2 * PAD)
    H = int(Y0 + 3 * card_h + 2 * PAD + 62)

    def is_anom(m):
        return state.get(m, 0) == 1

    def col(m):
        return RED if is_anom(m) else GREEN

    down_total = sum(1 for m in graph["depends_on"] if is_anom(m))
    n_anom = sum(1 for m in state.values() if m == 1)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           f'font-family="ui-monospace,Menlo,Consolas,monospace">',
           f'<rect width="{W}" height="{H}" fill="{c["bg"]}"/>',
           f'<text x="{X0}" y="46" fill="{c["ink"]}" font-size="20" font-weight="700">'
           f'Fleet status — {len(state)} machines at one moment</text>',
           f'<text x="{X0}" y="68" fill="{c["faint"]}" font-size="13">{esc(data_name)} · snapshot at '
           f'{esc(at)} · {n_anom} anomalous, {len(state) - n_anom} normal</text>']

    # legend
    ly = 100
    out.append(f'<circle cx="{X0 + 7}" cy="{ly - 4}" r="7" fill="{RED}" stroke="{c["bg"]}"/>')
    out.append(f'<text x="{X0 + 20}" y="{ly}" fill="{c["dim"]}" font-size="12.5">anomalous now</text>')
    out.append(f'<circle cx="{X0 + 150}" cy="{ly - 4}" r="7" fill="{GREEN}" stroke="{c["bg"]}"/>')
    out.append(f'<text x="{X0 + 163}" y="{ly}" fill="{c["dim"]}" font-size="12.5">normal</text>')
    out.append(f'<text x="{X0 + 250}" y="{ly}" fill="{c["dim"]}" font-size="12.5">icon = equipment type:</text>')
    seen = sorted({mtype(m) for m in state} | {"router"})
    for k, t in enumerate(seen):
        gx = X0 + 462 + (k % 6) * 112
        gy = ly - 4 + (k // 6) * 24
        out.append(icon(t, gx, gy, 16, c["dim"], disc=False))
        out.append(f'<text x="{gx + 12}" y="{gy + 4}" fill="{c["dim"]}" font-size="11.5">{esc(t)}</text>')

    def card(ix, title, members, hub=None, note="", cols=COLS):
        cw = (CARD_W - 28) / cols
        cx = X0 + (ix % 3) * (CARD_W + PAD)
        cy = Y0 + (ix // 3) * (card_h + PAD)
        g = [f'<rect x="{cx}" y="{cy}" width="{CARD_W}" height="{card_h}" rx="12" '
             f'fill="{c["panel"]}" stroke="{c["line"]}" stroke-width="1"/>']
        if hub:
            g.append(icon("router", cx + 25, cy + 22, 22, col(hub)))
            g.append(f'<text x="{cx + 42}" y="{cy + 28}" fill="{c["ink"]}" font-size="14" '
                     f'font-weight="700">{esc(title)}</text>')
        else:
            g.append(f'<text x="{cx + 16}" y="{cy + 28}" fill="{c["ink"]}" font-size="14" '
                     f'font-weight="700">{esc(title)}</text>')
        g.append(f'<text x="{cx + CARD_W - 14}" y="{cy + 28}" fill="{c["faint"]}" font-size="11.5" '
                 f'text-anchor="end">{esc(note)}</text>')
        for j, m in enumerate(members):
            mx = cx + 14 + (j % cols) * cw + cw / 2
            my = cy + HEAD + (j // cols) * CH + 18
            g.append(icon(mtype(m), mx, my, 21, col(m)))
            g.append(f'<text x="{mx}" y="{my + 24}" fill="{c["dim"]}" font-size="9.5" '
                     f'text-anchor="middle">{esc(m)}</text>')
        return "".join(g)

    slot = 0
    for r in routers:
        kids = sorted(down.get(r, []), key=lambda m: (mtype(m), m))
        bad = sum(1 for k in kids if is_anom(k))
        out.append(card(slot, r, kids, hub=r, note=f"{bad}/{len(kids)} anomalous"))
        slot += 1
        if slot == 4:                      # keep the middle slot for the independent card
            bad_e = sum(1 for m in extra if is_anom(m))
            out.append(card(slot, "independent (no router)", extra,
                            note=f"{bad_e}/{len(extra)} anomalous", cols=4))
            slot += 1
    out.append(f'<text x="{X0}" y="{H - 34}" fill="{c["faint"]}" font-size="11.5">'
               f'A router and its dependents turning red together is a cascade: the router fails and the '
               f'machines depending on it are dragged with it.</text>')
    out.append(f'<text x="{X0}" y="{H - 16}" fill="{c["faint"]}" font-size="11.5">'
               f'{down_total} of {len(graph["depends_on"])} dependent machines are anomalous here.</text>')
    out.append("</svg>")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "telecom_fleet_v2_labeled.csv")
    ap.add_argument("--graph", type=Path, default=GRAPH)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "fleet_status.svg")
    ap.add_argument("--at", help='exact timestamp, e.g. "2026-01-07 16:25:30" (default: the worst moment)')
    ap.add_argument("--light", action="store_true", help="white background, for printing")
    args = ap.parse_args()

    if not args.graph.exists():
        print(f"missing {args.graph}", file=sys.stderr)
        return 1
    if not args.data.exists():
        print(f"missing {args.data} — run this on the machine that has the datasets", file=sys.stderr)
        return 1
    graph = json.loads(args.graph.read_text())

    at = args.at
    if not at:
        print("scanning for the moment with the most anomalies ...")
        at, n = worst_moment(args.data)
        if at is None:
            print("no rows found", file=sys.stderr)
            return 1
        print(f"worst moment: {at} ({n} anomalous machines)")
    state = snapshot(args.data, at)
    if not state:
        print(f"no rows at {at!r} — check the timestamp format", file=sys.stderr)
        return 1

    in_graph = set(graph["depends_on"]) | set(graph["routers"])
    extra = sorted(set(state) - in_graph)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_svg(graph, state, LIGHT if args.light else DARK, at, extra, args.data.name))
    print(f"wrote {args.out}")

    n_anom = sum(state.values())
    print(f"machines: {len(state)}   anomalous: {n_anom}   normal: {len(state) - n_anom}")
    print()
    print(f"{'router':>11} | {'router state':>12} | {'dependents anomalous':>20}")
    print("-" * 50)
    for r in graph["routers"]:
        kids = [k for k in graph["downstream_of"].get(r, []) if k in state]
        bad = sum(state[k] for k in kids)
        print(f"{r:>11} | {'ANOMALY' if state.get(r) else 'normal':>12} | {bad:>8} / {len(kids):<9}")
    if extra:
        print(f"\nindependent (no router): {sum(state[m] for m in extra)}/{len(extra)} anomalous")
    return 0


if __name__ == "__main__":
    sys.exit(main())
