# Unified Architecture – Synthese aus `05-platinen-elektronik` & `Topaz`

## 1. Architektur-Konvergenz (Cross-Domain-Synergien)

Statt zwei getrennte Projekte nebeneinander abzulegen, extrahiert `unified-deterministic-studio` die jeweils staerksten Architekturmuster in `packages/platform_core/` und hebt beide Domaenen auf das naechste Qualitaetsniveau:

| Faehigkeit | Herkunft | Gemeinsame Plattform-Schicht | Nutzen in beiden Domaenen |
|---|---|---|---|
| **SHA-256 Artefakt- & Datei-Provenienz** | EDA `export_manifest` + ART `sha256_file` | `platform_core.provenance` | Deterministische Fertigungs- & Restaurierungs-Manifeste |
| **Rotierendes Logging & JSONL-Telemetrie + Secret-Redaktion** | EDA `telemetry_log` | `platform_core.telemetry` | Jetzt aktiv in `eda-multiagent-core` **und** `art-restoration-ai` |
| **Atomarer, Config-Hash-gesicherter Job-Store** | ART `RestorationEngine` | `platform_core.job_store` | Jetzt aktiv fuer persistierte EDA-Jobs (`POST /jobs`, `GET /jobs/{id}`) |
| **Quality-First Ressourcen- & Budget-Guard** | ART `resource_manager` + EDA 8-GB-Guardrail | `platform_core.resource_guard` | Einheitliche RAM/VRAM/Cargo-Steuerung ohne stilles Qualitaets-Downgrade |
| **Pfad-Kanonisierung & Secret-Scanner** | Terra `telegram.rs` + EDA `verify_eda_workspace` | `platform_core.security` | Traversal-Sperre (`..`), `.env*`-Sperre, automatischer Token-Scan |
| **Windows Monitoring Sidecar** | `TerraRemoteEngine 1.2` | `services/terra-remote-engine` | Ueberwacht EDA-, ART- & Topaz-Prozesse; `/tail eda|art`, `/getlog eda|art` |

## 2. Unverrueckbare Invarianten

1. **Keine LLM-Heuristik in Kern-Algorithmen:** EDA nutzt deterministische Solver (Z3, Force-Directed, Pathfinder A*, Spatial-Hash-DRC); ART nutzt deterministische Kachelgeometrie, Feather-Stitching und explizite Adapter-Allowlists.
2. **Original- & Schema-Schutz:** `apps/eda-multiagent-core/core/schema.py` bleibt die unveraenderte Single Source of Truth; hochgeladene Originale in `art-restoration-ai` werden niemals ueberschrieben.
3. **Hardware-Schutz fuer 8-GB-Workstations:** Alle Rust-Builds setzen `CARGO_TARGET_DIR` ausserhalb von `C:` und begrenzen Parallelitaet auf `--jobs 2`.
4. **Fail-Closed Security:** Keine automatischen Windows-Scheduled-Tasks, keine offenen Secrets, kein externes Binding ohne explizite Betreiberentscheidung.
