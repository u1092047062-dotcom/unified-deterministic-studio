//! core_fast — Rust-Innerloop für den Negotiated-Congestion-Router (Stufe 6)
//! und gemeinsame Basis der Tauri-Desktop-App (Stufe 12).
//!
//! - `astar`: reine A*-Logik OHNE PyO3 — von Python-Binding und Tauri
//!   gleichermaßen genutzt (Feature `python` abschalten -> reines Rust-Crate).
//! - `python_api`: dünner PyO3-Wrapper (`route_negotiated_fast`), Drop-in
//!   für `NegotiatedPathfinder.route_net_negotiated`; bitidentische Pfade
//!   zur Python-Referenz (f64, identische Heap-Ordnung).

pub mod astar;

#[cfg(feature = "python")]
mod python_api;
