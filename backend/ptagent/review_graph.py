"""Render evidence-bound review graphs without inferring model physics."""
from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path
from typing import Any

COLORS = {
    "source": ("#DFF2E7", "#2F8A61", "#17613E"),
    "derived": ("#DCEBFA", "#337DBD", "#174D78"),
    "user": ("#FCE8D2", "#D17B20", "#7B430A"),
    "proposal": ("#F7DEDE", "#C85A5A", "#7B2828"),
    "blocked": ("#F2F4F6", "#53616D", "#26333D"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(template: Path) -> tuple[str, dict[str, Any]]:
    markdown = template.read_text(encoding="utf-8")
    from .ptagent_3deft.contract import CONTRACT_MARKER

    if CONTRACT_MARKER in markdown:
        from .ptagent_3deft.contract import parse_contract
        return "3deft", parse_contract(markdown)
    from .ptagent_4d.template_contract import is_contract_template, parse_contract
    if not is_contract_template(markdown):
        raise ValueError("Expected a PTagent 4D or 3DEFT Markdown contract")
    return "4d", parse_contract(markdown)


def pointer_value(value: Any, pointer: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError(f"Expected a non-root JSON pointer, got {pointer!r}")
    try:
        for part in pointer[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                if not part.isdigit():
                    raise ValueError("List index must be nonnegative")
                value = value[int(part)]
            else:
                value = value[part]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(f"Contract pointer does not exist: {pointer}") from exc
    return value


def review_sections(engine: str, contract: dict[str, Any]) -> list[str]:
    if engine == "3deft":
        keys = [key for key in contract if key not in {
            "schema", "marker_present", "source_hash", "model_name", "runner",
        }]
        return [f"/{key}" for key in keys]
    sections = ["/fields", "/parameters", "/potential", "/masses", "/loops", "/implementation"]
    for key in ("sarah_import", "thermal_derivation"):
        if key in contract:
            sections.append(f"/{key}")
    return sections


def inspect_template(template: Path) -> dict[str, Any]:
    engine, contract = load_contract(template)
    return {"version": 1, "template_sha256": sha256(template), "engine": engine,
            "review_sections": review_sections(engine, contract), "contract": contract}


def _unresolved(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_unresolved(v) for k, v in value.items()
                   if k not in {"notes", "description", "question", "reviewer_notes"})
    if isinstance(value, list):
        return any(_unresolved(v) for v in value)
    return isinstance(value, str) and value.strip().upper() in {"ASK_USER", "UNKNOWN", "TBD"}


def validate_spec(template: Path, spec: dict[str, Any]) -> dict[str, Any]:
    """Check bindings and audit records, not the scientific truth of annotations."""
    snapshot = inspect_template(template)
    if spec.get("version") != 1 or spec.get("template_sha256") != snapshot["template_sha256"]:
        raise ValueError("Stale or unsupported graph spec. Inspect the current template and refresh its bindings.")
    sources = spec.get("sources", [])
    branches = spec.get("branches", [])
    if not sources or not branches:
        raise ValueError("The graph needs sources and quantity branches.")
    if len(branches) > 30:
        raise ValueError("Too many branches; group related quantities before plotting.")
    if sum(len(b.get("inputs", [])) + len(b.get("outputs", [])) for b in branches) > 100:
        raise ValueError("Too many ports; group related quantities before plotting.")
    ids: set[str] = set()
    evidence_hashes: dict[str, str] = {}
    bindings: dict[str, Any] = {}
    refs: list[str] = []
    parents: dict[str, list[str]] = {}

    def register(item: dict[str, Any]) -> str:
        name = item.get("id")
        if not isinstance(name, str) or not name or name in ids or name == "contract":
            raise ValueError(f"Missing, duplicate or reserved id: {name!r}")
        if not isinstance(item.get("label"), str) or not item["label"].strip():
            raise ValueError(f"Missing label for {name}")
        ids.add(name)
        return name

    def evidence(item: dict[str, Any], required: bool = False) -> list[Path]:
        records = item.get("evidence", [])
        if required and not records:
            raise ValueError(f"{item['id']}: evidence file and location are required")
        paths = []
        for record in records:
            if not record.get("file") or not record.get("location"):
                raise ValueError(f"{item['id']}: evidence requires file and location")
            path = Path(record["file"]).expanduser()
            if not path.is_absolute():
                path = template.parent / path
            path = path.resolve()
            if not path.is_file():
                raise ValueError(f"Missing evidence file: {path}")
            evidence_hashes[str(path)] = sha256(path)
            paths.append(path)
        return paths

    source_by_id = {}
    for source in sources:
        name = register(source)
        if source.get("kind") not in {"source", "user", "proposal"}:
            raise ValueError(f"Invalid source kind for {name}")
        evidence(source, source["kind"] == "source")
        source_by_id[name] = source
        parents[name] = []
    for branch in branches:
        source_id = branch.get("source")
        if source_id not in source_by_id:
            raise ValueError(f"Unknown branch source: {source_id}")
        if not branch.get("inputs"):
            raise ValueError("Every branch requires input quantity ports")
        if branch.get("outputs") and not branch.get("operation"):
            raise ValueError("Derived output ports require a named operation")
        inputs = branch["inputs"]
        for item in inputs + branch.get("outputs", []):
            name = register(item)
            kind = item.get("kind")
            if kind not in COLORS:
                raise ValueError(f"{name}: invalid provenance kind")
            paths = evidence(item, kind in {"source", "user", "derived"})
            if kind == "user":
                quote = item.get("user_quote", "")
                if not quote or not any(quote in p.read_text(encoding="utf-8") for p in paths):
                    raise ValueError(f"{name}: user-confirmed color requires an exact quote in a saved user message")
            if kind == "derived" and not item.get("calculation"):
                raise ValueError(f"{name}: deterministic derivation requires a calculation description and proof")
            if item in inputs:
                if source_by_id[source_id]["kind"] == "proposal" and kind not in {"proposal", "blocked"}:
                    raise ValueError("LLM-only inputs cannot be marked sourced, derived, or user-confirmed")
                parents[name] = [source_id]
            else:
                parents[name] = [entry["id"] for entry in inputs]
            pointers = item.get("refs", [])
            if not pointers and not item.get("context_only"):
                raise ValueError(f"{name}: bind contract refs or explicitly mark context_only")
            values = {p: pointer_value(snapshot["contract"], p) for p in pointers}
            if kind in {"source", "user", "derived"} and any(_unresolved(v) for v in values.values()):
                raise ValueError(f"{name}: unresolved values must be pending or blocked, not {kind}")
            bindings[name] = values
            refs.extend(pointers)
    for start, end in spec.get("links", []):
        if start not in bindings or end not in bindings or start == end:
            raise ValueError(f"Invalid dependency link: {start} -> {end}")
        parents[end].append(start)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ValueError("Cyclic provenance dependencies")
        if name in visited:
            return
        visiting.add(name)
        for parent in parents[name]:
            visit(parent)
        visiting.remove(name)
        visited.add(name)

    for name in parents:
        visit(name)
    # Every major review section must be represented, including policies.
    missing = [section for section in snapshot["review_sections"]
               if not any(p == section or p.startswith(section + "/") for p in refs)]
    if missing:
        raise ValueError("Unrepresented review sections: " + ", ".join(missing))
    return {"template_sha256": snapshot["template_sha256"], "engine": snapshot["engine"],
            "bindings": bindings, "evidence_sha256": evidence_hashes,
            "review_sections": snapshot["review_sections"],
            "note": "Bindings checked; evidence labels and scientific interpretations require agent review."}


def compact_layout(spec: dict[str, Any]) -> dict[str, Any]:
    """Separate the evidence fan from horizontal user/proposal input lanes."""
    sources = {s["id"]: s for s in spec["sources"]}
    branches = spec["branches"]
    upper = [i for i, b in enumerate(branches)
             if b.get("operation") or sources[b["source"]]["kind"] == "source"]
    lower = [i for i in range(len(branches)) if i not in upper]
    proposals = [item for i in upper for item in branches[i].get("outputs", [])
                 if item["kind"] == "proposal"]
    proposal_ids = {p["id"] for p in proposals}
    pitch, gap = .42, .10
    heights = {}
    for i in upper:
        b = branches[i]
        outputs = [p for p in b.get("outputs", []) if p["id"] not in proposal_ids]
        heights[i] = max(len(b["inputs"]), len(outputs), 2 if b.get("operation") else 1)*pitch
    upper_height = sum(heights.values()) + max(len(upper)-1, 0)*gap
    lower_height = sum(len(branches[i]["inputs"])*pitch for i in lower) + max(len(lower)-1, 0)*.32
    roof = len(proposals)*.40
    height = max(4.2, roof+upper_height+lower_height+1.1)
    top = height-.32-roof
    result: dict[str, Any] = {"height": height, "width": 12.0, "upper": upper,
                             "lower": lower, "branches": {}, "ports": {},
                             "sources": {}, "proposals": [p["id"] for p in proposals]}
    for i in upper:
        b = branches[i]
        center = top-heights[i]/2
        result["branches"][i] = center
        outputs = [p for p in b.get("outputs", []) if p["id"] not in proposal_ids]
        for items, x in ((b["inputs"], 4.35), (outputs, 9.6)):
            for n, item in enumerate(items):
                result["ports"][item["id"]] = (x, center+(len(items)-1)*pitch/2-n*pitch)
        top -= heights[i]+gap
    upper_bottom = top+gap if upper else height-.32-roof
    top = upper_bottom-.42
    for i in lower:
        items = branches[i]["inputs"]
        for n, item in enumerate(items):
            result["ports"][item["id"]] = (9.6, top-n*pitch)
        result["branches"][i] = top-(len(items)-1)*pitch/2
        top -= len(items)*pitch+.32
    for n, item in enumerate(proposals):
        result["ports"][item["id"]] = (9.6, height-.33-n*.40)
    occupied = []
    for source in spec["sources"]:
        source_branches = [i for i, b in enumerate(branches) if b["source"] == source["id"]]
        ys = [result["ports"][p["id"]][1] for i in source_branches for p in branches[i]["inputs"]]
        if not ys:
            raise ValueError(f"Unused source: {source['id']}")
        y = (min(ys)+max(ys))/2
        while any(abs(y-other) < .72 for other in occupied):
            y -= .72
        if y < .3:
            raise ValueError("Source boxes collide; group branches by source")
        occupied.append(y)
        result["sources"][source["id"]] = (.95, y)
    center = height-.32-roof-upper_height*.53 if upper else (height+top)/2
    result["contract"] = (11.15, center)
    return result


def render_graph(template: Path, spec: dict[str, Any], output: Path) -> dict[str, str]:
    audit = validate_spec(template, spec)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as path_effects
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
    from matplotlib.path import Path as MplPath

    layout = compact_layout(spec)
    branches = spec["branches"]
    height = layout["height"]
    fig, ax = plt.subplots(figsize=(12, height))
    ax.set(xlim=(0, 12), ylim=(0, height))
    ax.axis("off")
    fig.subplots_adjust(left=.01, right=.99, bottom=.025, top=.975)
    coords = layout["ports"]
    proposal_ids = set(layout["proposals"])
    by_id = {item["id"]: item for b in branches for item in b["inputs"]+b.get("outputs", [])}
    owners = {item["id"]: i for i, b in enumerate(branches) for item in b["inputs"]+b.get("outputs", [])}

    def box(x, y, text, kind, width=1.25):
        face, edge, dark = COLORS[kind]
        wrap_width = 18 if width > 1.5 else 12
        wrapped = "\n".join(textwrap.fill(line, wrap_width) for line in text.replace(" + ", "\n+ ").splitlines())
        h = max(.52, .16 * len(wrapped.splitlines()) + .20)
        ax.add_patch(FancyBboxPatch((x-width/2, y-h/2), width, h,
                                   boxstyle="round,pad=0.035,rounding_size=0.08",
                                   facecolor=face, edgecolor=edge, linewidth=1.4, zorder=5))
        ax.text(x, y, wrapped, ha="center", va="center", fontsize=9.6,
                fontweight="bold", color=dark, zorder=6)

    def path_edge(vertices, codes, kind, *, dashed=False, head=True, halo=False):
        edge = FancyArrowPatch(path=MplPath(vertices, codes), arrowstyle="-|>" if head else "-",
                               mutation_scale=6, linewidth=.9, color=COLORS[kind][1],
                               alpha=.85, linestyle="--" if dashed else "-", zorder=3 if halo else 1)
        if halo:
            edge.set_path_effects([path_effects.Stroke(linewidth=2.8, foreground="white"),
                                  path_effects.Normal()])
        ax.add_patch(edge)

    def wire(start, end, kind, dashed=False, *, head=True, halo=False):
        x0, y0 = start
        x1, y1 = end
        dx = x1-x0
        path_edge([start, (x0+.4*dx, y0), (x1-.4*dx, y1), end],
                  [MplPath.MOVETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4],
                  kind, dashed=dashed, head=head, halo=halo)

    texts = []
    def port(item, *, horizontal=False, rooftop=False):
        x, y = coords[item["id"]]
        kind = item["kind"]
        label = item["label"]
        locations = [e["location"] for e in item.get("evidence", [])]
        if kind == "source" and locations:
            label += "  [" + "; ".join(dict.fromkeys(locations)) + "]"
        if kind == "proposal":
            label += " (pending)"
        width = 36 if x < 5 else 52
        # Explicit newlines are allowed; never split a mathtext expression.
        if "$" not in label:
            label = "\n".join(textwrap.fill(line, width, break_long_words=False)
                              for line in label.splitlines())
        ax.scatter([x], [y], s=13, marker="s" if kind == "source" else "o",
                   color=COLORS[kind][1], edgecolor="white", linewidth=.4, zorder=7)
        tx = 6.55 if rooftop else 6.2 if horizontal else x-.065
        ty = y+.075 if horizontal or rooftop else y
        texts.append(ax.text(tx, ty, label, ha="center" if horizontal or rooftop else "right",
                             va="bottom" if horizontal or rooftop else "center", fontsize=7.8,
                             color=COLORS[kind][2], zorder=8,
                             bbox={"facecolor": "white", "edgecolor": "none", "pad": .7}))

    for source in spec["sources"]:
        x, y = layout["sources"][source["id"]]
        box(x, y, source["label"], source["kind"], 1.7 if source["kind"] == "source" else 1.1)
    terminal_x, terminal_y = layout["contract"]
    box(terminal_x, terminal_y, "Contract\nfor review", "blocked", 1.3)
    upper_sinks, lower_sinks = [], []
    for i, branch in enumerate(branches):
        center = layout["branches"][i]
        sx, sy = layout["sources"][branch["source"]]
        source_kind = next(s["kind"] for s in spec["sources"] if s["id"] == branch["source"])
        source_edge = sx + (.885 if source_kind == "source" else .585)
        operation = branch.get("operation")
        if operation:
            box(5.7, center, operation, "derived")
        for n, item in enumerate(branch["inputs"]):
            x, y = coords[item["id"]]
            is_lower = i in layout["lower"]
            port(item, horizontal=is_lower)
            start = (source_edge, sy+.09-.18*n/max(len(branch["inputs"])-1, 1))
            if is_lower:
                # One continuous fan-to-horizontal path, so there are no
                # detached joints or whitespace halos cutting adjacent pieces.
                path_edge([start, (start[0]+.4, start[1]), (2.35, y), (2.7, y), (x, y)],
                          [MplPath.MOVETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4, MplPath.LINETO],
                          item["kind"], head=False)
                lower_sinks.append(item)
                continue
            wire(start, (x, y), item["kind"], head=False)
            if operation:
                wire((x, y), (5.04, center+.11-.22*n/max(len(branch["inputs"])-1, 1)), item["kind"])
            if not operation or item.get("to_contract"):
                upper_sinks.append(item)
        ordinary = [p for p in branch.get("outputs", []) if p["id"] not in proposal_ids]
        for n, item in enumerate(ordinary):
            port(item)
            wire((6.36, center+.12-.24*n/max(len(ordinary)-1, 1)), coords[item["id"]], item["kind"], head=False)
            upper_sinks.append(item)
    for n, item in enumerate(upper_sinks):
        xy = coords[item["id"]]
        yy = terminal_y + .20 - .40*n/max(len(upper_sinks)-1, 1)
        if xy[0] < 5:
            wire(xy, (9.6, xy[1]), item["kind"], head=False)
            xy = (9.6, xy[1])
        wire(xy, (terminal_x-.685, yy), item["kind"], item["kind"] in {"proposal", "blocked"})
    for n, item in enumerate(lower_sinks):
        x0, y0 = coords[item["id"]]
        x1, y1 = terminal_x-.46+.92*n/max(len(lower_sinks)-1, 1), terminal_y-.295
        path_edge([(x0,y0), (x0+.45,y0), (x1,y1-.45), (x1,y1)],
                  [MplPath.MOVETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4],
                  item["kind"], dashed=item["kind"] in {"proposal", "blocked"})

    links = spec.get("links", [])
    for ident in proposal_ids:
        item = by_id[ident]
        port(item, rooftop=True)
        x, y = coords[ident]
        origins = [start for start, end in links if end == ident]
        if not origins:
            origins = [p["id"] for p in branches[owners[ident]]["inputs"]]
        for origin in origins:
            x0, y0 = coords[origin]
            path_edge([(x0,y0), (x0+.2,y0), (4.6,y), (4.95,y), (x,y)],
                      [MplPath.MOVETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4, MplPath.LINETO],
                      "proposal", head=False)
        wire((x,y), (terminal_x,terminal_y+.295), "proposal", True)

    # Feed cross-branch dependencies into their operation rather than running
    # return wires through the contract's crowded output fan. Group only links
    # already present in the specification; the audit retains their exact ids.
    feeds: dict[tuple[int, str], list[str]] = {}
    for start, end in links:
        if end in proposal_ids:
            continue
        target_branch = owners[end]
        kind = by_id[start]["kind"]
        if branches[target_branch].get("operation") and owners[start] in layout["lower"]:
            feeds.setdefault((target_branch, kind), []).append(start)
        elif branches[owners[start]].get("operation") and branches[target_branch].get("operation"):
            y0, y1 = layout["branches"][owners[start]], layout["branches"][target_branch]
            if owners[start] == target_branch:
                continue
            direction = 1 if y1 > y0 else -1
            path_edge([(6.36,y0+.16*direction), (6.52,y0+.16*direction),
                       (6.60,y0+.16*direction), (6.60,y0+.32*direction),
                       (6.60,y1-.16*direction), (6.60,y1), (6.52,y1), (6.36,y1)],
                      [MplPath.MOVETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
                       MplPath.LINETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4],
                      kind, halo=True)
        else:
            # Short dependency arcs need to leave their column so unrelated
            # ports never look like junctions on one long vertical wire.
            x0,y0 = coords[start]
            x1,y1 = coords[end]
            rail = max(x0,x1)+.25
            path_edge([(x0,y0), (rail,y0), (rail,y1), (x1,y1)],
                      [MplPath.MOVETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4],
                      kind, dashed=by_id[end]["kind"] == "proposal", halo=True)
    for n, ((target, kind), origins) in enumerate(feeds.items()):
        ys = [coords[o][1] for o in set(origins)]
        junction = 3.0 + n*.15
        rail = 4.60 + n*.10
        low, high = min(ys), max(ys)
        ax.plot([junction,junction], [low,high], color=COLORS[kind][1], linewidth=.9, zorder=4)
        ax.scatter([junction]*len(ys), ys, s=14, color=COLORS[kind][1], edgecolor="white", linewidth=.4, zorder=7)
        y1 = layout["branches"][target]-.16
        path_edge([(junction,high), (junction,high+.30), (rail,high+.20), (rail,high+.65),
                   (rail,y1-.22), (rail,y1), (4.85,y1), (5.04,y1)],
                  [MplPath.MOVETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4,
                   MplPath.LINETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4], kind, halo=True)
    output.mkdir(parents=True, exist_ok=True)
    try:
        # Refuse silently overlapping or clipped annotations. The agent can
        # shorten/split labels or separate branches, without changing evidence.
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        bounds = [t.get_window_extent(renderer) for t in texts]
        for i, rect in enumerate(bounds):
            if rect.x0 < fig.bbox.x0 or rect.x1 > fig.bbox.x1 or rect.y0 < fig.bbox.y0 or rect.y1 > fig.bbox.y1:
                raise ValueError(f"Label outside canvas: {texts[i].get_text()}")
            if any(rect.overlaps(other) for other in bounds[:i]):
                raise ValueError(f"Overlapping labels: shorten or split {texts[i].get_text()!r}")
            if any(rect.overlaps(patch.get_window_extent(renderer)) for patch in ax.patches
                   if isinstance(patch, FancyBboxPatch)):
                raise ValueError(f"Label overlaps a box: shorten or split {texts[i].get_text()!r}")
        with plt.rc_context({"pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none"}):
            paths = {}
            for extension in ("png", "pdf", "svg"):
                path = output / f"contract_graph.{extension}"
                fig.savefig(path, dpi=200, facecolor="white")
                paths[extension] = str(path)
        audit["spec_sha256"] = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
        audit["layout"] = layout
        audit["files"] = paths
        audit_path = output / "contract_graph.json"
        audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
        return {**paths, "audit": str(audit_path)}
    finally:
        plt.close(fig)
