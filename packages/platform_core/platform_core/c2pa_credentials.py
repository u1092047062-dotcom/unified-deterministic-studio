"""C2PA-kompatible Content Credentials & verlustfreie PNG-Einbettung (Phase 3).

Implementiert das C2PA-Assertions-Datenmodell (c2pa.ingredients, c2pa.actions,
c2pa.ai_disclosure) ohne schwere native Abhaengigkeiten durch verlustfreie
PNG-iTXt-Chunk-Einbettung mit kryptografischer Pixel-Bindung (SHA-256 ueber
IHDR/PLTE/IDAT) und optionaler Erkennung von `c2pa-python`.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import struct
from typing import Any, Dict, List, Optional, Tuple
import zlib

C2PA_SCHEMA_VERSION = "c2pa.studio.v1"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
C2PA_CHUNK_KEYWORD = "c2pa.manifest"


def detect_c2pa_backend() -> Dict[str, Any]:
    """Prueft rein lesend, ob optionales `c2pa` (c2pa-python) installiert ist."""
    c2pa_installed = importlib.util.find_spec("c2pa") is not None
    return {
        "c2pa_python_installed": c2pa_installed,
        "active_backend": "c2pa-python" if c2pa_installed else "embedded-png-itxt-c2pa",
        "lossless_png_embedding": True,
        "schema_version": C2PA_SCHEMA_VERSION,
    }


def _canonical_sha256(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _iter_png_chunks(png_bytes: bytes) -> List[Tuple[bytes, bytes]]:
    if not png_bytes.startswith(PNG_SIGNATURE):
        raise ValueError("Ungueltige PNG-Signatur: Datei ist kein gueltiges PNG")
    pos = len(PNG_SIGNATURE)
    chunks: List[Tuple[bytes, bytes]] = []
    total = len(png_bytes)
    while pos < total:
        if pos + 12 > total:
            raise ValueError("Abgeschnittener PNG-Chunk erkannt")
        length = struct.unpack(">I", png_bytes[pos : pos + 4])[0]
        chunk_type = png_bytes[pos + 4 : pos + 8]
        data_start = pos + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > total:
            raise ValueError(f"PNG-Chunk {chunk_type!r} ueberschreitet Dateigroesse")
        data = png_bytes[data_start:data_end]
        expected_crc = struct.unpack(">I", png_bytes[data_end:crc_end])[0]
        actual_crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ValueError(f"PNG-CRC32-Fehler in Chunk {chunk_type!r}")
        chunks.append((chunk_type, data))
        pos = crc_end
        if chunk_type == b"IEND":
            break
    if not chunks or chunks[-1][0] != b"IEND":
        raise ValueError("PNG enthaelt keinen abschliessenden IEND-Chunk")
    return chunks


def _pack_png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def compute_png_pixel_digest(png_bytes: bytes) -> str:
    """Berechnet einen deterministischen SHA-256-Hash ueber Bildstruktur und Pixeldaten (IHDR/PLTE/IDAT)."""
    chunks = _iter_png_chunks(png_bytes)
    h = hashlib.sha256()
    idat_count = 0
    for chunk_type, data in chunks:
        if chunk_type in (b"IHDR", b"PLTE", b"tRNS", b"IDAT"):
            h.update(chunk_type)
            h.update(struct.pack(">I", len(data)))
            h.update(data)
            if chunk_type == b"IDAT":
                idat_count += 1
    if idat_count == 0:
        raise ValueError("PNG enthaelt keine IDAT-Bilddaten")
    return h.hexdigest()


def build_c2pa_manifest(
    title: str,
    *,
    source_sha256: str,
    actions: List[Dict[str, Any]],
    claim_generator: str = "Unified Deterministic Studio",
    ai_disclosed: bool = False,
    simulated: bool = False,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Erstellt ein C2PA-konformes Assertions-Manifest inkl. kanonischem Claim-Hash."""
    body: Dict[str, Any] = {
        "schema_version": C2PA_SCHEMA_VERSION,
        "title": title,
        "claim_generator": claim_generator,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "ai_disclosed": bool(ai_disclosed),
        "simulated": bool(simulated),
        "assertions": [
            {
                "label": "c2pa.ingredients",
                "data": {"source_sha256": source_sha256},
            },
            {
                "label": "c2pa.actions",
                "data": {"actions": actions},
            },
            {
                "label": "c2pa.ai_disclosure",
                "data": {
                    "ai_generated_or_modified": bool(ai_disclosed),
                    "simulated_pipeline": bool(simulated),
                },
            },
        ],
        "metadata": dict(extra_metadata or {}),
    }
    manifest = copy.deepcopy(body)
    manifest["claim_sha256"] = _canonical_sha256(body)
    return manifest


def _is_c2pa_text_chunk(chunk_type: bytes, data: bytes) -> bool:
    if chunk_type not in (b"iTXt", b"tEXt"):
        return False
    prefix = C2PA_CHUNK_KEYWORD.encode("latin-1") + b"\x00"
    return data.startswith(prefix)


def embed_png_content_credentials(png_bytes: bytes, manifest: Dict[str, Any]) -> bytes:
    """Bettet ein C2PA-Manifest verlustfrei als PNG-iTXt-Chunk inkl. Pixel-Hash-Bindung ein."""
    chunks = _iter_png_chunks(png_bytes)
    pixel_digest = compute_png_pixel_digest(png_bytes)

    bound_body = {k: v for k, v in copy.deepcopy(manifest).items() if k != "claim_sha256"}
    bound_body["asset_binding"] = {
        "format": "image/png",
        "pixel_data_sha256": pixel_digest,
    }
    bound_manifest = copy.deepcopy(bound_body)
    bound_manifest["claim_sha256"] = _canonical_sha256(bound_body)

    json_bytes = json.dumps(bound_manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    # PNG iTXt layout: keyword \0 compression_flag(0) compression_method(0) language_tag \0 translated_keyword \0 utf8_text
    itxt_payload = C2PA_CHUNK_KEYWORD.encode("latin-1") + b"\x00\x00\x00\x00\x00" + json_bytes
    c2pa_chunk = _pack_png_chunk(b"iTXt", itxt_payload)

    out = bytearray(PNG_SIGNATURE)
    for chunk_type, data in chunks:
        if _is_c2pa_text_chunk(chunk_type, data):
            continue
        if chunk_type == b"IEND":
            out.extend(c2pa_chunk)
        out.extend(_pack_png_chunk(chunk_type, data))
    return bytes(out)


def read_png_content_credentials(png_bytes: bytes) -> Optional[Dict[str, Any]]:
    """Liest ein eingebettetes C2PA-Manifest aus einem PNG-Byte-Strom."""
    chunks = _iter_png_chunks(png_bytes)
    keyword_prefix = C2PA_CHUNK_KEYWORD.encode("latin-1") + b"\x00"
    for chunk_type, data in chunks:
        if not _is_c2pa_text_chunk(chunk_type, data):
            continue
        rest = data[len(keyword_prefix) :]
        if chunk_type == b"iTXt":
            if len(rest) < 4:
                raise ValueError("Beschaedigter iTXt-C2PA-Chunk")
            comp_flag = rest[0]
            # rest[1] = compression_method, then language_tag\0 translated_keyword\0 text
            after_flags = rest[2:]
            parts = after_flags.split(b"\x00", 2)
            if len(parts) != 3:
                raise ValueError("Ungueltiges iTXt-C2PA-Feldformat")
            raw_text = zlib.decompress(parts[2]) if comp_flag == 1 else parts[2]
            return json.loads(raw_text.decode("utf-8"))
        return json.loads(rest.decode("utf-8"))
    return None


def verify_png_content_credentials(png_bytes: bytes) -> Dict[str, Any]:
    """Prueft kryptografisch sowohl den C2PA-Claim-Hash als auch die PNG-Pixel-Integritaet."""
    try:
        manifest = read_png_content_credentials(png_bytes)
    except Exception as exc:
        return {
            "valid": False,
            "reason": f"parse_error: {exc}",
            "claim_integrity_ok": False,
            "pixel_integrity_ok": False,
            "manifest": None,
        }

    if manifest is None:
        return {
            "valid": False,
            "reason": "no_c2pa_manifest_found",
            "claim_integrity_ok": False,
            "pixel_integrity_ok": False,
            "manifest": None,
        }

    claimed_hash = str(manifest.get("claim_sha256", ""))
    body = {k: v for k, v in manifest.items() if k != "claim_sha256"}
    expected_claim_hash = _canonical_sha256(body)
    claim_ok = bool(claimed_hash) and claimed_hash == expected_claim_hash

    actual_pixel_digest = compute_png_pixel_digest(png_bytes)
    expected_pixel_digest = str(manifest.get("asset_binding", {}).get("pixel_data_sha256", ""))
    pixel_ok = bool(expected_pixel_digest) and actual_pixel_digest == expected_pixel_digest

    valid = claim_ok and pixel_ok
    reason = "verified" if valid else (
        "claim_hash_mismatch" if not claim_ok else "pixel_tampering_detected"
    )
    return {
        "valid": valid,
        "reason": reason,
        "claim_integrity_ok": claim_ok,
        "pixel_integrity_ok": pixel_ok,
        "ai_disclosed": bool(manifest.get("ai_disclosed", False)),
        "simulated": bool(manifest.get("simulated", False)),
        "claim_sha256": claimed_hash,
        "pixel_data_sha256": actual_pixel_digest,
        "manifest": manifest,
    }
