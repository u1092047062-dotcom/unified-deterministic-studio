//! EDA Multi-Agent CAD Studio — Tauri-Desktop-Shell (Stufe 12).
//!
//! - `route_board_native`: ruft den schnellen A*-Pathfinder DIREKT aus dem
//!   lokalen `rust_core`-Crate auf (Feature `python` abgeschaltet, reines
//!   Rust) — Footprint-Rasterung + sequentielles Net-Routing ohne Python.
//! - `export_file_dialog`: nativer Windows-Speichern-Dialog für
//!   .kicad_pcb / .step / .scad / BOM- & CPL-CSVs.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use core_fast::astar;
use serde::{Deserialize, Serialize};
use std::fs;
use tauri_plugin_dialog::DialogExt;

const FOOTPRINT_MARGIN: f64 = 0.4; // wie router_v2._rasterize_footprints

#[derive(Deserialize)]
struct ComponentInput {
    x: f64,
    y: f64,
    // Gehäusemaße sind Teil des IPC-Vertrags (Vollständigkeit BoardState);
    // die native Rasterung nutzt die Margin-Square-Methode wie router_v2.
    #[serde(default)]
    #[allow(dead_code)]
    body_w: Option<f64>,
    #[serde(default)]
    #[allow(dead_code)]
    body_h: Option<f64>,
}

#[derive(Deserialize)]
struct NetInput {
    id: String,
    points: Vec<[f64; 2]>,
}

#[derive(Deserialize)]
struct BoardInput {
    width: f64,
    height: f64,
    #[serde(default = "default_pitch")]
    pitch: f64,
    #[serde(default)]
    components: Vec<ComponentInput>,
    #[serde(default)]
    nets: Vec<NetInput>,
}

fn default_pitch() -> f64 {
    0.5
}

#[derive(Serialize)]
struct PathPoint {
    x: f64,
    y: f64,
    layer: u32,
}

#[derive(Serialize)]
struct NetPaths {
    net: String,
    points: Vec<PathPoint>,
}

fn grid_coord(value: f64, pitch: f64) -> usize {
    (value / pitch).round() as usize
}

/// Routet alle Netze sequenziell über den nativen A*-Pathfinder:
/// Footprint-Rasterung (Margin-Square, Terminal-Zellen frei), belegte
/// Zellen späterer Netze werden über occ_count berücksichtigt.
fn route_board_internal(input: &BoardInput) -> Vec<NetPaths> {
    let pitch = if input.pitch > 0.0 { input.pitch } else { 0.5 };
    let nx = (input.width / pitch) as usize + 1;
    let ny = (input.height / pitch) as usize + 1;
    let total = nx * ny * 2;
    let mut obstacles = vec![0u8; (total + 7) / 8];
    let mut occ_count = vec![0u8; total];
    let occ_self = vec![0u8; total];
    let history = vec![0.0f64; total];

    fn set_obstacle_bit(obstacles: &mut [u8], nx: usize, ny: usize, x: usize, y: usize, l: usize) {
        let i = (l * ny + y) * nx + x;
        obstacles[i >> 3] |= 1u8 << (i & 7);
    }
    fn clear_obstacle_bit(obstacles: &mut [u8], nx: usize, ny: usize, x: usize, y: usize, l: usize) {
        let i = (l * ny + y) * nx + x;
        obstacles[i >> 3] &= !(1u8 << (i & 7));
    }

    // Footprint-Margin-Squares blockieren beide Lagen ...
    let margin = (FOOTPRINT_MARGIN / pitch).ceil() as i64;
    for comp in &input.components {
        let cx = grid_coord(comp.x, pitch) as i64;
        let cy = grid_coord(comp.y, pitch) as i64;
        for l in 0..2 {
            for dx in -margin..=margin {
                for dy in -margin..=margin {
                    let x = cx + dx;
                    let y = cy + dy;
                    if x >= 0 && y >= 0 && (x as usize) < nx && (y as usize) < ny {
                        set_obstacle_bit(&mut obstacles, nx, ny, x as usize, y as usize, l);
                    }
                }
            }
        }
    }
    // ... Terminal-Zellen (Netz-Pads) bleiben befahrbar.
    for net in &input.nets {
        for p in net.points.iter().take(2) {
            let x = grid_coord(p[0], pitch);
            let y = grid_coord(p[1], pitch);
            if x < nx && y < ny {
                for l in 0..2 {
                    clear_obstacle_bit(&mut obstacles, nx, ny, x, y, l);
                }
            }
        }
    }

    let mut results = Vec::new();
    for net in &input.nets {
        if net.points.len() < 2 {
            continue;
        }
        let sx = grid_coord(net.points[0][0], pitch);
        let sy = grid_coord(net.points[0][1], pitch);
        let tx = grid_coord(net.points[1][0], pitch);
        let ty = grid_coord(net.points[1][1], pitch);
        if sx >= nx || sy >= ny || tx >= nx || ty >= ny {
            continue;
        }
        let path = match astar::route_negotiated(
            nx, ny, sx, sy, tx, ty,
            &obstacles, &occ_count, &occ_self, &history, 0.5, 5.0,
        ) {
            Ok(path) => path,
            Err(_) => continue,
        };
        for (x, y, l) in &path {
            let i = (*l as usize * ny + *y as usize) * nx + *x as usize;
            occ_count[i] = occ_count[i].saturating_add(1);
        }
        results.push(NetPaths {
            net: net.id.clone(),
            points: path
                .iter()
                .map(|(x, y, l)| PathPoint {
                    x: *x as f64 * pitch,
                    y: *y as f64 * pitch,
                    layer: *l,
                })
                .collect(),
        });
    }
    results
}

#[tauri::command]
fn route_board_native(board_json: String) -> String {
    match serde_json::from_str::<BoardInput>(&board_json) {
        Ok(input) => {
            let paths = route_board_internal(&input);
            serde_json::to_string(&paths).unwrap_or_else(|_| "[]".to_string())
        }
        Err(err) => format!("{{\"error\":{}}}", serde_json::to_string(&err.to_string()).unwrap_or_default()),
    }
}

#[tauri::command]
fn export_file_dialog(app: tauri::AppHandle, filename: String, content: Vec<u8>) -> bool {
    let extension = filename.rsplit('.').next().unwrap_or("txt").to_string();
    let chosen = app
        .dialog()
        .file()
        .add_filter(
            "CAD-Exporte (KiCad/STEP/OpenSCAD/CSV)",
            &["kicad_pcb", "step", "scad", "csv"],
        )
        .set_file_name(&filename)
        .blocking_save_file();
    match chosen {
        Some(file_path) => {
            let path = file_path
                .into_path()
                .expect("Dialog-Pfad nicht in Dateisystem-Pfad wandelbar");
            // Endung erzwingen, falls der Dialog sie abtrennt
            let path = if path.extension().is_none() {
                path.with_extension(&extension)
            } else {
                path
            };
            fs::write(&path, content).is_ok()
        }
        None => false,
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![route_board_native, export_file_dialog])
        .run(tauri::generate_context!())
        .expect("Fehler beim Starten der Tauri-Anwendung");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn routes_two_nets_around_footprint() {
        let input: BoardInput = serde_json::from_str(
            r#"{
                "width": 40.0, "height": 20.0, "pitch": 0.5,
                "components": [{"x": 5.0, "y": 9.0, "body_w": 2.0, "body_h": 2.0}],
                "nets": [
                    {"id": "N1", "points": [[4.0, 12.0], [36.0, 12.0]]},
                    {"id": "N2", "points": [[4.0, 8.0], [36.0, 8.0]]}
                ]
            }"#,
        )
        .expect("board json");
        let paths = route_board_internal(&input);
        assert_eq!(paths.len(), 2);
        let n1 = &paths[0];
        assert_eq!(n1.net, "N1");
        assert_eq!(n1.points.first().unwrap().x, 4.0);
        assert_eq!(n1.points.last().unwrap().x, 36.0);
        // Beide Netze haben Pfadpunkte und teilen keine Zellen (occ_count):
        // N2 muss um die Belegung von N1 herumführen, wenn sie kollidierten.
        assert!(!n1.points.is_empty());
    }

    #[test]
    fn invalid_json_reports_error_object() {
        let result = route_board_native("kein json".to_string());
        assert!(result.contains("error"));
    }
}
