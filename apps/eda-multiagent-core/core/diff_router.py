"""Differential-Pair-Routing (Stufe 11).

Paar-Erkennung, gekoppelte Parallelführung und Phasenabgleich:

- Paar-Erkennung über Netznamen: Suffix '_P'/'_N' oder '+'/'-' mit
  gemeinsamem Basisnamen (z. B. USB_DP/USB_DN, ETH+/-).
- Parallelführung: Die Plus-Leitung wird normal (Negotiated Pathfinder)
  geroutet; die Minus-Leitung entsteht als Miter-Offset der Plus-Polylines
  mit festem Mittellinien-Abstand auf eigenem feinen Raster
  (pitch = spacing/2, damit der Offset rasterscharf snappt). Seitenwahl
  deterministisch (Offset-Anfang/-Ende nahe der Minus-Pads); Züge und
  Endanbindungen werden gegen statische Hindernisse und Fremdbelegung
  validiert, sonst Fallback auf unabhängiges Routing.
- Phasen-/Längenabgleich: Ist die Längendifferenz > length_tolerance,
  erhält die kürzere Leitung eine Trombone-Mäander (extra = 4*amp*periode,
  exakt gerechnet — kein Quantisierungsrest),Inserted in den längsten
  geraden Lauf derselben Lage, auf der vom Partner wegzeigenden Seite.

Alle Schritte deterministisch; gleiche Eingabe -> gleiche Geometrie.
"""
import math
from typing import Dict, Generator, List, Optional, Tuple

from core.router_v2 import NegotiatedPathfinder
from core.schema import BoardState, Net, Trace, Via

Point = Tuple[float, float]
WorldNode = Tuple[float, float, int]   # (x, y, layer_index)

LAYER_INDEX = {"TOP": 0, "BOTTOM": 1}

def _split_polarity(name: str) -> Optional[Tuple[str, int]]:
    """Netznamen-Suffixe: '_P'/'DP'/'+' -> (+1), '_N'/'DN'/'-' -> (-1).

    'USB_DP'/'USB_DN' teilen sich die Basis 'USB' (haeufige Schreibweise
    von D+/D- als DP/DN), 'DIFF_P'/'DIFF_N' die Basis 'DIFF'.
    """
    n = name.strip()
    if len(n) >= 3 and n.endswith(("_P", "_N")):
        return n[:-2].rstrip("_"), (1 if n.endswith("_P") else -1)
    if len(n) >= 3 and n.endswith(("DP", "DN")):
        return n[:-2].rstrip("_"), (1 if n.endswith("DP") else -1)
    if len(n) >= 2 and n.endswith("+"):
        return n[:-1], 1
    if len(n) >= 2 and n.endswith("-"):
        return n[:-1], -1
    return None

def find_differential_pairs(board: BoardState) -> List[Tuple[Net, Net]]:
    """Erkennt Paare (Plus, Minus) über die Netznamen, sortiert nach Basis."""
    plus: Dict[str, Net] = {}
    minus: Dict[str, Net] = {}
    for net in board.nets.values():
        split = _split_polarity(net.name)
        if split is None:
            continue
        base, polarity = split
        (plus if polarity > 0 else minus)[base] = net
    return [(plus[base], minus[base]) for base in sorted(set(plus) & set(minus))]

# ---------- Geometrie-Helfer ----------

def _path_length(path: List[WorldNode]) -> float:
    total = 0.0
    for i in range(len(path) - 1):
        total += math.hypot(path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1])
    return total

def _dedupe(points: List[Point]) -> List[Point]:
    out: List[Point] = []
    for p in points:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > 1e-9:
            out.append(p)
    return out

def _offset_polyline(points: List[Point], spacing: float, side: int,
                     pitch: float) -> List[Point]:
    """Miter-Offset der Polyline um `spacing` (Mittellinien-Abstand) und
    Rastersnap auf `pitch`. Seiten: +1/-1 = links/rechts der Laufrichtung."""
    pts = _dedupe(points)
    n = len(pts)
    if n < 2:
        return list(pts)
    dirs: List[Optional[Point]] = []
    for i in range(n - 1):
        dx, dy = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
        length = math.hypot(dx, dy)
        dirs.append((dx / length, dy / length) if length > 1e-12 else None)
    normals = [None if d is None else (-d[1] * side, d[0] * side) for d in dirs]

    result: List[Point] = []
    for i in range(n):
        prev_n = normals[i - 1] if i > 0 else None
        next_n = normals[i] if i < n - 1 else None
        off = next_n if prev_n is None else prev_n
        if prev_n is not None and next_n is not None:
            mx, my = prev_n[0] + next_n[0], prev_n[1] + next_n[1]
            ml = math.hypot(mx, my)
            if ml < 1e-9:
                off = next_n
            else:
                mx, my = mx / ml, my / ml
                cos_half = max(0.3, mx * prev_n[0] + my * prev_n[1])
                off = (mx * spacing / cos_half, my * spacing / cos_half)
        elif off is not None:
            off = (off[0] * spacing, off[1] * spacing)
        if off is None:
            continue
        result.append((round((pts[i][0] + off[0]) / pitch) * pitch,
                       round((pts[i][1] + off[1]) / pitch) * pitch))
    return _dedupe(result)

def _world_to_grid(p: Point, pitch: float) -> Tuple[int, int]:
    return int(round(p[0] / pitch)), int(round(p[1] / pitch))

def _walk_cells(a: Point, b: Point, pitch: float) -> Generator[Tuple[int, int], None, None]:
    """Alle Zellen entlang der Strecke a->b (Sampling pitch/2, Endzellen inkl.)."""
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    steps = max(1, int(length / (pitch / 2.0)) + 1)
    seen = set()
    for i in range(steps + 1):
        t = i / steps
        cell = _world_to_grid((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])), pitch)
        if cell not in seen:
            seen.add(cell)
            yield cell

def tune_lengths(path_a: List[WorldNode], path_b: List[WorldNode],
                 tolerance: float = 0.1, seg: float = 1.0,
                 min_amp: float = 0.02, max_amp: float = 2.0
                 ) -> Tuple[List[WorldNode], List[WorldNode], float]:
    """Phasenabgleich: kürzere Leitung erhält Trombone-Mäander mit exakt
    fehlender Länge. Rückgabe: (path_a, path_b, eingefügte_Länge)."""
    len_a, len_b = _path_length(path_a), _path_length(path_b)
    diff = len_a - len_b
    if abs(diff) <= tolerance:
        return path_a, path_b, 0.0
    shorter, longer = (path_b, path_a) if diff > 0 else (path_a, path_b)
    hint = _away_hint(shorter, longer)
    tuned, ok = _insert_trombone(shorter, abs(diff), hint, seg, min_amp, max_amp)
    if not ok:
        return path_a, path_b, 0.0
    if diff > 0:
        return path_a, tuned, abs(diff)
    return tuned, path_b, abs(diff)

def _away_hint(shorter: List[WorldNode], longer: List[WorldNode]) -> Point:
    """Richtung vom Mittelpunkt der kürzeren Leitung WEG vom Partner."""
    mid = shorter[len(shorter) // 2]
    best: Optional[Point] = None
    best_d = math.inf
    for i in range(len(longer) - 1):
        ax, ay, _ = longer[i]
        bx, by, _ = longer[i + 1]
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
            px, py = ax + t * (bx - ax), ay + t * (by - ay)
            d = math.hypot(mid[0] - px, mid[1] - py)
            if d < best_d:
                best_d = d
                best = (px, py)
    if best is None or best_d < 1e-9:
        return (0.0, 1.0)
    dx, dy = mid[0] - best[0], mid[1] - best[1]
    length = math.hypot(dx, dy)
    return (dx / length, dy / length)

def _insert_trombone(path: List[WorldNode], extra: float, hint: Point,
                     seg: float, min_amp: float, max_amp: float
                     ) -> Tuple[List[WorldNode], bool]:
    """Fügt Trombone-Bumps (extra = 4*amp je Bump) in den längsten geraden
    Lauf ein. Läufe sind abschnittsweise lagenrein (Via bricht den Lauf)."""
    k = max(1, math.ceil(extra / (4 * max_amp)))
    amp = extra / (4 * k)
    if amp < min_amp:
        k = max(1, int(extra / (4 * min_amp)))
        amp = extra / (4 * k)
        if amp < min_amp:
            return path, False

    best_run: Optional[Tuple[int, int, Point, float]] = None
    i = 0
    n = len(path)
    while i < n - 1:
        ux, uy = path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1]
        ul = math.hypot(ux, uy)
        if ul <= 1e-9 or path[i + 1][2] != path[i][2]:
            i += 1
            continue
        ux, uy = ux / ul, uy / ul
        j = i
        run_len = ul
        while j + 1 < n:
            dx, dy = path[j + 1][0] - path[j][0], path[j + 1][1] - path[j][1]
            dl = math.hypot(dx, dy)
            if (dl <= 1e-9 or path[j + 1][2] != path[i][2]
                    or abs(dx / dl - ux) > 1e-9 or abs(dy / dl - uy) > 1e-9):
                break
            run_len += dl
            j += 1
        if run_len > (best_run[3] if best_run else 0.0):
            best_run = (i, j, (ux, uy), run_len)
        i = j if j > i else i + 1

    if best_run is None:
        return path, False
    i, j, u, run_len = best_run
    total_bump = k * 2 * seg
    if run_len < total_bump + 2 * seg:
        return path, False
    p = (-u[1], u[0])
    if p[0] * hint[0] + p[1] * hint[1] < 0:
        p = (-p[0], -p[1])
    layer = path[i][2]
    start = (path[i][0], path[i][1])
    base0_shift = (run_len - total_bump) / 2.0

    new_path: List[WorldNode] = list(path[:i]) + [(start[0], start[1], layer)]
    for b in range(k):
        bx = start[0] + u[0] * (base0_shift + b * 2 * seg)
        by = start[1] + u[1] * (base0_shift + b * 2 * seg)
        new_path.extend([
            (bx + p[0] * amp, by + p[1] * amp, layer),
            (bx + p[0] * amp + u[0] * seg, by + p[1] * amp + u[1] * seg, layer),
            (bx - p[0] * amp + u[0] * seg, by - p[1] * amp + u[1] * seg, layer),
            (bx - p[0] * amp + u[0] * 2 * seg, by - p[1] * amp + u[1] * 2 * seg, layer),
            (bx + u[0] * 2 * seg, by + u[1] * 2 * seg, layer),
        ])
    new_path.extend(path[j + 1:])
    return new_path, True

def _materialize_world(path: List[WorldNode], net_id: str, width: float
                       ) -> Tuple[List[Trace], List[Via]]:
    layer_names = ("TOP", "BOTTOM")
    traces: List[Trace] = []
    vias: List[Via] = []
    cur_layer = path[0][2]
    segment: List[Point] = [(path[0][0], path[0][1])]
    for x, y, layer in path[1:]:
        if layer == cur_layer:
            segment.append((x, y))
        else:
            vias.append(Via(net_id=net_id, x=x, y=y))
            if len(segment) >= 2:
                traces.append(Trace(net_id=net_id, layer=layer_names[cur_layer],
                                    width=width, path=segment))
            cur_layer = layer
            segment = [(x, y)]
    if len(segment) >= 2:
        traces.append(Trace(net_id=net_id, layer=layer_names[cur_layer],
                            width=width, path=segment))
    return traces, vias

class DiffPairRouter:
    """Routet erkannte Differentialpaare parallel und gleichen die Längen ab."""

    def __init__(self, spacing: float = 0.5, trace_width: float = 0.2,
                 length_tolerance: float = 0.1, pitch: Optional[float] = None):
        self.spacing = spacing            # Mittellinien-Abstand des Paares
        self.trace_width = trace_width
        self.length_tolerance = length_tolerance
        self.pitch = pitch if pitch is not None else spacing / 2.0

    # ---------- Öffentliche API ----------

    def route_pairs(self, board: BoardState) -> List[str]:
        """Ersetzt die Traces aller erkannten Paare; gibt Report-Zeilen zurück."""
        report: List[str] = []
        for p_net, n_net in find_differential_pairs(board):
            report.extend(self._route_single_pair(board, p_net, n_net))
        return report

    # ---------- Einzelnes Paar ----------

    def _route_single_pair(self, board: BoardState, p_net: Net, n_net: Net) -> List[str]:
        label = f"{p_net.name}/{n_net.name}"
        if len(p_net.nodes) != 2 or len(n_net.nodes) != 2:
            return [f"Diff-Paar {label}: übersprungen (nur 2-Pin-Netze)"]

        work = board.model_copy(deep=True)
        pair_ids = {p_net.id, n_net.id}
        work.traces = [t for t in work.traces if t.net_id not in pair_ids]
        work.vias = [v for v in work.vias if v.net_id not in pair_ids]
        pf = NegotiatedPathfinder(work, pitch=self.pitch, trace_width=self.trace_width)
        for trace in work.traces:
            self._mark_world_trace(pf, trace)

        p_terminals = pf._net_terminals(p_net)
        n_terminals = pf._net_terminals(n_net)
        if len(p_terminals) < 2 or len(n_terminals) < 2:
            return [f"Diff-Paar {label}: übersprungen (Terminals unvollständig)"]

        p_nodes = pf.route_net_negotiated(p_net.id, p_terminals[0], p_terminals[1], 0.5)
        if len(p_nodes) < 2:
            return [f"Diff-Paar {label}: Plus-Leitung nicht routbar - übersprungen"]
        self._mark_grid_path(pf, p_nodes, p_net.id)
        p_world: List[WorldNode] = [(pf._to_world(x, y)[0], pf._to_world(x, y)[1], layer)
                                    for x, y, layer in p_nodes]

        n_nodes = self._build_parallel_path(pf, p_world, n_terminals, n_net.id, p_net.id)
        fallback = n_nodes is None
        if fallback:
            n_nodes = pf.route_net_negotiated(n_net.id, n_terminals[0], n_terminals[1], 0.5)
            if len(n_nodes) < 2:
                return [f"Diff-Paar {label}: nicht routbar"]
            # Fallback liefert Grid-Knoten -> in Weltkoordinaten heben
            n_world: List[WorldNode] = [(pf._to_world(x, y)[0], pf._to_world(x, y)[1], layer)
                                        for x, y, layer in n_nodes]
        else:
            # Parallelpfad ist bereits in Weltkoordinaten
            n_world: List[WorldNode] = [(x, y, layer) for x, y, layer in n_nodes]

        p_world, n_world, added = tune_lengths(p_world, n_world, self.length_tolerance)

        p_traces, p_vias = _materialize_world(p_world, p_net.id, self.trace_width)
        n_traces, n_vias = _materialize_world(n_world, n_net.id, self.trace_width)
        board.traces = [t for t in board.traces if t.net_id not in pair_ids] + p_traces + n_traces
        board.vias = [v for v in board.vias if v.net_id not in pair_ids] + p_vias + n_vias

        mode = "Minus unabhängig geroutet (Fallback)" if fallback else "parallel geführt"
        line = (f"Diff-Paar {label}: {mode}, Abstand {self.spacing:.2f} mm, "
                f"Länge {max(_path_length(p_world), _path_length(n_world)):.2f} mm")
        if added > 0:
            line += f", Trombone +{added:.2f} mm"
        return [line]

    # ---------- Intern ----------

    def _build_parallel_path(self, pf: NegotiatedPathfinder, p_world: List[WorldNode],
                             n_terminals: List[Point], n_net_id: str,
                             p_net_id: str) -> Optional[List[WorldNode]]:
        pitch = self.pitch
        p_pts = [(x, y) for x, y, _ in p_world]
        layers = [layer for _, _, layer in p_world]
        # Via-Zellen der Plus-Leitung (beide Lagen): dort darf die Minus-Leitung
        # ihr eigenes Via an gleicher Stelle setzen.
        p_via_cells = set()
        for i in range(len(p_world) - 1):
            if (math.hypot(p_world[i + 1][0] - p_world[i][0],
                           p_world[i + 1][1] - p_world[i][1]) <= 1e-9):
                for l in (p_world[i][2], p_world[i + 1][2]):
                    for cell in _walk_cells((p_world[i][0], p_world[i][1]),
                                            (p_world[i + 1][0], p_world[i + 1][1]), pitch):
                        p_via_cells.add((cell[0], cell[1], l))

        candidates: List[Tuple[float, List[Point]]] = []
        for side in (1, -1):
            off = _offset_polyline(p_pts, self.spacing, side, pitch)
            score = (math.hypot(off[0][0] - n_terminals[0][0], off[0][1] - n_terminals[0][1])
                     + math.hypot(off[-1][0] - n_terminals[1][0], off[-1][1] - n_terminals[1][1]))
            candidates.append((score, off))
        candidates.sort(key=lambda item: item[0])

        t0, t1 = n_terminals[0], n_terminals[1]
        for _score, offset in candidates:
            o0, o1 = offset[0], offset[-1]
            for mode in (0, 1):
                if mode == 0:   # X zuerst
                    esc_start = [(t0[0], t0[1]), (o0[0], t0[1]), o0]
                    esc_end = [(t1[0], t1[1]), (o1[0], t1[1]), o1]
                else:           # Y zuerst
                    esc_start = [(t0[0], t0[1]), (t0[0], o0[1]), o0]
                    esc_end = [(t1[0], t1[1]), (t1[0], o1[1]), o1]
                nodes: List[WorldNode] = [(x, y, layers[0]) for x, y in esc_start[:-1]]
                nodes += [(x, y, layers[i]) for i, (x, y) in enumerate(offset)]
                nodes += [(x, y, layers[-1]) for x, y in esc_end[1:]]
                if self._validate_nodes(pf, nodes, n_net_id, p_net_id, p_via_cells):
                    return nodes
        return None

    def _validate_nodes(self, pf: NegotiatedPathfinder, nodes: List[WorldNode],
                        net_id: str, p_net_id: str, p_via_cells: set) -> bool:
        for i in range(len(nodes) - 1):
            a = (nodes[i][0], nodes[i][1])
            b = (nodes[i + 1][0], nodes[i + 1][1])
            layers = {nodes[i][2], nodes[i + 1][2]}
            for gx, gy in _walk_cells(a, b, pf.pitch):
                for layer in layers:
                    if (gx, gy, layer) in pf.static_obstacles:
                        return False
                    cell_nets = pf.occupancy[(gx, gy, layer)] - {net_id, p_net_id}
                    if cell_nets:
                        return False
                    if p_net_id in pf.occupancy[(gx, gy, layer)] \
                            and (gx, gy, layer) not in p_via_cells:
                        return False
        return True

    def _mark_grid_path(self, pf: NegotiatedPathfinder, nodes: List[WorldNode], net_id: str):
        """Markiert Grid-Knoten (Pfad des Routers) inkl. Via-Zellen (beide
        Lagen) und konservativer Diagonal-Seitenzellen."""
        for i in range(len(nodes) - 1):
            x0, y0, l0 = nodes[i]
            x1, y1, l1 = nodes[i + 1]
            if (x0, y0) == (x1, y1):
                for layer in (l0, l1):
                    pf._occ_add((x0, y0, layer), net_id)
            else:
                pf._occ_add((x0, y0, l0), net_id)
                if l0 == l1 and (x1 - x0) and (y1 - y0):
                    pf._occ_add((x0, y1, l0), net_id)
                    pf._occ_add((x1, y0, l0), net_id)
        x, y, layer = nodes[-1]
        pf._occ_add((x, y, layer), net_id)

    def _mark_world_trace(self, pf: NegotiatedPathfinder, trace: Trace):
        layer = LAYER_INDEX.get(trace.layer, 0)
        for p1, p2 in zip(trace.path, trace.path[1:]):
            for gx, gy in _walk_cells(p1, p2, pf.pitch):
                pf._occ_add((gx, gy, layer), trace.net_id)
