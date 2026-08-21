#!/usr/bin/env python3
"""Philon's local, newline-delimited JSON engine over a Unix domain socket.

The current implementation deliberately makes no network calls. It is a
native-text-first adapter with evidence-rich output and explicit warnings for
unavailable OCR/model packs. The protocol is stable enough for the desktop
shell and can later host isolated CoreML/ONNX/MLX adapters.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import glob
import hashlib
import hmac
import html
import io
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
import warnings as std_warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

IR_VERSION = "0.2.0"
ENGINE_VERSION = "philon-0.2.0"
MAX_INPUT_BYTES = 500 * 1024 * 1024
MAX_PDF_PAGES = 2_000
MAX_IMAGE_PIXELS = 100_000_000
MAX_EXTRACTED_ASSETS = 1_000
MAX_EXTRACTED_ASSET_BYTES = 250 * 1024 * 1024
SUPPORTED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp"}
MODEL_MANIFEST_PATH = Path(__file__).with_name("model-manifest.json")
VISION_HELPER: Path | None = None


@dataclass
class WarningRecord:
    code: str
    message: str
    page: int | None = None
    block_id: str | None = None
    severity: str = "warning"


@dataclass
class Timing:
    stage: str
    milliseconds: int


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_model_manifest() -> dict[str, Any]:
    """Validate model governance metadata before exposing any pack state."""
    manifest = json.loads(MODEL_MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(manifest.get("schema_version"), str) or not isinstance(manifest.get("packs"), list):
        raise ValueError("Model manifest must declare a schema version and pack list.")
    seen: set[str] = set()
    for pack in manifest["packs"]:
        if not isinstance(pack, dict) or not isinstance(pack.get("id"), str) or not pack["id"] or pack["id"] in seen:
            raise ValueError("Model manifest pack IDs must be present and unique.")
        seen.add(pack["id"])
        if not isinstance(pack.get("approved"), bool) or not isinstance(pack.get("required"), bool):
            raise ValueError(f"Model pack {pack['id']} must declare boolean approval and required state.")
        if not isinstance(pack.get("distribution"), str) or not isinstance(pack.get("runtime"), str) or not isinstance(pack.get("license"), str):
            raise ValueError(f"Model pack {pack['id']} is missing distribution, runtime, or licence metadata.")
        if "discovery_paths" in pack and (not isinstance(pack["discovery_paths"], list) or not all(isinstance(item, str) and item for item in pack["discovery_paths"])):
            raise ValueError(f"Model pack {pack['id']} has invalid local discovery paths.")
        if pack["required"] and (not pack["approved"] or not pack.get("integrity") or pack["distribution"] == "not-distributed"):
            raise ValueError(f"Required model pack {pack['id']} is not approved with integrity metadata.")
        if pack["approved"] and not pack.get("integrity"):
            raise ValueError(f"Approved model pack {pack['id']} requires integrity metadata before installation.")
    return manifest


def model_runtime_readiness(pack: dict[str, Any], local_path: str | None, built_in: bool) -> tuple[str, list[str]]:
    """Report what is actually runnable without loading a model or using a GPU.

    Discovery alone is not an activation signal: a local directory can contain
    incomplete weights, or the matching llama.cpp executable can be absent.
    The UI uses these diagnostics to make the distinction visible before a
    person requests a manual repair or a Verified embedding run.
    """
    if not pack.get("approved", False):
        return "blocked", ["This pack is not approved by Philon's local model policy."]
    pack_id = str(pack["id"])
    if pack_id == "native-pdfium":
        return ("ready", []) if built_in else ("unavailable", ["The bundled native PDF runtime is unavailable."])
    if pack_id == "apple-vision-ocr":
        return ("ready", []) if built_in else ("unavailable", ["The bundled Apple Vision helper is unavailable."])
    if not local_path:
        return "not-found", ["No locally managed model directory matched this manifest entry."]
    root = Path(local_path)
    if pack_id == "qwen3.8-27b-local-repair":
        issues: list[str] = []
        if not list(root.glob("Qwen3.8-27B-*.gguf")):
            issues.append("The Qwen GGUF weight is missing.")
        if not list(root.glob("mmproj-*.gguf")):
            issues.append("The Qwen multimodal projector is missing.")
        executable = shutil.which("llama-cli") or str(Path.home() / ".local" / "bin" / "llama-cli")
        if not Path(executable).is_file():
            issues.append("llama-cli is not installed locally.")
        return ("ready", []) if not issues else ("incomplete", issues)
    if pack_id == "bge-m3-local-candidate":
        issues = []
        if not list(root.glob("*.gguf")):
            issues.append("The BGE-M3 GGUF weight is missing.")
        executable = shutil.which("llama-embedding") or str(Path.home() / ".local" / "bin" / "llama-embedding")
        if not Path(executable).is_file():
            issues.append("llama-embedding is not installed locally.")
        return ("ready", []) if not issues else ("incomplete", issues)
    if pack_id == "olmocr-2-7b-local-candidate":
        return "probe-required", ["Local files are present. Runtime availability is checked only when a manual repair is requested."]
    return "available", ["Local files are present; this adapter is not activated automatically."]


def model_status() -> dict[str, Any]:
    """Expose declared model gates and offline runtime readiness without fetching."""
    manifest = load_model_manifest()
    packs = []
    for pack in manifest.get("packs", []):
        local_path = next((str(found) for candidate in pack.get("discovery_paths", []) for found in sorted(Path(item) for item in glob.glob(os.path.expanduser(candidate))) if found.exists()), None)
        available_locally = local_path is not None
        built_in = pack["id"] == "native-pdfium" or (pack["id"] == "apple-vision-ocr" and VISION_HELPER is not None and VISION_HELPER.exists())
        readiness, diagnostics = model_runtime_readiness(pack, local_path, built_in)
        packs.append({
            "id": pack["id"], "role": pack["role"], "required": pack["required"], "approved": pack.get("approved", False),
            "license": pack["license"], "runtime": pack["runtime"], "distribution": pack.get("distribution", "unknown"),
            "installed": built_in or (pack.get("approved", False) and available_locally),
            "available_locally": available_locally,
            "local_path": local_path,
            "integrity": pack.get("integrity"),
            "readiness": readiness,
            "diagnostics": diagnostics,
        })
    return {"schema_version": manifest.get("schema_version"), "policy": manifest.get("policy"), "packs": packs}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preflight_input(path: Path) -> dict[str, Any]:
    """Apply bounded, deterministic intake checks before a parser sees a file."""
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Input file was not found: {path}")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported input type: {suffix or 'no extension'}")
    byte_count = path.stat().st_size
    if byte_count == 0:
        raise ValueError("Input is empty.")
    if byte_count > MAX_INPUT_BYTES:
        raise ValueError("Input exceeds the 500 MB V1 safety limit.")
    with path.open("rb") as stream:
        signature = stream.read(16)
    if suffix == ".pdf":
        if not signature.startswith(b"%PDF-"):
            raise ValueError("PDF signature is invalid. Philon did not send this file to a parser.")
        try:
            from pypdf import PdfReader  # type: ignore

            previous_logging_threshold = logging.root.manager.disable
            logging.disable(logging.CRITICAL)
            try:
                reader = PdfReader(str(path), strict=False)
                if reader.is_encrypted:
                    raise ValueError("Encrypted PDFs are unsupported in V1. Remove encryption locally and try again.")
                declared_pages = len(reader.pages)
            finally:
                logging.disable(previous_logging_threshold)
            if declared_pages > MAX_PDF_PAGES:
                raise ValueError(f"PDF exceeds Philon's {MAX_PDF_PAGES}-page safety limit.")
        except ImportError:
            declared_pages = None
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("PDF container could not be parsed safely. Philon did not continue to extraction.") from exc
        return {
            "kind": "pdf",
            "bytes": byte_count,
            "bytes_sha256": sha256_file(path),
            "signature": "pdf",
            "declared_page_count": declared_pages,
            "limits": {"max_bytes": MAX_INPUT_BYTES, "max_pages": MAX_PDF_PAGES},
        }
    image_signatures = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*", b"RIFF")
    if not any(signature.startswith(marker) for marker in image_signatures):
        raise ValueError("Image signature is invalid. Philon retained no inferred text.")
    dimensions: dict[str, int] = {}
    try:
        from PIL import Image  # type: ignore

        with std_warnings.catch_warnings():
            std_warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                width, height = image.size
                frames = getattr(image, "n_frames", 1)
        if frames != 1:
            raise ValueError("Multi-frame image containers are unsupported in V1. Export one image per page and try again.")
        if width * height > MAX_IMAGE_PIXELS:
            raise ValueError(f"Image exceeds Philon's {MAX_IMAGE_PIXELS:,}-pixel safety limit.")
        dimensions = {"width": width, "height": height, "pixels": width * height}
    except ImportError:
        dimensions = {"width": 0, "height": 0, "pixels": 0}
    except Exception as exc:
        raise ValueError("Image container could not be parsed safely. Philon did not send it to OCR.") from exc
    return {
        "kind": "image",
        "bytes": byte_count,
        "bytes_sha256": sha256_file(path),
        "signature": "image",
        "declared_page_count": 1,
        "dimensions": dimensions,
        "limits": {"max_bytes": MAX_INPUT_BYTES, "max_pages": 1, "max_pixels": MAX_IMAGE_PIXELS},
    }


def safe_slug(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._")
    return clean or "document"


def atomic_write_text(path: Path, content: str) -> None:
    """Commit one UTF-8 export atomically so a crash never leaves a half-file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Commit a binary asset without exposing a partially written image."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]


def normalise_artifact(value: str) -> str:
    """Normalise likely running headers/footers without changing source text."""
    return re.sub(r"\s+", " ", value).strip().casefold()


def is_numeric_source_marker(value: str) -> bool:
    """Recognise isolated PDF footnote/page markers without treating them as prose."""
    return bool(re.fullmatch(r"[.·]?\s*\d{1,4}(?:\s+\d{1,4}){0,5}", value.strip()))


def repeated_page_artifacts(source_pages: list[dict[str, Any]]) -> set[str]:
    """Find repeated first/last lines only when the evidence is strong.

    A repeated line is never deleted from the source PDF. It is simply excluded
    from body assembly and retained in document provenance as a page artifact.
    """
    if len(source_pages) < 3:
        return set()
    counts: dict[str, int] = {}
    for page in source_pages:
        lines = [normalise_artifact(line) for line in page["text"].splitlines() if normalise_artifact(line)]
        for line in (lines[:2] + lines[-2:]):
            if 3 <= len(line) <= 130 and not line.isdigit():
                counts[line] = counts.get(line, 0) + 1
    threshold = max(3, round(len(source_pages) * 0.6))
    return {line for line, count in counts.items() if count >= threshold}


def is_formula(text: str) -> bool:
    compact = text.replace("\n", " ").strip()
    if len(compact) < 3 or len(compact) > 500:
        return False
    markers = sum(marker in compact for marker in ("=", "∑", "∫", "√", "≈", "≤", "≥", "^", "_", "\\\\"))
    letters = sum(char.isalpha() for char in compact)
    digits = sum(char.isdigit() for char in compact)
    return markers >= 2 and (digits > 0 or letters < len(compact) * 0.45)


def table_rows(text: str) -> list[list[str]] | None:
    """Return a normalized table only for consistently delimited text.

    This does not infer columns from visual spacing. A manually selected model
    candidate may contain Markdown's separator row; it is validation syntax,
    not table data, and must not leak into CSV or semantic HTML.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    delimiter = "|" if sum("|" in line for line in lines) >= 2 else "\t"
    if delimiter == "\t" and sum("\t" in line for line in lines) < 2:
        return None
    rows: list[list[str]] = []
    for line in lines:
        parts = [cell.strip() for cell in line.strip("|").split(delimiter)]
        if len(parts) < 2:
            return None
        rows.append(parts)
    widths = {len(row) for row in rows}
    if len(widths) != 1:
        return None
    if delimiter == "|" and len(rows) >= 2 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]):
        rows.pop(1)
    return rows


def structured_parts(text: str, artifacts: set[str]) -> list[str]:
    lines = text.splitlines()
    retained = [line for line in lines if normalise_artifact(line) not in artifacts]
    return paragraphs("\n".join(retained))


def structured_parts_with_spans(text: str, artifacts: set[str]) -> list[dict[str, Any]]:
    """Keep body assembly and source positions together.

    A span always refers to the PDFium text stream, never to an inferred visual
    order.  When a repeated running header/footer is suppressed, the span still
    covers its original paragraph; this makes that conservative transformation
    reviewable instead of pretending the source did not contain it.
    """
    parts: list[dict[str, Any]] = []
    for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", text):
        original = match.group(0)
        retained = [line for line in original.splitlines() if normalise_artifact(line) not in artifacts]
        value = "\n".join(retained).strip()
        if value:
            parts.append({"text": value, "start": match.start(), "end": match.end()})
    return parts


def ocr_parts_with_geometry(page: dict[str, Any], artifacts: set[str]) -> list[dict[str, Any]]:
    """Turn Vision lines into conservative, measured reading blocks."""
    lines = page.get("ocr_lines", [])
    if not lines:
        return structured_parts_with_spans(page.get("text", ""), artifacts)
    parts: list[dict[str, Any]] = []
    current: list[tuple[int, dict[str, Any]]] = []

    def box_for(line: dict[str, Any]) -> dict[str, Any] | None:
        raw = line.get("bbox")
        if not isinstance(raw, list) or len(raw) != 4:
            return None
        try:
            x, y, width, height = (float(value) for value in raw)
        except (TypeError, ValueError):
            return None
        return make_bbox(x, y, x + width, y + height, "normalized-image")

    def flush() -> None:
        if not current:
            return
        text = "\n".join(str(line["text"]).strip() for _, line in current if str(line.get("text", "")).strip()).strip()
        if text:
            parts.append({"text": text, "line_indexes": [index for index, _ in current]})
        current.clear()

    previous_box: dict[str, Any] | None = None
    for index, line in enumerate(lines):
        text = str(line.get("text", "")).strip()
        if not text or normalise_artifact(text) in artifacts:
            flush(); previous_box = None; continue
        if is_numeric_source_marker(text):
            flush(); page.setdefault("numeric_source_markers", []).append({"text": text, "bbox": box_for(line)}); previous_box = None; continue
        current_box = box_for(line)
        if current and current_box and previous_box:
            previous_height = float(previous_box["y1"]) - float(previous_box["y0"])
            current_height = float(current_box["y1"]) - float(current_box["y0"])
            vertical_gap = float(previous_box["y0"]) - float(current_box["y1"])
            moved_upward = float(current_box["y0"]) > float(previous_box["y0"]) + max(0.035, 2.0 * previous_height)
            changed_column = abs(float(current_box["x0"]) - float(previous_box["x0"])) > 0.26 and abs(vertical_gap) > max(0.01, previous_height)
            if vertical_gap > max(0.025, 1.45 * max(previous_height, current_height)) or moved_upward or changed_column:
                flush()
        current.append((index, line)); previous_box = current_box
    flush()
    return parts or structured_parts_with_spans(page.get("text", ""), artifacts)


def geometric_native_parts(page: dict[str, Any], artifacts: set[str]) -> list[dict[str, Any]]:
    """Assemble measured native lines into conservative paragraph candidates."""
    lines = page.get("native_text_lines", [])
    if not lines:
        return structured_parts_with_spans(page["text"], artifacts)
    parts: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if not current:
            return
        value = "\n".join(entry["text"] for entry in current).strip()
        if value:
            parts.append({"text": value, "start": current[0]["start"], "end": current[-1]["end"]})
        current.clear()

    for line in lines:
        if not line["text"].strip() or normalise_artifact(line["text"]) in artifacts:
            flush()
            continue
        if is_numeric_source_marker(line["text"]):
            flush()
            page.setdefault("numeric_source_markers", []).append({"text": line["text"], "bbox": line.get("bbox")})
            continue
        prior = current[-1] if current else None
        current_box, prior_box = line.get("bbox"), prior.get("bbox") if prior else None
        if current_box and prior_box:
            prior_height = float(prior_box["y1"]) - float(prior_box["y0"])
            current_height = float(current_box["y1"]) - float(current_box["y0"])
            vertical_gap = float(prior_box["y0"]) - float(current_box["y1"])
            # A larger-than-leading gap is source evidence of a new paragraph.
            if vertical_gap > max(10.0, 1.15 * max(prior_height, current_height)):
                flush()
        current.append(line)
    flush()
    return parts or structured_parts_with_spans(page["text"], artifacts)


def make_bbox(left: float, bottom: float, right: float, top: float, coordinate_space: str) -> dict[str, Any] | None:
    """Normalise a source rectangle and reject invalid geometry at the edge."""
    values = (left, bottom, right, top)
    if not all(isinstance(value, (int, float)) for value in values):
        return None
    if right <= left or top <= bottom:
        return None
    return {
        "x0": round(float(left), 4), "y0": round(float(bottom), 4),
        "x1": round(float(right), 4), "y1": round(float(top), 4),
        "coordinate_space": coordinate_space, "origin": "bottom-left",
    }


def union_bboxes(boxes: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [box for box in boxes if box]
    if not usable:
        return None
    spaces = {box.get("coordinate_space") for box in usable}
    origins = {box.get("origin") for box in usable}
    if len(spaces) != 1 or len(origins) != 1:
        return None
    return make_bbox(
        min(float(box["x0"]) for box in usable), min(float(box["y0"]) for box in usable),
        max(float(box["x1"]) for box in usable), max(float(box["y1"]) for box in usable),
        str(next(iter(spaces))),
    )


def language_hint(text: str) -> str:
    if not text.strip():
        return "und"
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    if re.search(r"[\u0600-\u06ff]", text):
        return "ar"
    if re.search(r"[\u0400-\u04ff]", text):
        return "ru"
    return "en"


def native_health(text: str) -> dict[str, Any]:
    visible = text.strip()
    replacement = text.count("\ufffd")
    control = sum(1 for char in text if ord(char) < 32 and char not in "\n\t\r")
    invisible = sum(1 for char in text if char in {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"})
    alphanumeric = sum(char.isalnum() for char in text)
    punctuation = sum(not char.isalnum() and not char.isspace() for char in text)
    repeated_lines: dict[str, int] = {}
    for line in text.splitlines():
        normalized = normalise_artifact(line)
        if len(normalized) >= 16 and not is_numeric_source_marker(normalized):
            repeated_lines[normalized] = repeated_lines.get(normalized, 0) + 1
    duplicate_line_count = sum(count - 1 for count in repeated_lines.values() if count >= 3)
    total = max(len(visible), 1)
    suspicious = replacement > 0 or control > 0 or invisible > 0 or (alphanumeric == 0 and len(visible) > 8)
    confidence = 0.98
    if not visible:
        confidence = 0.0
    elif suspicious:
        confidence = 0.32
    elif duplicate_line_count >= 3:
        confidence = 0.76
    elif punctuation / total > 0.55:
        confidence = 0.65
    return {
        "native_text_present": bool(visible),
        "replacement_characters": replacement,
        "control_characters": control,
        "invisible_characters": invisible,
        "duplicate_source_line_count": duplicate_line_count,
        "alphanumeric_ratio": round(alphanumeric / total, 4),
        "punctuation_ratio": round(punctuation / total, 4),
        "confidence": confidence,
        "requires_escalation": confidence < 0.8,
    }


def pdfium_span_bbox(textpage: Any, start: int, count: int) -> dict[str, Any] | None:
    """Get a measured PDFium rectangle union for an exact text-stream span."""
    if count <= 0:
        return None
    try:
        rectangle_count = textpage.count_rects(start, count)
        rectangles = [textpage.get_rect(index) for index in range(rectangle_count)]
        return union_bboxes(make_bbox(left, bottom, right, top, "pdf-page-points") for left, bottom, right, top in rectangles)
    except Exception:
        return None


def source_bbox_for_block(page: dict[str, Any], text: str, start: int | None, end: int | None, ocr_line_indexes: list[int] | None = None) -> dict[str, Any] | None:
    """Return only measured geometry that corresponds to this selected block."""
    if start is not None and end is not None:
        source_spans = page.get("native_text_spans", [])
        matches = [entry.get("bbox") for entry in source_spans if entry.get("start", -1) < end and entry.get("end", -1) > start]
        measured = union_bboxes(matches)
        if measured:
            return measured
    lines = page.get("ocr_lines", [])
    line_boxes = []
    candidates = ((index, line) for index, line in enumerate(lines) if ocr_line_indexes is None or index in ocr_line_indexes)
    for _, line in candidates:
        line_text = str(line.get("text", "")).strip()
        bounds = line.get("bbox")
        if not line_text or line_text not in text or not isinstance(bounds, list) or len(bounds) != 4:
            continue
        x, y, width, height = bounds
        box = make_bbox(x, y, x + width, y + height, "normalized-image")
        if box:
            line_boxes.append(box)
    return union_bboxes(line_boxes)


def pdfium_extract(path: Path) -> tuple[list[dict[str, Any]], list[WarningRecord]]:
    """Use PDFium first, preserving a pypdf fallback for non-packaged tests."""
    warnings: list[WarningRecord] = []
    try:
        import pypdfium2 as pdfium  # type: ignore

        document = pdfium.PdfDocument(str(path))
        pages: list[dict[str, Any]] = []
        for index in range(len(document)):
            page = document[index]
            width, height = page.get_size()
            textpage = page.get_textpage()
            extracted_text = textpage.get_text_range()
            leading_trim = len(extracted_text) - len(extracted_text.lstrip())
            text = extracted_text.strip()
            spans: list[dict[str, Any]] = []
            line_spans: list[dict[str, Any]] = []
            raw_offset = 0
            for raw_line in extracted_text.splitlines(keepends=True):
                line_text = raw_line.rstrip("\r\n")
                line_start = raw_offset
                raw_offset += len(raw_line)
                if not line_text or line_start < leading_trim:
                    continue
                start = line_start - leading_trim
                end = start + len(line_text)
                if start < 0 or end > len(text):
                    continue
                bbox = pdfium_span_bbox(textpage, line_start, len(line_text))
                line_spans.append({"text": line_text, "start": start, "end": end, "bbox": bbox})
                spans.append({"start": start, "end": end, "bbox": bbox})
            pages.append({
                "number": index + 1, "width": width, "height": height, "text": text,
                "method": "pdfium-native", "native_text_spans": spans, "native_text_lines": line_spans,
            })
            textpage.close()
            page.close()
        document.close()
        return pages, warnings
    except ImportError:
        warnings.append(WarningRecord("PDFIUM_UNAVAILABLE", "PDFium is not installed. Using the development fallback."))
    except Exception as exc:  # pragma: no cover - source documents vary widely
        warnings.append(WarningRecord("PDFIUM_PARSE_FAILED", f"PDFium could not read this file: {exc}"))

    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        pages = []
        for index, page in enumerate(reader.pages):
            box = page.mediabox
            pages.append({
                "number": index + 1,
                "width": float(box.width),
                "height": float(box.height),
                "text": (page.extract_text() or "").strip(),
                "method": "pypdf-development-fallback",
            })
        return pages, warnings
    except ImportError:
        raise RuntimeError("Install pypdfium2 or pypdf before converting PDF files.")


def native_pdf_features(path: Path, page_count: int) -> list[dict[str, Any]]:
    """Collect source-declared links and fonts without inferring geometry."""
    features = [{"fonts": [], "links": [], "geometry": "pdfium-text-rectangles"} for _ in range(page_count)]
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path), strict=False)
        for index, page in enumerate(reader.pages[:page_count]):
            resources = page.get("/Resources") or {}
            if hasattr(resources, "get_object"):
                resources = resources.get_object()
            fonts = resources.get("/Font") or {}
            if hasattr(fonts, "get_object"):
                fonts = fonts.get_object()
            font_names: list[str] = []
            for font in fonts.values() if hasattr(fonts, "values") else []:
                font_object = font.get_object() if hasattr(font, "get_object") else font
                name = font_object.get("/BaseFont") if hasattr(font_object, "get") else None
                if name:
                    font_names.append(str(name).lstrip("/"))
            links: list[dict[str, str]] = []
            annotations = page.get("/Annots") or []
            for annotation in annotations:
                item = annotation.get_object() if hasattr(annotation, "get_object") else annotation
                if item.get("/Subtype") != "/Link":
                    continue
                action = item.get("/A") or {}
                if hasattr(action, "get_object"):
                    action = action.get_object()
                uri = action.get("/URI") if hasattr(action, "get") else None
                if uri:
                    links.append({"uri": str(uri), "kind": "uri"})
            features[index] = {"fonts": sorted(set(font_names)), "links": links, "geometry": "pdfium-text-rectangles"}
    except Exception:
        pass
    return features


def vision_ocr(path: Path, profile: str) -> tuple[dict[str, Any] | None, WarningRecord | None]:
    if VISION_HELPER is None or not VISION_HELPER.exists():
        return None, WarningRecord("VISION_OCR_UNAVAILABLE", "The bundled Apple Vision OCR helper is unavailable. No OCR text was invented.", page=1)
    staged_path: Path | None = None
    try:
        mode = "fast" if profile == "Fast" else "accurate"
        # Vision uses a service behind the process boundary. That service can
        # reject the per-process directory macOS gives Python's tempfile
        # module, even when the calling process can read it. Stage a private
        # copy directly in /private/tmp (the shared macOS scratch directory),
        # then delete it immediately after recognition.
        descriptor, staged_name = tempfile.mkstemp(prefix="philon-vision-", suffix=path.suffix, dir="/private/tmp")
        os.close(descriptor)
        staged_path = Path(staged_name)
        shutil.copyfile(path, staged_path)
        process = subprocess.run([str(VISION_HELPER), str(staged_path), mode], check=True, capture_output=True, text=True, timeout=90)
        result = json.loads(process.stdout)
        lines = [line for line in result.get("lines", []) if isinstance(line.get("text"), str) and line["text"].strip()]
        if not lines:
            return {"text": "", "confidence": 0.0, "lines": []}, WarningRecord("OCR_NO_TEXT", "Apple Vision found no readable text in this source region.", page=1)
        return {"text": "\n".join(line["text"] for line in lines), "confidence": round(sum(float(line.get("confidence", 0.0)) for line in lines) / len(lines), 4), "lines": lines}, None
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as exc:
        return None, WarningRecord("VISION_OCR_FAILED", f"Apple Vision OCR did not produce verifiable text: {exc}", page=1)
    finally:
        if staged_path is not None:
            staged_path.unlink(missing_ok=True)


def image_extract(path: Path, profile: str) -> tuple[list[dict[str, Any]], list[WarningRecord]]:
    warnings: list[WarningRecord] = []
    width, height = 0, 0
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as image:
            width, height = image.size
    except ImportError:
        warnings.append(WarningRecord("PILLOW_UNAVAILABLE", "Image dimensions are unavailable until Pillow is installed."))
    except Exception:
        warnings.append(WarningRecord("IMAGE_METADATA_UNAVAILABLE", "Image dimensions could not be read. The original file remains available for an OCR model pack."))
    if profile == "Fast":
        warnings.append(WarningRecord("FAST_PROFILE_SKIPPED_OCR", "Fast uses native text only. This image was retained without invoking local OCR.", page=1))
        return [{"number": 1, "width": width, "height": height, "text": "", "method": "image-awaiting-ocr"}], warnings
    ocr, ocr_warning = vision_ocr(path, profile)
    if ocr_warning:
        warnings.append(ocr_warning)
    if ocr is None:
        warnings.append(WarningRecord("OCR_PACK_NOT_INSTALLED", "This image requires the bundled Apple Vision OCR helper. The source was retained and no text was invented.", page=1))
        return [{"number": 1, "width": width, "height": height, "text": "", "method": "image-awaiting-ocr"}], warnings
    return [{"number": 1, "width": width, "height": height, "text": ocr["text"], "method": "apple-vision-ocr", "ocr_confidence": ocr["confidence"], "ocr_lines": ocr["lines"]}], warnings


def adaptive_ocr_dpi(page: dict[str, Any]) -> int:
    """Choose a bounded local OCR raster scale from measured page geometry."""
    width, height = float(page.get("width") or 0), float(page.get("height") or 0)
    longest = max(width, height)
    shortest = min(width, height)
    if longest <= 0 or shortest <= 0:
        return 300
    # Keep a useful small-text raster while bounding wide technical pages.
    if longest * (300 / 72) > 3_600:
        return 240
    if shortest * (300 / 72) < 1_100:
        return 360
    return 300


def ocr_textless_pdf_pages(path: Path, pages: list[dict[str, Any]], profile: str) -> list[WarningRecord]:
    """Rasterize only native-text failures and retain the local OCR alternative."""
    if profile == "Fast" or VISION_HELPER is None or not VISION_HELPER.exists():
        return []
    warnings: list[WarningRecord] = []
    try:
        import pypdfium2 as pdfium  # type: ignore

        document = pdfium.PdfDocument(str(path))
        for page_info in pages:
            if native_health(page_info["text"])["native_text_present"]:
                continue
            page = document[page_info["number"] - 1]
            dpi = adaptive_ocr_dpi(page_info)
            bitmap = page.render(scale=dpi / 72)
            image = bitmap.to_pil()
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as stream:
                image.save(stream.name)
                raster_path = Path(stream.name)
            try:
                ocr, warning = vision_ocr(raster_path, profile)
            finally:
                raster_path.unlink(missing_ok=True)
            page.close()
            if warning:
                warning.page = page_info["number"]
                warnings.append(warning)
            if ocr and ocr["text"]:
                page_info.update({"text": ocr["text"], "method": "apple-vision-ocr", "ocr_confidence": ocr["confidence"], "ocr_lines": ocr["lines"], "raster_dpi": dpi})
            else:
                warnings.append(WarningRecord("OCR_NO_TEXT", f"Apple Vision found no readable text after a {dpi} DPI local raster pass.", page=page_info["number"]))
        document.close()
    except Exception as exc:
        warnings.append(WarningRecord("VISION_PDF_OCR_FAILED", f"A textless PDF page could not be rasterized for local OCR: {exc}"))
    return warnings


def classify_block(text: str) -> tuple[str, int | None]:
    first_line = text.splitlines()[0] if text else ""
    line_count = len([line for line in text.splitlines() if line.strip()])
    if table_rows(text):
        return "table", None
    if is_formula(text):
        return "formula", None
    if re.match(r"^(?:Figure|Fig\.|Table)\s+\d+[.:]", first_line, re.IGNORECASE):
        return "caption", None
    if re.match(r"^(?:\[\d+\]|\d+\.)\s+.+(?:\d{4}|doi:)", first_line, re.IGNORECASE):
        return "citation", None
    # A heading stands on its own line. Allowing a block of up to three lines
    # here meant judging ordinary prose by its first line, which starts with a
    # capital and ends mid-clause rather than with a full stop, and so turned
    # wrapped paragraphs into headings in the converted document.
    if line_count == 1 and re.match(r"^(?:\d+(?:\.\d+)*\s+)?[A-Z][A-Za-z0-9 ,:;()/-]{3,}$", first_line) and len(first_line) < 100:
        depth = min(6, first_line.count(".") + 1) if re.match(r"^\d+", first_line) else 2
        return "heading", depth
    return "paragraph", None


def route_for_page(page: dict[str, Any], health: dict[str, Any]) -> dict[str, Any]:
    page_kind = "image-or-scanned" if page["method"] in {"apple-vision-ocr", "image-awaiting-ocr"} else "born-digital"
    if health["native_text_present"] and health["requires_escalation"]:
        page_kind = "mixed-or-uncertain"
    if page["method"] == "apple-vision-ocr":
        return {
            "decision": "local-vision-ocr",
            "reason": "Native text was unavailable and Apple Vision performed local OCR.",
            "suggested_dpi": None,
            "automatic_model_execution": True,
            "page_kind": page_kind,
            "native_text_health": health,
            "raster_dpi": page.get("raster_dpi"),
        }
    if health["requires_escalation"]:
        return {
            "decision": "manual-local-recognition-required",
            "reason": "Native text did not pass the confidence gate.",
            "suggested_dpi": 300 if page["method"] == "image-awaiting-ocr" else 240,
            "automatic_model_execution": False,
            "page_kind": page_kind,
            "native_text_health": health,
            "raster_dpi": None,
        }
    return {
        "decision": "native-fast-path",
        "reason": "Native text passed integrity checks; no recognition model was invoked.",
        "suggested_dpi": None,
        "automatic_model_execution": False,
        "page_kind": page_kind,
        "native_text_health": health,
        "raster_dpi": None,
    }


def make_block(page: dict[str, Any], ordinal: int, text: str, start: int | None = None, end: int | None = None, ocr_line_indexes: list[int] | None = None) -> dict[str, Any]:
    kind, level = classify_block(text)
    health = native_health(text)
    page_id = f"page-{page['number']}"
    block_id = f"{page_id}-block-{ordinal}"
    confidence = page.get("ocr_confidence", health["confidence"])
    bbox = source_bbox_for_block(page, text, start, end, ocr_line_indexes)
    return {
        "id": block_id,
        "page": page_id,
        "type": kind,
        "level": level,
        "text": text,
        "bbox": bbox,
        "source": {"method": page["method"], "confidence": confidence, "language": language_hint(text)},
        "evidence": {
            "native_health": health,
            "validation": ["native-text-integrity", "reading-order-source-order"],
            "findings": {
                "native_text_present": health["native_text_present"],
                "replacement_characters": health["replacement_characters"],
                "source_bbox_available": bbox is not None,
                "source_bbox_coordinate_space": bbox["coordinate_space"] if bbox else None,
                "ocr_line_count": len(ocr_line_indexes) if ocr_line_indexes is not None else len(page.get("ocr_lines", [])),
            },
            "alternatives": [],
            "repair_history": [],
        },
    }


def validate_ir(ir: dict[str, Any]) -> None:
    """Reject malformed internal data before it reaches a renderer or cache."""
    if ir.get("philon_ir_version") != IR_VERSION:
        raise ValueError("IR version does not match this engine.")
    pages = ir.get("pages")
    blocks = ir.get("blocks")
    if not isinstance(pages, list) or not isinstance(blocks, list):
        raise ValueError("IR must contain pages and blocks lists.")
    page_ids = {page.get("id") for page in pages}
    block_ids = [block.get("id") for block in blocks]
    if len(block_ids) != len(set(block_ids)) or any(not block_id for block_id in block_ids):
        raise ValueError("IR block IDs must be present and unique.")
    for block in blocks:
        if block.get("page") not in page_ids:
            raise ValueError("Every block must refer to a known page.")
        if not isinstance(block.get("text"), str):
            raise ValueError("Every block must have string text.")
        bbox = block.get("bbox")
        if bbox is not None:
            required = ("x0", "y0", "x1", "y1", "coordinate_space", "origin")
            if not isinstance(bbox, dict) or any(key not in bbox for key in required):
                raise ValueError("Block geometry must be a complete source rectangle or null.")
            if not all(isinstance(bbox[key], (int, float)) for key in ("x0", "y0", "x1", "y1")):
                raise ValueError("Block geometry coordinates must be numeric.")
            if bbox["x1"] <= bbox["x0"] or bbox["y1"] <= bbox["y0"]:
                raise ValueError("Block geometry must have positive area.")


def resolve_citations(blocks: list[dict[str, Any]]) -> None:
    """Link numeric in-text citations to source-derived reference blocks."""
    references: dict[str, str] = {}
    for block in blocks:
        match = re.match(r"^\[(\d+)\]", block["text"].strip())
        if match and block["type"] == "citation":
            references[match.group(1)] = block["id"]
    for block in blocks:
        if block["type"] == "citation":
            continue
        numbers = re.findall(r"\[(\d+(?:\s*,\s*\d+)*)\]", block["text"])
        targets = []
        for group in numbers:
            for number in re.split(r"\s*,\s*", group):
                if number in references:
                    targets.append({"citation": number, "reference_block_id": references[number]})
        if targets:
            block["evidence"]["citation_targets"] = targets


def resolve_cross_page_tables(blocks: list[dict[str, Any]]) -> None:
    previous: dict[str, Any] | None = None
    for block in blocks:
        if block["type"] != "table":
            continue
        rows = table_rows(block["text"])
        if not rows:
            continue
        if previous:
            prior_rows = table_rows(previous["text"])
            different_pages = not previous.get("page") or not block.get("page") or previous.get("page") != block.get("page")
            if different_pages and prior_rows and prior_rows[0] == rows[0] and len(prior_rows[0]) == len(rows[0]):
                block["evidence"]["cross_page_continuation_of"] = previous["id"]
                previous["evidence"]["continues_on_block"] = block["id"]
        previous = block


def table_export_groups(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join only evidence-linked table continuations for CSV export.

    Markdown and HTML retain their per-page blocks; the joined CSV is an
    additional logical-table output with an explicit list of source blocks.
    """
    tables = [block for block in blocks if block.get("type") == "table" and table_rows(block.get("text", ""))]
    by_id = {block["id"]: block for block in tables}
    groups: list[dict[str, Any]] = []
    visited: set[str] = set()
    for root in tables:
        if root["id"] in visited or root.get("evidence", {}).get("cross_page_continuation_of") in by_id:
            continue
        current = root
        root_rows = table_rows(current["text"])
        assert root_rows is not None
        merged_rows = list(root_rows)
        source_blocks = [current["id"]]
        visited.add(current["id"])
        while (next_id := current.get("evidence", {}).get("continues_on_block")) in by_id and next_id not in visited:
            current = by_id[next_id]
            rows = table_rows(current["text"])
            if not rows or rows[0] != merged_rows[0]:
                break
            merged_rows.extend(rows[1:])
            source_blocks.append(current["id"])
            visited.add(current["id"])
        groups.append({"id": root["id"], "rows": merged_rows, "source_block_ids": source_blocks})
    for orphan in tables:
        if orphan["id"] not in visited:
            rows = table_rows(orphan["text"])
            assert rows is not None
            groups.append({"id": orphan["id"], "rows": rows, "source_block_ids": [orphan["id"]]})
    return groups


def verified_checks(pages: list[dict[str, Any]], blocks: list[dict[str, Any]]) -> list[WarningRecord]:
    """Apply stricter deterministic checks without turning uncertainty into text.

    These checks intentionally report ambiguity. They do not reroute to an
    unapproved model pack or reorder source text speculatively.
    """
    findings: list[WarningRecord] = []
    blocks_by_page: dict[str, list[dict[str, Any]]] = {}
    for block in blocks:
        block["evidence"].setdefault("validation", []).append("verified-deterministic-checks")
        blocks_by_page.setdefault(block["page"], []).append(block)
    for page in pages:
        page_blocks = blocks_by_page.get(page["id"], [])
        health = page.get("route", {}).get("native_text_health", {})
        if health.get("invisible_characters", 0):
            findings.append(WarningRecord("INVISIBLE_TEXT_SUSPECTED", "Verified found zero-width characters in native text. The source text is retained unchanged; inspect this page before reuse.", page=page["number"]))
        if health.get("duplicate_source_line_count", 0) >= 3:
            findings.append(WarningRecord("DUPLICATE_SOURCE_LINES", "Verified found repeated native lines on this page. They may be intentional source content or overlapping/invisible PDF text; Philon retained them for review.", page=page["number"]))
        if page["method"] in {"pdfium-native", "apple-vision-ocr"}:
            unlocated = next((block for block in page_blocks if block.get("bbox") is None and block.get("text", "").strip()), None)
            if unlocated:
                findings.append(WarningRecord("SOURCE_GEOMETRY_INCOMPLETE", "Verified could not measure every emitted block on this page. The text is retained but its source overlay needs review.", page=page["number"], block_id=unlocated["id"]))
        seen: set[str] = set()
        for block in page_blocks:
            normalized = re.sub(r"\s+", " ", block["text"]).strip().casefold()
            if len(normalized) > 24 and normalized in seen:
                findings.append(WarningRecord("DUPLICATE_BLOCK_CONTENT", "Verified found repeated source-derived block text on this page. Confirm whether it is intentional before using the export.", page=page["number"], block_id=block["id"]))
                break
            seen.add(normalized)
        measured = [block for block in page_blocks if isinstance(block.get("bbox"), dict) and block["bbox"].get("coordinate_space") == "pdf-page-points"]
        for previous, current in zip(measured, measured[1:]):
            previous_box, current_box = previous["bbox"], current["bbox"]
            if current_box["y1"] > previous_box["y1"] + 12:
                findings.append(WarningRecord("READING_ORDER_AMBIGUOUS", "Measured source geometry moves upward between emitted blocks. This may be a multi-column transition; Verified retained source order and marked it for review.", page=page["number"], block_id=current["id"]))
                break
    return findings


def make_ir(path: Path, profile: str) -> tuple[dict[str, Any], list[WarningRecord], list[Timing]]:
    started = time.perf_counter()
    suffix = path.suffix.lower()
    preflight = preflight_input(path)
    if suffix == ".pdf":
        source_pages, warnings = pdfium_extract(path)
        warnings.extend(ocr_textless_pdf_pages(path, source_pages, profile))
        for source_page, features in zip(source_pages, native_pdf_features(path, len(source_pages))):
            source_page["native_features"] = features
    else:
        source_pages, warnings = image_extract(path, profile)

    artifacts = repeated_page_artifacts(source_pages)
    pages: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    for source_page in source_pages:
        page_id = f"page-{source_page['number']}"
        source_parts = geometric_native_parts(source_page, artifacts) if source_page["method"] == "pdfium-native" else ocr_parts_with_geometry(source_page, artifacts)
        page_blocks = [
            make_block(source_page, ordinal, part["text"], part.get("start"), part.get("end"), part.get("line_indexes"))
            for ordinal, part in enumerate(source_parts, start=1)
        ]
        health = native_health(source_page["text"])
        confidence = source_page.get("ocr_confidence", health["confidence"])
        if health["requires_escalation"]:
            warnings.append(WarningRecord(
                "NEEDS_REVIEW",
                "Native text did not meet Philon's confidence gate. An approved OCR or repair pack can be invoked manually.",
                page=source_page["number"],
            ))
        pages.append({
            "id": page_id,
            "number": source_page["number"],
            "width": source_page["width"],
            "height": source_page["height"],
            "method": source_page["method"],
            "confidence": confidence,
            "route": route_for_page(source_page, health),
            "native_features": source_page.get("native_features", {"fonts": [], "links": [], "geometry": "normalized-vision-rectangles" if source_page.get("ocr_lines") else "not-applicable"}),
            "source_artifacts": {"numeric_markers": source_page.get("numeric_source_markers", [])},
            "block_ids": [block["id"] for block in page_blocks],
        })
        blocks.extend(page_blocks)

    if len(pages) > MAX_PDF_PAGES:
        raise ValueError(f"Document exceeds Philon's {MAX_PDF_PAGES}-page safety limit after extraction.")
    doc_hash = preflight["bytes_sha256"]
    resolve_citations(blocks)
    resolve_cross_page_tables(blocks)
    ir = {
        "philon_ir_version": IR_VERSION,
        "document": {
            "id": f"sha256:{doc_hash}",
            "source": {"path": str(path), "filename": path.name, "bytes_sha256": doc_hash, "page_count": len(pages), "preflight": preflight},
            "pipeline": {"profile": profile, "local_only": True, "engine": ENGINE_VERSION, "validation_mode": "deterministic-verified" if profile == "Verified" else "standard"},
            "created_at": now(),
        },
        "pages": pages,
        "blocks": blocks,
        "document_artifacts": {
            "repeated_headers_or_footers": sorted(artifacts),
            "numeric_source_markers": [{"page": page["id"], **marker} for page in pages for marker in page.get("source_artifacts", {}).get("numeric_markers", [])],
        },
    }
    if profile == "Verified":
        warnings.extend(verified_checks(pages, blocks))
    validate_ir(ir)
    return ir, warnings, [Timing("native-extraction", round((time.perf_counter() - started) * 1000))]


def render_markdown(ir: dict[str, Any]) -> str:
    lines: list[str] = []
    for block in ir["blocks"]:
        if block["type"] == "heading":
            lines.extend(["#" * (block["level"] or 2) + " " + block["text"], ""])
        elif block["type"] == "table" and table_rows(block["text"]):
            rows = table_rows(block["text"]) or []
            lines.append("| " + " | ".join(rows[0]) + " |")
            lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
            lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
            lines.append("")
        elif block["type"] == "formula":
            lines.extend(["```text", block["text"], "```", ""])
        else:
            lines.extend([block["text"], ""])
    native_images = ir.get("document_artifacts", {}).get("native_images", [])
    if native_images:
        lines.extend(["## Extracted source images", ""])
        for image in native_images:
            pages = ", ".join(str(page) for page in image.get("source_pages", [])) or "unknown"
            asset_id = image.get("id", "source image")
            relative_path = image.get("relative_path", "")
            width, height = image.get("pixel_width"), image.get("pixel_height")
            dimensions = f" · {width} × {height}px" if width and height else ""
            # This deliberately identifies the asset rather than inventing visual alt text.
            lines.extend([
                f"![Extracted source image {asset_id}; pages {pages}; visual description requires review.]({relative_path})",
                f"_Evidence: native PDF image {asset_id} · source page(s) {pages}{dimensions} · visual description requires review._",
                "",
            ])
    return "\n".join(lines).strip() + "\n"


def render_html(ir: dict[str, Any]) -> str:
    body: list[str] = []
    for block in ir["blocks"]:
        source = block["source"]
        attrs = f'data-philon-id="{block["id"]}" data-philon-page="{block["page"]}" data-philon-confidence="{source["confidence"]}"'
        content = html.escape(block["text"]).replace("\n", "<br />")
        if block["type"] == "heading":
            body.append(f'<h{block["level"] or 2} {attrs}>{content}</h{block["level"] or 2}>')
        elif block["type"] == "table" and table_rows(block["text"]):
            rows = table_rows(block["text"]) or []
            header = "<thead><tr>" + "".join(f"<th scope=\"col\">{html.escape(cell)}</th>" for cell in rows[0]) + "</tr></thead>"
            body_rows = "".join("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>" for row in rows[1:])
            body.append("<table " + attrs + ">" + header + "<tbody>" + body_rows + "</tbody></table>")
        elif block["type"] == "formula":
            body.append(f"<pre {attrs}><code>{content}</code></pre>")
        elif block["type"] == "caption":
            body.append(f"<figcaption {attrs}>{content}</figcaption>")
        else:
            body.append(f"<p {attrs}>{content}</p>")
    native_images = ir.get("document_artifacts", {}).get("native_images", [])
    if native_images:
        body.append("<section data-philon-artifact-section=\"native-images\"><h2>Extracted source images</h2>")
        for image in native_images:
            pages = ", ".join(str(page) for page in image.get("source_pages", [])) or "unknown"
            asset_id = str(image.get("id", "source-image"))
            relative_path = html.escape(str(image.get("relative_path", "")), quote=True)
            alt = html.escape(f"Extracted source image {asset_id}; source page(s) {pages}; visual description requires review.", quote=True)
            dimensions = ""
            if image.get("pixel_width") and image.get("pixel_height"):
                dimensions = f" · {image['pixel_width']} × {image['pixel_height']}px"
            body.append(
                f'<figure data-philon-asset-id="{html.escape(asset_id, quote=True)}" '
                f'data-philon-source-pages="{html.escape(pages, quote=True)}" '
                'data-philon-extraction-method="native-pdf-image">'
                f'<img src="{relative_path}" alt="{alt}" />'
                f'<figcaption>Extracted native PDF image {html.escape(asset_id)} · source page(s) {html.escape(pages)}{dimensions} · visual description requires review.</figcaption>'
                "</figure>"
            )
        body.append("</section>")
    return "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\" /><title>Philon export</title></head><body><main>" + "\n".join(body) + "</main></body></html>\n"


def clean_reading_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined: list[str] = []
    for line in lines:
        if joined and re.search(r"[A-Za-z]{2,}-$", joined[-1]) and re.match(r"^[a-z][A-Za-z'-]*\b", line):
            joined[-1] = joined[-1][:-1] + line
        else:
            joined.append(line)
    return re.sub(r"\s+", " ", " ".join(joined)).strip()


def render_markdown(ir: dict[str, Any]) -> str:
    """Clean Markdown for reading and text apps; provenance remains in machine/."""
    name = str(ir.get("document", {}).get("source", {}).get("filename", "Philon document"))
    pages = {str(page.get("id")): page.get("number") for page in ir.get("pages", [])}
    lines = ["---", f"title: {name}", "generated_by: Philon 0.2", "---", ""]
    current = None
    for block in ir.get("blocks", []):
        if block.get("page") != current:
            current = block.get("page")
            if (number := pages.get(str(current))) is not None:
                lines.extend([f"<!-- Philon source page {number} -->", ""])
        text = clean_reading_text(str(block.get("text", "")))
        if not text:
            continue
        if block.get("type") == "heading": lines.extend(["#" * (block.get("level") or 2) + " " + text, ""])
        elif block.get("type") == "table" and table_rows(str(block.get("text", ""))):
            rows = table_rows(str(block.get("text", ""))) or []
            lines.append("| " + " | ".join(rows[0]) + " |"); lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
            lines.extend("| " + " | ".join(row) + " |" for row in rows[1:]); lines.append("")
        elif block.get("type") == "caption": lines.extend([f"> {text}", ""])
        elif block.get("type") == "formula": lines.extend(["```text", text, "```", ""])
        else: lines.extend([text, ""])
    return "\n".join(lines).strip() + "\n"


def render_html(ir: dict[str, Any], include_facsimiles: bool = False) -> str:
    """Text-first responsive HTML with source pages available in place."""
    name = str(ir.get("document", {}).get("source", {}).get("filename", "Philon document"))
    by_page: dict[str, list[dict[str, Any]]] = {}
    for block in ir.get("blocks", []):
        by_page.setdefault(str(block.get("page")), []).append(block)
    body = [f"<header><p>Philon 0.2 presentation export</p><h1>{html.escape(name)}</h1></header><main>"]
    pages = list(ir.get("pages", []))
    if not pages:
        seen = []
        for block in ir.get("blocks", []):
            page_id = str(block.get("page", "page-1"))
            if page_id not in seen: seen.append(page_id)
        pages = [{"id": page_id, "number": index + 1} for index, page_id in enumerate(seen)]
    for page in pages:
        page_id, number = str(page.get("id")), int(page.get("number") or 0)
        body.append(f'<section id="source-page-{number}"><small>Source page {number}</small>')
        if include_facsimiles:
            body.append(f'<details><summary>Show original page</summary><img src="assets/page-previews/page-{number:04}.png" alt="Original source page {number}; consult it to verify visual layout and figures." loading="lazy" /></details>')
        for block in by_page.get(page_id, []):
            text = html.escape(clean_reading_text(str(block.get("text", ""))))
            if not text: continue
            attrs = f'data-philon-id="{block["id"]}" data-philon-page="{page_id}"'
            if block.get("type") == "heading": body.append(f'<h{block.get("level") or 2} {attrs}>{text}</h{block.get("level") or 2}>')
            elif block.get("type") == "table" and table_rows(str(block.get("text", ""))):
                rows = table_rows(str(block.get("text", ""))) or []
                header = "<thead><tr>" + "".join(f"<th scope=\"col\">{html.escape(cell)}</th>" for cell in rows[0]) + "</tr></thead>"
                contents = "".join("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>" for row in rows[1:])
                body.append("<table " + attrs + ">" + header + "<tbody>" + contents + "</tbody></table>")
            elif block.get("type") == "caption": body.append(f'<p class="caption" {attrs}>{text}</p>')
            else: body.append(f'<p {attrs}>{text}</p>')
        body.append("</section>")
    body.append("</main>")
    style = "body{margin:0;background:#f5f3ee;color:#1d1d1f;font:18px/1.65 Georgia,serif}header,main{max-width:46rem;margin:auto;padding:2rem 1.5rem}header{padding-top:5rem}h1{font-size:clamp(2rem,6vw,4rem);line-height:1.05}section{border-top:1px solid #d8d3cb;padding:2rem 0}small,header p{font:600 .72rem system-ui;color:#777;letter-spacing:.1em;text-transform:uppercase}details{margin:1rem 0;font-family:system-ui}summary{cursor:pointer;color:#735d44}img{display:block;width:100%;margin-top:1rem}.caption{font-style:italic;color:#6d6259}"
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(name)}</title><style>{style}</style></head><body>' + "\n".join(body) + "</body></html>\n"


def write_machine_package(ir: dict[str, Any], output_dir: Path) -> Path:
    root, pages_dir = output_dir / "machine", output_dir / "machine" / "pages"
    numbers = {str(page.get("id")): page.get("number") for page in ir.get("pages", [])}
    records = [{"id": block.get("id"), "page_id": block.get("page"), "page_number": numbers.get(str(block.get("page"))), "type": block.get("type"), "level": block.get("level"), "text": block.get("text", ""), "reading_text": clean_reading_text(str(block.get("text", ""))), "bbox": block.get("bbox"), "source": block.get("source"), "evidence": block.get("evidence"), "review": block.get("review")} for block in ir.get("blocks", [])]
    atomic_write_text(root / "blocks.ndjson", "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records))
    atomic_write_text(root / "reading-order.json", json.dumps({"schema_version": "1.0", "items": [{"page_id": page.get("id"), "page_number": page.get("number"), "block_ids": page.get("block_ids", [])} for page in ir.get("pages", [])]}, indent=2, ensure_ascii=False))
    atomic_write_text(root / "assets.json", json.dumps({"schema_version": "1.0", "native_images": ir.get("document_artifacts", {}).get("native_images", [])}, indent=2, ensure_ascii=False))
    for page in ir.get("pages", []):
        number, page_id = int(page.get("number") or 0), str(page.get("id")); page_records = [record for record in records if record["page_id"] == page_id]
        atomic_write_text(pages_dir / f"page-{number:04}.json", json.dumps({"schema_version": "1.0", "page": page, "blocks": page_records}, indent=2, ensure_ascii=False))
        atomic_write_text(pages_dir / f"page-{number:04}.md", "<!-- Philon source page " + str(number) + " -->\n\n" + "\n\n".join(str(record["reading_text"]) for record in page_records) + "\n")
    atomic_write_text(root / "package.json", json.dumps({"schema_version": "philon-machine-package/1.0", "document": ir.get("document", {}), "files": {"blocks": "blocks.ndjson", "reading_order": "reading-order.json", "pages": "pages/", "assets": "assets.json"}}, indent=2, ensure_ascii=False))
    atomic_write_text(root / "README.md", "# Philon machine package\n\nUse `blocks.ndjson` for streaming ingestion, `reading-order.json` for document order, `pages/` for page-level records, and `assets.json` for source-image provenance.\n")
    return root


def marker_style_block_type(block: dict[str, Any]) -> str:
    """Map Philon's stable block taxonomy to the familiar page-tree names.

    This is a clean-room export adapter. It intentionally shares the useful
    page/children/images shape that downstream Marker-oriented tools expect,
    while keeping Philon's provenance in separate, explicit fields.
    """
    return {
        "heading": "SectionHeader", "table": "Table", "formula": "Equation",
        "figure": "Figure", "caption": "Caption", "citation": "Text",
    }.get(block.get("type"), "Text")


def marker_style_polygon(block: dict[str, Any], page: dict[str, Any]) -> list[list[float]] | None:
    bbox = block.get("bbox")
    if not isinstance(bbox, dict):
        return None
    width, height = float(page.get("width") or 0), float(page.get("height") or 0)
    if width <= 0 or height <= 0:
        return None
    try:
        if bbox.get("coordinate_space") == "normalized-image":
            x0, x1 = float(bbox["x0"]) * width, float(bbox["x1"]) * width
            y0, y1 = float(bbox["y0"]) * height, float(bbox["y1"]) * height
        elif bbox.get("coordinate_space") == "pdf-page-points":
            x0, x1 = float(bbox["x0"]), float(bbox["x1"])
            # Philon's PDF coordinates are bottom-left; this tree uses the
            # top-left convention expected by page-oriented JSON consumers.
            y0, y1 = height - float(bbox["y1"]), height - float(bbox["y0"])
        else:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def marker_style_html(block: dict[str, Any]) -> str:
    content = html.escape(str(block.get("text", ""))).replace("\n", "<br />")
    block_type = block.get("type")
    if block_type == "heading":
        level = int(block.get("level") or 2)
        return f"<h{level}>{content}</h{level}>"
    if block_type == "table" and table_rows(str(block.get("text", ""))):
        rows = table_rows(str(block["text"])) or []
        head = "".join(f"<th>{html.escape(cell)}</th>" for cell in rows[0])
        body = "".join("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>" for row in rows[1:])
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    if block_type == "formula":
        return f"<math>{content}</math>"
    if block_type == "caption":
        return f"<figcaption>{content}</figcaption>"
    return f"<p>{content}</p>"


def render_marker_style_json(ir: dict[str, Any], output_dir: Path) -> list[dict[str, Any]]:
    """Render a portable, Marker-shaped page tree with embedded images.

    Every page has `id`, `block_type`, `html`, `polygon`, and `children`.
    Leaves retain source polygons and section hierarchy. Native image bytes are
    represented in each applicable page's `images` map as data URLs, matching
    the established JSON interchange convention while Philon's main IR keeps
    compact relative-path asset records.
    """
    blocks_by_page: dict[str, list[dict[str, Any]]] = {}
    for block in ir.get("blocks", []):
        blocks_by_page.setdefault(str(block.get("page")), []).append(block)
    assets_by_page: dict[int, list[dict[str, Any]]] = {}
    for asset in ir.get("document_artifacts", {}).get("native_images", []):
        for page_number in asset.get("source_pages", []):
            if isinstance(page_number, int):
                assets_by_page.setdefault(page_number, []).append(asset)

    rendered_pages: list[dict[str, Any]] = []
    for page in ir.get("pages", []):
        page_number = int(page.get("number") or 0)
        page_id = str(page.get("id") or f"page-{page_number}")
        section_hierarchy: dict[str, str] = {}
        children: list[dict[str, Any]] = []
        references: list[str] = []
        for index, block in enumerate(blocks_by_page.get(page_id, [])):
            node_type = marker_style_block_type(block)
            node_id = f"/page/{page_number}/{node_type}/{index}"
            if block.get("type") == "heading":
                level = int(block.get("level") or 2)
                section_hierarchy = {key: value for key, value in section_hierarchy.items() if int(key) < level}
                section_hierarchy[str(level)] = node_id
            children.append({
                "id": node_id,
                "block_type": node_type,
                "html": marker_style_html(block),
                "polygon": marker_style_polygon(block, page),
                "children": None,
                "section_hierarchy": dict(section_hierarchy),
                "images": {},
                "philon": {"block_id": block.get("id"), "confidence": block.get("source", {}).get("confidence"), "method": block.get("source", {}).get("method")},
            })
            references.append(f'<content-ref src="{node_id}"></content-ref>')
        for asset_index, asset in enumerate(assets_by_page.get(page_number, [])):
            relative_path = asset.get("relative_path")
            if not isinstance(relative_path, str):
                continue
            try:
                encoded = base64.b64encode((output_dir / relative_path).read_bytes()).decode("ascii")
            except OSError:
                continue
            asset_id = str(asset.get("id") or f"asset-{asset_index + 1:04}")
            node_id = f"/page/{page_number}/Picture/{asset_index}"
            mime_type = str(asset.get("mime_type") or "application/octet-stream")
            children.append({
                "id": node_id,
                "block_type": "Picture",
                "html": f'<img src="{html.escape(relative_path, quote=True)}" data-philon-asset-id="{html.escape(asset_id, quote=True)}" />',
                "polygon": None,
                "children": None,
                "section_hierarchy": dict(section_hierarchy),
                "images": {asset_id: f"data:{mime_type};base64,{encoded}"},
                "philon": {"asset_id": asset_id, "relative_path": relative_path, "source_pages": asset.get("source_pages", []), "bytes_sha256": asset.get("bytes_sha256")},
            })
            references.append(f'<content-ref src="{node_id}"></content-ref>')
        width, height = float(page.get("width") or 0), float(page.get("height") or 0)
        rendered_pages.append({
            "id": f"/page/{page_number}/Page/0",
            "block_type": "Page",
            "html": "".join(references),
            "polygon": [[0.0, 0.0], [width, 0.0], [width, height], [0.0, height]],
            "children": children,
            "philon": {"page_id": page_id, "method": page.get("method"), "confidence": page.get("confidence")},
        })
    return rendered_pages


def render_chunks(ir: dict[str, Any], target_size: int = 1200) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    buffer: list[str] = []
    source_ids: list[str] = []
    heading_path: list[str] = []
    for block in ir["blocks"]:
        if block["type"] == "heading":
            level = block["level"] or 2
            heading_path = heading_path[:max(0, level - 1)]
            heading_path.append(block["text"])
        if sum(len(part) for part in buffer) + len(block["text"]) > target_size and buffer:
            chunks.append({"id": f"chunk-{len(chunks) + 1}", "text": "\n\n".join(buffer), "source_block_ids": source_ids, "heading_path": heading_path})
            buffer, source_ids = [], []
        buffer.append(block["text"])
        source_ids.append(block["id"])
    if buffer:
        chunks.append({"id": f"chunk-{len(chunks) + 1}", "text": "\n\n".join(buffer), "source_block_ids": source_ids, "heading_path": heading_path})
    return chunks


def evidence_report(ir: dict[str, Any], warnings: list[WarningRecord], timings: list[Timing], cached: bool) -> dict[str, Any]:
    confidences = [block["source"]["confidence"] for block in ir["blocks"]]
    return {
        "document_id": ir["document"]["id"],
        "created_at": now(),
        "local_only": True,
        "cache_hit": cached,
        "summary": {
            "pages": len(ir["pages"]),
            "blocks": len(ir["blocks"]),
            "average_block_confidence": round(sum(confidences) / len(confidences), 3) if confidences else 0,
            "warnings": len(warnings),
            "tables": sum(block["type"] == "table" for block in ir["blocks"]),
            "formula_candidates": sum(block["type"] == "formula" for block in ir["blocks"]),
            "extracted_native_images": len(ir.get("document_artifacts", {}).get("native_images", [])),
            "manual_recognition_routes": sum(page["route"]["decision"] != "native-fast-path" for page in ir["pages"]),
        },
        "warnings": [asdict(warning) for warning in warnings],
        "timings": [asdict(timing) for timing in timings],
        "accessibility": accessibility_report(ir),
    }


def accessibility_report(ir: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    headings = [block for block in ir["blocks"] if block["type"] == "heading"]
    levels = [block["level"] or 2 for block in headings]
    if any(current - previous > 1 for previous, current in zip(levels, levels[1:])):
        findings.append({"rule": "heading-order", "status": "review", "message": "Heading levels jump by more than one level in the source-derived structure."})
    else:
        findings.append({"rule": "heading-order", "status": "pass", "message": "Emitted heading levels do not skip levels."})
    tables = sum(block["type"] == "table" and table_rows(block["text"]) is not None for block in ir["blocks"])
    findings.append({"rule": "native-tables", "status": "pass" if tables else "not-applicable", "message": "Native tables include header cells in semantic HTML." if tables else "No deterministically structured native tables were emitted."})
    figures = sum(block["type"] == "figure" for block in ir["blocks"]) + len(ir.get("document_artifacts", {}).get("native_images", []))
    if figures:
        findings.append({"rule": "figure-alternatives", "status": "review", "message": "Figure alternatives require human authoring; Philon does not infer them."})
    findings.append({"rule": "conformance", "status": "review", "message": "This report is an accessibility finding set, not a WCAG conformance claim."})
    return {"status": "assessed", "findings": findings}


def render_source_previews(path: Path, output_dir: Path, page_count: int) -> tuple[list[str], list[WarningRecord]]:
    """Export bounded local page rasters for source review, never as OCR input.

    These preview assets let the desktop client draw an evidence rectangle over
    the actual source page. They are deliberately rendered at 144 DPI: enough
    for inspection without turning a long document into an unbounded cache.
    """
    preview_dir = output_dir / "assets" / "page-previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    previews: list[str] = []
    warnings: list[WarningRecord] = []
    try:
        if path.suffix.lower() == ".pdf":
            import pypdfium2 as pdfium  # type: ignore

            document = pdfium.PdfDocument(str(path))
            for index in range(min(len(document), page_count)):
                target = preview_dir / f"page-{index + 1:04}.png"
                page = document[index]
                try:
                    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
                    try:
                        page.render(scale=2).to_pil().save(temporary, "PNG", optimize=True)
                        os.replace(temporary, target)
                    finally:
                        temporary.unlink(missing_ok=True)
                    previews.append(str(target))
                finally:
                    page.close()
            document.close()
        else:
            from PIL import Image  # type: ignore

            target = preview_dir / "page-0001.png"
            with Image.open(path) as source:
                image = source.convert("RGB")
                image.thumbnail((2400, 2400))
                temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
                try:
                    image.save(temporary, "PNG", optimize=True)
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            previews.append(str(target))
    except Exception as exc:
        warnings.append(WarningRecord("SOURCE_PREVIEW_UNAVAILABLE", f"A local source preview could not be rendered: {exc}"))
    return previews, warnings


def describe_native_pdf_image(image: Any) -> tuple[bytes, str, dict[str, Any]]:
    """Return a viewable embedded-image export plus source-safe metadata.

    pypdf exposes an image XObject's original bytes in the common case. For
    uncommon PDF filters that have no portable filename extension, it also
    exposes a decoded Pillow image. In that case Philon exports a PNG instead
    of leaving a person with an opaque `.bin` file.
    """
    data = bytes(image.data)
    original_name = str(image.name)
    suffix = Path(original_name).suffix.lower()
    metadata: dict[str, Any] = {
        "object_name": original_name,
        "extraction_method": "pypdf-image-xobject",
        "format": "unknown",
        "mime_type": "application/octet-stream",
        "pixel_width": None,
        "pixel_height": None,
        "source_bytes_sha256": hashlib.sha256(data).hexdigest(),
    }
    try:
        from PIL import Image  # type: ignore

        with std_warnings.catch_warnings():
            std_warnings.simplefilter("error", Image.DecompressionBombWarning)
            decoded = image.image
            image_format = (decoded.format or "PNG").upper()
            width, height = decoded.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError(f"Embedded image exceeds Philon's {MAX_IMAGE_PIXELS:,}-pixel safety limit.")
            metadata.update({
                "format": image_format,
                "mime_type": Image.MIME.get(image_format, "application/octet-stream"),
                "pixel_width": width,
                "pixel_height": height,
            })
            supported_suffixes = {".png", ".jpg", ".jpeg", ".jp2", ".tif", ".tiff", ".jpx", ".webp"}
            if suffix not in supported_suffixes:
                encoded = io.BytesIO()
                decoded.save(encoded, format="PNG")
                data = encoded.getvalue()
                suffix = ".png"
                metadata.update({"format": "PNG", "mime_type": "image/png", "normalised_for_export": True})
    except Exception as exc:
        metadata["inspection_error"] = str(exc)
    if not suffix:
        suffix = ".bin"
    return data, suffix, metadata


def extract_native_pdf_assets(path: Path, output_dir: Path) -> tuple[dict[str, Any] | None, list[WarningRecord]]:
    """Extract native PDF image streams with byte/hash/page provenance.

    This is intentionally extraction, not image understanding: captions and
    alternatives remain source-derived review tasks. Assets are bounded to
    protect a local job from pathological PDFs.
    """
    if path.suffix.lower() != ".pdf":
        return None, []
    warnings: list[WarningRecord] = []
    # Keep exported visual assets beside the document exports, as established
    # document-conversion tools do. Page-review rasters remain internal under
    # assets/, while source images are portable deliverables in images/.
    asset_dir = output_dir / "images"
    items: list[dict[str, Any]] = []
    references: dict[str, dict[str, Any]] = {}
    written_bytes = 0
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path), strict=False)
        for page_index, page in enumerate(reader.pages, start=1):
            for image in page.images:
                data, suffix, metadata = describe_native_pdf_image(image)
                digest = hashlib.sha256(data).hexdigest()
                if digest in references:
                    record = references[digest]
                    if page_index not in record["source_pages"]:
                        record["source_pages"].append(page_index)
                    reference = {"page": page_index, "object_name": metadata["object_name"]}
                    if reference not in record["source_references"]:
                        record["source_references"].append(reference)
                    continue
                if len(items) >= MAX_EXTRACTED_ASSETS or written_bytes + len(data) > MAX_EXTRACTED_ASSET_BYTES:
                    warnings.append(WarningRecord("ASSET_EXTRACTION_LIMIT", "Native image extraction reached Philon's bounded asset limit. Remaining source images were not exported.", page=page_index))
                    manifest_path = asset_dir / "manifest.json"
                    atomic_write_text(manifest_path, json.dumps({"schema_version": "1.1", "source": str(path), "items": items, "limits": {"max_assets": MAX_EXTRACTED_ASSETS, "max_bytes": MAX_EXTRACTED_ASSET_BYTES}, "truncated": True}, indent=2, ensure_ascii=False))
                    return {"manifest": str(manifest_path), "items": items}, warnings
                filename = f"asset-{len(items) + 1:04}-{digest[:12]}{suffix}"
                target = asset_dir / filename
                atomic_write_bytes(target, data)
                record = {
                    "id": f"asset-{len(items) + 1:04}", "path": str(target), "bytes": len(data), "bytes_sha256": digest,
                    "source_pages": [page_index], "source_references": [{"page": page_index, "object_name": metadata["object_name"]}],
                    "original_name": metadata["object_name"], "kind": "native-pdf-image", **metadata,
                }
                items.append(record)
                references[digest] = record
                written_bytes += len(data)
        manifest = {"schema_version": "1.1", "source": str(path), "items": items, "limits": {"max_assets": MAX_EXTRACTED_ASSETS, "max_bytes": MAX_EXTRACTED_ASSET_BYTES}}
        manifest_path = asset_dir / "manifest.json"
        atomic_write_text(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False))
        return {"manifest": str(manifest_path), "items": items}, warnings
    except Exception as exc:
        warnings.append(WarningRecord("ASSET_EXTRACTION_UNAVAILABLE", f"Native PDF image streams could not be extracted safely: {exc}"))
        return None, warnings


def render_source_overlay_diagnostics(ir: dict[str, Any], output_dir: Path) -> list[str]:
    """Export transparent SVG geometry overlays for deterministic visual review.

    These are diagnostic source maps, not pixel-diff claims. A block is drawn
    only when its source rectangle was actually measured.
    """
    overlay_dir = output_dir / "assets" / "overlays"
    overlays: list[str] = []
    blocks_by_page: dict[str, list[dict[str, Any]]] = {}
    for block in ir["blocks"]:
        if isinstance(block.get("bbox"), dict):
            blocks_by_page.setdefault(block["page"], []).append(block)
    palette = {"heading": "#007a5e", "paragraph": "#d47a00", "table": "#6957d3", "formula": "#bc3c74", "caption": "#3867b7", "citation": "#8e5b13"}
    for page in ir["pages"]:
        page_blocks = blocks_by_page.get(page["id"], [])
        if not page_blocks or not page.get("width") or not page.get("height"):
            continue
        width, height = float(page["width"]), float(page["height"])
        rectangles: list[str] = []
        for block in page_blocks:
            box = block["bbox"]
            if box["coordinate_space"] == "normalized-image":
                x, y = float(box["x0"]) * width, (1 - float(box["y1"])) * height
                rect_width, rect_height = (float(box["x1"]) - float(box["x0"])) * width, (float(box["y1"]) - float(box["y0"])) * height
            elif box["coordinate_space"] == "pdf-page-points":
                x, y = float(box["x0"]), height - float(box["y1"])
                rect_width, rect_height = float(box["x1"]) - float(box["x0"]), float(box["y1"]) - float(box["y0"])
            else:
                continue
            colour = palette.get(block["type"], "#6e7f75")
            rectangles.append(f'<rect data-philon-id="{html.escape(block["id"], quote=True)}" x="{x:.3f}" y="{y:.3f}" width="{rect_width:.3f}" height="{rect_height:.3f}" fill="{colour}" fill-opacity="0.12" stroke="{colour}" stroke-width="1.5" />')
        if not rectangles:
            continue
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.3f} {height:.3f}" role="img" aria-label="Philon measured source overlay for page {page["number"]}">' + "".join(rectangles) + "</svg>\n"
        target = overlay_dir / f"page-{page['number']:04}.svg"
        atomic_write_text(target, svg)
        overlays.append(str(target))
    return overlays


def attach_native_pdf_assets_to_ir(ir: dict[str, Any], extracted_assets: dict[str, Any] | None, output_dir: Path) -> None:
    """Attach portable, source-grounded native image records before rendering.

    The extraction manifest retains operational absolute paths for the local UI.
    The IR and document exports use only paths relative to the export root, so a
    complete export folder can be copied without losing its image references.
    """
    if not extracted_assets:
        return
    manifest = extracted_assets.get("manifest")
    native_images: list[dict[str, Any]] = []
    for item in extracted_assets.get("items", []):
        try:
            relative_path = Path(item["path"]).relative_to(output_dir).as_posix()
        except (KeyError, TypeError, ValueError):
            continue
        record = {
            "id": item.get("id"),
            "relative_path": relative_path,
            "bytes": item.get("bytes"),
            "bytes_sha256": item.get("bytes_sha256"),
            "source_pages": item.get("source_pages", []),
            "source_references": item.get("source_references", []),
            "original_name": item.get("original_name"),
            "kind": item.get("kind", "native-pdf-image"),
            "format": item.get("format"),
            "mime_type": item.get("mime_type"),
            "pixel_width": item.get("pixel_width"),
            "pixel_height": item.get("pixel_height"),
            "extraction_method": "native-pdf-image-stream",
        }
        native_images.append(record)
    artifacts = ir.setdefault("document_artifacts", {})
    artifacts["native_images"] = native_images
    if manifest:
        try:
            artifacts["native_image_manifest"] = Path(manifest).relative_to(output_dir).as_posix()
        except (TypeError, ValueError):
            pass


def write_output_manifest(output_dir: Path, ir: dict[str, Any], outputs: dict[str, Any]) -> Path:
    """Describe a completed conversion bundle using stable relative paths.

    The manifest is written last, after every selected export and asset has
    been atomically committed. It intentionally excludes itself and temporary
    files, so its file list can be hashed and independently checked without a
    recursive self-reference.
    """
    manifest_path = output_dir / "philon-output-manifest.json"
    files = []
    for candidate in sorted(output_dir.rglob("*")):
        if not candidate.is_file() or candidate == manifest_path or candidate.name.endswith(".tmp"):
            continue
        files.append({
            "path": candidate.relative_to(output_dir).as_posix(),
            "bytes": candidate.stat().st_size,
            "sha256": sha256_file(candidate),
        })
    manifest = {
        "schema_version": "1.0",
        "document_id": ir["document"]["id"],
        # Early alpha IRs may predate preflight hashes. Keep those reviews
        # exportable, but make the missing integrity datum explicit.
        "source_sha256": ir["document"].get("source", {}).get("bytes_sha256"),
        "pipeline": {"engine": ENGINE_VERSION, "ir_version": IR_VERSION, "profile": ir["document"].get("pipeline", {}).get("profile")},
        "declared_outputs": sorted(key for key in outputs if key != "manifest"),
        "files": files,
    }
    atomic_write_text(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest_path


def write_outputs(ir: dict[str, Any], warnings: list[WarningRecord], timings: list[Timing], output_dir: Path, cached: bool, selected_outputs: Iterable[str] | None = None) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = set(selected_outputs or ("machine", "markdown", "html", "ir", "chunks", "evidence", "table_csv", "assets", "manifest"))
    base = safe_slug(Path(ir["document"]["source"]["filename"]).stem)
    paths = {
        "markdown": output_dir / f"{base}.md",
        "html": output_dir / f"{base}.html",
        "ir": output_dir / f"{base}.philon.json",
        "marker_json": output_dir / f"{base}.marker.json",
        "chunks": output_dir / f"{base}.chunks.json",
        "embeddings": output_dir / f"{base}.embeddings.json",
        "evidence": output_dir / f"{base}.evidence.json",
    }
    if "markdown" in selected:
        atomic_write_text(paths["markdown"], render_markdown(ir))
    if "html" in selected:
        atomic_write_text(paths["html"], render_html(ir, include_facsimiles=(output_dir / "assets" / "page-previews").exists()))
    if "ir" in selected:
        atomic_write_text(paths["ir"], json.dumps(ir, indent=2, ensure_ascii=False))
    if "marker_json" in selected:
        atomic_write_text(paths["marker_json"], json.dumps(render_marker_style_json(ir, output_dir), indent=2, ensure_ascii=False))
    machine_root: Path | None = write_machine_package(ir, output_dir) if "machine" in selected else None
    chunks = render_chunks(ir)
    if "chunks" in selected:
        atomic_write_text(paths["chunks"], json.dumps(chunks, indent=2, ensure_ascii=False))
    if "embeddings" in selected:
        embeddings, embedding_warning = render_bge_embeddings(chunks)
        if embedding_warning:
            warnings.append(embedding_warning)
        elif embeddings is not None:
            atomic_write_text(paths["embeddings"], json.dumps(embeddings, ensure_ascii=False))
    table_dir = output_dir / "tables"
    table_paths: list[str] = []
    table_manifest: list[dict[str, Any]] = []
    for group in table_export_groups(ir["blocks"]) if "table_csv" in selected else []:
        table_dir.mkdir(exist_ok=True)
        target = table_dir / f"{group['id']}.csv"
        atomic_write_text(target, "\n".join(",".join('"' + cell.replace('"', '""') + '"' for cell in row) for row in group["rows"]) + "\n")
        table_paths.append(str(target))
        table_manifest.append({"path": str(target), "source_block_ids": group["source_block_ids"], "rows": len(group["rows"]), "cross_page": len(group["source_block_ids"]) > 1})
    exported = {kind: str(path) for kind, path in paths.items() if kind in selected}
    if machine_root is not None:
        exported["machine"] = str(machine_root)
    if table_paths:
        exported["table_csv"] = table_paths
        manifest_path = table_dir / "manifest.json"
        atomic_write_text(manifest_path, json.dumps({"schema_version": "1.0", "tables": table_manifest}, indent=2, ensure_ascii=False))
        exported["table_csv_manifest"] = str(manifest_path)
    if "embeddings" in selected and paths["embeddings"].exists():
        exported["embeddings"] = str(paths["embeddings"])
    if "evidence" in selected:
        atomic_write_text(paths["evidence"], json.dumps(evidence_report(ir, warnings, timings, cached), indent=2, ensure_ascii=False))
    if "manifest" in selected:
        exported["manifest"] = str(write_output_manifest(output_dir, ir, {key: None for key in selected}))
    return exported


def cache_path(cache_dir: Path, content_hash: str, profile: str) -> Path:
    return cache_dir / f"{content_hash}-{profile.lower()}-{safe_slug(ENGINE_VERSION)}.json"


def convert_file(path: Path, profile: str, output_root: Path, cache_root: Path, cache_policy: str = "use", outputs: Iterable[str] | None = None, progress: Any | None = None) -> dict[str, Any]:
    """Convert one file while reporting conservative, truthful milestones."""
    report = progress or (lambda _stage, _percent, _message: None)
    report("validating", 4, "Validating the local source")
    preflight_input(path)
    if cache_policy not in {"use", "bypass", "refresh"}:
        raise ValueError("Cache policy must be use, bypass, or refresh.")
    content_hash = sha256_file(path)
    cache_file = cache_path(cache_root, content_hash, profile)
    cached = cache_policy == "use" and cache_file.exists()
    if cached:
        report("cache", 28, "Reusing verified local conversion data")
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        ir = payload["ir"]
        warnings = [WarningRecord(**warning) for warning in payload["warnings"]]
        timings = [Timing(**timing) for timing in payload["timings"]]
        validate_ir(ir)
    else:
        report("extracting", 22, "Extracting source structure locally")
        ir, warnings, timings = make_ir(path, profile)
        if cache_policy != "bypass":
            report("caching", 67, "Saving local conversion evidence")
            cache_root.mkdir(parents=True, exist_ok=True)
            atomic_write_text(cache_file, json.dumps({"ir": ir, "warnings": [asdict(warning) for warning in warnings], "timings": [asdict(timing) for timing in timings]}, ensure_ascii=False))
    destination = output_root / f"{safe_slug(path.stem)}-{content_hash[:12]}-{profile.lower()}"
    selected_outputs = set(outputs or ("machine", "markdown", "html", "ir", "chunks", "evidence", "table_csv", "assets", "manifest"))
    preview_paths: list[str] = []
    extracted_assets: dict[str, Any] | None = None
    overlay_paths: list[str] = []
    needs_source_previews = bool({"assets", "html", "machine"} & selected_outputs)
    if needs_source_previews:
        report("previews", 76, "Rendering source previews for review")
        preview_paths, preview_warnings = render_source_previews(path, destination, len(ir["pages"]))
        warnings.extend(preview_warnings)
    if "assets" in selected_outputs:
        report("assets", 86, "Extracting native source assets")
        extracted_assets, extraction_warnings = extract_native_pdf_assets(path, destination)
        warnings.extend(extraction_warnings)
        attach_native_pdf_assets_to_ir(ir, extracted_assets, destination)
        overlay_paths = render_source_overlay_diagnostics(ir, destination)
    report("publishing", 94, "Writing evidence-linked exports")
    output_paths = write_outputs(ir, warnings, timings, destination, cached, selected_outputs)
    if needs_source_previews:
        output_paths["assets"] = preview_paths
        output_paths["overlay_diagnostics"] = overlay_paths
        if extracted_assets is not None:
            output_paths["extracted_assets"] = extracted_assets
    if "manifest" in selected_outputs:
        output_paths["manifest"] = str(write_output_manifest(destination, ir, output_paths))
    report("complete", 100, "Conversion complete")
    return {
        "id": str(uuid.uuid4()),
        "source_path": str(path),
        "status": "completed_with_warnings" if warnings else "completed",
        "outputs": output_paths,
        "evidence_report": evidence_report(ir, warnings, timings, cached),
        "warnings": [asdict(warning) for warning in warnings],
        "pages": ir["pages"],
        "blocks": ir["blocks"],
        "timings": [asdict(timing) for timing in timings],
        "cache_hit": cached,
        "cache_policy": cache_policy,
        "created_at": now(),
    }


def action_convert(request: dict[str, Any], progress: Any | None = None) -> dict[str, Any]:
    config = request["config"]
    files = [Path(item) for item in config.get("input_paths", [])]
    if not files:
        raise ValueError("A job must include at least one local input path.")
    profile = config.get("profile", "Balanced")
    if profile not in {"Fast", "Balanced", "Verified"}:
        raise ValueError("Profile must be Fast, Balanced, or Verified.")
    cache_policy = config.get("cache_policy", "use")
    outputs = config.get("outputs", ["machine", "markdown", "html", "ir", "chunks", "evidence", "table_csv", "assets", "manifest"])
    if not isinstance(outputs, list) or not outputs:
        raise ValueError("At least one output must be requested.")
    if profile == "Verified" and "embeddings" not in outputs:
        outputs.append("embeddings")
    root = Path(config.get("workspace_dir") or Path.home() / "Library" / "Application Support" / "Philon")
    output_root = root / "exports"
    cache_root = root / "cache"
    results, failures = [], []
    total = len(files)
    for index, item in enumerate(files):
        def report_file(stage: str, percent: int, message: str, *, index: int = index, item: Path = item) -> None:
            if progress:
                progress({"current": index + 1, "total": total, "percent": round(((index + percent / 100) / total) * 100), "stage": stage, "message": message, "source_path": str(item)})
        try:
            report_file("starting", 1, f"Starting {item.name}")
            results.append(convert_file(item, profile, output_root, cache_root, cache_policy, outputs, report_file))
        except Exception as exc:  # batch items fail independently
            failures.append({"source_path": str(item), "error": str(exc)})
            report_file("failed", 100, f"Could not convert {item.name}")
    return {"id": str(uuid.uuid4()), "profile": profile, "local_only": True, "outputs": outputs, "cache_policy": cache_policy, "local_repair_requested": bool(config.get("local_repair", False)), "results": results, "failures": failures, "created_at": now()}


def action_preflight(request: dict[str, Any], progress: Any | None = None) -> dict[str, Any]:
    files = [Path(item) for item in request.get("config", {}).get("input_paths", [])]
    if not files:
        raise ValueError("Choose at least one PDF or image.")
    items: list[dict[str, Any]] = []
    total = len(files)
    for index, path in enumerate(files):
        if progress:
            progress({"current": index + 1, "total": total, "percent": round((index / total) * 100), "stage": "validating", "message": f"Inspecting {path.name}", "source_path": str(path)})
        try:
            inspection = preflight_input(path)
            items.append({"source_path": str(path), "status": "ready", "preflight": inspection, "route": "manual-local-recognition-required" if inspection["kind"] == "image" else "native-text-pending"})
        except Exception as exc:
            items.append({"source_path": str(path), "status": "blocked", "error": str(exc)})
        if progress:
            progress({"current": index + 1, "total": total, "percent": round(((index + 1) / total) * 100), "stage": "complete", "message": f"Inspected {path.name}", "source_path": str(path)})
    return {"created_at": now(), "items": items, "local_only": True}


def local_model_fingerprint(model_path: Path) -> str:
    """Fingerprint lightweight model manifests without scanning multi-GB weights."""
    digest = hashlib.sha256()
    included = 0
    for name in ("config.json", "model.safetensors.index.json", "tokenizer_config.json"):
        candidate = model_path / name
        if candidate.exists() and candidate.is_file():
            digest.update(name.encode("utf-8"))
            digest.update(candidate.read_bytes())
            included += 1
    # GGUF releases typically have no JSON manifest. Record their exact local
    # artefact names and sizes, without needlessly rereading tens of gigabytes
    # before every user-requested repair.
    if not included:
        for candidate in sorted(model_path.glob("*.gguf")):
            digest.update(candidate.name.encode("utf-8"))
            digest.update(str(candidate.stat().st_size).encode("ascii"))
            included += 1
    if not included:
        raise ValueError("The selected local model has no recognizable manifest files.")
    return f"sha256:{digest.hexdigest()}"


def find_mlx_vlm_python() -> Path | None:
    """Find an already-installed MLX-VLM interpreter without installing anything."""
    candidates: list[Path] = []
    configured = os.environ.get("PHILON_MLX_VLM_PYTHON")
    if configured:
        candidates.append(Path(os.path.expanduser(configured)))
    for command in (shutil.which("python3"), str(Path.home() / ".pyenv" / "shims" / "python3")):
        if command:
            candidates.append(Path(command))
    candidates.extend(sorted((Path.home() / ".pyenv" / "versions").glob("*/bin/python3"), reverse=True))
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved in seen or not resolved.is_file() or not os.access(resolved, os.X_OK):
                continue
            seen.add(resolved)
            # This is deliberately a no-GPU probe. Importing MLX would claim a
            # Metal device before the user has actually requested a repair.
            subprocess.run([str(resolved), "--version"], check=True, capture_output=True, text=True, timeout=4)
            return resolved
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def repair_crop(source: Path, page: dict[str, Any], block: dict[str, Any], output_dir: Path) -> Path:
    """Persist the exact locally rendered source region sent to manual OCR."""
    from PIL import Image  # type: ignore

    target_dir = output_dir / "assets" / "repair-crops"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{safe_slug(str(block['id']))}-{uuid.uuid4().hex[:8]}.png"
    box = block.get("bbox") if isinstance(block.get("bbox"), dict) else None
    if source.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium  # type: ignore

        document = pdfium.PdfDocument(str(source))
        pdf_page = document[int(page["number"]) - 1]
        try:
            scale = 300 / 72
            image = pdf_page.render(scale=scale).to_pil().convert("RGB")
        finally:
            pdf_page.close()
            document.close()
        if box and box.get("coordinate_space") == "pdf-page-points":
            left = max(0, int(float(box["x0"]) * scale))
            top = max(0, int((float(page["height"]) - float(box["y1"])) * scale))
            right = min(image.width, max(left + 1, int(float(box["x1"]) * scale)))
            bottom = min(image.height, max(top + 1, int((float(page["height"]) - float(box["y0"])) * scale)))
            image = image.crop((left, top, right, bottom))
    else:
        with Image.open(source) as opened:
            image = opened.convert("RGB")
        if box and box.get("coordinate_space") == "normalized-image":
            left = max(0, int(float(box["x0"]) * image.width))
            top = max(0, int((1 - float(box["y1"])) * image.height))
            right = min(image.width, max(left + 1, int(float(box["x1"]) * image.width)))
            bottom = min(image.height, max(top + 1, int((1 - float(box["y0"])) * image.height)))
            image = image.crop((left, top, right, bottom))
    image.thumbnail((4096, 4096))
    atomic_write_bytes(target, _image_png_bytes(image))
    return target


def _image_png_bytes(image: Any) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def run_olmocr(model_path: Path, crop_path: Path) -> tuple[str, dict[str, Any]]:
    """Run the approved, user-managed MLX OCR pack only after an explicit click."""
    python = find_mlx_vlm_python()
    if python is None:
        raise RuntimeError("No usable local Python runtime was found for MLX-VLM. Set PHILON_MLX_VLM_PYTHON to an existing interpreter with mlx-vlm installed.")
    prompt = "Transcribe exactly the text in this document crop. Preserve reading order and Markdown where clear. Do not infer missing characters; write [unclear] for unreadable text. Return only the transcription."
    environment = os.environ.copy()
    environment.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"})
    process = subprocess.run(
        [str(python), "-m", "mlx_vlm.generate", "--model", str(model_path), "--image", str(crop_path), "--prompt", prompt, "--max-tokens", "2048"],
        check=False, capture_output=True, text=True, timeout=240, env=environment,
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip().replace("\n", " ")
        raise RuntimeError(f"olmOCR did not complete locally ({detail[:280] or f'exit {process.returncode}'}).")
    output = process.stdout.strip()
    if not output:
        raise RuntimeError("olmOCR returned no candidate text; Philon retained the original block.")
    return output, {"python": str(python), "prompt": prompt, "model_manifest_fingerprint": local_model_fingerprint(model_path)}


def clean_vlm_transcript(raw_output: str) -> str:
    """Reject llama.cpp status/progress output rather than offering it as text.

    Some local llama.cpp builds write startup banners and terminal progress bars
    to stdout even in simple I/O mode. A repair candidate must be a document
    answer, never a model-loading log. This filtering is deliberately narrow
    and is followed by a non-empty transcript gate.
    """
    ignored_prefixes = (
        "ggml_", "llama_", "load_", "init:", "main:", "system_info:", "build:", "warning:",
        "build      :", "model      :", "ftype      :", "modalities :", "available commands:",
        "/exit", "/regen", "/clear", "/read", "/glob", "/image", "/video", "loaded media from", "[ prompt:", "exiting...",
    )
    ignored_exact = {"loading model...", "loading model", "model loaded", "assistant", "```markdown", "```text", "```"}
    progress_characters = set(" ▁▂▃▄▅▆▇█▀▏▎▍▌▋▊▉▓▒░")
    cleaned: list[str] = []
    for raw_line in raw_output.replace("\r", "\n").splitlines():
        line = raw_line.strip()
        normalized = line.lower()
        if not line or normalized in ignored_exact or normalized.startswith(ignored_prefixes) or line.startswith("> "):
            continue
        if line.startswith("[") and "%" in line and "]" in line:
            continue
        if set(line).issubset(progress_characters):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def assess_repair_candidate(text: str, repair_mode: str) -> dict[str, Any]:
    """Attach deterministic quality signals without judging a model as correct.

    A syntactically weak table or formula remains reviewable evidence, but the
    UI and downstream exports can make clear that it is not ready to restore.
    This is deliberately a format check, never a claim that the visual source
    was read accurately.
    """
    issues: list[str] = []
    if not text.strip():
        issues.append("Candidate is empty.")
    if repair_mode == "table" and table_rows(text) is None:
        issues.append("Candidate is not a structurally valid Markdown table.")
    if repair_mode == "formula" and not is_formula(text):
        issues.append("Candidate does not look like a bounded mathematical formula.")
    if "\ufffd" in text:
        issues.append("Candidate contains replacement glyphs.")
    return {"format": repair_mode, "status": "format-valid" if not issues else "review-required", "issues": issues}


def run_qwen38(model_path: Path, crop_path: Path, repair_mode: str = "transcription") -> tuple[str, dict[str, Any]]:
    """Use the local multimodal Qwen 3.8 GGUF only for explicit repair."""
    model = next(iter(sorted(model_path.glob("Qwen3.8-27B-*.gguf"))), None)
    projector = next(iter(sorted(model_path.glob("mmproj-*.gguf"))), None)
    executable = shutil.which("llama-cli") or str(Path.home() / ".local" / "bin" / "llama-cli")
    if model is None or projector is None:
        raise RuntimeError("The local Qwen 3.8 GGUF or its multimodal projector is incomplete.")
    if not Path(executable).exists():
        raise RuntimeError("llama-cli is not installed locally, so Qwen 3.8 cannot run offline.")
    prompts = {
        "transcription": "Read this selected document region carefully. Return only a faithful Markdown transcription in visual reading order. Preserve tables and equations where unambiguous. Never fill gaps: use [unclear] for text you cannot read.",
        "table": "Read this selected document table region carefully. Return only one GitHub-Flavored Markdown table: a header row, a separator row, then data rows in visual reading order. Preserve empty cells. Never infer a value; use [unclear] in an unreadable cell.",
        "formula": "Read this selected mathematical region carefully. Return only a faithful LaTeX-style formula transcription. Preserve symbols, subscripts, superscripts, fractions, and limits where visible. Never infer missing notation; use \\text{[unclear]} for unreadable parts.",
    }
    prompt = prompts[repair_mode]
    environment = os.environ.copy()
    environment.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"})
    process = subprocess.run(
        [executable, "--model", str(model), "--mmproj", str(projector), "--image", str(crop_path), "--prompt", prompt, "--n-predict", "2048", "--ctx-size", "8192", "--temp", "0", "--chat-template-kwargs", '{"enable_thinking":false}', "--single-turn", "--simple-io", "--no-display-prompt", "--no-perf", "--log-disable", "--color", "off"],
        check=False, capture_output=True, text=True, timeout=420, env=environment,
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip().replace("\n", " ")
        raise RuntimeError(f"Qwen 3.8 did not complete locally ({detail[:280] or f'exit {process.returncode}'}).")
    output = clean_vlm_transcript(process.stdout)
    if not output:
        raise RuntimeError("Qwen 3.8 returned only runtime status/progress output, not a document transcription. Philon retained the original block.")
    return output, {"executable": executable, "prompt": prompt, "repair_mode": repair_mode, "model_manifest_fingerprint": local_model_fingerprint(model_path), "multimodal_projector": projector.name}


def parse_embedding_payload(payload: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate llama.cpp's OpenAI-style embedding response before export."""
    response = json.loads(payload)
    rows = response.get("data") if isinstance(response, dict) else None
    if not isinstance(rows, list) or len(rows) != len(chunks):
        raise ValueError("The local embedding runtime returned an unexpected number of vectors.")
    vectors: list[dict[str, Any]] = []
    dimensions: int | None = None
    ordered = sorted(rows, key=lambda item: item.get("index", -1) if isinstance(item, dict) else -1)
    for index, row in enumerate(ordered):
        if not isinstance(row, dict) or row.get("index") != index:
            raise ValueError("The local embedding runtime returned invalid vector indexes.")
        vector = row.get("embedding") if isinstance(row, dict) else None
        if not isinstance(vector, list) or not vector or not all(isinstance(value, (int, float)) for value in vector):
            raise ValueError("The local embedding runtime returned a malformed vector.")
        if dimensions is None:
            dimensions = len(vector)
        if len(vector) != dimensions:
            raise ValueError("The local embedding runtime returned inconsistent vector dimensions.")
        vectors.append({"id": chunks[index]["id"], "source_block_ids": chunks[index]["source_block_ids"], "embedding": vector})
    return {"dimensions": dimensions or 0, "vectors": vectors}


def render_bge_embeddings(chunks: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, WarningRecord | None]:
    """Embed Philon chunks locally in one bounded BGE-M3 batch."""
    pack = next((item for item in model_status()["packs"] if item["id"] == "bge-m3-local-candidate"), None)
    if not pack or not pack["approved"] or not pack["available_locally"] or not pack.get("local_path"):
        return None, WarningRecord("EMBEDDING_PACK_UNAVAILABLE", "Verified could not find the approved local BGE-M3 pack. Chunks were exported without vectors.")
    executable = shutil.which("llama-embedding") or str(Path.home() / ".local" / "bin" / "llama-embedding")
    model = next(iter(sorted(Path(pack["local_path"]).glob("*.gguf"))), None)
    if model is None or not Path(executable).exists():
        return None, WarningRecord("EMBEDDING_RUNTIME_UNAVAILABLE", "Verified could not find the local BGE-M3 embedding runtime. Chunks were exported without vectors.")
    if not chunks:
        return {"schema_version": "1.0", "model": pack["id"], "dimensions": 0, "vectors": []}, None
    separator = "<|philon-chunk-separator|>"
    if any(separator in chunk["text"] for chunk in chunks):
        return None, WarningRecord("EMBEDDING_INPUT_AMBIGUOUS", "A local chunk contains Philon's embedding separator. Chunks were exported without vectors.")
    environment = os.environ.copy()
    environment.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"})
    try:
        process = subprocess.run([executable, "--model", str(model), "--prompt", separator.join(chunk["text"] for chunk in chunks), "--embd-separator", separator, "--embd-output-format", "json", "--no-perf"], check=False, capture_output=True, text=True, timeout=240, env=environment)
        if process.returncode != 0:
            detail = (process.stderr or process.stdout).strip().replace("\n", " ")
            raise RuntimeError(detail[:280] or f"exit {process.returncode}")
        parsed = parse_embedding_payload(process.stdout, chunks)
        return {"schema_version": "1.0", "model": pack["id"], "runtime": "llama-embedding", "model_artifact_fingerprint": local_model_fingerprint(Path(pack["local_path"])), **parsed}, None
    # A non-zero local embedding process is deliberately converted into an
    # evidence warning. It must never abort an otherwise valid document
    # conversion or cause Philon to manufacture a vector sidecar.
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        return None, WarningRecord("EMBEDDING_FAILED", f"Local BGE-M3 embedding was not produced: {exc}. Chunks were exported without vectors.")


def action_repair(request: dict[str, Any], progress: Any | None = None) -> dict[str, Any]:
    repair_mode = request.get("repair_mode", "transcription")
    if repair_mode not in {"transcription", "table", "formula"}:
        raise ValueError("Repair mode must be transcription, table, or formula.")
    packs = model_status()["packs"]
    pack = next((item for pack_id in ("qwen3.8-27b-local-repair", "olmocr-2-7b-local-candidate") for item in packs if item["id"] == pack_id and item["approved"] and item["available_locally"] and item.get("local_path")), None)
    if not pack or not pack["approved"] or not pack["available_locally"] or not pack.get("local_path"):
        return {"status": "unavailable", "message": "No approved local repair pack was found. Philon retained the source evidence and did not fabricate a repair.", "block_id": request.get("block_id")}
    ir_path = Path(request.get("ir_path", ""))
    block_id = request.get("block_id")
    if not ir_path.exists() or ir_path.suffix != ".json":
        raise ValueError("A valid local Philon IR export is required for manual repair.")
    ir = json.loads(ir_path.read_text(encoding="utf-8"))
    block = next((item for item in ir.get("blocks", []) if item.get("id") == block_id), None)
    if not block:
        raise ValueError("The requested block was not found in this Philon IR.")
    page = next((item for item in ir.get("pages", []) if item.get("id") == block.get("page")), None)
    source = Path(ir.get("document", {}).get("source", {}).get("path", ""))
    if not page or not source.exists():
        raise ValueError("The source page needed for manual repair is no longer available locally.")
    try:
        if progress:
            progress({"stage": "preparing", "message": "Preparing the selected source region", "percent": 8, "indeterminate": True, "source_path": str(source)})
        crop_path = repair_crop(source, page, block, ir_path.parent)
        if progress:
            progress({"stage": "recognising", "message": "Running the local repair model", "percent": 30, "indeterminate": True, "source_path": str(source)})
        text, run = run_qwen38(Path(pack["local_path"]), crop_path, repair_mode) if pack["id"] == "qwen3.8-27b-local-repair" else run_olmocr(Path(pack["local_path"]), crop_path)
    except Exception as exc:
        return {"status": "unavailable", "message": f"Manual {pack['id']} repair was not run: {exc}", "block_id": block_id}
    evidence = block.setdefault("evidence", {})
    candidate = {"kind": pack["id"], "text": text, "selected": False, "created_at": now(), "source_crop": str(crop_path), "source_crop_sha256": sha256_file(crop_path), "model": pack["id"], "repair_mode": repair_mode, "quality": assess_repair_candidate(text, repair_mode), **run}
    evidence.setdefault("alternatives", []).append(candidate)
    evidence.setdefault("repair_history", []).append({"kind": "manual-local-ocr", "status": "candidate-retained", "at": now(), "candidate_kind": candidate["kind"], "repair_mode": repair_mode, "source_crop": str(crop_path)})
    warnings_path = ir_path.with_name(ir_path.name.replace(".philon.json", ".evidence.json"))
    warnings = [WarningRecord(**item) for item in json.loads(warnings_path.read_text(encoding="utf-8")).get("warnings", [])] if warnings_path.exists() else []
    outputs = write_outputs(ir, warnings, [Timing("manual-olmocr", 0)], ir_path.parent, cached=False)
    if progress:
        progress({"stage": "complete", "message": "Repair candidate is ready", "percent": 100, "source_path": str(source)})
    quality_note = " Its format needs review before restoration." if candidate["quality"]["issues"] else " Its format passed deterministic checks; still compare it with the source before restoration."
    return {"status": "candidate", "message": f"{pack['id']} completed locally as a {repair_mode} candidate.{quality_note}", "block_id": block_id, "block": block, "candidate": candidate, "outputs": outputs}


def action_review(request: dict[str, Any]) -> dict[str, Any]:
    """Persist a human review decision beside the exported IR.

    Review never overwrites the source candidate. An edit becomes a new selected
    candidate with the native output retained in evidence, so it is reversible.
    """
    ir_path = Path(request.get("ir_path", ""))
    block_id = request.get("block_id")
    action = request.get("review_action")
    allowed = {"accept", "edit", "restore_candidate", "rerun_region", "ignore_warning"}
    if action not in allowed:
        raise ValueError("Review action must be accept, edit, restore_candidate, rerun_region, or ignore_warning.")
    if not ir_path.exists() or ir_path.suffix != ".json":
        raise ValueError("A valid local Philon IR export is required for review.")
    ir = json.loads(ir_path.read_text(encoding="utf-8"))
    block = next((item for item in ir.get("blocks", []) if item.get("id") == block_id), None)
    if not block:
        raise ValueError("The requested block was not found in this Philon IR.")
    evidence = block.setdefault("evidence", {})
    history = evidence.setdefault("repair_history", [])
    review = {"action": action, "at": now(), "actor": "local-user"}
    if action == "edit":
        replacement = str(request.get("text", "")).strip()
        if not replacement:
            raise ValueError("An edit requires replacement text.")
        evidence.setdefault("alternatives", []).append({"text": block["text"], "kind": "native-source", "selected": False})
        block["text"] = replacement
        review["selected_candidate"] = "manual-edit"
    elif action == "restore_candidate":
        alternatives = evidence.get("alternatives", [])
        if not alternatives:
            raise ValueError("There is no retained alternative to restore.")
        candidate_index = request.get("candidate_index")
        if candidate_index is None:
            selected = alternatives[-1]
        elif isinstance(candidate_index, int) and 0 <= candidate_index < len(alternatives):
            selected = alternatives[candidate_index]
        else:
            raise ValueError("The requested retained candidate is no longer available.")
        block["text"] = selected["text"]
        review["selected_candidate"] = selected.get("kind", "retained-candidate")
        inferred_type, _ = classify_block(block["text"])
        if inferred_type in {"table", "formula"}:
            block["type"] = inferred_type
            block["level"] = None
            review["structure_promoted"] = inferred_type
            evidence.setdefault("validation", []).append(f"user-selected-{inferred_type}-candidate")
        resolve_cross_page_tables(ir.get("blocks", []))
    elif action == "rerun_region":
        review["status"] = "not-run"
        review["reason"] = "No approved local recognition pack is installed. The request was retained for later review."
    block["review"] = review
    history.append({"kind": "review", **review})
    warnings_path = ir_path.with_name(ir_path.name.replace(".philon.json", ".evidence.json"))
    warnings: list[WarningRecord] = []
    if warnings_path.exists():
        report = json.loads(warnings_path.read_text(encoding="utf-8"))
        warnings = [WarningRecord(**item) for item in report.get("warnings", [])]
    outputs = write_outputs(ir, warnings, [Timing("review-update", 0)], ir_path.parent, cached=False)
    return {"status": "recorded", "block_id": block_id, "block": block, "review": review, "outputs": outputs}


def action_models() -> dict[str, Any]:
    return model_status()


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, expected_token: str) -> None:
    try:
        raw = await reader.readline()
        request = json.loads(raw.decode("utf-8"))
        if not expected_token or not isinstance(request.get("token"), str) or not hmac.compare_digest(request["token"], expected_token):
            raise PermissionError("Unauthenticated local engine request.")
        action = request.get("action")
        if action == "convert":
            response = {"ok": True, "data": action_convert(request)}
        elif action == "preflight":
            response = {"ok": True, "data": action_preflight(request)}
        elif action == "repair":
            response = {"ok": True, "data": action_repair(request)}
        elif action == "review":
            response = {"ok": True, "data": action_review(request)}
        elif action == "models":
            response = {"ok": True, "data": action_models()}
        elif action == "health":
            response = {"ok": True, "data": {"engine": ENGINE_VERSION, "local_only": True, "review_actions": ["accept", "edit", "restore_candidate", "rerun_region", "ignore_warning"]}}
        else:
            response = {"ok": False, "error": f"Unknown action: {action}"}
    except Exception as exc:
        response = {"ok": False, "error": str(exc)}
    writer.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def serve(socket_path: Path, token: str) -> None:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()
    server = await asyncio.start_unix_server(lambda reader, writer: handle(reader, writer, token), path=str(socket_path))
    os.chmod(socket_path, 0o600)
    async with server:
        await server.serve_forever()


def main() -> None:
    global VISION_HELPER
    parser = argparse.ArgumentParser(description="Philon local engine")
    parser.add_argument("--socket", required=True, help="Unix-domain socket path")
    parser.add_argument("--token", required=True, help="Per-session bridge token supplied only by the host process")
    parser.add_argument("--vision-helper", help="Bundled Apple Vision OCR helper path")
    args = parser.parse_args()
    VISION_HELPER = Path(args.vision_helper) if args.vision_helper else None
    try:
        asyncio.run(serve(Path(args.socket), args.token))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
