# Multi-Agent EDA Core - System Invariants \& Rules for Zcode

## 1\. System Invariants

* Keine LLM-Heuristiken für Geometrie oder Pinouts: Pin-Multiplexing läuft über solver.py (Z3), Routing über router\_v2.py (Pathfinder Negotiated Congestion).
* Geometrische Integrität: Kollisionen und Clearances laufen über spatial\_drc.py (SpatialHashIndex, O(N)).
* Zero-Copy / Low-Draw-Call: Frontend visualisiert Bauteile und Vias strikt per THREE.InstancedMesh.

## 2\. Directory Map

* core/schema.py: Domänenmodelle (BoardState, Net, Trace, Pin).
* core/solver.py: Z3 SMT-Solver für Pinout-Multiplexing und Strapping-Schutz.
* core/stackup\_manager.py: 4-Lagen-Stackup (JLC 7628) + IPC-2141-Impedanzen (SE/Diff, 50/90/100 Ohm).
* core/diff\_router.py: Differential-Pair-Routing (Paar-Erkennung, Miter-Offset, Trombone-Längenabgleich).
* core/part\_library.py: LCSC/JLCPCB-Bauteilkatalog (In-Memory-SQLite) mit Fuzzy-Resolve + Pin-Maßen.
* core/placer.py: Force-Directed Placement (Hooke-Attraktion, paarweise Footprint-Abstoßung).
* core/spatial\_drc.py: O(N) Spatial Hashing DRC \& IPC-2141 Wellenwiderstand.
* core/kicad\_export.py: KiCad-v8-Export (.kicad\_pcb, 4 Kupferlagen, S-Expression).
* core/manufacturing\_export.py: JLCPCB-Fertigungsdaten (BOM \& CPL als CSV).
* core/step\_export.py: 3D-STEP-Export (AP214, Board + Bauteilkörper als B-Reps).
* core/enclosure\_gen.py: Parametrisches OpenSCAD-Gehäuse (Tray/Lid, Mounting-Posts, Port-Cutouts).
* core/copper\_pour.py: Copper Pour mit Thermal-Reliefs (Shapely-Polygon-Booleans).
* core/router\_v2.py: A\* Negotiated Congestion Router mit Rip-Up \& Reroute und Meandern.
* rust_core/: PyO3-Modul core\_fast (A\*-Innerloop in Rust, 1D-Bitset, binärer Min-Heap; Drop-in via route\_negotiated\_fast, Build: maturin develop --release).
* core/pipeline\_v2.py: LangGraph Orchestrator (Feedback Loop).
* server/server.py: FastAPI WebSocket Streamer.
* frontend/cad\_viewer\_v2.html: Three.js Instanced CAD Viewer.



# Projekt-Briefing für Zcode

Du arbeitest als leitender EDA- und Systems-Engineer an der Codebasis 'eda-multiagent-core'.
Das Projekt implementiert eine deterministische Hardware-Design-Pipeline, die generative KI mit mathematischen Solvern und Web-CAD verbindet.

## Deine Aufgaben:

1. Behalte die Typdefinitionen in core/schema.py als Single Source of Truth bei.
2. Optimiere Algorithmen auf minimale Laufzeitkomplexität (z. B. Spatial Hashing, Bitsets).
3. Modifiziere bestehende Dateien modular und erstelle isolierte Unit-Tests in tests/.







