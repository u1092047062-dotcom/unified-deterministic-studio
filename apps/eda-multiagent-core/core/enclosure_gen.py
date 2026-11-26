"""Parametrische Gehäuse-Generierung via OpenSCAD (Stufe 10).

Erzeugt modularen, kommentierten OpenSCAD-Code (.scad) für ein
zweiteiliges Gehäuse (Bottom Tray + Top Lid mit Nut-/Feder-Falzung):

- Außenmaße = Board-Bounding-Box + Clearance je Seite + 2x Wandstärke;
  das Gehäuse bleibt im Board-Koordinatenraum, d. h. Schraubdome erben
  exakt die Koordinaten der 'MountingHole'-Komponenten.
- Schraubdome: Zylinder mit Kernloch für Heat-Set-Gewindeeinsätze
  (M2.5/M3 als umschaltbare SCAD-Variablen), Höhe = Bodenplatte bis
  über die Board-Oberkante.
- Wanddurchbrüche: Verbinder (Kategorie 'connector' bzw. USB-Footprint),
  deren Körper-AABB die Wandzone einer Board-Kante erreicht, erhalten
  ein Port-Fenster über die volle Wandhöhe (Breite = Körper + Clearance).
- Alle Parameter stehen als deklarierte Variablen am Dateianfang und
  lassen sich in OpenSCAD nachjustieren (Customizer-kompatibel).

Determinismus: sortierte Komponentenreihenfolge, feste Formatierung —
gleiche Eingabe -> byte-identischer SCAD-String.
"""
from typing import Dict, List, Tuple

from core.placer import DEFAULT_BODY
from core.schema import BoardState, Component

BOARD_THICKNESS = 1.6          # wie step_export/viewer
BODY_HEIGHT = 1.6              # Bauteilhöhe auf dem Board
LIP_WIDTH = 0.8                # Nut-/Feder-Breite
LID_FIT = 0.2                  # spielfreie Passung der Feder
POST_DIAMETER = 7.0            # Schraubdome-Durchmesser
POST_CAP = 3.0                 # Boss über Board-Oberkante
MIN_PORT_HEIGHT = 4.0          # Mindesthöhe eines Wanddurchbruchs
INSERT_HOLE_M2_5 = 3.5         # Kernloch Heat-Set-Einsatz M2.5
INSERT_HOLE_M3 = 4.3           # Kernloch Heat-Set-Einsatz M3

def _is_mounting_hole(comp: Component) -> bool:
    haystack = f"{comp.footprint} {comp.name} {comp.category}".lower().replace("_", "").replace("-", "")
    return "mountinghole" in haystack

def _is_connector(comp: Component) -> bool:
    return ("connector" in comp.category.lower()
            or "usb" in comp.footprint.lower())

def _body_size(comp: Component) -> Tuple[float, float]:
    w = comp.body_w if comp.body_w is not None else DEFAULT_BODY[0]
    h = comp.body_h if comp.body_h is not None else DEFAULT_BODY[1]
    return float(w), float(h)

def _edge_ports(board: BoardState, wall: float, clearance: float) -> List[Tuple[str, float, float, float]]:
    """Verbinder an einer Plattenkante -> (Seite, a_min, a_max, comp_id).

    Trigger: der Körper-AABB (inkl. Clearance) erreicht die Wandzone
    (Abstand zur Kante <= wall + clearance), ggf. überstehend.
    """
    trigger = wall + clearance
    ports: List[Tuple[str, float, float, float]] = []
    for comp in sorted(board.components.values(), key=lambda c: c.id):
        if not _is_connector(comp):
            continue
        w, h = _body_size(comp)
        x0, x1 = comp.x - w / 2.0 - clearance, comp.x + w / 2.0 + clearance
        y0, y1 = comp.y - h / 2.0 - clearance, comp.y + h / 2.0 + clearance
        if board.width - x1 <= trigger:      # rechte Kante
            ports.append(("right", y0, y1, comp.id))
        if x0 <= trigger:                    # linke Kante
            ports.append(("left", y0, y1, comp.id))
        if board.height - y1 <= trigger:     # obere Kante
            ports.append(("top", x0, x1, comp.id))
        if y0 <= trigger:                    # untere Kante
            ports.append(("bottom", x0, x1, comp.id))
    return ports

def _scad_number(value: float) -> str:
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text if text else "0"

def generate_openscad_case(board: BoardState, wall_thickness: float = 2.0,
                           clearance: float = 0.4, height_padding: float = 3.0,
                           lip_height: float = 1.5) -> str:
    """Erzeugt parametrischen OpenSCAD-Code für Tray + Deckel."""
    wall = float(wall_thickness)
    clr = float(clearance)
    comp_max_h = BODY_HEIGHT
    posts = [(comp.x, comp.y) for comp in
             sorted((c for c in board.components.values() if _is_mounting_hole(c)),
                    key=lambda c: c.id)]
    ports = _edge_ports(board, wall, clr)

    max_component_height = BODY_HEIGHT
    tray_h = wall + BOARD_THICKNESS + max_component_height + height_padding
    port_h = max(MIN_PORT_HEIGHT, tray_h - wall)

    template_vars: Dict[str, str] = {
        "wall": _scad_number(wall),
        "clearance": _scad_number(clr),
        "height_padding": _scad_number(height_padding),
        "lip_height": _scad_number(lip_height),
        "board_w": _scad_number(board.width),
        "board_h": _scad_number(board.height),
        "board_t": _scad_number(BOARD_THICKNESS),
        "comp_max_h": _scad_number(max_component_height),
        "tray_h": _scad_number(tray_h),
        "port_h": _scad_number(port_h),
        "lip_w": _scad_number(LIP_WIDTH),
        "lid_fit": _scad_number(LID_FIT),
        "post_d": _scad_number(POST_DIAMETER),
        "post_cap": _scad_number(POST_CAP),
        "insert_d_m2_5": _scad_number(INSERT_HOLE_M2_5),
        "insert_d_m3": _scad_number(INSERT_HOLE_M3),
        "origin_x": _scad_number(-(wall + clr)),
        "origin_y": _scad_number(-(wall + clr)),
    }
    for key, value in template_vars.items():
        # abgeleitete Größen als SCAD-Ausdrücke (bleiben nachjustierbar)
        if key == "tray_h":
            template_vars[key] = "wall + board_t + comp_max_h + height_padding"
        elif key == "port_h":
            template_vars[key] = "max(min_port_h, tray_h - wall)"

    template_vars["min_port_h"] = _scad_number(MIN_PORT_HEIGHT)

    post_calls = "\n".join(
        f"mounting_post({_scad_number(x)}, {_scad_number(y)});" for x, y in posts) or "// keine MountingHole-Komponenten im Board"
    port_calls = "\n".join(
        f"    port_{side}({_scad_number(a0)}, {_scad_number(a1)}); // {comp_id}"
        for side, a0, a1, comp_id in ports) or "    // keine kantennahen Verbinder"

    scad = _TEMPLATE
    scad = scad.replace("@TITLE@", _esc(board_nice_name(board)))
    for key, value in template_vars.items():
        scad = scad.replace(f"@{key.upper()}@", value)
    scad = scad.replace("@POST_CALLS@", post_calls)
    scad = scad.replace("@PORT_CALLS@", port_calls)
    return scad

def _esc(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"')

def board_nice_name(board: BoardState) -> str:
    if board.components:
        return "PCB " + ", ".join(sorted(board.components))
    return "leere Platine"

_TEMPLATE = """// Parametrisches Gehaeuse fuer "@TITLE@"
// Generiert von eda-multiagent-core (Stufe 10) - alle Groessen in mm.
// Nachjustieren: Variablen unten anpassen (OpenSCAD-Customizer-kompatibel).

/* ---------- Grundparameter ---------- */
wall           = @WALL@;          // Wandstaerke
clearance      = @CLEARANCE@;     // Spiel rund um die Platine
height_padding = @HEIGHT_PADDING@;// Luftraum ueber den Bauteilen
lip_height     = @LIP_HEIGHT@;    // Hoehe der Nut-/Feder-Falzung
lip_w          = @LIP_W@;         // Federbreite (Umfang)
lid_fit        = @LID_FIT@;       // Passungsspiel der Feder im Deckel

/* ---------- Board (aus der Pipeline) ---------- */
board_w = @BOARD_W@;
board_h = @BOARD_H@;
board_t = @BOARD_T@;
comp_max_h = @COMP_MAX_H@;

/* ---------- Schraubdome (Heat-Set-Einsaetze) ---------- */
post_d = @POST_D@;
post_cap = @POST_CAP@;
insert_d_m2_5 = @INSERT_D_M2_5@;   // Kernloch M2.5
insert_d_m3   = @INSERT_D_M3@;     // Kernloch M3
insert_hole_d = insert_d_m2_5;     // <- M3 nutzen? auf insert_d_m3 setzen

/* ---------- Abgeleitet ---------- */
inner_w  = board_w + 2 * clearance;
inner_h  = board_h + 2 * clearance;
outer_w  = inner_w + 2 * wall;
outer_h  = inner_h + 2 * wall;
floor_t  = wall;
min_port_h = @MIN_PORT_H@;
tray_h   = @TRAY_H@;
port_h   = @PORT_H@;
origin_x = @ORIGIN_X@;             // Gehaeuse bleibt im Board-Koordinatenraum
origin_y = @ORIGIN_Y@;

/* ---------- Module ---------- */

module mounting_post(x, y) {
    // Dome unter der Platinenbohrung, Kernloch fuer Heat-Set-Einsatz
    translate([x, y, floor_t])
        difference() {
            cylinder(d = post_d, h = board_t + post_cap, $fn = 48);
            translate([0, 0, -0.1])
                cylinder(d = insert_hole_d, h = board_t + post_cap + 0.2, $fn = 48);
        }
}

module port_right(y0, y1) {   // Durchbruch rechte Wand (x = board_w)
    translate([board_w + clearance - 0.5, y0, floor_t])
        cube([wall + 1, y1 - y0, port_h]);
}

module port_left(y0, y1) {    // Durchbruch linke Wand (x = 0)
    translate([-clearance - wall - 0.5, y0, floor_t])
        cube([wall + 1, y1 - y0, port_h]);
}

module port_top(x0, x1) {     // Durchbruch obere Wand (y = board_h)
    translate([x0, board_h + clearance - 0.5, floor_t])
        cube([x1 - x0, wall + 1, port_h]);
}

module port_bottom(x0, x1) {  // Durchbruch untere Wand (y = 0)
    translate([x0, -clearance - wall - 0.5, floor_t])
        cube([x1 - x0, wall + 1, port_h]);
}

module tray_walls() {
    difference() {
        translate([origin_x, origin_y, 0])
            cube([outer_w, outer_h, tray_h]);
        // Innenhohlraum: ab Bodenplatte bis Deckelansatz
        translate([origin_x + wall, origin_y + wall, floor_t])
            cube([inner_w, inner_h, tray_h]);
        // Wanddurchbrueche fuer kantennahe Verbinder
@PORT_CALLS@
    }
}

module tray_lip() {
    // Feder: Ring, der am Tray-Innenrand nach innen uebersteht
    translate([origin_x + wall - lip_w, origin_y + wall - lip_w, tray_h - lip_height])
        difference() {
            cube([inner_w + 2 * lip_w, inner_h + 2 * lip_w, lip_height]);
            translate([lip_w, lip_w, -0.1])
                cube([inner_w, inner_h, lip_height + 0.2]);
        }
}

module mounting_posts() {
@POST_CALLS@
}

module bottom_tray() {
    tray_walls();
    tray_lip();
    mounting_posts();
}

module top_lid() {
    // Deckel in Druckorientierung: Platte unten, Falz-Rand nach oben.
    // Passt nach 180-Grad-Drehung ueber die Feder des Trays.
    rim_w = lip_w + lid_fit;
    lid_w = inner_w + 2 * rim_w + 2 * wall;
    lid_h = inner_h + 2 * rim_w + 2 * wall;
    difference() {
        union() {
            cube([lid_w, lid_h, wall]);                       // Deckelplatte
            translate([wall, wall, wall])                      // Falz-Rand
                difference() {
                    cube([lid_w - 2 * wall, lid_h - 2 * wall, lip_height + 0.2]);
                    translate([rim_w, rim_w, -0.1])
                        cube([inner_w + 2 * rim_w, inner_h + 2 * rim_w,
                              lip_height + 0.4]);
                }
        }
    }
}

/* ---------- Bauteile ---------- */
bottom_tray();
translate([outer_w + 10, 0, 0]) top_lid();

// Referenzanzeige der Platine (nur Vorschau, % = transparent):
%translate([0, 0, floor_t]) cube([board_w, board_h, board_t]);
"""
