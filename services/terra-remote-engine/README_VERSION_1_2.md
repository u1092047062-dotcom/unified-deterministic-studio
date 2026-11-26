# TerraRemoteEngine 1.2.0 – Development Candidate

**Status: Entwicklungskandidat, noch keine freigegebene Produktionsversion.**

## Grundlage und Schutz der Originale

Diese Entwicklungsbasis wurde aus `ProcessMonitor_v1_1(1).zip` erstellt. Das ältere Archiv `TerraRemoteEngine_Projekt.zip` wurde als Vergleichsquelle verwendet. Beide Eingabearchive bleiben unverändert.

## Änderungen in 1.2.0

- Paketversion auf 1.2.0 angehoben.
- Ctrl-C beendet den Dispatcher kontrolliert, statt als Verbindungsabbruch in die Reconnect-Schleife zu laufen.
- Die reine Formatprüfung verändert keine Quelldateien.
- Build- und Testlauf sind in `VERIFY_AND_BUILD.ps1` zusammengeführt; der Lauf dokumentiert EXE-Pfad und SHA-256.
- Deployment-Skript arbeitet standardmäßig nur lesend. Änderungen an der geplanten Windows-Aufgabe erfordern ausdrücklich `-ApplyScheduledTask` und vorheriges XML-Backup.
- `.env.example` ergänzt; echte Zugangsdaten gehören ausschließlich in eine lokale `.env`.
- Lock-, Sleep-, Shutdown- und Reboot-Meldungen melden Windows-Fehler nicht mehr pauschal als Erfolg.

## Sicherheitsvorgaben

- Fern-Screenshots bleiben standardmäßig deaktiviert.
- Fern-Prozessbeendigung bleibt standardmäßig deaktiviert.
- Keine Webcam-Funktion.
- Keine Zugangsdaten in Quellcode, ZIP oder Diagnosebericht.

## Build und Prüfung unter Windows 11

In PowerShell im Projektordner:

```powershell
Copy-Item .env.example .env
notepad .env
# Eigene Werte eintragen und speichern.
.\VERIFY_AND_BUILD.ps1
```

Das Skript führt `cargo fmt --check`, `cargo test --all-targets --locked`, `cargo clippy --all-targets --locked -- -D warnings` und `cargo build --release --locked` aus. Es verändert die Rust-Quelldateien nicht.

## Deployment – ausdrücklich getrennt

Zuerst die erfolgreiche Build-Prüfung abwarten. Danach nur bei bewusster Freigabe:

```powershell
.\Update-TerraRemoteEngine.ps1 -ApplyScheduledTask
```

Ohne `-ApplyScheduledTask` wird nur gelesen und geprüft. Das Deployment startet die Aufgabe nicht automatisch.

## Noch offene Release-Gates

1. Windows-11-Build und vollständiger Testlauf auf dem Zielgerät müssen erfolgreich sein.
2. Native Win32-Screenshot-Funktion wurde so geändert, dass die Bitmap vor `GetDIBits` aus dem Memory-DC entfernt und die Rückgabe von `BitBlt`/`GetDIBits` geprüft wird. Das muss noch unter Windows kompiliert und mit einem realen Screenshot (auch Mehrmonitor-/HiDPI-Konfiguration) praktisch getestet werden.
3. Telegram-Live-Test mit gültigem Bot-Token und freigegebener Benutzer-ID.
4. Prozessmonitor über längeren Zeitraum sowie PID-Reuse-/Fehlerfälle testen.
5. Scheduled-Task-Start, Arbeitsverzeichnis, lokale `.env`, Neustart und kontrolliertes Stoppen auf dem Zielgerät verifizieren.
6. EXE-Hash und Buildbericht sichern; erst danach Produktionsfreigabe erteilen.

Ein erfolgreicher Build allein ist kein Nachweis für alle Laufzeitfunktionen.

## Build-Pruefung starten
Unter Windows bitte `START_VERIFY_AND_BUILD.cmd` doppelklicken. Das PowerShell-Fenster bleibt absichtlich offen, damit rote Fehlermeldungen nicht verschwinden. Alternativ `VERIFY_AND_BUILD.ps1` aus einer bereits geoeffneten PowerShell starten.
