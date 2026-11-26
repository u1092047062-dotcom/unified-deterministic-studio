# TerraRemoteEngine 1.2.0 – QA- und Release-Status

## Ergebnis dieser Arbeitsrunde

- Beide bereitgestellten ZIP-Archive wurden ohne ZIP-Integritätsfehler geöffnet.
- Die vier zentralen Rust-Dateien wurden direkt aus den Archiven verglichen.
- Neue Entwicklungsbasis aus Version 1.1 angelegt; Eingabearchive wurden nicht verändert.
- Paketversion auf 1.2.0 gesetzt.
- Ctrl-C-Pfad so geändert, dass ein kontrolliertes Beenden nicht als Reconnect behandelt wird.
- Screenshot-Pfad korrigiert: Bitmap wird vor `GetDIBits` abgewählt; `BitBlt` und `GetDIBits` werden geprüft; Größenberechnungen sind abgesichert.
- Lock-/Sleep-/Shutdown-/Reboot-Meldungen prüfen nun den Windows-Rückgabewert.
- Build-/Testskript ist lesend bezüglich Quelldateien (`cargo fmt --check` statt Autoformatierung).
- Deployment ist standardmäßig nur lesend; Änderung der geplanten Aufgabe erfordert `-ApplyScheduledTask` und erstellt vorher ein XML-Backup.
- `.env.example` hinzugefügt; echte `.env` und Zugangsdaten sind nicht enthalten.

## Lokal ausgeführte Prüfungen in dieser Umgebung

- ZIP-Integrität beider Eingabearchive: PASS.
- TOML-Syntax für `Cargo.toml` und `Cargo.lock`: PASS.
- Statische Invarianten der geänderten Skripte und Quellstellen: PASS.

## Nicht ausgeführte Prüfungen / Release-Blocker

- Rust/Cargo sind in der aktuellen Build-Umgebung nicht installiert; daher konnten `cargo fmt`, `cargo test`, `cargo clippy` und `cargo build --release` nicht ausgeführt werden.
- Es gab keinen Windows-11-Laufzeittest, keinen Telegram-Live-Test und keinen echten Screenshot-Test.
- Die geplante Windows-Aufgabe wurde nicht geändert oder gestartet.
- Der Prozessmonitor muss unter Windows über längere Zeit sowie bei Snapshot-Fehlern/PID-Wiederverwendung getestet werden.

## Release-Entscheidung

**NO RELEASE – Development Candidate only.** Die Quelle und Prüfskripte sind vorbereitet. Eine Funktionsfreigabe wird erst erteilt, wenn das Windows-Build- und Laufzeitprüfprogramm vollständig erfolgreich war. Ein statischer Check ist kein Ersatz für einen Compiler- und Zielsystemtest.
