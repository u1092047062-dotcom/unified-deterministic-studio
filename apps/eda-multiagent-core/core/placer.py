"""Force-Directed Placement (Stufe 3).

Deterministischer Kraft-Placer für Bauteil-Footprints:

- Anziehung: Hooke-Federn (F = k * (d - L0)) entlang verbundener Netze.
  Mehr-Pin-Netze werden per Prim-MST sparsam verbunden (gemeinsame
  Utility mit dem Router: core.router_v2.mst_edges).
- Abstoßung: paarweise zwischen ALLEN Footprints (O(N^2) je Iteration;
  bei großen Boards ist der Ausbau auf SpatialHashIndex vorgesehen).
  Gap-Verstoß (< min_gap auf beiden Achsen) erzeugt eine harte Trenn-
  kraft entlang der Achse minimaler Durchdringung, im Umfeld wirkt
  zusätzlich eine schwache, gekappte Coulomb-Kraft.
- Integration: temperaturgeführt mit linearer Abkühlung und Klemmung
  ans Board; danach beseitigt ein deterministischer Aufräumlauf
  Restverletzungen, sodass das Ergebnis überlappungsfrei ist, solange
  das Board die Fläche hergibt (andernfalls wird die Restdurchdringung
  in PlacementResult.residual_overlap ehrlich gemeldet).

Nur Translation; Rotation und Layer bleiben unverändert. Pins wandern
mit ihrem Bauteil, sodass die Pin-Offsets gegenüber dem Body erhalten
bleiben und der Router anschließend korrekte Termini vorfindet.
"""
import math
from typing import Dict, List, Tuple

from pydantic import BaseModel

from core.router_v2 import mst_edges
from core.schema import BoardState

Point = Tuple[float, float]

DEFAULT_BODY: Tuple[float, float] = (4.0, 4.0)  # mm, falls body_w/body_h fehlen

# Physik-Konstanten (Einheiten: mm bzw. mm-Skalen)
K_ATTRACT = 0.15       # Hooke-Federkonstante
K_SEPARATE = 2.0       # Trennkraft pro mm Gap-Durchdringung
K_COULOMB = 8.0        # schwache Fernabstoßung
K_COULOMB_CAP = 1.5    # Cap der Fernabstoßung
TEMP_START = 8.0       # maximale Verschiebung je Iteration (mm)
TEMP_MIN = 0.25

class PlacementResult(BaseModel):
    board: BoardState
    iterations: int
    wirelength_before: float
    wirelength_after: float
    residual_overlap: float  # max. verbleibende Gap-Durchdringung in mm; 0.0 = frei

def _body_size(comp) -> Tuple[float, float]:
    w = comp.body_w if comp.body_w is not None else DEFAULT_BODY[0]
    h = comp.body_h if comp.body_h is not None else DEFAULT_BODY[1]
    return float(w), float(h)

def _net_component_ids(board: BoardState) -> Dict[str, List[str]]:
    """Netz -> Liste der distinct vorhandenen Komponenten-IDs (Reihenfolge stabil)."""
    out: Dict[str, List[str]] = {}
    for net_id, net in board.nets.items():
        ids: List[str] = []
        for comp_id, _pin_id in net.nodes:
            if comp_id in board.components and comp_id not in ids:
                ids.append(comp_id)
        out[net_id] = ids
    return out

def net_wirelength(board: BoardState) -> float:
    """Summe der MST-Kantenlängen aller Netze über Komponentenmittelpunkte."""
    total = 0.0
    for comp_ids in _net_component_ids(board).values():
        pts = [(board.components[c].x, board.components[c].y) for c in comp_ids]
        for i, j in mst_edges(pts):
            total += math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
    return total

class ForceDirectedPlacer:
    def __init__(self, min_gap: float = 1.0, rest_length: float = 12.0,
                 max_iterations: int = 250, board_margin: float = 2.0,
                 influence_radius: float = 30.0):
        self.min_gap = min_gap
        self.rest_length = rest_length
        self.max_iterations = max_iterations
        self.board_margin = board_margin
        self.influence_radius = influence_radius

    # ---------- Öffentliche API ----------

    def place(self, board: BoardState) -> PlacementResult:
        work = board.model_copy(deep=True)
        ids = sorted(work.components)  # deterministische Reihenfolge
        wl_before = net_wirelength(work)
        if not ids:
            return PlacementResult(board=work, iterations=0, wirelength_before=0.0,
                                   wirelength_after=0.0, residual_overlap=0.0)

        start = {cid: (work.components[cid].x, work.components[cid].y) for cid in ids}
        sizes = {cid: _body_size(work.components[cid]) for cid in ids}
        positions = {cid: [work.components[cid].x, work.components[cid].y] for cid in ids}

        self._spread_coincident(ids, positions, work)
        net_comp_ids = _net_component_ids(work)

        iterations_used = 0
        for it in range(self.max_iterations):
            iterations_used = it + 1
            temp = TEMP_START + (TEMP_MIN - TEMP_START) * (it / max(1, self.max_iterations - 1))
            forces: Dict[str, List[float]] = {cid: [0.0, 0.0] for cid in ids}
            self._accumulate_attraction(net_comp_ids, positions, forces)
            self._accumulate_repulsion(ids, positions, sizes, forces)

            moved = False
            for cid in ids:
                fx, fy = forces[cid]
                norm = math.hypot(fx, fy)
                if norm > temp:
                    fx *= temp / norm
                    fy *= temp / norm
                if fx == 0.0 and fy == 0.0:
                    continue
                px, py = positions[cid]
                positions[cid] = [self._clamp(px + fx, sizes[cid][0] / 2.0, work.width),
                                  self._clamp(py + fy, sizes[cid][1] / 2.0, work.height)]
                moved = True
            if not moved:
                break

        self._resolve_overlaps(ids, positions, sizes, work)
        residual = self._residual_overlap(ids, positions, sizes)

        for cid in ids:
            comp = work.components[cid]
            dx = positions[cid][0] - start[cid][0]
            dy = positions[cid][1] - start[cid][1]
            comp.x, comp.y = positions[cid]
            for pin in comp.pins.values():
                if pin.x is not None:
                    pin.x += dx
                if pin.y is not None:
                    pin.y += dy

        return PlacementResult(board=work, iterations=iterations_used,
                               wirelength_before=wl_before,
                               wirelength_after=net_wirelength(work),
                               residual_overlap=residual)

    # ---------- Kraftterme ----------

    def _accumulate_attraction(self, net_comp_ids: Dict[str, List[str]],
                               positions: Dict[str, List[float]],
                               forces: Dict[str, List[float]]):
        for comp_ids in net_comp_ids.values():
            pts = [positions[cid] for cid in comp_ids]
            for i, j in mst_edges(pts):
                a, b = comp_ids[i], comp_ids[j]
                dx = positions[b][0] - positions[a][0]
                dy = positions[b][1] - positions[a][1]
                d = math.hypot(dx, dy)
                if d < 1e-9:
                    continue
                f = K_ATTRACT * (d - self.rest_length)  # Hooke
                ux, uy = dx / d, dy / d
                forces[a][0] += ux * f
                forces[a][1] += uy * f
                forces[b][0] -= ux * f
                forces[b][1] -= uy * f

    def _accumulate_repulsion(self, ids: List[str], positions: Dict[str, List[float]],
                              sizes: Dict[str, Tuple[float, float]],
                              forces: Dict[str, List[float]]):
        n = len(ids)
        for ii in range(n):
            for jj in range(ii + 1, n):
                a, b = ids[ii], ids[jj]
                ax, ay = positions[a]
                bx, by = positions[b]
                gap_x = abs(ax - bx) - (sizes[a][0] + sizes[b][0]) / 2.0
                gap_y = abs(ay - by) - (sizes[a][1] + sizes[b][1]) / 2.0
                if gap_x < self.min_gap and gap_y < self.min_gap:
                    # Gap-Verstoß: harte Trennung entlang der Achse mit der
                    # geringeren Durchdringung.
                    pen_x = self.min_gap - gap_x
                    pen_y = self.min_gap - gap_y
                    if pen_x <= pen_y:
                        s = self._sign(ax - bx)
                        f = K_SEPARATE * (pen_x + 0.5)
                        forces[a][0] += s * f
                        forces[b][0] -= s * f
                    else:
                        s = self._sign(ay - by)
                        f = K_SEPARATE * (pen_y + 0.5)
                        forces[a][1] += s * f
                        forces[b][1] -= s * f
                    continue
                d = math.hypot(ax - bx, ay - by)
                if 1e-9 < d < self.influence_radius:
                    f = min(K_COULOMB / (d * d), K_COULOMB_CAP)
                    ux, uy = (ax - bx) / d, (ay - by) / d
                    forces[a][0] += ux * f
                    forces[a][1] += uy * f
                    forces[b][0] -= ux * f
                    forces[b][1] -= uy * f

    # ---------- Aufräumlauf ----------

    def _resolve_overlaps(self, ids: List[str], positions: Dict[str, List[float]],
                          sizes: Dict[str, Tuple[float, float]], board: BoardState):
        """Deterministischer Sweep: schiebt jedes Gap-verletzende Paar entlang
        der Achse minimaler Durchdringung um die halbe Durchdringung auseinander."""
        n = len(ids)
        for _ in range(64):
            violated = False
            for ii in range(n):
                for jj in range(ii + 1, n):
                    a, b = ids[ii], ids[jj]
                    ax, ay = positions[a]
                    bx, by = positions[b]
                    gap_x = abs(ax - bx) - (sizes[a][0] + sizes[b][0]) / 2.0
                    gap_y = abs(ay - by) - (sizes[a][1] + sizes[b][1]) / 2.0
                    if gap_x >= self.min_gap or gap_y >= self.min_gap:
                        continue
                    violated = True
                    pen_x = self.min_gap - gap_x
                    pen_y = self.min_gap - gap_y
                    if pen_x <= pen_y:
                        s = self._sign(ax - bx)
                        push = pen_x / 2.0 + 0.01
                        positions[a][0] = self._clamp(ax + s * push, sizes[a][0] / 2.0, board.width)
                        positions[b][0] = self._clamp(bx - s * push, sizes[b][0] / 2.0, board.width)
                    else:
                        s = self._sign(ay - by)
                        push = pen_y / 2.0 + 0.01
                        positions[a][1] = self._clamp(ay + s * push, sizes[a][1] / 2.0, board.height)
                        positions[b][1] = self._clamp(by - s * push, sizes[b][1] / 2.0, board.height)
            if not violated:
                return

    def _residual_overlap(self, ids: List[str], positions: Dict[str, List[float]],
                          sizes: Dict[str, Tuple[float, float]]) -> float:
        worst = 0.0
        n = len(ids)
        for ii in range(n):
            for jj in range(ii + 1, n):
                ax, ay = positions[ids[ii]]
                bx, by = positions[ids[jj]]
                gap_x = abs(ax - bx) - (sizes[ids[ii]][0] + sizes[ids[jj]][0]) / 2.0
                gap_y = abs(ay - by) - (sizes[ids[ii]][1] + sizes[ids[jj]][1]) / 2.0
                if gap_x < self.min_gap and gap_y < self.min_gap:
                    worst = max(worst, min(self.min_gap - gap_x, self.min_gap - gap_y))
        return worst

    # ---------- Helfer ----------

    def _spread_coincident(self, ids: List[str], positions: Dict[str, List[float]],
                           board: BoardState):
        """Deterministische Initialisierung: exakt zusammenliegende Komponenten
        werden auf einem Raster verteilt; eindeutig platzierte bleiben erhalten."""
        clusters: Dict[Point, List[str]] = {}
        for cid in ids:
            clusters.setdefault((positions[cid][0], positions[cid][1]), []).append(cid)
        coincident = [cid for group in clusters.values() if len(group) > 1 for cid in group]
        if not coincident:
            return
        cols = int(math.ceil(math.sqrt(len(coincident))))
        rows = int(math.ceil(len(coincident) / cols))
        usable_w = max(1e-6, board.width - 2 * self.board_margin)
        usable_h = max(1e-6, board.height - 2 * self.board_margin)
        cell_w, cell_h = usable_w / cols, usable_h / rows
        for k, cid in enumerate(coincident):
            positions[cid] = [self.board_margin + (k % cols + 0.5) * cell_w,
                              self.board_margin + (k // cols + 0.5) * cell_h]

    def _clamp(self, value: float, half: float, board_dim: float) -> float:
        lo = self.board_margin + half
        hi = board_dim - self.board_margin - half
        if lo > hi:
            return board_dim / 2.0
        return min(max(value, lo), hi)

    @staticmethod
    def _sign(delta: float) -> float:
        # Bei exakt gleicher Position deterministic: a (niedrigere ID) nach -1.
        return 1.0 if delta > 0.0 else -1.0
