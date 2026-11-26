// Parametrisches Gehaeuse fuer "PCB R1, U1"
// Generiert von eda-multiagent-core (Stufe 10) - alle Groessen in mm.
// Nachjustieren: Variablen unten anpassen (OpenSCAD-Customizer-kompatibel).

/* ---------- Grundparameter ---------- */
wall           = 2;          // Wandstaerke
clearance      = 0.4;     // Spiel rund um die Platine
height_padding = 3;// Luftraum ueber den Bauteilen
lip_height     = 1.5;    // Hoehe der Nut-/Feder-Falzung
lip_w          = 0.8;         // Federbreite (Umfang)
lid_fit        = 0.2;       // Passungsspiel der Feder im Deckel

/* ---------- Board (aus der Pipeline) ---------- */
board_w = 100;
board_h = 80;
board_t = 1.6;
comp_max_h = 1.6;

/* ---------- Schraubdome (Heat-Set-Einsaetze) ---------- */
post_d = 7;
post_cap = 3;
insert_d_m2_5 = 3.5;   // Kernloch M2.5
insert_d_m3   = 4.3;     // Kernloch M3
insert_hole_d = insert_d_m2_5;     // <- M3 nutzen? auf insert_d_m3 setzen

/* ---------- Abgeleitet ---------- */
inner_w  = board_w + 2 * clearance;
inner_h  = board_h + 2 * clearance;
outer_w  = inner_w + 2 * wall;
outer_h  = inner_h + 2 * wall;
floor_t  = wall;
min_port_h = 4;
tray_h   = wall + board_t + comp_max_h + height_padding;
port_h   = max(min_port_h, tray_h - wall);
origin_x = -2.4;             // Gehaeuse bleibt im Board-Koordinatenraum
origin_y = -2.4;

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
    // keine kantennahen Verbinder
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
// keine MountingHole-Komponenten im Board
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
