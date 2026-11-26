import heapq
import math
from array import array
from collections import defaultdict
from typing import Dict, Iterator, List, Optional, Set, Tuple

from core.schema import BoardState, Net, Trace, Via

try:  # Optionale Rust-Beschleunigung (Stufe 6); Fallback bleibt rein Python.
    from core_fast import route_negotiated_fast as _rust_route
except ImportError:  # pragma: no cover - gebautes Modul nicht vorhanden
    _rust_route = None

SQRT2 = math.sqrt(2.0)
GridNode = Tuple[int, int, int]

def mst_edges(points: List[Tuple[float, float]]) -> List[Tuple[int, int]]:
    """Prim-MST über die Punkte; gibt Kanten als Indexpaare zurück.

    Gemeinsame Utility von Router (Netz-Aufspannung) und Placer
    (Netz-Attraktion), O(k^2) über k Termini.
    """
    n = len(points)
    if n < 2:
        return []
    in_tree = [False] * n
    best_dist = [math.inf] * n
    best_from = [-1] * n
    best_dist[0] = 0.0
    edges: List[Tuple[int, int]] = []
    for _ in range(n):
        # n Iterationen: jede holt einen Knoten in den Baum,
        # der Startknoten liefert keine Kante.
        u = min((d, i) for i, d in enumerate(best_dist) if not in_tree[i])[1]
        in_tree[u] = True
        if best_from[u] >= 0:
            edges.append((best_from[u], u))
        for v in range(n):
            if not in_tree[v]:
                d = math.hypot(points[u][0] - points[v][0], points[u][1] - points[v][1])
                if d < best_dist[v]:
                    best_dist[v] = d
                    best_from[v] = u
    return edges

class NegotiatedPathfinder:
    """A* Negotiated Congestion Router (Pathfinder) mit Rip-Up & Reroute.

    Termini sind Pins (falls mit Koordinaten versehen), sonst die
    Komponentenmitte. Multi-Pin-Netze werden über einen Prim-MST in
    O(k^2) in Punkt-zu-Punkt-Verbindungen zerlegt. Bewegungen kosten
    orthogonal 1.0 und diagonal sqrt(2); Layerwechsel kosten via_cost
    plus Congestion der Via-Zelle.
    """

    def __init__(self, board: BoardState, pitch: float = 0.5, via_cost: float = 5.0,
                 history_factor: float = 1.5, trace_width: float = 0.25):
        self.board = board
        self.pitch = pitch
        self.via_cost = via_cost
        self.history_factor = history_factor
        self.trace_width = trace_width
        self.nx = int(board.width / pitch) + 1
        self.ny = int(board.height / pitch) + 1
        self.layers = ["TOP", "BOTTOM"]
        self.occupancy: Dict[GridNode, Set[str]] = defaultdict(set)
        self.static_obstacles: Set[GridNode] = set()
        # Flache Fast-Path-Strukturen (Stufe 6): das Rust-Modul core_fast
        # konsumiert Bitset + Counts + History als 1D-Buffer; der Dict-/Set-
        # Zustand bleibt Quelle der Wahrheit für Fallback und Inspektion.
        self.fast = _rust_route is not None
        total = self.nx * self.ny * 2
        self._history = array("d", bytes(8 * total))
        self._occ_count = array("B", bytes(total))       # Anzahl Netze je Zelle
        self._occ_self_zero = bytes(total)
        self._occ_self_buf = bytearray(total)            # eigenes Netz je Zelle
        self._obstacle_bits = bytearray((total + 7) // 8)
        self._cells_by_net: Dict[str, Set[GridNode]] = defaultdict(set)
        # Vorhandene (fixe) Vias bleiben erhalten und belegen ihre Zellen,
        # damit andere Netze im Negotiation-Loop Platz lassen.
        self.fixed_vias: List[Via] = list(board.vias)
        for via in self.fixed_vias:
            gx, gy = self._to_grid(via.x, via.y)
            for l in range(2):
                self._occ_add((gx, gy, l), via.net_id)
        self._rasterize_footprints()
        self._sync_obstacle_bits()

    # ---------- Koordinaten ----------

    def _to_grid(self, x: float, y: float) -> Tuple[int, int]:
        return int(round(x / self.pitch)), int(round(y / self.pitch))

    def _to_world(self, gx: int, gy: int) -> Tuple[float, float]:
        return round(gx * self.pitch, 3), round(gy * self.pitch, 3)

    @staticmethod
    def _pin_pos(comp, pin_ref: str) -> Optional[Tuple[float, float]]:
        pin = comp.pins.get(pin_ref)
        if pin is not None and pin.x is not None and pin.y is not None:
            return pin.x, pin.y
        return None

    def _net_terminals(self, net: Net) -> List[Tuple[float, float]]:
        """Pin-Positionen aller gültigen Nodes; Fallback: Komponentenmitte."""
        positions: List[Tuple[float, float]] = []
        for comp_id, pin_id in net.nodes:
            comp = self.board.components.get(comp_id)
            if comp is None:
                continue
            pos = self._pin_pos(comp, pin_id)
            if pos is None:
                pos = comp.x, comp.y
            positions.append(pos)
        return positions

    def _rasterize_footprints(self):
        margin = int(math.ceil(0.4 / self.pitch))
        for comp in self.board.components.values():
            cgx, cgy = self._to_grid(comp.x, comp.y)
            for l in range(2):
                for x in range(cgx - margin, cgx + margin + 1):
                    for y in range(cgy - margin, cgy + margin + 1):
                        self.static_obstacles.add((x, y, l))
            # Pins und Komponentenmitte bleiben befahrbar (Termini).
            keep = {(cgx, cgy)}
            for pin in comp.pins.values():
                if pin.x is not None and pin.y is not None:
                    keep.add(self._to_grid(pin.x, pin.y))
            for gx, gy in keep:
                for l in range(2):
                    self.static_obstacles.discard((gx, gy, l))

    # ---------- Fast-Path-Pflege (flache Strukturen) ----------

    def _node_idx(self, node: GridNode) -> int:
        x, y, l = node
        return (l * self.ny + y) * self.nx + x

    def _occ_add(self, cell: GridNode, net_id: str):
        """Belegt eine Zelle; hält Dict, Counts und Netz-Index synchron."""
        bucket = self.occupancy[cell]
        if net_id not in bucket:
            bucket.add(net_id)
            self._occ_count[self._node_idx(cell)] += 1
            self._cells_by_net[net_id].add(cell)

    def _occ_remove(self, cell: GridNode, net_id: str):
        """Gibt eine Zelle frei (Rip-Up); synchron über alle Strukturen."""
        bucket = self.occupancy.get(cell)
        if bucket and net_id in bucket:
            bucket.discard(net_id)
            self._occ_count[self._node_idx(cell)] -= 1
            self._cells_by_net[net_id].discard(cell)

    def _sync_obstacle_bits(self):
        """Überträgt das statische Hindernis-Set ins 1D-Bitset."""
        bits = self._obstacle_bits
        bits[:] = bytes(len(bits))
        for x, y, l in self.static_obstacles:
            i = (l * self.ny + y) * self.nx + x
            bits[i >> 3] |= 1 << (i & 7)

    # ---------- MST ----------

    @staticmethod
    def _mst_edges(points: List[Tuple[float, float]]) -> List[Tuple[int, int]]:
        """Alias auf die öffentliche Utility (Abwärtskompatibilität)."""
        return mst_edges(points)

    # ---------- A* ----------

    def route_net_negotiated(self, net_id: str, p1: Tuple[float, float], p2: Tuple[float, float],
                             p_fac: float) -> List[GridNode]:
        """Drop-in: delegiert an das Rust-Modul core_fast (falls gebaut),
        sonst an die Python-Referenz — beide liefern bitidentische Pfade."""
        sx, sy = self._to_grid(*p1)
        tx, ty = self._to_grid(*p2)
        if not (0 <= sx < self.nx and 0 <= sy < self.ny and 0 <= tx < self.nx and 0 <= ty < self.ny):
            return []
        if self.fast and _rust_route is not None:
            self._occ_self_buf[:] = self._occ_self_zero
            for cell in self._cells_by_net.get(net_id, ()):
                self._occ_self_buf[self._node_idx(cell)] = 1
            path = _rust_route(self.nx, self.ny, sx, sy, tx, ty,
                               bytes(self._obstacle_bits),
                               self._occ_count.tobytes(),
                               bytes(self._occ_self_buf),
                               self._history.tobytes(),
                               float(p_fac), float(self.via_cost))
            return [(x, y, layer) for x, y, layer in path]
        return self._route_net_python(net_id, sx, sy, tx, ty, p_fac)

    def _route_net_python(self, net_id: str, sx: int, sy: int, tx: int, ty: int,
                          p_fac: float) -> List[GridNode]:
        open_set: List[Tuple[float, float, int, int, int]] = []
        heapq.heappush(open_set, (0.0, 0.0, sx, sy, 0))
        came_from: Dict[GridNode, GridNode] = {}
        cost_so_far: Dict[GridNode, float] = {(sx, sy, 0): 0.0}

        while open_set:
            _, c_cost, x, y, layer = heapq.heappop(open_set)
            if (x, y) == (tx, ty):
                path = []
                curr: GridNode = (x, y, layer)
                while curr in came_from:
                    path.append(curr)
                    curr = came_from[curr]
                path.append((sx, sy, 0))
                return path[::-1]

            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1)):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < self.nx and 0 <= ny < self.ny):
                    continue
                node = (nx, ny, layer)
                if node in self.static_obstacles:
                    continue
                if dx and dy:
                    # Diagonalzug: beide Seitzen müssen frei sein (kein
                    # Eckenschneiden durch Hindernisse) und werden konservativ
                    # als Konfliktfläche gewertet (kein Eckkreuzen fremder Netze).
                    side_a = (x + dx, y, layer)
                    side_b = (x, y + dy, layer)
                    if side_a in self.static_obstacles or side_b in self.static_obstacles:
                        continue
                    cells = (node, side_a, side_b)
                else:
                    cells = (node,)
                sharing = max(len(self.occupancy[c] - {net_id}) for c in cells)
                step = SQRT2 if (dx and dy) else 1.0
                new_cost = c_cost + step + max(self._history[self._node_idx(c)] for c in cells) + sharing * p_fac
                if new_cost < cost_so_far.get(node, math.inf):
                    cost_so_far[node] = new_cost
                    h = math.hypot(nx - tx, ny - ty)
                    heapq.heappush(open_set, (new_cost + h, new_cost, nx, ny, layer))
                    came_from[node] = (x, y, layer)

            other_layer = 1 - layer
            via_node = (x, y, other_layer)
            if via_node in self.static_obstacles:
                continue
            sharing = len(self.occupancy[via_node] - {net_id})
            via_total = c_cost + self.via_cost + sharing * p_fac
            if via_total < cost_so_far.get(via_node, math.inf):
                cost_so_far[via_node] = via_total
                h = math.hypot(x - tx, y - ty)
                heapq.heappush(open_set, (via_total + h, via_total, x, y, other_layer))
                came_from[via_node] = (x, y, layer)

        return []

    # ---------- Rip-Up & Reroute ----------

    @staticmethod
    def _path_cells(path: List[GridNode]) -> Iterator[GridNode]:
        """Geometrischer Pfad plus konservative Diagonalseitzen:
        Jeder Diagonalzug belegt auch die beiden orthogonalen Seitzen,
        damit Eckkreuzungen fremder Netze als Konflikt erscheinen."""
        if not path:
            return
        yield path[0]
        for prev, curr in zip(path, path[1:]):
            dx, dy = curr[0] - prev[0], curr[1] - prev[1]
            if curr[2] == prev[2] and dx != 0 and dy != 0:
                yield (prev[0], curr[1], curr[2])
                yield (curr[0], prev[1], curr[2])
            yield curr

    def solve_ripup_and_reroute(self, max_iterations: int = 10) -> Tuple[List[Trace], List[Via]]:
        self._sync_obstacle_bits()
        p_fac = 0.5
        net_paths: Dict[str, List[GridNode]] = {}
        net_cells: Dict[str, List[GridNode]] = {}

        for _ in range(max_iterations):
            conflicts = 0
            for net_id, net in self.board.nets.items():
                if net_id in net_cells:
                    for cell in net_cells[net_id]:
                        self._occ_remove(cell, net_id)

                terminals = self._net_terminals(net)
                if len(terminals) < 2:
                    net_paths.pop(net_id, None)
                    net_cells.pop(net_id, None)
                    continue

                merged: List[GridNode] = []
                for i, j in self._mst_edges(terminals):
                    segment = self.route_net_negotiated(net_id, terminals[i], terminals[j], p_fac)
                    if len(segment) < 2:
                        continue
                    if merged and merged[-1] == segment[0]:
                        merged.extend(segment[1:])
                    else:
                        merged.extend(segment)

                if merged:
                    net_paths[net_id] = merged
                    net_cells[net_id] = list(self._path_cells(merged))
                    for cell in net_cells[net_id]:
                        self._occ_add(cell, net_id)
                        if len(self.occupancy[cell]) > 1:
                            conflicts += 1
                else:
                    net_paths.pop(net_id, None)
                    net_cells.pop(net_id, None)

            if conflicts == 0:
                break
            for cell, nets in self.occupancy.items():
                if len(nets) > 1:
                    self._history[self._node_idx(cell)] += self.history_factor * len(nets)
            p_fac *= 1.3

        return self._materialize(net_paths)

    def _materialize(self, net_paths: Dict[str, List[GridNode]]) -> Tuple[List[Trace], List[Via]]:
        traces: List[Trace] = []
        vias: List[Via] = []
        for net_id in self.board.nets:
            path = net_paths.get(net_id)
            if not path:
                continue
            curr_layer = path[0][2]
            segment: List[Tuple[float, float]] = [self._to_world(path[0][0], path[0][1])]
            for node in path[1:]:
                world = self._to_world(node[0], node[1])
                if node[2] == curr_layer:
                    segment.append(world)
                else:
                    vias.append(Via(net_id=net_id, x=world[0], y=world[1],
                                    from_layer=self.layers[curr_layer],
                                    to_layer=self.layers[node[2]]))
                    if len(segment) >= 2:
                        traces.append(Trace(net_id=net_id, layer=self.layers[curr_layer], path=segment))
                    curr_layer = node[2]
                    segment = [world]
            if len(segment) >= 2:
                traces.append(Trace(net_id=net_id, layer=self.layers[curr_layer],
                                    width=self.trace_width, path=segment))
        return traces, list(self.fixed_vias) + vias
