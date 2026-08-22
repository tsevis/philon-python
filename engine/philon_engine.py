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
import ctypes
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
import statistics
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

#: The shape of the evidence an export carries. Raised when a field is added
#: or changed, independently of the application version and of the engine
#: contract, so a consumer can tell what it is reading. 0.3.0 added the page's
#: own /Rotate, the source-declared links measured onto a block, and the page
#: selection a conversion covers. 0.4.0 added the tables recovered from the
#: rules a page draws: each page's `ruled_tables` records the grids measured on
#: it, and a block enclosed by one carries the cells they prove in `table`.
IR_VERSION = "0.4.0"
ENGINE_VERSION = "philon-0.2.0"
MAX_INPUT_BYTES = 500 * 1024 * 1024
MAX_PDF_PAGES = 2_000
MAX_IMAGE_PIXELS = 100_000_000
MAX_EXTRACTED_ASSETS = 1_000
MAX_EXTRACTED_ASSET_BYTES = 250 * 1024 * 1024
SUPPORTED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp"}
DEFAULT_OUTPUTS = ("machine", "markdown", "html", "ir", "chunks", "evidence", "table_csv", "assets", "manifest")
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
        encryption = "none"
        try:
            from pypdf import PdfReader  # type: ignore

            previous_logging_threshold = logging.root.manager.disable
            logging.disable(logging.CRITICAL)
            try:
                reader = PdfReader(str(path), strict=False)
                if reader.is_encrypted:
                    # A publisher PDF is commonly "encrypted" with an EMPTY user
                    # password: it carries permission flags but opens for anyone,
                    # and PDFium reads it without being given a password at all.
                    # Refusing it would refuse a document the user can already
                    # read, so the empty password is tried and the outcome is
                    # recorded as evidence rather than assumed either way.
                    if not reader.decrypt(""):
                        raise ValueError("This PDF needs a password. Philon does not ask for one and did not send the file to a parser.")
                    encryption = "opened-with-empty-user-password"
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
            "encryption": encryption,
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


def parse_page_selection(value: Any) -> tuple[int, ...] | None:
    """Read a 1-based page selection, or None meaning the document entire.

    Accepts "1-5,8" or a list of numbers. Pages are counted from one because
    that is how they are printed on the page, recorded in the evidence and
    named in the exports; an absent or empty selection is not a selection.
    """
    if value is None or value == "" or value == []:
        return None
    numbers: set[int] = set()
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        match = re.fullmatch(r"(\d{1,6})(?:\s*-\s*(\d{1,6}))?", text)
        if not match:
            raise ValueError(f"Page selection '{text}' is not a page or a page range.")
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) else first
        if first < 1 or last < first:
            raise ValueError(f"Page selection '{text}' is not a page or a page range.")
        numbers.update(range(first, last + 1))
    return tuple(sorted(numbers)) or None


def compact_page_selection(selection: Iterable[int]) -> str:
    """A selection written back in its shortest form: (1, 2, 3, 8) -> '1-3,8'."""
    parts: list[str] = []
    run_start: int | None = None
    previous: int | None = None
    for number in selection:
        if run_start is None:
            run_start = previous = number
        elif previous is not None and number == previous + 1:
            previous = number
        else:
            parts.append(str(run_start) if run_start == previous else f"{run_start}-{previous}")
            run_start = previous = number
    if run_start is not None:
        parts.append(str(run_start) if run_start == previous else f"{run_start}-{previous}")
    return ",".join(parts)


def page_selection_token(selection: tuple[int, ...] | None) -> str:
    """A short, stable name for a selection, for cache keys and export paths.

    A conversion of part of a document must never be served for, or written
    over, a conversion of the whole of it, so the selection is part of both
    names. The compact form is kept where it stays short enough to read.
    """
    if not selection:
        return ""
    compact = compact_page_selection(selection)
    if len(compact) <= 24:
        return "pages-" + compact.replace(",", "_")
    return "pages-" + hashlib.sha256(compact.encode("utf-8")).hexdigest()[:12]


def validate_page_selection(selection: tuple[int, ...] | None, page_count: int | None) -> None:
    """Refuse a page this document does not have, before anything is extracted."""
    if not selection or not page_count:
        return
    beyond = tuple(number for number in selection if number > page_count)
    if beyond:
        raise ValueError(
            f"This document has {page_count} pages; the selection asks for "
            f"{compact_page_selection(beyond)}."
        )


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
    """Normalise likely running headers/footers without changing source text.

    The page number a running head carries is set aside before counting. A head
    printed as "Symmetries of Culture   47" is a different string on every page
    it appears on, so counted literally it never repeats, never reaches the
    threshold below, and is emitted as body text on every page of the book.
    Only leading or trailing numbering is set aside, never a digit inside the
    words, and only for the comparison: the source line itself is untouched and
    is what any retained artifact still records.
    """
    collapsed = re.sub(r"\s+", " ", value).strip()
    without_number = re.sub(r"^[\[(]?\d{1,4}[\])]?\s*[.\u00b7:|\u2014\u2013-]?\s+", "", collapsed)
    without_number = re.sub(r"\s+[.\u00b7:|\u2014\u2013-]?\s*[\[(]?\d{1,4}[\])]?$", "", without_number)
    return (without_number or collapsed).casefold()


def is_numeric_source_marker(value: str) -> bool:
    """Recognise isolated PDF footnote/page markers without treating them as prose."""
    return bool(re.fullmatch(r"[.·]?\s*\d{1,4}(?:\s+\d{1,4}){0,5}", value.strip()))


#: How many pages a running-head variant must appear on before it is counted
#: as one side of an alternating pair. Two is not enough -- a sentence can open
#: two pages by chance -- and three is the same floor the whole-document rule
#: already uses.
ALTERNATING_MINIMUM_PAGES = 3

#: How many *consecutive* pages a line must open or close before the run is
#: itself strong enough evidence, whatever share of the document it comes to.
#: A book sets a new running head at every chapter, so no one variant reaches
#: the share threshold however plainly each repeats. Four is one above the
#: three-page floor the other rules use, because three consecutive pages is
#: reachable by a sentence that happens to break the same way twice.
CONSECUTIVE_PAGE_RUN = 4


def longest_page_run(page_indexes: list[int]) -> int:
    """The longest run of consecutive pages in an ascending list of indexes."""
    if not page_indexes:
        return 0
    longest = run = 1
    for previous, following in zip(page_indexes, page_indexes[1:]):
        run = run + 1 if following == previous + 1 else 1
        longest = max(longest, run)
    return longest


def repeated_page_artifacts(source_pages: list[dict[str, Any]]) -> set[str]:
    """Find repeated first/last lines only when the evidence is strong.

    A repeated line is never deleted from the source PDF. It is simply excluded
    from body assembly and retained in document provenance as a page artifact.
    """
    if len(source_pages) < 3:
        return set()
    # Which pages each candidate appeared on, not how many times it was seen.
    # A short page makes lines[:2] and lines[-2:] overlap, and counting the same
    # line twice for one page inflated it against a threshold that is expressed
    # in pages.
    appearances: dict[str, list[int]] = {}
    for index, page in enumerate(source_pages):
        lines = [normalise_artifact(line) for line in page["text"].splitlines() if normalise_artifact(line)]
        for line in dict.fromkeys(lines[:2] + lines[-2:]):
            if 3 <= len(line) <= 130 and not line.isdigit():
                appearances.setdefault(line, []).append(index)
    counts = {line: len(pages_seen) for line, pages_seen in appearances.items()}
    # A running head is commonly set differently on left- and right-hand pages,
    # so each variant appears on about half the pages and NEITHER reaches a
    # 60% threshold. Requiring the strong evidence of a repeat is right; taking
    # that evidence one variant at a time is what let a recto/verso header
    # through on every page of a real paper. Variants are counted together and
    # then each is judged on its own share.
    threshold = max(3, round(len(source_pages) * 0.6))
    artifacts = {line for line, count in counts.items() if count >= threshold}
    alternating = sum(count for line, count in counts.items() if count >= ALTERNATING_MINIMUM_PAGES)
    if alternating >= threshold:
        artifacts |= {line for line, count in counts.items() if count >= ALTERNATING_MINIMUM_PAGES}
    artifacts |= {
        line for line, pages_seen in appearances.items()
        if longest_page_run(pages_seen) >= CONSECUTIVE_PAGE_RUN
    }
    return artifacts


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


def markdown_table_cell(value: str) -> str:
    """Make one cell safe to sit between Markdown's own column separators.

    A recovered cell is whatever the page put in it, and a pipe inside one
    would silently split it into two columns and misalign every row after it.
    """
    return str(value).replace("\\", "\\\\").replace("|", "\\|")


def block_table_rows(block: dict[str, Any]) -> list[list[str]] | None:
    """The rows a table block has, preferring the ones its page proved.

    Rules recovered from the page's own drawing outrank the delimited reading
    of the block's text: the rules are what the producer used to separate the
    columns, while a delimiter is a character that happens to sit between them.
    A ruled table usually has no delimiter at all, so for those two readings
    this is not a tie-break but the only answer.
    """
    recovered = (block.get("table") or {}).get("rows")
    if recovered:
        return [list(row) for row in recovered]
    return table_rows(str(block.get("text", "")))


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
    """Turn Vision lines into conservative, measured reading blocks.

    Vision supplies one observation per line, not paragraph boundaries.  The
    previous exporter therefore treated a complete scan as one block.  This
    uses only measured line positions and keeps Vision's supplied reading
    order: a meaningful vertical gap, a column jump, or a return to the top of
    a page starts a new block.  It deliberately does *not* invent figure
    descriptions or a speculative visual reading order.
    """
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
            flush()
            previous_box = None
            continue
        if is_numeric_source_marker(text):
            flush()
            page.setdefault("numeric_source_markers", []).append({"text": text, "bbox": box_for(line)})
            previous_box = None
            continue
        current_box = box_for(line)
        if current and current_box and previous_box:
            previous_height = float(previous_box["y1"]) - float(previous_box["y0"])
            current_height = float(current_box["y1"]) - float(current_box["y0"])
            # Vision coordinates have a bottom-left origin.  A large positive
            # gap means the next line visibly begins below the prior one.  A
            # large upward jump is a reliable signal that a new column/page
            # region has begun.
            vertical_gap = float(previous_box["y0"]) - float(current_box["y1"])
            moved_upward = float(current_box["y0"]) > float(previous_box["y0"]) + max(0.035, 2.0 * previous_height)
            changed_column = abs(float(current_box["x0"]) - float(previous_box["x0"])) > 0.26 and abs(vertical_gap) > max(0.01, previous_height)
            if vertical_gap > max(0.025, 1.45 * max(previous_height, current_height)) or moved_upward or changed_column:
                flush()
        current.append((index, line))
        previous_box = current_box
    flush()
    return parts or structured_parts_with_spans(page.get("text", ""), artifacts)


#: How much narrower than the page's median measured line a bolder line must be
#: before it is read as standing on its own. Measured rather than chosen: on a
#: two-column paper the body lines are justified and sit at 1.00x the median,
#: while "Abstract" and "CCS Concepts" sit at 0.17x and 0.29x.
SHORT_LINE_FRACTION = 0.5


def line_width(line: dict[str, Any]) -> float | None:
    box = line.get("bbox")
    if not isinstance(box, dict):
        return None
    try:
        return float(box["x1"]) - float(box["x0"])
    except (KeyError, TypeError, ValueError):
        return None


def closes_a_sentence(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and stripped[-1] in ".!?\u2026"


#: How far past a short bold line to look for the numeric rows that would make
#: it a table's column headings rather than a heading. Four, because a table
#: commonly carries a second header row and a units row before its data.
TABLE_LOOKAHEAD = 4
NUMERIC_TOKEN = re.compile(r"^[-+(\u2013\u2014]?(?:\d[\d.,:%/x\u00d7-]*|[\u2013\u2014])\)?$")


def looks_like_a_numeric_row(text: str) -> bool:
    """A line that is mostly numbers, as a table's data rows are."""
    tokens = text.split()
    if len(tokens) < 2:
        return False
    numeric = sum(1 for token in tokens if NUMERIC_TOKEN.match(token))
    return numeric >= 2 and numeric >= len(tokens) / 2


def stands_alone_as_short_bold_line(line: dict[str, Any], previous: dict[str, Any] | None,
                                    following: list[dict[str, Any]], median_width: float | None,
                                    body_face: str) -> bool:
    """A short line in a bolder face, between two closed sentences, is a heading.

    The face rule cannot reach these: a paper sets its figure captions in the
    same bold as its headings, so "Abstract" is absorbed by the caption above
    it, and it sets the CCS category list in bold too, so "CCS Concepts" is
    absorbed by the list below it. Neither is a change of face, and the gaps
    are smaller than a paragraph break, so nothing separated them.

    Width is what separates them, and it is the reason the rule is safe: a
    heading occupies a fraction of the measure while body text fills it. The
    sentence tests are what keep the last line of a bold caption -- also short
    -- from being read the same way, since that line continues the one above it.
    """
    face = line.get("font") or ""
    if not (is_bold_face(face) or _is_bolder_sibling(face, body_face)):
        return False
    width = line_width(line)
    if width is None or not median_width or width >= SHORT_LINE_FRACTION * median_width:
        return False
    if previous is not None and not closes_a_sentence(previous["text"]):
        return False
    if following and continues_sentence(line["text"], following[0]["text"]):
        return False
    # A table's column headings are short and bold and sit between two closed
    # sentences exactly as a heading does. What separates them is what comes
    # after: rows of numbers.
    if any(looks_like_a_numeric_row(entry["text"]) for entry in following[:TABLE_LOOKAHEAD]):
        return False
    return True


#: The floats a technical document titles rather than numbers into its section
#: hierarchy. An algorithm listing, a figure and a table are the same kind of
#: thing: a block with a title of its own that is NOT a section of the document.
FLOAT_CAPTION = re.compile(
    r"^(?:Figure|Fig\.|Table|Algorithm|Listing|Scheme|Equation|Chart|Plate)\s+\d+[.:]?\s+\S",
    re.IGNORECASE)


def opens_a_float_caption(text: str) -> bool:
    """True for a line that titles a figure, table or algorithm listing.

    The word after the number must be capitalised, which is what separates the
    caption "Algorithm 1 Compute loss" from the sentence "Algorithm 1 details
    the method for adaptively weighting pixel contributions" -- the same test
    that keeps numbered list items out of the heading set.
    """
    line = text.strip()
    if not FLOAT_CAPTION.match(line):
        return False
    after_number = re.match(r"^\S+\s+\d+[.:]?\s+(.)", line)
    return bool(after_number) and after_number.group(1).isupper()


def stands_alone_as_numbered_heading(text: str) -> bool:
    """A line that is a section number and a short phrase, and nothing else.

    Deliberately strict. A numbered list item usually runs longer and closes
    with a full stop, and a heading does neither, so the bound and the absent
    terminator are what keep list items out.
    """
    line = text.strip()
    if not line or len(line) > NUMBERED_HEADING_CHARS:
        return False
    if line[-1] in ".!?,;:":
        return False
    # The phrase after the number has to be capitalised and the number has to
    # look like a section number. Without both, a numbered list item ("2. a
    # single tile may cover..."), an equation fragment ("1 - a(t)e(x)") and a
    # bibliography entry opening with a year ("2021. Stochastic Polyak...") all
    # match, and each of those is common enough to swamp the real headings.
    return bool(re.match(r"^\d{1,3}(?:\.\d{1,3}){0,3}\.?\s+[A-Z\u0386-\u03ab\u0400-\u042f]", line))


def continues_sentence(previous: str, following: str) -> bool:
    """True when two measured lines are plainly one sentence carried across.

    Deliberately narrow: the first line must not close, and the second must open
    with a lower-case word. Anything less certain is left to the other rules,
    because merging two blocks that are genuinely separate is the worse error.
    """
    first, second = previous.strip(), following.strip()
    if not first or not second:
        return False
    if first[-1] in ".!?:;\u2026":
        return False
    # A line opening with punctuation that cannot begin a sentence is plainly a
    # continuation. A figure caption broken around inline mathematics does this
    # constantly -- ", a SLIC segmentation map" -- and reading it as a fresh
    # block leaves a fragment that the face rules then promote to a heading.
    if second[0] in ",;:)]}\u2019\u201d":
        return True
    return bool(re.match(r"^[a-z\u00df-\u00ff\u03b1-\u03c9\u0430-\u044f]", second))


def geometric_native_parts(page: dict[str, Any], artifacts: set[str]) -> list[dict[str, Any]]:
    """Assemble measured native lines into conservative paragraph candidates."""
    lines = page.get("native_text_lines", [])
    if not lines:
        return structured_parts_with_spans(page["text"], artifacts)
    widths = [width for width in (line_width(line) for line in lines) if width]
    median_width = statistics.median(widths) if len(widths) >= 4 else None
    body_face = str(page.get("body_font") or "")
    parts: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    #: The tables this page was measured to have ruled, and whose cells its own
    #: geometry therefore proves. An incomplete lattice is deliberately absent:
    #: it is reported as uncertainty and never assembled into a block.
    ruled = [table for table in page.get("ruled_tables", []) if table.get("complete") and table.get("bbox")]

    #: Whether the block being assembled was opened by a numbered heading line.
    #: A list, so the nested flush() can clear it without a nonlocal binding.
    numbered = [False]
    #: ...and whether it was opened by a float's caption line.
    floating = [False]

    def enclosing_table(line: dict[str, Any]) -> int | None:
        """Which ruled table a measured line sits inside, by its own centre."""
        box = line.get("bbox")
        if not box or not ruled:
            return None
        x = (float(box["x0"]) + float(box["x1"])) / 2
        y = (float(box["y0"]) + float(box["y1"])) / 2
        for index, table in enumerate(ruled):
            rect = table["bbox"]
            if rect["x0"] <= x <= rect["x1"] and rect["y0"] <= y <= rect["y1"]:
                return index
        return None

    def flush() -> None:
        numbered[0] = False
        floating[0] = False
        if not current:
            return
        value = "\n".join(entry["text"] for entry in current).strip()
        if value:
            parts.append({"text": value, "start": current[0]["start"], "end": current[-1]["end"]})
        current.clear()

    # Each table is gathered whole before the walk begins, rather than
    # accumulated as its lines are met. A table's lines need not arrive in one
    # unbroken run -- a line whose centre falls just outside the rules, or a
    # caption PDFium reads mid-table, interrupts it -- and a segmenter that
    # closed the block at the interruption emitted the table twice, each copy
    # carrying the full set of recovered rows into Markdown and into the CSV.
    owners = [enclosing_table(line) for line in lines]
    table_lines: dict[int, list[dict[str, Any]]] = {}
    for owner, line in zip(owners, lines):
        if owner is not None and line["text"].strip():
            table_lines.setdefault(owner, []).append(line)
    emitted: set[int] = set()

    previous_line: dict[str, Any] | None = None
    for index, line in enumerate(lines):
        # A ruled table is decided before any other rule looks at the line. Its
        # cells hold exactly the short, capitalised, numeric lines the heading
        # and page-marker rules are built to catch, and a cell taken for a
        # heading -- or a lone "14" discarded as a page number -- is a hole in
        # a table the page drew in full.
        owner = owners[index]
        if owner is not None:
            flush()
            grouped = table_lines.get(owner)
            if owner not in emitted and grouped:
                emitted.add(owner)
                parts.append({
                    "text": "\n".join(entry["text"] for entry in grouped).strip(),
                    "start": grouped[0]["start"], "end": grouped[-1]["end"],
                    "table_rows": ruled[owner]["rows"], "table_bbox": ruled[owner]["bbox"],
                })
            previous_line = line
            continue
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
        # So is a change of face. A heading is set closer to the text it heads
        # than to the text above it, so the gap rule alone never separates one:
        # on a two-column paper every inter-line gap is smaller than the 10pt
        # floor, and the heading is absorbed into the paragraph beneath it and
        # ceases to exist as structure. The face the page sets a line in is
        # measured source evidence of the same kind as its position.
        # A line that is nothing but a section number and a short phrase is a
        # heading in essentially every technical document, and some papers set
        # one in the plain body face at the body size -- no measurement of face
        # or size can separate it, so the number itself has to.
        # A float's title starts a block. Its caption commonly interrupts the
        # column flow, so the line before it can end mid-word and none of the
        # sentence or whitespace rules can fire: on a real paper this left
        # "Algorithm 1 Compute loss" and its whole listing inside a 29-line
        # paragraph that opened with unrelated prose.
        if opens_a_float_caption(line["text"]):
            flush()
            current.append(line)
            floating[0] = True
            previous_line = line
            continue
        if floating[0] and current and prior:
            if continues_sentence(prior["text"], line["text"]):
                current.append(line)
                previous_line = line
                continue
            flush()
        if stands_alone_as_numbered_heading(line["text"]):
            flush()
            numbered[0] = True
            current.append(line)
            continue
        # ...and it closes at the next line, unless that line plainly finishes
        # it: a heading long enough to wrap breaks mid-phrase, and orphaning the
        # remainder makes two wrong blocks out of one right one.
        if numbered[0] and current and prior:
            if continues_sentence(prior["text"], line["text"]):
                current.append(line)
                previous_line = line
                continue
            flush()
        if stands_alone_as_short_bold_line(line, prior or previous_line,
                                           lines[index + 1:index + 1 + TABLE_LOOKAHEAD],
                                           median_width, body_face):
            flush()
            parts.append({"text": line["text"].strip(), "start": line["start"], "end": line["end"]})
            previous_line = line
            continue
        if current and line.get("font") and prior and prior.get("font") and line["font"] != prior["font"]:
            # ...unless the sentence plainly runs on across it. An italic term
            # opening a definition, or a title inside a bibliography entry,
            # changes face mid-sentence and is emphasis rather than structure.
            # Splitting there cuts a paragraph in half and leaves the first half
            # looking exactly like a heading.
            if not continues_sentence(prior["text"], line["text"]):
                flush()
        current.append(line)
        previous_line = line
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


def source_page_size(displayed_width: float, displayed_height: float, rotation: int) -> tuple[float, float]:
    """The page's own unrotated size, which is the frame PDFium measures text in."""
    if rotation % 360 in (90, 270):
        return displayed_height, displayed_width
    return displayed_width, displayed_height


def rotate_point_to_displayed_frame(x: float, y: float, rotation: int, source_width: float, source_height: float) -> tuple[float, float]:
    """Turn one point by the page's own /Rotate, bottom-left origin throughout."""
    turn = rotation % 360
    if turn == 90:
        return y, source_width - x
    if turn == 180:
        return source_width - x, source_height - y
    if turn == 270:
        return source_height - y, x
    return x, y


def bbox_to_displayed_frame(box: dict[str, Any] | None, rotation: int, source_width: float, source_height: float) -> dict[str, Any] | None:
    """Move a measured rectangle from the page's own frame into the displayed one.

    PDFium reports text rectangles in the page's unrotated coordinates, but it
    reports page size -- and renders previews -- with /Rotate already applied.
    Recorded together and unreconciled the two disagree on every rotated page:
    a rectangle sits outside the page box it is measured against, the evidence
    overlay draws it away from the text it marks, and the reading-order check
    compares the axis the page is no longer read along. Everything downstream
    is expressed in the displayed frame, because that is the one a reviewer
    sees on the source panel, so the measurement is moved into it here rather
    than left for each consumer to guess about.
    """
    if not box or rotation % 360 == 0:
        return box
    corners = [
        rotate_point_to_displayed_frame(float(box["x0"]), float(box["y0"]), rotation, source_width, source_height),
        rotate_point_to_displayed_frame(float(box["x1"]), float(box["y0"]), rotation, source_width, source_height),
        rotate_point_to_displayed_frame(float(box["x1"]), float(box["y1"]), rotation, source_width, source_height),
        rotate_point_to_displayed_frame(float(box["x0"]), float(box["y1"]), rotation, source_width, source_height),
    ]
    return make_bbox(
        min(point[0] for point in corners), min(point[1] for point in corners),
        max(point[0] for point in corners), max(point[1] for point in corners),
        str(box["coordinate_space"]),
    )


#: A drawn rule is thin. Two and a half points is a heavy rule in print and
#: still well under the height of a line of text, so anything thicker is
#: something the page means to be looked at rather than a table's edge.
RULE_MAX_THICKNESS_POINTS = 2.5
#: ...and long. Below about one character's width a thin mark is a glyph
#: fragment, an underscore or a bullet, none of which bound a cell.
RULE_MIN_LENGTH_POINTS = 12.0
#: Rules meant as one line are rarely placed on one coordinate: a producer that
#: draws a border per cell emits a rectangle per cell, each a fraction of a
#: point off its neighbour.
RULE_POSITION_TOLERANCE_POINTS = 2.0
#: A rule may stop a hair short of the one it meets, or overshoot it. Both are
#: still a crossing; neither is a rule that merely passes nearby.
RULE_CROSSING_TOLERANCE_POINTS = 2.0
#: Guards against pages that are drawings rather than documents. A map or a
#: vector chart can carry tens of thousands of paths and hundreds of collinear
#: thin ones, and pairing every line with every other is quadratic. Past these
#: counts the page is not a ruled table and is left alone.
MAX_SCANNED_PATH_OBJECTS = 20_000
MAX_RULES_PER_AXIS = 2_000
MAX_RULE_LINES_PER_AXIS = 200


def rule_from_bbox(box: dict[str, Any] | None) -> dict[str, Any] | None:
    """Read one drawn rectangle as a horizontal rule, a vertical rule, or neither.

    The rectangle is a path object's *bounds*, not its segments. Many producers
    draw a rule as a thin filled rectangle rather than as a stroked line, and a
    reader that parses segments sees the fill and misses the rule; bounds see
    both, and a rule is fully described by where it is and how far it runs.
    """
    if not box:
        return None
    x0, y0 = float(box["x0"]), float(box["y0"])
    x1, y1 = float(box["x1"]), float(box["y1"])
    width, height = x1 - x0, y1 - y0
    if height <= RULE_MAX_THICKNESS_POINTS and width >= RULE_MIN_LENGTH_POINTS:
        return {"axis": "horizontal", "position": (y0 + y1) / 2, "start": x0, "end": x1}
    if width <= RULE_MAX_THICKNESS_POINTS and height >= RULE_MIN_LENGTH_POINTS:
        return {"axis": "vertical", "position": (x0 + x1) / 2, "start": y0, "end": y1}
    return None


def cluster_positions(values: Iterable[float], tolerance: float) -> list[float]:
    """Collapse near-equal rule positions onto one line each.

    Each returned position is the mean of the cluster it stands for, so a line
    is placed where its rules actually are rather than on whichever of them was
    read first.
    """
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return []
    clusters: list[list[float]] = [[ordered[0]]]
    for value in ordered[1:]:
        if value - clusters[-1][-1] <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [round(statistics.fmean(cluster), 4) for cluster in clusters]


def merge_rules(rules: list[dict[str, Any]], tolerance: float) -> list[dict[str, Any]]:
    """One line per cluster of rules, keeping the separate runs it is drawn in.

    The runs are kept rather than unioned into one extent. A table drawn with a
    border per cell produces a row of touching segments, which union harmlessly;
    two tables side by side produce two runs at the same height with a gap
    between them, and a union would claim a rule across the gap that the page
    never drew -- and with it a crossing, and with that a grid spanning both.
    """
    merged = [
        {"position": position, "segments": []}
        for position in cluster_positions([rule["position"] for rule in rules], tolerance)
    ]
    if not merged:
        return []
    for rule in rules:
        nearest = min(merged, key=lambda line: abs(line["position"] - rule["position"]))
        nearest["segments"].append((float(rule["start"]), float(rule["end"])))
    lines = []
    for line in merged:
        if not line["segments"]:
            continue
        line["start"] = min(start for start, _ in line["segments"])
        line["end"] = max(end for _, end in line["segments"])
        lines.append(line)
    return lines


def rules_cross(along: dict[str, Any], across: dict[str, Any], tolerance: float) -> bool:
    """Whether two lines on opposite axes actually meet, run against run."""
    return (
        any(start - tolerance <= across["position"] <= end + tolerance for start, end in along["segments"])
        and any(start - tolerance <= along["position"] <= end + tolerance for start, end in across["segments"])
    )


def ruled_table_grids(horizontals: list[dict[str, Any]], verticals: list[dict[str, Any]],
                      tolerance: float = RULE_CROSSING_TOLERANCE_POINTS) -> list[dict[str, Any]]:
    """Find the grids in a page's rules: where two lines cross two others.

    Crossing is what makes a grid provable. Rules that meet enclose cells;
    rules that merely share a page do not, so the crossings are followed as a
    graph and each connected group is one candidate. Two tables on one page
    share no crossing and stay two candidates.

    A group in which every horizontal meets every vertical is a complete
    lattice, and the cells it encloses are proven by the page's own drawing. A
    group missing crossings describes a shape the lines alone do not determine
    -- a merged cell, a rule drawn only under the headings, a figure's axes --
    so it is returned marked incomplete, to be reported rather than emitted.
    """
    if len(horizontals) > MAX_RULE_LINES_PER_AXIS or len(verticals) > MAX_RULE_LINES_PER_AXIS:
        return []
    crossings = {
        (h_index, v_index)
        for h_index, horizontal in enumerate(horizontals)
        for v_index, vertical in enumerate(verticals)
        if rules_cross(horizontal, vertical, tolerance)
    }
    meets_vertical: dict[int, set[int]] = {index: set() for index in range(len(horizontals))}
    meets_horizontal: dict[int, set[int]] = {index: set() for index in range(len(verticals))}
    for h_index, v_index in crossings:
        meets_vertical[h_index].add(v_index)
        meets_horizontal[v_index].add(h_index)

    grids: list[dict[str, Any]] = []
    assigned: set[int] = set()
    for first in range(len(horizontals)):
        if first in assigned or not meets_vertical[first]:
            continue
        group_h: set[int] = set()
        group_v: set[int] = set()
        pending = [("h", first)]
        while pending:
            axis, index = pending.pop()
            if axis == "h" and index not in group_h:
                group_h.add(index)
                pending.extend(("v", other) for other in meets_vertical[index])
            elif axis == "v" and index not in group_v:
                group_v.add(index)
                pending.extend(("h", other) for other in meets_horizontal[index])
        assigned |= group_h
        if len(group_h) < 2 or len(group_v) < 2:
            continue
        row_lines = sorted(horizontals[index]["position"] for index in group_h)
        column_lines = sorted(verticals[index]["position"] for index in group_v)
        grids.append({
            "row_lines": row_lines,
            "column_lines": column_lines,
            "complete": all((h_index, v_index) in crossings for h_index in group_h for v_index in group_v),
            "crossing_count": sum(len(meets_vertical[h_index] & group_v) for h_index in group_h),
        })
    # Page order: topmost first, then leftmost, so a document's tables are
    # recorded in the order a reader meets them.
    return sorted(grids, key=lambda grid: (-grid["row_lines"][-1], grid["column_lines"][0]))


def table_cell_text(characters: list[tuple[str, dict[str, Any] | None]], grid: dict[str, Any]) -> list[list[str]]:
    """Place each measured character in the cell its own centre falls inside.

    The centre decides, not the edges: a glyph may overhang the rule beside it,
    and a cell a character merely touches is not the cell it is in. Characters
    keep the order PDFium read them in, so a cell reads as the source wrote it,
    and a gap in that order becomes a space -- a character PDFium gives no
    rectangle for is one it drew nothing for, which is what a space is.

    A cell no character falls inside stays empty. Borrowing from a neighbour to
    fill it would be inventing a value that the page does not contain.
    """
    row_count = len(grid["row_lines"]) - 1
    column_count = len(grid["column_lines"]) - 1
    if row_count < 1 or column_count < 1:
        return []

    def band(lines: list[float], value: float) -> int | None:
        for index in range(len(lines) - 1):
            if lines[index] <= value <= lines[index + 1]:
                return index
        return None

    collected: list[list[list[tuple[int, str]]]] = [
        [[] for _ in range(column_count)] for _ in range(row_count)
    ]
    for index, (character, box) in enumerate(characters):
        if not box:
            continue
        column = band(grid["column_lines"], (float(box["x0"]) + float(box["x1"])) / 2)
        if column is None:
            continue
        row = band(grid["row_lines"], (float(box["y0"]) + float(box["y1"])) / 2)
        if row is None:
            continue
        # Rows are read from the top of the page down, while the lines that
        # bound them ascend from its foot, so the topmost band is the last one.
        collected[row_count - 1 - row][column].append((index, character))

    rows: list[list[str]] = []
    for row in collected:
        cells: list[str] = []
        for cell in row:
            pieces: list[str] = []
            previous: int | None = None
            for index, character in sorted(cell):
                if previous is not None and index != previous + 1:
                    pieces.append(" ")
                pieces.append(character)
                previous = index
            cells.append(" ".join("".join(pieces).split()))
        rows.append(cells)
    return rows


def page_character_boxes(textpage: Any) -> list[Any] | None:
    """Every character's rectangle, in the page's own unrotated frame.

    One scan, shared by the two measurements that need it: which characters a
    declared link rectangle is drawn over, and which table cell a character
    sits in. `None` means PDFium could not report the rectangles at all, which
    is a different answer from a page that has no characters to report.
    """
    try:
        return [textpage.get_charbox(index) for index in range(textpage.count_chars())]
    except Exception:
        return None


def displayed_character_boxes(textpage: Any, extracted_text: str, rotation: int,
                              source_width: float, source_height: float) -> list[tuple[str, dict[str, Any] | None]]:
    """Each character of a page paired with its rectangle in the displayed frame."""
    boxes = page_character_boxes(textpage)
    if not boxes:
        return []
    paired: list[tuple[str, dict[str, Any] | None]] = []
    for index, box in enumerate(boxes):
        character = extracted_text[index] if index < len(extracted_text) else " "
        measured = make_bbox(box[0], box[1], box[2], box[3], "pdf-page-points") if box else None
        paired.append((character, bbox_to_displayed_frame(measured, rotation, source_width, source_height)))
    return paired


def page_rules(page: Any, rotation: int, source_width: float, source_height: float) -> dict[str, list[dict[str, Any]]]:
    """The rules a page draws, measured in the frame the page is displayed in.

    PDFium reports path bounds in the page's own unrotated coordinates while a
    reader sees the page turned. On a quarter-turned page the rules that
    separate rows on screen are drawn across the source frame, so each
    rectangle is moved into the displayed frame *before* it is called
    horizontal or vertical. Classifying first would transpose every landscape
    table's rows and columns, which is the same frame confusion 36e787c fixed
    for text.
    """
    horizontal: list[dict[str, Any]] = []
    vertical: list[dict[str, Any]] = []
    try:
        import pypdfium2.raw as raw  # type: ignore

        drawn_paths = page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH], max_depth=4)
    except Exception:
        return {"horizontal": horizontal, "vertical": vertical}
    try:
        for scanned, drawn in enumerate(drawn_paths):
            if scanned >= MAX_SCANNED_PATH_OBJECTS:
                break
            if len(horizontal) >= MAX_RULES_PER_AXIS and len(vertical) >= MAX_RULES_PER_AXIS:
                break
            try:
                left, bottom, right, top = drawn.get_bounds()
            except Exception:
                continue
            rule = rule_from_bbox(bbox_to_displayed_frame(
                make_bbox(left, bottom, right, top, "pdf-page-points"),
                rotation, source_width, source_height,
            ))
            if not rule:
                continue
            axis = horizontal if rule["axis"] == "horizontal" else vertical
            if len(axis) < MAX_RULES_PER_AXIS:
                axis.append(rule)
    except Exception:  # pragma: no cover - source documents vary widely
        return {"horizontal": horizontal, "vertical": vertical}
    return {"horizontal": horizontal, "vertical": vertical}


def page_ruled_tables(page: Any, textpage: Any, extracted_text: str, rotation: int,
                      source_width: float, source_height: float) -> list[dict[str, Any]]:
    """Recover the ruled tables a page draws, with the text inside their cells.

    Everything here is measured: the rules are drawn by the page, the cells are
    the rectangles those rules enclose, and a cell's text is the characters
    whose centres land in it. Nothing is inferred from alignment or spacing, so
    a table the page did not rule is not a table Philon reports.
    """
    rules = page_rules(page, rotation, source_width, source_height)
    grids = ruled_table_grids(
        merge_rules(rules["horizontal"], RULE_POSITION_TOLERANCE_POINTS),
        merge_rules(rules["vertical"], RULE_POSITION_TOLERANCE_POINTS),
    )
    if not grids:
        return []
    characters = displayed_character_boxes(textpage, extracted_text, rotation, source_width, source_height)
    recovered: list[dict[str, Any]] = []
    for grid in grids:
        # The grid's lines are already displayed-frame positions, so its
        # rectangle is too, and must not be converted a second time.
        bbox = make_bbox(grid["column_lines"][0], grid["row_lines"][0],
                         grid["column_lines"][-1], grid["row_lines"][-1], "pdf-page-points")
        if not bbox:
            continue
        recovered.append({
            "bbox": bbox,
            "row_count": len(grid["row_lines"]) - 1,
            "column_count": len(grid["column_lines"]) - 1,
            "complete": grid["complete"],
            "crossing_count": grid["crossing_count"],
            "rows": table_cell_text(characters, grid) if grid["complete"] else [],
        })
    return recovered


#: Schemes Philon will turn into a link. A PDF may declare any URI in an
#: annotation, `javascript:` included, and the presentation export is a document
#: someone opens locally. Anything outside this set stays recorded as page
#: evidence and never becomes something to click.
ANCHORABLE_LINK_SCHEMES = ("http://", "https://", "mailto:")


def is_anchorable_link(uri: str) -> bool:
    return str(uri).strip().lower().startswith(ANCHORABLE_LINK_SCHEMES)


def rect_contains_point(rect: tuple[float, float, float, float], x: float, y: float) -> bool:
    left, bottom, right, top = rect
    return left <= x <= right and bottom <= y <= top


def measure_link_anchors(textpage: Any, extracted_text: str, leading_trim: int, text: str,
                         annotations: list[dict[str, Any]], rotation: int,
                         source_width: float, source_height: float) -> list[dict[str, Any]]:
    """Find the characters a PDF's own link rectangle is drawn over.

    The rectangle and its target are source-declared; which characters sit
    inside it is measured, one character box at a time, in the page's own
    unrotated frame where both are expressed. A rectangle covering no text is
    reported without an anchor rather than attached to whatever was nearest,
    because a link on the wrong words is worse than a link left unmade.
    """
    if not annotations:
        return []
    boxes = page_character_boxes(textpage)
    if boxes is None:
        return []
    anchors: list[dict[str, Any]] = []
    for annotation in annotations:
        rect = annotation.get("rect")
        if not rect:
            continue
        # Whitespace is never part of a link. A line break sitting just inside
        # the rectangle would otherwise be carried into the anchor, which the
        # machine outputs record verbatim.
        covered = [
            index for index, box in enumerate(boxes)
            if box and not extracted_text[index : index + 1].isspace()
            and rect_contains_point(rect, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        ]
        if not covered:
            anchors.append({"uri": annotation["uri"], "start": None, "end": None, "text": "", "bbox": None})
            continue
        start = min(covered) - leading_trim
        end = max(covered) + 1 - leading_trim
        if start < 0 or end > len(text) or end <= start:
            anchors.append({"uri": annotation["uri"], "start": None, "end": None, "text": "", "bbox": None})
            continue
        measured = union_bboxes(
            make_bbox(boxes[index][0], boxes[index][1], boxes[index][2], boxes[index][3], "pdf-page-points")
            for index in covered
        )
        anchors.append({
            "uri": annotation["uri"],
            "start": start,
            "end": end,
            "text": text[start:end],
            "bbox": bbox_to_displayed_frame(measured, rotation, source_width, source_height),
        })
    return anchors


def pdf_link_annotations(path: Path, page_count: int) -> list[list[dict[str, Any]]]:
    """Read each page's declared link rectangles, in the page's own frame."""
    per_page: list[list[dict[str, Any]]] = [[] for _ in range(page_count)]
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path), strict=False)
        for index, page in enumerate(reader.pages[:page_count]):
            found: list[dict[str, Any]] = []
            for annotation in page.get("/Annots") or []:
                item = annotation.get_object() if hasattr(annotation, "get_object") else annotation
                if not hasattr(item, "get") or item.get("/Subtype") != "/Link":
                    continue
                action = item.get("/A") or {}
                if hasattr(action, "get_object"):
                    action = action.get_object()
                uri = action.get("/URI") if hasattr(action, "get") else None
                rectangle = item.get("/Rect")
                if not uri or not rectangle or len(rectangle) != 4:
                    continue
                left, bottom, right, top = (float(value) for value in rectangle)
                found.append({
                    "uri": str(uri),
                    "rect": (min(left, right), min(bottom, top), max(left, right), max(bottom, top)),
                })
            per_page[index] = found
    except Exception:
        pass
    return per_page


def block_links(page: dict[str, Any], start: int | None, end: int | None) -> list[dict[str, Any]]:
    """The measured anchors that fall inside one block's span of page text."""
    if start is None or end is None:
        return []
    return [
        {"uri": link["uri"], "text": link["text"], "bbox": link["bbox"]}
        for link in page.get("links", [])
        if link.get("start") is not None and start <= link["start"] and link["end"] <= end
    ]


def anchored_spans(reading_text: str, links: list[dict[str, Any]]) -> list[tuple[int, int, str]]:
    """Where each anchor still appears verbatim, without overlaps.

    `clean_reading_text` reflows the source lines, so an anchor is placed only
    where its measured text survived that reflow intact. Anything else would
    move a link onto words it was never drawn over, so it is left unmade.
    """
    placed: list[tuple[int, int, str]] = []
    for link in links:
        if not is_anchorable_link(link.get("uri", "")):
            continue
        anchor = re.sub(r"\s+", " ", str(link.get("text", ""))).strip()
        if not anchor:
            continue
        position = reading_text.find(anchor)
        while position != -1:
            span = (position, position + len(anchor))
            if all(span[1] <= begin or span[0] >= finish for begin, finish, _ in placed):
                placed.append((span[0], span[1], str(link["uri"])))
                break
            position = reading_text.find(anchor, position + 1)
    return sorted(placed, reverse=True)


def anchor_links_markdown(reading_text: str, links: list[dict[str, Any]]) -> str:
    """Turn measured anchors into Markdown links, right to left so offsets hold."""
    for begin, finish, uri in anchored_spans(reading_text, links):
        label = reading_text[begin:finish].replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        target = f"<{uri}>" if re.search(r"[\s()<>]", uri) else uri
        reading_text = reading_text[:begin] + f"[{label}]({target})" + reading_text[finish:]
    return reading_text


def anchor_links_html(escaped_text: str, reading_text: str, links: list[dict[str, Any]]) -> str:
    """Turn measured anchors into HTML links in already-escaped text.

    The spans are found in the unescaped reading text and re-escaped piecewise,
    so an escape sequence can never be split down the middle.
    """
    spans = anchored_spans(reading_text, links)
    if not spans:
        return escaped_text
    result = reading_text
    for begin, finish, uri in spans:
        label = html.escape(result[begin:finish])
        target = html.escape(uri, quote=True)
        result = result[:begin] + f'<a href="{target}" rel="noopener noreferrer">{label}</a>' + result[finish:]
    # Escape everything that is not one of the anchors just inserted.
    parts = re.split(r"(<a href=\"[^\"]*\" rel=\"noopener noreferrer\">.*?</a>)", result, flags=re.DOTALL)
    return "".join(part if part.startswith("<a href=") else html.escape(part) for part in parts)


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
    # Counted separately from `invisible`: a noncharacter is a broken font
    # mapping in the source, not a hidden instruction, and it is resolved in the
    # reading form rather than being grounds to distrust the whole page.
    discardable = count_discardable_formatting(text)
    # A private-use character is a real glyph the PDF's font never mapped to
    # Unicode -- Adobe writes the registered sign at U+F6D9, and a maths font
    # commonly puts its own brackets in the E000 block. Philon cannot know what
    # one means, so it neither deletes it (that would lose text) nor guesses at
    # it (that would invent text). It is counted, and the record says so.
    private_use = sum(1 for char in text if 0xE000 <= ord(char) <= 0xF8FF
                      or 0xF0000 <= ord(char) <= 0xFFFFD or 0x100000 <= ord(char) <= 0x10FFFD)
    # Of those, the ones Adobe's published list names as variants of a real
    # character, which the reading form resolves. The remainder is what
    # genuinely cannot be read, and is what the warning is about.
    adobe_variants = count_adobe_glyph_variants(text)
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
        "discardable_formatting_characters": discardable,
        "private_use_characters": private_use,
        "adobe_glyph_variants": adobe_variants,
        "unresolved_private_use_characters": private_use - adobe_variants,
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


#: A face whose name carries one of these is set bolder than its family's text
#: weight. The name is used rather than PDFium's flags because a subset font
#: often declares no flags at all while still being named for its weight. The
#: trailing-capital form is how a subset font commonly spells it: a document set
#: in LinLibertineT titles itself in LinBiolinumTB, a bold face from a different
#: family, which no comparison against the body face's own name can see.
BOLD_WORD = re.compile(r"(?:bold|black|heavy|semibold)", re.IGNORECASE)
BOLD_SUFFIX = re.compile(r"[A-Za-z](?:B|BD)$")


def is_bold_face(face: str) -> bool:
    return bool(face) and bool(BOLD_WORD.search(face) or BOLD_SUFFIX.search(face))
#: How many characters of a line to sample when naming the face it is set in.
#: A line is typographically uniform in the ordinary case, and sampling keeps a
#: dense page from costing one FFI call per character.
FACE_SAMPLE = 16


def line_typeface(textpage: Any, start: int, length: int, text: str) -> tuple[str, float]:
    """Name the face a measured line is set in, and its type size.

    PDFium already knows both; Philon previously inferred size from glyph
    bounding boxes, which inverts on a line with no descender -- a heading in
    larger type can measure *shorter* than the body text around it. The face
    name is the more portable of the two signals: FPDFText_GetFontSize returns
    1.0 whenever a PDF scales type through the text matrix instead of the Tf
    operand, which is the case for many publisher PDFs.
    """
    try:
        import pypdfium2.raw as raw  # type: ignore
    except ImportError:
        return "", 0.0
    indexes = [index for index in range(start, min(start + length, len(text))) if not text[index].isspace()]
    if not indexes:
        return "", 0.0
    step = max(1, len(indexes) // FACE_SAMPLE)
    sampled = indexes[::step][:FACE_SAMPLE]
    faces: dict[str, int] = {}
    sizes: list[float] = []
    buffer = ctypes.create_string_buffer(160)
    flags = ctypes.c_int()
    for index in sampled:
        try:
            written = raw.FPDFText_GetFontInfo(textpage, index, buffer, 160, ctypes.byref(flags))
            name = buffer.raw[:max(0, written - 1)].decode("utf-8", "replace")
            sizes.append(float(raw.FPDFText_GetFontSize(textpage, index)))
        except Exception:
            return "", 0.0
        faces[name] = faces.get(name, 0) + 1
    face = max(faces.items(), key=lambda item: item[1])[0] if faces else ""
    return face, statistics.median(sizes) if sizes else 0.0


def document_body_typeface(source_pages: list[dict[str, Any]]) -> tuple[str, float]:
    """The face and size most of this document's measured text is set in.

    Taken across the whole document rather than per page, because a page can be
    mostly heading, mostly caption or mostly figure, and a per-page answer would
    then call the body text unusual.
    """
    faces: dict[str, int] = {}
    sizes: list[float] = []
    for page in source_pages:
        for line in page.get("native_text_lines", []):
            face = line.get("font")
            if face:
                faces[face] = faces.get(face, 0) + len(line.get("text", ""))
            if line.get("size"):
                sizes.append(float(line["size"]))
    face = max(faces.items(), key=lambda item: item[1])[0] if faces else ""
    return face, statistics.median(sizes) if sizes else 0.0


def pdfium_extract(path: Path, selection: tuple[int, ...] | None = None) -> tuple[list[dict[str, Any]], list[WarningRecord]]:
    """Use PDFium first, preserving a pypdf fallback for non-packaged tests."""
    warnings: list[WarningRecord] = []
    try:
        import pypdfium2 as pdfium  # type: ignore

        document = pdfium.PdfDocument(str(path))
        link_annotations = pdf_link_annotations(path, len(document))
        wanted = set(selection or ())
        pages: list[dict[str, Any]] = []
        for index in range(len(document)):
            if wanted and (index + 1) not in wanted:
                continue
            page = document[index]
            width, height = page.get_size()
            # PDFium's page size already has /Rotate applied; its text
            # rectangles do not. Measure the source frame so they can be
            # reconciled before either is recorded.
            rotation = int(page.get_rotation() or 0)
            source_width, source_height = source_page_size(width, height, rotation)
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
                bbox = bbox_to_displayed_frame(
                    pdfium_span_bbox(textpage, line_start, len(line_text)),
                    rotation, source_width, source_height,
                )
                face, size = line_typeface(textpage, line_start, len(line_text), extracted_text)
                line_spans.append({"text": line_text, "start": start, "end": end, "bbox": bbox, "font": face, "size": size})
                spans.append({"start": start, "end": end, "bbox": bbox})
            pages.append({
                "number": index + 1, "width": width, "height": height, "text": text,
                "rotation": rotation,
                "ruled_tables": page_ruled_tables(
                    page, textpage, extracted_text, rotation, source_width, source_height,
                ),
                "links": measure_link_anchors(
                    textpage, extracted_text, leading_trim, text,
                    link_annotations[index] if index < len(link_annotations) else [],
                    rotation, source_width, source_height,
                ),
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
        wanted = set(selection or ())
        pages = []
        for index, page in enumerate(reader.pages):
            if wanted and (index + 1) not in wanted:
                continue
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


def native_pdf_features(path: Path, page_numbers: list[int]) -> list[dict[str, Any]]:
    """Collect source-declared links and fonts without inferring geometry.

    Keyed by the page's own number rather than its position in the result, so
    a conversion of part of a document still reads each page's own fonts.
    """
    features = [{"fonts": [], "links": [], "geometry": "pdfium-text-rectangles"} for _ in page_numbers]
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path), strict=False)
        for index, number in enumerate(page_numbers):
            if not 1 <= number <= len(reader.pages):
                continue
            page = reader.pages[number - 1]
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


#: How much larger than the page's median line a block must be set before it
#: can be a heading across more than one line. Judged against real documents
#: rather than chosen: a subheading is commonly 1.15x body text, so the gate
#: sits above that to keep emphasis from being read as structure.
HEADING_PROMINENCE = 1.25
#: A heading set in its own face may wrap, but not far. Beyond this it is a
#: bold lead-in sentence or an emphasised paragraph, not a section title.
HEADING_FACE_LINES = 2
HEADING_FACE_CHARS = 120
#: A numbered heading is a short phrase; past this it is a numbered sentence.
NUMBERED_HEADING_CHARS = 80


def is_numbered_heading(first_line: str) -> bool:
    """A leading section number is structure the source states outright."""
    return bool(re.match(r"^\d+(?:\.\d+)*\.?\s+\S", first_line))


def heading_depth(first_line: str) -> int:
    """Read a heading's level from its own section number, else default to 2."""
    number = re.match(r"^(\d+(?:\.\d+)*)", first_line)
    return min(6, number.group(1).count(".") + 1) if number else 2


def classify_block(text: str, prominence: float | None = None, typeface: dict[str, Any] | None = None) -> tuple[str, int | None]:
    """Name a block from its text, and from how the page sets that text.

    `prominence` is the block's line height against the page's median line
    height, where the page measured it. A heading is a line of its own, because
    the first line of ordinary prose looks exactly like one; a title that wraps
    is only readable as a heading when the page shows it set larger than the
    body text around it.

    `typeface` is what PDFium says the block is actually set in: whether its
    face differs from the document's body face, and whether that face is a bold
    one. This is what lets a heading be recognised in a script the text rules
    cannot read, and it is measured rather than inferred -- unlike line height,
    which a heading without descenders makes *smaller* than the body text.
    """
    first_line = text.splitlines()[0] if text else ""
    line_count = len([line for line in text.splitlines() if line.strip()])
    if table_rows(text):
        return "table", None
    if is_formula(text):
        return "formula", None
    if opens_a_float_caption(first_line):
        return "caption", None
    if re.match(r"^(?:\[\d+\]|\d+\.)\s+.+(?:\d{4}|doi:)", first_line, re.IGNORECASE):
        return "citation", None
    # A heading stands on its own line, unless the page measured it as larger
    # type than the prose around it. Judging by the first line alone turned
    # wrapped paragraphs into headings, because that line starts with a capital
    # and ends mid-clause rather than with a full stop.
    prominent = prominence is not None and prominence >= HEADING_PROMINENCE and line_count <= 3
    # The page's own measurement is allowed to say no, not only yes. A short
    # line that starts with a capital and ends mid-clause reads exactly like a
    # heading -- an author line, an affiliation, a keyword list -- and the text
    # rule alone promoted all three. Where the page sets the line in the plain
    # body face at the body size, it has already answered the question, and a
    # guess from the characters must not overrule a measurement of the type.
    # The page sets a heading apart by weight. A face that merely *differs* from
    # the body face is as likely to be italic -- emphasis, a defined term, a
    # cited title -- and the text rule promoted those too. So the measurement
    # grants a heading only where the face is bolder, and refuses everywhere
    # else it was actually taken.
    # ...and a block the page shows to sit above rows of numbers is a table's
    # column headings, which are short, capitalised and bold exactly as a
    # heading is. The characters cannot tell the two apart; what follows can.
    set_apart = bool(typeface) and bool(typeface.get("bold")) and not typeface.get("precedes_numeric_rows")
    measured_as_prose = bool(typeface) and not set_apart and not is_numbered_heading(first_line)
    if (line_count == 1 or prominent) and not measured_as_prose and re.match(r"^(?:\d+(?:\.\d+)*\s+)?[A-Z][A-Za-z0-9 ,:;()/-]{3,}$", first_line) and len(first_line) < 100:
        return "heading", heading_depth(first_line)
    # A run the page sets in a different, bolder face than the body text is a
    # heading whatever alphabet it is written in. The text rules above are
    # ASCII-Latin only, so without this a Greek, Cyrillic or accented heading
    # could never be one; and they demand no terminal punctuation, so a heading
    # ending in '?', '&' or a full stop could not be one either.
    # A numbered heading may be set in the plain body face and may wrap. The
    # segmenter isolates it on the strength of its number, so the classifier
    # honours the same evidence rather than losing it to the single-line rule.
    if stands_alone_as_numbered_heading(first_line) and line_count <= HEADING_FACE_LINES \
            and len(text.strip()) <= HEADING_FACE_CHARS:
        return "heading", heading_depth(first_line)
    if typeface and typeface.get("differs_from_body") and typeface.get("bold") \
            and not typeface.get("precedes_numeric_rows") and line_count <= HEADING_FACE_LINES:
        stripped = text.strip()
        # A heading opens a phrase; a fragment cut out of one does not. Without
        # this the face rule promotes the middle of a broken caption.
        opens_a_phrase = bool(stripped) and (stripped[0].isalnum() or stripped[0] in "\u2018\u201c(")
        if opens_a_phrase and len(stripped) <= HEADING_FACE_CHARS and not stripped.endswith((".", ";")):
            return "heading", heading_depth(first_line)
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


def block_prominence(page: dict[str, Any], start: int | None, end: int | None) -> float | None:
    """How large this block is set, against the median line on its own page.

    Only native extraction measures line boxes, so an OCR page returns None and
    the single-line heading rule stands there on its own. A page with almost no
    lines returns None too: a median drawn from one or two lines says nothing
    about what counts as body text.
    """
    if start is None or end is None:
        return None
    page_heights: list[float] = []
    block_heights: list[float] = []
    for line in page.get("native_text_lines", []):
        box = line.get("bbox")
        if not isinstance(box, dict):
            continue
        try:
            height = float(box["y1"]) - float(box["y0"])
        except (KeyError, TypeError, ValueError):
            continue
        if height <= 0:
            continue
        page_heights.append(height)
        if line.get("start", -1) < end and line.get("end", -1) > start:
            block_heights.append(height)
    if len(page_heights) < 4 or not block_heights:
        return None
    page_median = statistics.median(page_heights)
    return statistics.median(block_heights) / page_median if page_median > 0 else None


def block_typeface(page: dict[str, Any], start: int | None, end: int | None) -> dict[str, Any] | None:
    """What face this block is set in, against the document's body face.

    Returns None where the face was never measured -- an OCR page, the pypdf
    fallback, or a build of PDFium without the font call -- so a caller keeps
    exactly the behaviour it had before the face was available.
    """
    if start is None or end is None:
        return None
    body_face = page.get("body_font")
    if not body_face:
        return None
    faces: dict[str, int] = {}
    for line in page.get("native_text_lines", []):
        face = line.get("font")
        if face and line.get("start", -1) < end and line.get("end", -1) > start:
            faces[face] = faces.get(face, 0) + len(str(line.get("text", "")))
    if not faces:
        return None
    face = max(faces.items(), key=lambda item: item[1])[0]
    # A table's column headings are short and set in the same bold as a
    # heading, and a change of face isolates them exactly as it isolates one.
    # What follows is the only thing that separates the two, so the block
    # carries that with it rather than being judged on its own appearance.
    after = sorted((line for line in page.get("native_text_lines", []) if line.get("start", -1) >= end),
                   key=lambda line: line.get("start", 0))[:TABLE_LOOKAHEAD]
    return {
        "face": face,
        "differs_from_body": face != body_face,
        "bold": is_bold_face(face) or _is_bolder_sibling(face, body_face),
        "precedes_numeric_rows": any(looks_like_a_numeric_row(str(line.get("text", ""))) for line in after),
    }


def _is_bolder_sibling(face: str, body_face: str) -> bool:
    """True for a face that is the body face plus a weight suffix.

    Subset fonts are often named by suffix rather than by word: a document set
    in `LinLibertineT` sets its headings in `LinLibertineTB`. The word-based
    test cannot see that, and a bare "is it different" test would call every
    italic and every small-caps face a heading.
    """
    return bool(body_face) and face != body_face and face.startswith(body_face) and face[len(body_face):].upper() in {"B", "BD", "-B", "-BD"}


def make_block(page: dict[str, Any], ordinal: int, text: str, start: int | None = None, end: int | None = None,
               ocr_line_indexes: list[int] | None = None, recovered_rows: list[list[str]] | None = None,
               recovered_bbox: dict[str, Any] | None = None) -> dict[str, Any]:
    typeface = block_typeface(page, start, end)
    kind, level = classify_block(text, prominence=block_prominence(page, start, end), typeface=typeface)
    # Rules the page drew outrank anything the characters suggest. The
    # classifier reads delimiters, and a ruled table carries none: its columns
    # are separated by geometry, so without this a proven table is filed as a
    # paragraph and rendered as one.
    if recovered_rows:
        kind, level = "table", None
    health = native_health(text)
    page_id = f"page-{page['number']}"
    block_id = f"{page_id}-block-{ordinal}"
    confidence = page.get("ocr_confidence", health["confidence"])
    # The ruled rectangle is the table's own outer edge, which is a truer
    # bound than the union of the character boxes inside it.
    bbox = recovered_bbox or source_bbox_for_block(page, text, start, end, ocr_line_indexes)
    links = block_links(page, start, end)
    block = {
        "id": block_id,
        "page": page_id,
        "type": kind,
        "level": level,
        "text": text,
        "bbox": bbox,
        "links": links,
        "source": {"method": page["method"], "confidence": confidence, "language": language_hint(text)},
        "evidence": {
            "native_health": health,
            "validation": ["native-text-integrity", "reading-order-source-order"],
            "findings": {
                "native_text_present": health["native_text_present"],
                "replacement_characters": health["replacement_characters"],
                "source_bbox_available": bbox is not None,
                "ruled_table_recovered": bool(recovered_rows),
                "ruled_table_cell_count": sum(len(row) for row in recovered_rows) if recovered_rows else 0,
                "source_declared_link_count": len(links),
                "unanchorable_link_count": sum(1 for link in links if not is_anchorable_link(link["uri"])),
                "source_bbox_coordinate_space": bbox["coordinate_space"] if bbox else None,
                "ocr_line_count": len(ocr_line_indexes) if ocr_line_indexes is not None else len(page.get("ocr_lines", [])),
            },
            "alternatives": [],
            "repair_history": [],
        },
    }
    if recovered_rows:
        block["table"] = {
            "rows": [list(row) for row in recovered_rows],
            "row_count": len(recovered_rows),
            "column_count": len(recovered_rows[0]) if recovered_rows else 0,
            "source": "ruled-geometry",
        }
    return block


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
        table = block.get("table")
        if table is not None:
            rows = table.get("rows")
            if not isinstance(rows, list) or not rows:
                raise ValueError("A recovered table must carry its rows.")
            if any(not isinstance(row, list) or any(not isinstance(cell, str) for cell in row) for row in rows):
                raise ValueError("Recovered table cells must be strings.")
            # A grid encloses the same number of cells in every row by
            # construction. A ragged one means the cells were not read from the
            # rules that bound them, and no consumer should be handed it.
            if len({len(row) for row in rows}) != 1:
                raise ValueError("A recovered table must have the same number of cells in every row.")


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
        rows = block_table_rows(block)
        if not rows:
            continue
        if previous:
            prior_rows = block_table_rows(previous)
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
    tables = [block for block in blocks if block.get("type") == "table" and block_table_rows(block)]
    by_id = {block["id"]: block for block in tables}
    groups: list[dict[str, Any]] = []
    visited: set[str] = set()
    for root in tables:
        if root["id"] in visited or root.get("evidence", {}).get("cross_page_continuation_of") in by_id:
            continue
        current = root
        root_rows = block_table_rows(current)
        assert root_rows is not None
        merged_rows = list(root_rows)
        source_blocks = [current["id"]]
        visited.add(current["id"])
        while (next_id := current.get("evidence", {}).get("continues_on_block")) in by_id and next_id not in visited:
            current = by_id[next_id]
            rows = block_table_rows(current)
            if not rows or rows[0] != merged_rows[0]:
                break
            merged_rows.extend(rows[1:])
            source_blocks.append(current["id"])
            visited.add(current["id"])
        groups.append({"id": root["id"], "rows": merged_rows, "source_block_ids": source_blocks})
    for orphan in tables:
        if orphan["id"] not in visited:
            rows = block_table_rows(orphan)
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
        if health.get("unresolved_private_use_characters", health.get("private_use_characters", 0)):
            unresolved = health.get("unresolved_private_use_characters", health.get("private_use_characters", 0))
            findings.append(WarningRecord("PRIVATE_USE_CHARACTERS", f"Verified found {unresolved} character(s) this PDF's fonts never mapped to Unicode. They are retained exactly as extracted; Philon did not guess what they represent, so this page needs review before machine reuse.", page=page["number"]))
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


def make_ir(path: Path, profile: str, selection: tuple[int, ...] | None = None) -> tuple[dict[str, Any], list[WarningRecord], list[Timing]]:
    started = time.perf_counter()
    suffix = path.suffix.lower()
    preflight = preflight_input(path)
    validate_page_selection(selection, preflight.get("declared_page_count"))
    if suffix == ".pdf":
        source_pages, warnings = pdfium_extract(path, selection)
        warnings.extend(ocr_textless_pdf_pages(path, source_pages, profile))
        page_numbers = [source_page["number"] for source_page in source_pages]
        for source_page, features in zip(source_pages, native_pdf_features(path, page_numbers)):
            source_page["native_features"] = features
    else:
        source_pages, warnings = image_extract(path, profile)
        validate_page_selection(selection, 1)
    if selection and not source_pages:
        raise ValueError("The page selection matched no page of this document.")

    body_face, body_size = document_body_typeface(source_pages)
    for source_page in source_pages:
        source_page["body_font"], source_page["body_size"] = body_face, body_size
    artifacts = repeated_page_artifacts(source_pages)
    pages: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    for source_page in source_pages:
        page_id = f"page-{source_page['number']}"
        source_parts = geometric_native_parts(source_page, artifacts) if source_page["method"] == "pdfium-native" else ocr_parts_with_geometry(source_page, artifacts)
        page_blocks = [
            make_block(source_page, ordinal, part["text"], part.get("start"), part.get("end"), part.get("line_indexes"),
                       part.get("table_rows"), part.get("table_bbox"))
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
            "rotation": source_page.get("rotation", 0),
            "method": source_page["method"],
            "confidence": confidence,
            "route": route_for_page(source_page, health),
            "native_features": source_page.get("native_features", {"fonts": [], "links": [], "geometry": "normalized-vision-rectangles" if source_page.get("ocr_lines") else "not-applicable"}),
            "source_artifacts": {"numeric_markers": source_page.get("numeric_source_markers", [])},
            "ruled_tables": [
                {key: table[key] for key in ("bbox", "row_count", "column_count", "complete", "crossing_count")}
                for table in source_page.get("ruled_tables", [])
            ],
            "block_ids": [block["id"] for block in page_blocks],
        })
        # A lattice with the shape of a table that its rules do not close is
        # the uncertainty this feature is most likely to meet: a merged cell, a
        # rule drawn only under the headings. Philon reports it and emits
        # nothing, because a table with invented cells is worse than none.
        for table in source_page.get("ruled_tables", []):
            if not table.get("complete") and table["row_count"] >= 2 and table["column_count"] >= 2:
                warnings.append(WarningRecord(
                    "RULED_TABLE_INCOMPLETE",
                    f"A {table['row_count']}x{table['column_count']} arrangement of rules on this page does not "
                    "close into a full grid, so its cells are not proven and no table was recovered from it.",
                    page=source_page["number"],
                ))
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
            "page_selection": compact_page_selection(selection) if selection else None,
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


#: Adobe's Corporate Use Subarea (U+F600-U+F8FF) is a PUBLISHED assignment, not a
#: font's private guess. Where it names a typographic VARIANT of a character that
#: already has a Unicode value, resolving it transcribes what Adobe states and
#: loses only the shape; it never substitutes a different character.
#:
#: Only the families where the base character is not in doubt: a serif or sans
#: copyright sign IS the copyright sign, an old-style figure IS that digit.
#: Small capitals and superior/inferior letters are deliberately NOT resolved --
#: `Asmall` could reasonably be "A" or "a", and a superior letter carries its
#: position as part of its meaning (a footnote marker, an ordinal), so
#: flattening either would decide something the source never said. Those stay in
#: the private-use area and are reported as unreadable, which is the honest
#: answer rather than the fuller-looking one.
#:
#: Generated from the Adobe Glyph List; regenerate with
#:
#:     python3 tools/generate_adobe_glyph_variants.py
#:
#: 16 entries. U+F6D9 is `copyrightserif`, which is how "Adobe Photoshop (c)"
#: reaches the text of one of the reference papers.
ADOBE_GLYPH_VARIANTS: dict[int, int] = {
    0xF6D9: 0x00A9,  # copyrightserif
    0xF6DB: 0x2122,  # trademarkserif
    0xF724: 0x0024,  # dollaroldstyle
    0xF730: 0x0030,  # zerooldstyle
    0xF731: 0x0031,  # oneoldstyle
    0xF732: 0x0032,  # twooldstyle
    0xF733: 0x0033,  # threeoldstyle
    0xF734: 0x0034,  # fouroldstyle
    0xF735: 0x0035,  # fiveoldstyle
    0xF736: 0x0036,  # sixoldstyle
    0xF737: 0x0037,  # sevenoldstyle
    0xF738: 0x0038,  # eightoldstyle
    0xF739: 0x0039,  # nineoldstyle
    0xF7A2: 0x00A2,  # centoldstyle
    0xF8E9: 0x00A9,  # copyrightsans
    0xF8EA: 0x2122,  # trademarksans
}


def resolve_adobe_glyph_variants(text: str) -> str:
    """Replace Adobe Corporate Use Subarea glyphs with the characters they name.

    Only the subarea, and only where Adobe's own list says the glyph is a
    variant of a character that has a Unicode value. A private-use character
    from anywhere else is left exactly as extracted: the font's assignment
    means nothing outside that font, and one of the reference papers proves the
    point -- its maths font ships a `/ToUnicode` CMap that maps some codes to
    real characters and deliberately leaves the rest in the private-use area,
    which is the producer stating its own limit. Resolving those would be
    inventing text.
    """
    if not text:
        return text
    return "".join(
        chr(ADOBE_GLYPH_VARIANTS[ord(character)]) if ord(character) in ADOBE_GLYPH_VARIANTS else character
        for character in text
    )


def count_adobe_glyph_variants(text: str) -> int:
    return sum(1 for character in text if ord(character) in ADOBE_GLYPH_VARIANTS)


def is_discardable_formatting(character: str) -> bool:
    """True for a character that carries layout, never a word.

    Two families. The zero-width joiners and the soft hyphen are legitimate
    formatting a reader is not meant to see. The Unicode *noncharacters* --
    U+FDD0..U+FDEF and U+nFFFE/U+nFFFF in every plane -- are permanently
    reserved and are never valid in interchange; PDFium hands them over where a
    PDF's font maps a hyphenation point to an unassigned slot. Either way the
    character belongs to the layout, so it is dropped from the reading form and
    retained verbatim in `text`.
    """
    code = ord(character)
    if character in {"\u00ad", "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}:
        return True
    if 0xFDD0 <= code <= 0xFDEF:
        return True
    return code & 0xFFFE == 0xFFFE


def count_discardable_formatting(text: str) -> int:
    return sum(1 for character in text if is_discardable_formatting(character))


def clean_reading_text(text: str) -> str:
    """Reflow measured source lines without changing their words or meaning.

    Whitespace, safe line-end hyphenation, and characters that carry layout
    rather than meaning. A noncharacter left in place corrupts the word it sits
    inside -- `de<U+FFFE>picted` -- and makes the output invalid UTF-8 for a
    strict consumer, so it is resolved here and never in `text`.
    """
    measured: list[tuple[str, bool]] = []
    for raw_line in resolve_adobe_glyph_variants(text).splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        # A discardable character at the end of a line is a hyphenation point,
        # exactly as a trailing "-" is; anywhere else it simply vanishes.
        hyphenates = is_discardable_formatting(stripped[-1])
        body = "".join(character for character in stripped if not is_discardable_formatting(character))
        if body:
            measured.append((body, hyphenates))
    if not measured:
        return ""
    joined: list[str] = [measured[0][0]]
    pending_hyphen = measured[0][1]
    for body, hyphenates in measured[1:]:
        continues = bool(re.match(r"^[a-z][A-Za-z'-]*\b", body))
        if continues and pending_hyphen:
            joined[-1] = joined[-1] + body
        elif continues and re.search(r"[A-Za-z]{2,}-$", joined[-1]):
            joined[-1] = joined[-1][:-1] + body
        else:
            joined.append(body)
        pending_hyphen = hyphenates
    return re.sub(r"\s+", " ", " ".join(joined)).strip()


def source_page_number(ir: dict[str, Any], page_id: str) -> int | None:
    page = next((item for item in ir.get("pages", []) if item.get("id") == page_id), None)
    return int(page["number"]) if isinstance(page, dict) and isinstance(page.get("number"), int) else None


def native_images_by_page(ir: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    """Group the extracted source images by the page each was drawn on."""
    grouped: dict[int, list[dict[str, Any]]] = {}
    for image in ir.get("document_artifacts", {}).get("native_images", []):
        for page_number in image.get("source_pages", []) or []:
            if isinstance(page_number, int):
                grouped.setdefault(page_number, []).append(image)
    return grouped


def markdown_page_images(grouped: dict[int, list[dict[str, Any]]], page_number: int | None) -> list[str]:
    """Reference one page's source images, naming them rather than describing them.

    The alt text identifies the asset and says a description is owed. Philon
    does not look at the image, so it must not write what is in it.
    """
    images = grouped.get(page_number or -1) or []
    if not images:
        return []
    lines: list[str] = []
    for image in images:
        asset_id = str(image.get("id", "source-image"))
        relative_path = str(image.get("relative_path", ""))
        if not relative_path:
            continue
        width, height = image.get("pixel_width"), image.get("pixel_height")
        dimensions = f" · {width} × {height}px" if width and height else ""
        lines.extend([
            f"![Extracted source image {asset_id}; source page {page_number}; visual description requires review.]({relative_path})",
            f"_Evidence: native PDF image {asset_id} · source page {page_number}{dimensions} · extraction native-pdf-image-stream._",
            "",
        ])
    return lines


def render_markdown(ir: dict[str, Any]) -> str:
    """Render clean, portable Markdown for reading and for machine ingestion.

    The canonical machine package retains line-level provenance. This layer is
    intentionally simple: source-page markers remain available as comments, and
    each page's extracted source images are referenced where that page ends.

    They are grouped by page rather than composed into figures, because Philon
    extracts embedded image streams and does not infer which of them make up one
    figure. A photomosaic paper embeds dozens of images inside a single printed
    figure; claiming a figure grouping would be inventing structure. The page is
    what the source proves, so the page is what is stated.
    """
    lines: list[str] = []
    document_name = str(ir.get("document", {}).get("source", {}).get("filename", "Philon document"))
    lines.extend(["---", f"title: {document_name}", "generated_by: Philon 0.2", "---", ""])
    images_by_page = native_images_by_page(ir)
    current_page: str | None = None
    for block in ir["blocks"]:
        if block.get("page") != current_page:
            lines.extend(markdown_page_images(images_by_page, source_page_number(ir, current_page) if current_page else None))
            current_page = str(block.get("page"))
            page_number = source_page_number(ir, current_page)
            if page_number is not None:
                lines.extend([f"<!-- Philon source page {page_number} -->", ""])
        text = clean_reading_text(str(block.get("text", "")))
        if not text:
            continue
        text = anchor_links_markdown(text, block.get("links", []))
        if block["type"] == "heading":
            lines.extend(["#" * (block["level"] or 2) + " " + text, ""])
        elif block["type"] == "table" and block_table_rows(block):
            rows = block_table_rows(block) or []
            lines.append("| " + " | ".join(markdown_table_cell(cell) for cell in rows[0]) + " |")
            lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
            lines.extend("| " + " | ".join(markdown_table_cell(cell) for cell in row) + " |" for row in rows[1:])
            lines.append("")
        elif block["type"] == "formula":
            lines.extend(["```text", text, "```", ""])
        elif block["type"] == "caption":
            lines.extend([f"> {text}", ""])
        else:
            lines.extend([text, ""])
    lines.extend(markdown_page_images(images_by_page, source_page_number(ir, current_page) if current_page else None))
    return "\n".join(lines).strip() + "\n"


def render_html(ir: dict[str, Any], include_facsimiles: bool = False) -> str:
    """Render a standalone, responsive presentation document.

    It is text-first for ordinary reading, with each original page available in
    place inside a disclosure control.  This avoids the old end-of-document
    image dump while preserving a truthful route back to the source.
    """
    body: list[str] = []
    blocks_by_page: dict[str, list[dict[str, Any]]] = {}
    for block in ir.get("blocks", []):
        blocks_by_page.setdefault(str(block.get("page")), []).append(block)
    title = str(ir.get("document", {}).get("source", {}).get("filename", "Philon document"))
    body.extend([f"<header class=\"philon-document-header\"><p>Philon 0.2 presentation export</p><h1>{html.escape(title)}</h1><span>Local, source-linked conversion</span></header>", "<main>"])
    pages = list(ir.get("pages", []))
    if not pages:
        seen_page_ids: list[str] = []
        for block in ir.get("blocks", []):
            page_id = str(block.get("page", "page-1"))
            if page_id not in seen_page_ids:
                seen_page_ids.append(page_id)
        pages = [{"id": page_id, "number": index + 1} for index, page_id in enumerate(seen_page_ids)]
    for page in pages:
        page_id = str(page.get("id"))
        page_number = int(page.get("number") or 0)
        body.append(f'<section class="philon-page" id="source-page-{page_number}" data-philon-page="{page_id}">')
        body.append(f'<div class="philon-page-label">Source page {page_number}</div>')
        if include_facsimiles:
            preview = f"assets/page-previews/page-{page_number:04}.png"
            body.append(f'<details class="philon-facsimile"><summary>Show original page</summary><img src="{preview}" alt="Original source page {page_number}; consult it to verify visual layout and figures." loading="lazy" /></details>')
        for block in blocks_by_page.get(page_id, []):
            source = block["source"]
            attrs = f'data-philon-id="{block["id"]}" data-philon-page="{block["page"]}" data-philon-confidence="{source["confidence"]}"'
            reading = clean_reading_text(str(block.get("text", "")))
            content = anchor_links_html(html.escape(reading), reading, block.get("links", []))
            if not content:
                continue
            if block["type"] == "heading":
                body.append(f'<h{block["level"] or 2} {attrs}>{content}</h{block["level"] or 2}>')
            elif block["type"] == "table" and block_table_rows(block):
                rows = block_table_rows(block) or []
                header = "<thead><tr>" + "".join(f"<th scope=\"col\">{html.escape(cell)}</th>" for cell in rows[0]) + "</tr></thead>"
                body_rows = "".join("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>" for row in rows[1:])
                body.append("<table " + attrs + ">" + header + "<tbody>" + body_rows + "</tbody></table>")
            elif block["type"] == "formula":
                body.append(f"<pre {attrs}><code>{content}</code></pre>")
            elif block["type"] == "caption":
                body.append(f"<p class=\"philon-caption\" {attrs}>{content}</p>")
            else:
                body.append(f"<p {attrs}>{content}</p>")
        body.append("</section>")
    body.append("</main>")
    stylesheet = """
    :root { color: #1d1d1f; background: #f5f3ee; font-family: Iowan Old Style, Charter, Georgia, serif; }
    body { margin: 0; line-height: 1.65; }
    .philon-document-header { max-width: 46rem; margin: 0 auto; padding: 5rem 1.5rem 2.5rem; }
    .philon-document-header p, .philon-page-label { color: #77716a; font: 600 .72rem/1.2 ui-sans-serif, system-ui, sans-serif; letter-spacing: .11em; text-transform: uppercase; }
    h1 { font-size: clamp(2.2rem, 6vw, 4.4rem); line-height: 1.03; letter-spacing: -.045em; margin: .35rem 0 1rem; }
    .philon-document-header span { color: #5d5b57; }
    main { max-width: 46rem; margin: 0 auto; padding: 0 1.5rem 6rem; }
    .philon-page { border-top: 1px solid #d8d3cb; padding: 2.2rem 0 2.8rem; }
    .philon-page > :last-child { margin-bottom: 0; }
    .philon-page h2, .philon-page h3 { line-height: 1.15; margin: 1.8em 0 .55em; }
    .philon-page p { margin: 0 0 1em; }
    .philon-caption { color: #6d6259; font-size: .93em; font-style: italic; }
    .philon-facsimile { margin: .5rem 0 1.5rem; font-family: ui-sans-serif, system-ui, sans-serif; }
    .philon-facsimile summary { cursor: pointer; color: #735d44; font-size: .88rem; }
    .philon-facsimile img { display: block; width: 100%; margin-top: .85rem; border: 1px solid #d8d3cb; background: white; }
    table { width: 100%; border-collapse: collapse; margin: 1.25rem 0; font-size: .94em; }
    th, td { padding: .5rem; text-align: left; vertical-align: top; border-bottom: 1px solid #d8d3cb; }
    pre { overflow: auto; padding: 1rem; background: #eeeae3; }
    @media (prefers-color-scheme: dark) { :root { color: #f3f0ea; background: #1d1b19; } .philon-document-header p, .philon-page-label, .philon-document-header span, .philon-caption { color: #c8c0b6; } .philon-page { border-color: #47423d; } .philon-facsimile summary { color: #ddbd91; } th, td { border-color: #47423d; } pre { background: #302c28; } }
    """
    return "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\" /><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" /><title>" + html.escape(title) + "</title><style>" + stylesheet + "</style></head><body>" + "\n".join(body) + "</body></html>\n"


def machine_block_record(block: dict[str, Any], page_number: int | None) -> dict[str, Any]:
    """Make a compact, schema-stable record for parsers and databases."""
    return {
        "id": block.get("id"), "page_id": block.get("page"), "page_number": page_number,
        "type": block.get("type"), "level": block.get("level"),
        "text": block.get("text", ""), "reading_text": clean_reading_text(str(block.get("text", ""))),
        "bbox": block.get("bbox"), "table": block.get("table"),
        "source": block.get("source"), "evidence": block.get("evidence"), "review": block.get("review"),
    }


def write_machine_package(ir: dict[str, Any], output_dir: Path) -> Path:
    """Write Philon's compact, lossless machine package beside human exports."""
    root = output_dir / "machine"
    pages_dir = root / "pages"
    page_numbers = {str(page.get("id")): int(page["number"]) for page in ir.get("pages", []) if isinstance(page.get("number"), int)}
    records = [machine_block_record(block, page_numbers.get(str(block.get("page")))) for block in ir.get("blocks", [])]
    reading_order = [{"page_id": page.get("id"), "page_number": page.get("number"), "block_ids": page.get("block_ids", [])} for page in ir.get("pages", [])]
    package = {
        "schema_version": "philon-machine-package/1.0", "document": ir.get("document", {}),
        "files": {"blocks": "blocks.ndjson", "reading_order": "reading-order.json", "pages": "pages/", "assets": "assets.json", "evidence": "../" + safe_slug(Path(str(ir.get("document", {}).get("source", {}).get("filename", "document"))).stem) + ".evidence.json"},
        "guarantees": ["source text is retained in blocks.ndjson", "reading_text changes whitespace, safe line-end hyphenation and characters that carry layout rather than meaning", "geometry and uncertainty remain explicit", "visual descriptions are never inferred"],
    }
    atomic_write_text(root / "package.json", json.dumps(package, indent=2, ensure_ascii=False))
    atomic_write_text(root / "reading-order.json", json.dumps({"schema_version": "1.0", "items": reading_order}, indent=2, ensure_ascii=False))
    atomic_write_text(root / "assets.json", json.dumps({"schema_version": "1.0", "native_images": ir.get("document_artifacts", {}).get("native_images", [])}, indent=2, ensure_ascii=False))
    atomic_write_text(root / "blocks.ndjson", "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records))
    for page in ir.get("pages", []):
        page_id = str(page.get("id"))
        number = int(page.get("number") or 0)
        page_records = [record for record in records if record["page_id"] == page_id]
        page_payload = {"schema_version": "1.0", "page": page, "blocks": page_records}
        atomic_write_text(pages_dir / f"page-{number:04}.json", json.dumps(page_payload, indent=2, ensure_ascii=False))
        page_markdown = [f"<!-- Philon source page {number} -->", ""]
        for record in page_records:
            text = str(record["reading_text"])
            if record["type"] == "heading":
                page_markdown.extend(["#" * (record["level"] or 2) + " " + text, ""])
            elif record["type"] == "caption":
                page_markdown.extend([f"> {text}", ""])
            else:
                page_markdown.extend([text, ""])
        atomic_write_text(pages_dir / f"page-{number:04}.md", "\n".join(page_markdown).strip() + "\n")
    atomic_write_text(root / "README.md", "# Philon machine package\n\nUse `blocks.ndjson` for streaming ingestion, `reading-order.json` for document order, `pages/` for page-level records, and `assets.json` for source-image provenance. `text` is source-retained; `reading_text` is the safe reflowed form.\n")
    return root
def page_tree_block_type(block: dict[str, Any]) -> str:
    """Map Philon's stable block taxonomy to the familiar page-tree names.

    This is a clean-room export adapter. It intentionally shares the useful
    page/children/images shape that downstream page-tree consumers expect,
    while keeping Philon's provenance in separate, explicit fields.
    """
    return {
        "heading": "SectionHeader", "table": "Table", "formula": "Equation",
        "figure": "Figure", "caption": "Caption", "citation": "Text",
    }.get(block.get("type"), "Text")


def page_tree_polygon(block: dict[str, Any], page: dict[str, Any]) -> list[list[float]] | None:
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


def page_tree_html(block: dict[str, Any]) -> str:
    content = html.escape(str(block.get("text", ""))).replace("\n", "<br />")
    block_type = block.get("type")
    if block_type == "heading":
        level = int(block.get("level") or 2)
        return f"<h{level}>{content}</h{level}>"
    if block_type == "table" and block_table_rows(block):
        rows = block_table_rows(block) or []
        head = "".join(f"<th>{html.escape(cell)}</th>" for cell in rows[0])
        body = "".join("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>" for row in rows[1:])
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    if block_type == "formula":
        return f"<math>{content}</math>"
    if block_type == "caption":
        return f"<figcaption>{content}</figcaption>"
    return f"<p>{content}</p>"


def render_page_tree_json(ir: dict[str, Any], output_dir: Path) -> list[dict[str, Any]]:
    """Render a portable page-tree JSON with embedded images.

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
            node_type = page_tree_block_type(block)
            node_id = f"/page/{page_number}/{node_type}/{index}"
            if block.get("type") == "heading":
                level = int(block.get("level") or 2)
                section_hierarchy = {key: value for key, value in section_hierarchy.items() if int(key) < level}
                section_hierarchy[str(level)] = node_id
            children.append({
                "id": node_id,
                "block_type": node_type,
                "html": page_tree_html(block),
                "polygon": page_tree_polygon(block, page),
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
    tables = sum(block["type"] == "table" and block_table_rows(block) is not None for block in ir["blocks"])
    findings.append({"rule": "native-tables", "status": "pass" if tables else "not-applicable", "message": "Native tables include header cells in semantic HTML." if tables else "No deterministically structured native tables were emitted."})
    figures = sum(block["type"] == "figure" for block in ir["blocks"]) + len(ir.get("document_artifacts", {}).get("native_images", []))
    if figures:
        findings.append({"rule": "figure-alternatives", "status": "review", "message": "Figure alternatives require human authoring; Philon does not infer them."})
    findings.append({"rule": "conformance", "status": "review", "message": "This report is an accessibility finding set, not a WCAG conformance claim."})
    return {"status": "assessed", "findings": findings}


#: The phases after the IR cache write large files into a content-addressed
#: destination, so a second conversion of the same bytes regenerates work it
#: already has on disk. A manifest written atomically AFTER a phase succeeds is
#: what makes reuse safe: an interrupted run leaves no manifest, so it cannot be
#: mistaken for a finished one.
ARTIFACT_MANIFEST_VERSION = "1.2"


def verified_artifact_manifest(manifest_path: Path, source: Path, expected_source_pages: int | None = None) -> dict[str, Any] | None:
    """Read a phase manifest only if every file it claims is still exactly right.

    Verifies the recorded sha256 of each file rather than its size alone. That
    costs milliseconds against the seconds the phase takes, and it is what lets
    reuse be equivalent to recomputation rather than merely likely to be.
    """
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict) or manifest.get("schema_version") != ARTIFACT_MANIFEST_VERSION:
        return None
    # Provenance is recorded per conversion, so a manifest written for another
    # source path is not this document's evidence even if the bytes match.
    if manifest.get("source") != str(source):
        return None
    if expected_source_pages is not None and manifest.get("source_pages") != expected_source_pages:
        return None
    for item in manifest.get("items", []):
        if not isinstance(item, dict):
            return None
        recorded = item.get("bytes_sha256")
        target = Path(str(item.get("path", "")))
        if not recorded or not target.is_file():
            return None
        try:
            if sha256_file(target) != recorded:
                return None
        except OSError:
            return None
    return manifest


def render_source_previews(path: Path, output_dir: Path, page_numbers: list[int], reuse: bool = False) -> tuple[list[str], list[WarningRecord]]:
    """Export bounded local page rasters for source review, never as OCR input.

    These preview assets let the desktop client draw an evidence rectangle over
    the actual source page. They are deliberately rendered at 144 DPI: enough
    for inspection without turning a long document into an unbounded cache.
    """
    preview_dir = output_dir / "assets" / "page-previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = preview_dir / "manifest.json"
    if reuse:
        recorded = verified_artifact_manifest(manifest_path, path, len(page_numbers))
        if recorded is not None:
            return [str(item["path"]) for item in recorded["items"]], []
    previews: list[str] = []
    warnings: list[WarningRecord] = []
    try:
        if path.suffix.lower() == ".pdf":
            import pypdfium2 as pdfium  # type: ignore

            document = pdfium.PdfDocument(str(path))
            for number in page_numbers:
                if not 1 <= number <= len(document):
                    continue
                # Named by the page's own number, so a preview of page 40 is
                # page-0040.png whether or not pages 1 to 39 were converted.
                target = preview_dir / f"page-{number:04}.png"
                page = document[number - 1]
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
    # Written only once every page rendered, and written atomically, so an
    # interrupted run leaves no manifest and cannot be reused.
    atomic_write_text(manifest_path, json.dumps({
        "schema_version": ARTIFACT_MANIFEST_VERSION, "source": str(path), "source_pages": len(page_numbers),
        "items": [{"path": item, "bytes_sha256": sha256_file(Path(item))} for item in previews],
    }, indent=2, ensure_ascii=False))
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


def extract_native_pdf_assets(path: Path, output_dir: Path, reuse: bool = False, page_numbers: list[int] | None = None) -> tuple[dict[str, Any] | None, list[WarningRecord]]:
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
    manifest_path = asset_dir / "manifest.json"
    if reuse:
        recorded = verified_artifact_manifest(manifest_path, path)
        if recorded is not None:
            # The warnings are replayed from the manifest, not dropped: a run
            # that hit the asset limit must say so again, or reusing its work
            # would quietly turn a truncated export into a complete-looking one.
            replayed = [WarningRecord(**warning) for warning in recorded.get("warnings", [])]
            return {"manifest": str(manifest_path), "items": recorded.get("items", [])}, replayed
    items: list[dict[str, Any]] = []
    references: dict[str, dict[str, Any]] = {}
    written_bytes = 0
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path), strict=False)
        wanted = set(page_numbers or ())
        for page_index, page in enumerate(reader.pages, start=1):
            if wanted and page_index not in wanted:
                continue
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
                    atomic_write_text(manifest_path, json.dumps({"schema_version": ARTIFACT_MANIFEST_VERSION, "source": str(path), "items": items, "limits": {"max_assets": MAX_EXTRACTED_ASSETS, "max_bytes": MAX_EXTRACTED_ASSET_BYTES}, "truncated": True, "warnings": [asdict(warning) for warning in warnings]}, indent=2, ensure_ascii=False))
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
        manifest = {"schema_version": ARTIFACT_MANIFEST_VERSION, "source": str(path), "items": items, "limits": {"max_assets": MAX_EXTRACTED_ASSETS, "max_bytes": MAX_EXTRACTED_ASSET_BYTES}, "warnings": [asdict(warning) for warning in warnings]}
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
    selected = set(selected_outputs or DEFAULT_OUTPUTS)
    base = safe_slug(Path(ir["document"]["source"]["filename"]).stem)
    paths = {
        "markdown": output_dir / f"{base}.md",
        "html": output_dir / f"{base}.html",
        "ir": output_dir / f"{base}.philon.json",
        "page_tree": output_dir / f"{base}.page-tree.json",
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
    if "page_tree" in selected:
        atomic_write_text(paths["page_tree"], json.dumps(render_page_tree_json(ir, output_dir), indent=2, ensure_ascii=False))
    machine_root: Path | None = None
    if "machine" in selected:
        machine_root = write_machine_package(ir, output_dir)
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


def cache_path(cache_dir: Path, content_hash: str, profile: str, selection: tuple[int, ...] | None = None) -> Path:
    """Name a cache entry after everything that decides what is inside it.

    The entry holds an IR, so it carries the IR's version: an engine that
    records a different evidence shape never reaches an entry written against
    the old one, rather than reading it and having to reject it.
    """
    token = page_selection_token(selection)
    suffix = f"-{token}" if token else ""
    return cache_dir / f"{content_hash}-{profile.lower()}-{safe_slug(ENGINE_VERSION)}-ir{safe_slug(IR_VERSION)}{suffix}.json"


def convert_file(path: Path, profile: str, output_root: Path, cache_root: Path, cache_policy: str = "use", outputs: Iterable[str] | None = None, progress: Any | None = None, selection: tuple[int, ...] | None = None) -> dict[str, Any]:
    """Convert one file while exposing conservative, truthful milestones.

    The native extraction itself is a bounded third-party operation and cannot
    report a trustworthy per-page percentage.  The surrounding milestones are
    therefore explicit rather than fabricating a smooth percentage.
    """
    report = progress or (lambda _stage, _percent, _message: None)
    report("validating", 4, "Validating the local source")
    validate_page_selection(selection, preflight_input(path).get("declared_page_count"))
    if cache_policy not in {"use", "bypass", "refresh"}:
        raise ValueError("Cache policy must be use, bypass, or refresh.")
    content_hash = sha256_file(path)
    cache_file = cache_path(cache_root, content_hash, profile, selection)
    cached = False
    if cache_policy == "use" and cache_file.exists():
        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            ir = payload["ir"]
            warnings = [WarningRecord(**warning) for warning in payload["warnings"]]
            timings = [Timing(**timing) for timing in payload["timings"]]
            validate_ir(ir)
            cached = True
        except (OSError, ValueError, KeyError, TypeError):
            # An entry that cannot be read back, or that this engine no longer
            # recognises, is not evidence of anything. It is recomputed from the
            # source rather than failing a conversion the source still supports:
            # reuse is an optimisation, and a broken optimisation must not be
            # able to refuse a document.
            cached = False
    if cached:
        report("cache", 28, "Reusing verified local conversion data")
    else:
        report("extracting", 22, "Extracting source structure locally")
        ir, warnings, timings = make_ir(path, profile, selection)
        if cache_policy != "bypass":
            report("caching", 67, "Saving local conversion evidence")
            cache_root.mkdir(parents=True, exist_ok=True)
            atomic_write_text(cache_file, json.dumps({"ir": ir, "warnings": [asdict(warning) for warning in warnings], "timings": [asdict(timing) for timing in timings]}, ensure_ascii=False))
    # The selection is part of the export's name for the same reason it is part
    # of the cache key: a conversion of ten pages must not be written over the
    # conversion of the whole book, nor be mistaken for it later.
    token = page_selection_token(selection)
    destination = output_root / (
        f"{safe_slug(path.stem)}-{content_hash[:12]}-{profile.lower()}" + (f"-{token}" if token else "")
    )
    selected_outputs = set(outputs or DEFAULT_OUTPUTS)
    preview_paths: list[str] = []
    extracted_assets: dict[str, Any] | None = None
    overlay_paths: list[str] = []
    needs_source_previews = bool({"assets", "html", "machine"} & selected_outputs)
    # The cache covered make_ir alone, which is 17.7% of a text-heavy document
    # and 1.1% of an image-heavy one, so a hit saved almost nothing on exactly
    # the documents that cost the most. These two phases are the expensive ones
    # and their outputs already live in a content-addressed destination; reusing
    # them is what makes a warm conversion warm. `bypass` and `refresh` still
    # recompute, so a caller can always demand the work be done again.
    reuse_artifacts = cache_policy == "use"
    if needs_source_previews:
        report("previews", 76, "Rendering source previews for review")
        converted_pages = [int(page["number"]) for page in ir["pages"]]
        preview_paths, preview_warnings = render_source_previews(path, destination, converted_pages, reuse=reuse_artifacts)
        warnings.extend(preview_warnings)
    if "assets" in selected_outputs:
        report("assets", 86, "Extracting native source assets")
        extracted_assets, extraction_warnings = extract_native_pdf_assets(
            path, destination, reuse=reuse_artifacts,
            page_numbers=[int(page["number"]) for page in ir["pages"]] if selection else None,
        )
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
        "page_selection": compact_page_selection(selection) if selection else None,
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
    selection = parse_page_selection(config.get("pages"))
    requested_outputs = config.get("outputs", DEFAULT_OUTPUTS)
    if not isinstance(requested_outputs, (list, tuple)) or not requested_outputs:
        raise ValueError("At least one output must be requested.")
    # Verified always exports embeddings. Build a new list rather than
    # appending, so a caller's request object is never altered and the
    # response never aliases it.
    outputs = list(requested_outputs)
    if profile == "Verified" and "embeddings" not in outputs:
        outputs = [*outputs, "embeddings"]
    root = Path(config.get("workspace_dir") or Path.home() / "Library" / "Application Support" / "Philon")
    output_root = root / "exports"
    cache_root = root / "cache"
    results, failures = [], []
    total = len(files)
    for index, item in enumerate(files):
        def report_file(stage: str, percent: int, message: str, *, index: int = index, item: Path = item) -> None:
            overall = round(((index + percent / 100) / total) * 100)
            if progress:
                progress({"job_id": config.get("job_id"), "current": index + 1, "total": total, "percent": overall, "stage": stage, "message": message, "source_path": str(item)})
        try:
            report_file("starting", 1, f"Starting {item.name}")
            results.append(convert_file(item, profile, output_root, cache_root, cache_policy, outputs, report_file, selection))
        except Exception as exc:  # batch items fail independently
            failures.append({"source_path": str(item), "error": str(exc)})
            report_file("failed", 100, f"Could not convert {item.name}")
    return {"id": str(uuid.uuid4()), "profile": profile, "local_only": True, "outputs": outputs, "cache_policy": cache_policy, "page_selection": compact_page_selection(selection) if selection else None, "local_repair_requested": bool(config.get("local_repair", False)), "results": results, "failures": failures, "created_at": now()}


def action_preflight(request: dict[str, Any], progress: Any | None = None) -> dict[str, Any]:
    files = [Path(item) for item in request.get("config", {}).get("input_paths", [])]
    if not files:
        raise ValueError("Choose at least one PDF or image.")
    items: list[dict[str, Any]] = []
    total = len(files)
    for index, path in enumerate(files):
        if progress:
            progress({"job_id": request.get("config", {}).get("job_id"), "current": index + 1, "total": total, "percent": round((index / total) * 100), "stage": "validating", "message": f"Inspecting {path.name}", "source_path": str(path)})
        try:
            inspection = preflight_input(path)
            items.append({"source_path": str(path), "status": "ready", "preflight": inspection, "route": "manual-local-recognition-required" if inspection["kind"] == "image" else "native-text-pending"})
        except Exception as exc:
            items.append({"source_path": str(path), "status": "blocked", "error": str(exc)})
        if progress:
            progress({"job_id": request.get("config", {}).get("job_id"), "current": index + 1, "total": total, "percent": round(((index + 1) / total) * 100), "stage": "complete", "message": f"Inspected {path.name}", "source_path": str(path)})
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
    # THE PORT'S ONE INTENTIONAL DIVERGENCE FROM THE SOURCE ENGINE.
    # A non-zero local embedding process is converted into evidence rather than
    # failing an otherwise valid conversion, and no vector is emitted. The extra
    # RuntimeError is what the non-zero exit above raises; the source project
    # lets it propagate. Documented in docs/PARITY.md.
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        return None, WarningRecord("EMBEDDING_FAILED", f"Local BGE-M3 embedding was not produced: {exc}. Chunks were exported without vectors.")


def action_repair(request: dict[str, Any], progress: Any | None = None) -> dict[str, Any]:
    repair_mode = request.get("repair_mode", "transcription")
    if repair_mode not in {"transcription", "table", "formula"}:
        raise ValueError("Repair mode must be transcription, table, or formula.")
    packs = model_status()["packs"]
    enabled_config = request.get("enabled_model_ids")
    enabled_model_ids = {item for item in enabled_config if isinstance(item, str)} if isinstance(enabled_config, list) else {"qwen3.8-27b-local-repair", "olmocr-2-7b-local-candidate"}
    pack = next((item for pack_id in ("qwen3.8-27b-local-repair", "olmocr-2-7b-local-candidate") for item in packs if item["id"] == pack_id and item["id"] in enabled_model_ids and item["approved"] and item["available_locally"] and item.get("local_path")), None)
    if not pack or not pack["approved"] or not pack["available_locally"] or not pack.get("local_path"):
        return {"status": "unavailable", "message": "No enabled local repair model was found. Enable an approved local model in Models; Philon retained the source evidence and did not fabricate a repair.", "block_id": request.get("block_id")}
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
            progress({"job_id": request.get("job_id"), "percent": 8, "stage": "preparing", "message": "Preparing the selected source region", "source_path": str(source), "indeterminate": True})
        crop_path = repair_crop(source, page, block, ir_path.parent)
        if progress:
            progress({"job_id": request.get("job_id"), "percent": 30, "stage": "recognising", "message": "Running the local repair model", "source_path": str(source), "indeterminate": True})
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
        progress({"job_id": request.get("job_id"), "percent": 100, "stage": "complete", "message": "Repair candidate is ready", "source_path": str(source)})
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
        def report_progress(payload: dict[str, Any]) -> None:
            # Unix socket writes are intentionally tiny and line-delimited. This
            # lets the desktop host forward progress while a CPU-bound local
            # conversion continues, without opening another listener or
            # exposing the engine beyond this authenticated socket.
            writer.write((json.dumps({"type": "progress", "data": payload}, ensure_ascii=False) + "\n").encode("utf-8"))
        action = request.get("action")
        if action == "convert":
            response = {"ok": True, "data": action_convert(request, report_progress)}
        elif action == "preflight":
            response = {"ok": True, "data": action_preflight(request, report_progress)}
        elif action == "repair":
            response = {"ok": True, "data": action_repair(request, report_progress)}
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
