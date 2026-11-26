# Quality-first policy

- Originaldatei ist unveränderlich; jede Ableitung erhält eine eigene Datei und SHA-256-Prüfsumme.
- Kein stilles Herabsetzen von Modell, Checkpoint, Präzision, Qualitätsprofil oder Ausgabeauflösung, um VRAM, Laufzeit oder Budget einzuhalten.
- Ressourcenoptimierung erfolgt durch kleinere Kacheln, geringere Parallelität, Caching, Resume und bedarfsgerechte GPU-Laufzeit.
- Bei Budgetüberschreitung wird Cloud-Dispatch gestoppt; es wird kein schwächeres Modell ausgewählt.
- Farbprofil/ICC und unterstützte Bit-Tiefe bleiben erhalten. Nicht unterstützte Modi werden abgewiesen statt automatisch verlustbehaftet konvertiert.
- KI-generierte Rekonstruktionen sind als solche zu dokumentieren und nicht als historisch authentischer Bildinhalt auszugeben.
- Modell-ID, Modellversion, Checkpoint-SHA-256, Parameter, Kachel-IDs, Toolversion und Herkunft müssen protokolliert werden.
- PSNR, SSIM und Pixelvergleiche sind Hilfsindikatoren, kein Beweis für historische Authentizität oder wahrgenommene Restaurierungsqualität.
- Bei unklarer Qualität: Verarbeitung stoppen oder zur menschlichen Prüfung eskalieren.
- Die Identity-Validation-Pipeline ist nur ein Test der Datenwege; sie verbessert keine Bilder.
