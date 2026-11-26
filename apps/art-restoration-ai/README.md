# ART Restoration AI – Restoration Engine 1.0

Quality-first Plattform zur originalgetreuen Restaurierung und kontrollierten Vergrößerung von Kunstwerken und Fotografien.

**Release-Stand: 1.0.0-dev (lokal testbarer Orchestrierungs-Kern; noch keine Produktionsfreigabe).**

## Was in 1.0 umgesetzt ist

- FastAPI-Projektverwaltung, Upload-Validierung, unveränderliches Original und SHA-256-Prüfung.
- Bildanalyse mit Format, Abmessungen, Farbraum, Megapixeln und einfachen Bildstatistiken.
- Kachelplanung mit vollständiger Bildabdeckung und überlappenden Bereichen.
- Feather-Blending/Stitching mit gewichteter Überblendung.
- Persistierte Kachel-Zwischenstände und Fortsetzung eines unterbrochenen Jobs mit gleicher Job-ID und identischer Konfiguration.
- Qualitätsbericht mit Prüfsummen, Abmessungen, MSE/PSNR und Pixelidentitätsprüfung.
- Schutzlimit für lokale Testläufe (standardmäßig 20 Megapixel), damit das 8-GB-Notebook nicht durch große In-Memory-Jobs überlastet wird. Größere Aufträge werden bis zur Bereitstellung eines Streaming-/Cloud-Workers abgewiesen statt still auf minderwertige Einstellungen umzuschalten.
- Quality-first-Ressourcenpolicy: kleinere Kacheln/serielle Verarbeitung statt stiller Modell- oder Präzisionsreduktion.
- Topaz-Desktop-Übergabe mit Manifest, SHA-256, getrenntem Ergebnisimport und Provenienzprotokoll.
- Adapter- und Modellregistry; nicht konfigurierte KI-Modelle bleiben ausdrücklich deaktiviert.
- RunPod-Konfigurationsstatus und Cloud-Dispatch-Sperre, bis Credentials, Worker, Storage, Budget und Sicherheit verifiziert sind.
- JSON-/CSV-/TXT-Diagnose ist unabhängig vom Bildverarbeitungsjob möglich.

### Wichtige Funktionsgrenze

Der mitgelieferte `identity-validation-only`-Adapter ist ein **Pipeline-Test**: Er kopiert Pixel unverändert, um Kachelaufteilung, Stitching, Speicherung, Resume und Qualitätskontrolle reproduzierbar zu testen. Er verbessert kein Bild und ist kein KI-Modell. Real-ESRGAN, SwinIR und Topaz-CLI/API-Automation werden nicht als verfügbar ausgegeben, solange geprüfte Modellgewichte, Lizenzen, Worker und Herstellerberechtigungen fehlen.

## Lokaler Start unter Windows 11

1. Python 3.11 oder neuer installieren.
2. PowerShell im Projektordner öffnen.
3. `Set-ExecutionPolicy -Scope Process Bypass -Force` ausführen.
4. `./install_windows.ps1` ausführen.
5. `./start_windows.ps1` ausführen.
6. Browser öffnen: `http://127.0.0.1:8000/docs`.

Die API bindet standardmäßig nur an `127.0.0.1`. Nicht ungeschützt ins Internet exponieren.

## API-Ablauf

1. `POST /projects` – Projekt anlegen.
2. `POST /projects/{id}/source` – Original hochladen. Ein vorhandenes Original wird nicht überschrieben.
3. `POST /projects/{id}/analyze` – Bildmetadaten analysieren.
4. `POST /projects/{id}/tiles/plan` – Kachelplan berechnen.
5. `POST /projects/{id}/restoration/jobs` – Validierungsjob starten (derzeit nur `identity-validation-only`).
6. `GET /projects/{id}/jobs/{job_id}` – Jobstatus und Ergebnis lesen.
7. `POST /projects/{id}/restoration/jobs/{job_id}/resume` – fehlgeschlagenen/unvollständigen Job mit gleicher Konfiguration fortsetzen.
8. `POST /projects/{id}/topaz/export` – hashgesicherte Arbeitskopie für Topaz erzeugen.
9. In der lizenzierten Topaz-Desktop-App manuell bearbeiten; Ergebnis separat exportieren.
10. `POST /projects/{id}/topaz/import` – Ergebnis validieren und Provenienz protokollieren.

## Topaz auf dem ermittelten Notebook

Die vom Betreiber gemeldeten Installationen:

- Photo AI 4.1.0: `C:\Program Files\Topaz Labs LLC\Topaz Photo AI\tpai.exe`
- Gigapixel AI 8.4.4: `C:\Program Files\Topaz Labs LLC\Topaz Gigapixel AI\gigapixel.exe`

Die Plattform nutzt zunächst den manuellen Desktop-Übergabepfad. Ein fehlender API-Key verhindert nicht automatisch die normale interaktive Desktop-Nutzung. Eine automatisierte CLI/API-Steuerung wird nur aktiviert, wenn Topaz für genau diese Produktversion, Lizenz und Nutzungsbedingungen eine dokumentierte Schnittstelle/Berechtigung bestätigt. Keine inoffizielle GUI-Klickautomation, Login-Automation oder Lizenzumgehung.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Die Tests laufen ohne GPU, ohne Topaz-Start und ohne Cloud-Zugang.

## Cloud / RunPod

Siehe `cloud/runpod/README.md`. Das Paket enthält noch keinen produktiven Cloud-Worker und sendet keine Cloud-Jobs. Zugangsdaten gehören ausschließlich in lokale Umgebungsvariablen bzw. einen Secret Manager, niemals in Git oder Logs. GPU-Dispatch muss Budget, Storage, Datenregion, Löschfristen und Worker-Image vorab validieren.

## Qualitätspolitik

- Originaldateien werden nicht verändert.
- KI-generierte Details sind als Rekonstruktion zu kennzeichnen; keine Behauptung historischer Authentizität.
- Qualitätsprofil, Modellversion, Checkpoint-Hash, Parameter, Tile-IDs und Ausgabedateien müssen protokolliert werden.
- PSNR/SSIM und Pixelvergleiche sind Hilfsmetriken, kein Beweis für künstlerische oder historische Korrektheit.
- Bei QC-Fehlern: stoppen, nicht automatisch ein schwächeres Modell wählen.
- Ressourcen sparen durch kleinere Kacheln, geringere Parallelität, Caching und bedarfsgerechte Cloud-Laufzeit – niemals durch stilles Herabsetzen von Modellqualität oder Präzision.

## Status / Produktionsfreigabe

Der lokale Orchestrierungs- und Validierungskern ist testbar. Für eine echte KI-Restaurierung fehlen noch: ausgewählte und lizenzgeprüfte Checkpoints, echte GPU-Adapter, Modell-QC-Kalibrierung, produktiver Cloud-Worker, Secret-Management, verschlüsselter Objektspeicher, Authentifizierung/Rollen, Rate-Limits, Backup/Restore und ein End-to-End-Test mit repräsentativen Kunstwerken. Daher: **nicht produktionsfreigegeben**.
