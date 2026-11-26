"""RS-274X Gerber- & Excellon-Drill-Syntaktik-Validator + Composite-SVG-Renderer.

Inspiriert von pygerber (MIT):
- Erkennt optional installiertes `pygerber` automatisch.
- Prueft RS-274X-Layer (Format-Spec, Einheiten, Apertur-Definitionen, definierte
  D-Codes, Koordinaten, M02*-EOF) und Excellon-Bohrdateien (M48, METRIC, Tools, M30).
- Rendert direkt aus dem geparsten RS-274X/Excellon-Rohtext eine skalierbare
  Fertigungs-Vorschau-SVG (Nachweis, dass die exportierten Dateien real lesbar sind).
"""

from __future__ import annotations

import importlib.util
import re
from typing import Dict, List, Mapping, Tuple

_APERTURE_RE = re.compile(r"^%ADD(\d+)C,([0-9.]+)\*%$")
_SELECT_D_RE = re.compile(r"^D(\d+)\*$")
_COORD_RE = re.compile(r"^X(-?\d+)Y(-?\d+)D0([123])\*$")
_EXCELLON_TOOL_RE = re.compile(r"^T(\d+)C([0-9.]+)$")
_EXCELLON_HIT_RE = re.compile(r"^X(-?[0-9.]+)Y(-?[0-9.]+)$")


def detect_pygerber() -> Dict[str, object]:
    spec = importlib.util.find_spec("pygerber")
    return {"available": spec is not None, "backend": "pygerber" if spec else "builtin-rs274x"}


def validate_gerber_layer(filename: str, content: str) -> Dict[str, object]:
    """Prueft eine einzelne RS-274X-Gerber-Datei strukturell und extrahiert Primitive."""
    errors: List[str] = []
    apertures: Dict[int, float] = {}
    segments: List[Tuple[float, float, float, float, float]] = []
    flashes: List[Tuple[float, float, float]] = []

    has_fs = False
    has_unit = False
    has_eof = False
    current_d: int | None = None
    cur_x, cur_y = 0.0, 0.0

    for lineno, raw in enumerate(content.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("G04"):
            continue
        if line == "%FSLAX46Y46*%":
            has_fs = True
            continue
        if line in ("%MOMM*%", "%MOIN*%"):
            has_unit = True
            continue
        if line in ("%LPD*%", "%LPC*%", "G01*"):
            continue
        if line == "M02*":
            has_eof = True
            continue

        m_ap = _APERTURE_RE.match(line)
        if m_ap:
            apertures[int(m_ap.group(1))] = float(m_ap.group(2))
            continue

        m_sel = _SELECT_D_RE.match(line)
        if m_sel:
            code = int(m_sel.group(1))
            if code >= 10:
                if code not in apertures:
                    errors.append(f"{filename}:{lineno}: Undefinierte Apertur D{code}")
                current_d = code
            continue

        m_xy = _COORD_RE.match(line)
        if m_xy:
            nx = int(m_xy.group(1)) / 1_000_000.0
            ny = int(m_xy.group(2)) / 1_000_000.0
            op = m_xy.group(3)
            width = apertures.get(current_d or -1, 0.25)
            if op == "2":
                cur_x, cur_y = nx, ny
            elif op == "1":
                if current_d is None or current_d not in apertures:
                    errors.append(f"{filename}:{lineno}: Draw D01 ohne gueltige Apertur")
                segments.append((cur_x, cur_y, nx, ny, width))
                cur_x, cur_y = nx, ny
            elif op == "3":
                if current_d is None or current_d not in apertures:
                    errors.append(f"{filename}:{lineno}: Flash D03 ohne gueltige Apertur")
                flashes.append((nx, ny, width))
                cur_x, cur_y = nx, ny
            continue

        errors.append(f"{filename}:{lineno}: Unbekannter RS-274X-Befehl '{line}'")

    if not has_fs:
        errors.append(f"{filename}: Fehlende Format-Spezifikation (%FSLAX46Y46*%)")
    if not has_unit:
        errors.append(f"{filename}: Fehlende Einheiten-Angabe (%MOMM*%)")
    if not has_eof:
        errors.append(f"{filename}: Fehlendes Dateiende (M02*)")

    return {
        "file": filename,
        "valid": len(errors) == 0,
        "errors": errors,
        "aperture_count": len(apertures),
        "segment_count": len(segments),
        "flash_count": len(flashes),
        "_segments": segments,
        "_flashes": flashes,
    }


def validate_excellon_drill(filename: str, content: str) -> Dict[str, object]:
    """Prueft eine Excellon-Bohrdatei (M48, METRIC, Tools, Bohrkoordinaten, M30)."""
    errors: List[str] = []
    tools: Dict[int, float] = {}
    hits: List[Tuple[float, float, float]] = []
    has_m48 = False
    has_metric = False
    has_m30 = False
    current_tool: int | None = None

    for lineno, raw in enumerate(content.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line == "M48":
            has_m48 = True
            continue
        if line.startswith("METRIC"):
            has_metric = True
            continue
        if line in ("%", "G90", "G05"):
            continue
        if line == "M30":
            has_m30 = True
            continue

        m_tool = _EXCELLON_TOOL_RE.match(line)
        if m_tool:
            tools[int(m_tool.group(1))] = float(m_tool.group(2))
            continue

        if re.match(r"^T(\d+)$", line):
            tnum = int(line[1:])
            if tnum not in tools:
                errors.append(f"{filename}:{lineno}: Undefiniertes Bohrwerkzeug T{tnum}")
            current_tool = tnum
            continue

        m_hit = _EXCELLON_HIT_RE.match(line)
        if m_hit:
            if current_tool is None or current_tool not in tools:
                errors.append(f"{filename}:{lineno}: Bohrung ohne aktives Werkzeug")
            hits.append((float(m_hit.group(1)), float(m_hit.group(2)), tools.get(current_tool or -1, 0.3)))
            continue

        errors.append(f"{filename}:{lineno}: Unbekannter Excellon-Befehl '{line}'")

    if not has_m48:
        errors.append(f"{filename}: Fehlender Excellon-Header (M48)")
    if not has_metric:
        errors.append(f"{filename}: Fehlende METRIC-Deklaration")
    if not has_m30:
        errors.append(f"{filename}: Fehlendes Programmende (M30)")

    return {
        "file": filename,
        "valid": len(errors) == 0,
        "errors": errors,
        "tool_count": len(tools),
        "drill_hits": len(hits),
        "_hits": hits,
    }


def verify_and_render_gerbers(gerber_files: Mapping[str, str]) -> Dict[str, object]:
    """Validiert ein komplettes Gerber-/Excellon-Bundle und rendert eine Composite-SVG."""
    layer_reports: Dict[str, Dict[str, object]] = {}
    all_errors: List[str] = []
    all_xs: List[float] = [0.0, 10.0]
    all_ys: List[float] = [0.0, 10.0]

    parsed_layers: Dict[str, Dict[str, object]] = {}
    for name in sorted(gerber_files.keys()):
        content = gerber_files[name]
        if name.lower().endswith(".drl"):
            res = validate_excellon_drill(name, content)
            for x, y, _ in res["_hits"]:
                all_xs.append(x)
                all_ys.append(y)
        else:
            res = validate_gerber_layer(name, content)
            for x1, y1, x2, y2, _ in res["_segments"]:
                all_xs.extend([x1, x2])
                all_ys.extend([y1, y2])
            for x, y, _ in res["_flashes"]:
                all_xs.append(x)
                all_ys.append(y)

        parsed_layers[name] = res
        all_errors.extend(res["errors"])
        layer_reports[name] = {k: v for k, v in res.items() if not k.startswith("_")}

    max_x = max(all_xs)
    max_y = max(all_ys)
    pad = 4.0
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-pad} {-pad} {max_x + 2*pad:.2f} {max_y + 2*pad:.2f}" '
        'style="width:100%;height:auto;background:#071318;border-radius:10px;">'
    ]

    colors = {
        "Edge_Cuts.gm1": "#22c55e",
        "B_Cu.gbl": "#38bdf8",
        "F_Cu.gtl": "#f97316",
    }
    for lname in ("Edge_Cuts.gm1", "B_Cu.gbl", "F_Cu.gtl"):
        if lname not in parsed_layers:
            continue
        col = colors[lname]
        for x1, y1, x2, y2, w in parsed_layers[lname]["_segments"]:
            svg.append(
                f'<line x1="{x1:.2f}" y1="{max_y - y1:.2f}" x2="{x2:.2f}" y2="{max_y - y2:.2f}" '
                f'stroke="{col}" stroke-width="{max(w, 0.25):.2f}" stroke-linecap="round"/>'
            )
        for x, y, d in parsed_layers[lname]["_flashes"]:
            svg.append(
                f'<circle cx="{x:.2f}" cy="{max_y - y:.2f}" r="{max(d/2.0, 0.3):.2f}" fill="#fbbf24"/>'
            )

    if "board.drl" in parsed_layers:
        for x, y, d in parsed_layers["board.drl"]["_hits"]:
            svg.append(
                f'<circle cx="{x:.2f}" cy="{max_y - y:.2f}" r="{max(d/2.0, 0.2):.2f}" fill="#ffffff" stroke="#0f172a" stroke-width="0.12"/>'
            )

    svg.append("</svg>")
    return {
        "valid": len(all_errors) == 0,
        "backend": detect_pygerber(),
        "error_count": len(all_errors),
        "errors": all_errors,
        "layers": layer_reports,
        "preview_svg": "".join(svg),
    }
