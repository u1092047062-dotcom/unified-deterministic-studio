# Unified Deterministic Studio

Konvergierte Engineering-Plattform, die die Architekturen, Qualitaets-Invarianten und Werkzeuge aus zwei spezialisierten Entwicklungsstaenden auf einem gemeinsamen Fundament vereint:

1. **Hardware/EDA-Domäne** (aus `05-platinen-elektronik`): Deterministische PCB-Design-Pipeline (`eda-multiagent-core`), Rust-A*-Router (`core_fast`), Tauri-v2-Desktop-App (`eda-cad-studio`), Three.js Instanced Viewer, KiCad v8 / 3D-STEP / OpenSCAD / JLCPCB BOM & CPL Exports.
2. **Quality-First Media-Restaurierung** (aus `Topaz` / `ART-Restoration-AI`): Unveraenderliche Original-Uploads mit SHA-256-Pruefung, Kachelplanung & Feather-Stitching, atomar fortsetzbare Jobs mit Konfigurations-Hash-Sperre, Quality-First-Ressourcen-/Budget-Policy und sicherer Topaz-Desktop-Handoff.
3. **Gemeinsamer Plattform-Kern (`packages/platform_core/`)** & **Unified Monitoring Sidecar (`services/terra-remote-engine/`)**: Beide Domaenen teilen sich nun kryptografische Provenienz, rotierende Telemetrie mit Secret-Redaktion, atomaren Job-Store, Quality-First-Ressourcen-Guards, CI/CD und einheitliche Windows-Observability.

## Repository-Struktur

```text
unified-deterministic-studio/
├── packages/
│   ├── platform_core/              # Gemeinsamer Stdlib-Kern (Provenienz, C2PA Content Credentials, Telemetrie, JobStore, ResourceGuard, Security)
│   └── platform_mcp/               # Nativer JSON-RPC-2.0 MCP-Server (Stdio & HTTP /mcp) fuer EDA, ART & Security-Tools
├── apps/
│   ├── platform_gateway/           # Lokales Control-Hub, Cockpit UI, MCP-Endpunkt & 100%-Simulator (127.0.0.1:8080/simulator)
│   ├── eda-multiagent-core/        # Deterministische PCB-Pipeline + Synthesizer + jlcparts-Importer + Gerber-Verifier + Panelizer + build123d-Validator + Rust core_fast + Tauri
│   └── art-restoration-ai/         # Quality-First Restaurierungs-Engine + Spandrel-Adapter + pyvips-Streaming-I/O + C2PA + Checkpoint-Verifier + Telemetrie
├── services/
│   └── terra-remote-engine/        # Rust 1.2 Monitoring-Sidecar (Watchlist, /tail eda|art, /getlog eda|art, rotierendes Debug-Log, Win32 C:/D: Disk-Monitor)
├── scripts/
│   ├── install_platform.ps1        # Ein-Klick-Setup fuer dedizierte lokale .venv
│   ├── start_platform.ps1          # Lokaler Launcher (127.0.0.1) fuer Gateway, EDA oder ART
│   ├── package_release.ps1         # Release-Build & SHA-256-Manifest-Erstellung (CARGO_BUILD_JOBS=2)
│   ├── verify_unified_platform.ps1 # Fail-Closed End-to-End-Verifikation ueber alle Python- & Rust-Komponenten + Secret-Scan
│   ├── check_platform_processes.ps1# Read-Only-Prozessdiagnose (EDA + ART + Terra + Topaz)
│   └── collect_crash_report.ps1    # Read-Only WER-/Dump-/EventLog-Diagnose
├── docs/UNIFIED_ARCHITECTURE.md    # Architektur-Synthese, Invarianten & Cross-Domain-Synergien
└── .github/workflows/ci.yml        # GitHub Actions CI-Matrix (Python + Rust)
```

## Verbindliche Sicherheits- & Qualitaets-Invarianten

- **Fail-Closed Secret-Hygiene:** `.env`-Dateien sind gesperrt; Logs und JSONL-Telemetrie redigieren Tokens/Keys automatisch; statischer Secret-Scan ist Teil von `verify_unified_platform.ps1`.
- **Quality-First & Determinismus:** Weder EDA-DRC-Regeln noch Restaurierungs-Qualitaetsprofile werden zur Ressourcenschonung still abgesenkt. Ressourcenanpassung erfolgt ueber Kachelgroesse, serielle Ausfuehrung und `CARGO_BUILD_JOBS=2`.
- **Lokales Binding & Opt-In-Freigaben:** FastAPI-Server binden standardmaessig an `127.0.0.1`; `ALLOW_REMOTE_SCREEN_CAPTURE=false`, `ALLOW_REMOTE_PROCESS_TERMINATION=false`, `RUNPOD_ENABLED=false`.
- **Unveraenderliche Single Source of Truth:** `apps/eda-multiagent-core/core/schema.py` bleibt rueckwaertskompatibel und unveraendert.

## Verifikation (Windows 11)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_unified_platform.ps1
```
