#!/usr/bin/env python3
"""Run Philon's deterministic local benchmark against a private corpus manifest."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import re
import resource
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("philon_engine", ROOT / "engine" / "philon_engine.py")
assert SPEC and SPEC.loader
engine = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = engine
SPEC.loader.exec_module(engine)


def render_argument(value: str, source: Path, output: Path) -> str:
    return value.replace("{input}", str(source)).replace("{output}", str(output))


def comparator_version(spec: dict[str, object]) -> dict[str, object]:
    command = spec.get("version_command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
        return {"status": "not-recorded"}
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
        return {"status": "recorded", "returncode": result.returncode, "stdout": result.stdout[-1_000:], "stderr": result.stderr[-1_000:]}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "failed", "error": str(exc)}


def run_comparator(name: str, spec: dict[str, object], source: Path, root: Path, case_id: str) -> dict[str, object]:
    """Run an explicitly declared external comparator without importing it.

    The private manifest owns commands and options. Philon never supplies a
    model, downloads a dependency, or assumes that a comparator is licensed
    for redistribution.
    """
    command = spec.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
        return {"status": "blocked", "error": "Comparator command must be a non-empty string array."}
    timeout_seconds = spec.get("timeout_seconds", 900)
    if not isinstance(timeout_seconds, int) or timeout_seconds < 1 or timeout_seconds > 3600:
        return {"status": "blocked", "error": "Comparator timeout_seconds must be an integer from 1 to 3600."}
    destination = root / safe_component(name) / safe_component(case_id)
    destination.mkdir(parents=True, exist_ok=True)
    invocation = [render_argument(item, source, destination) for item in command]
    started = time.perf_counter()
    try:
        process = subprocess.run(invocation, check=False, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "milliseconds": round((time.perf_counter() - started) * 1000), "command": invocation}
    except OSError as exc:
        return {"status": "failed", "milliseconds": round((time.perf_counter() - started) * 1000), "command": invocation, "error": str(exc)}
    artifacts = [path for path in destination.rglob("*") if path.is_file()]
    return {
        "status": "completed" if process.returncode == 0 else "failed",
        "milliseconds": round((time.perf_counter() - started) * 1000),
        "command": invocation,
        "returncode": process.returncode,
        "artifacts": [{"path": str(path.relative_to(destination)), "bytes": path.stat().st_size} for path in artifacts],
        "stdout": process.stdout[-2_000:],
        "stderr": process.stderr[-2_000:],
    }


def source_identity(source: Path) -> dict[str, object]:
    """Name the document a result was measured on, without publishing it.

    `bench/README.md` gates a public claim on running "the same version-pinned
    corpus, hardware, and methodology" -- and until now a result recorded the
    hardware and the methodology and not one word about which files it read.
    The corpus is private, so the path is not the thing to record; the digest
    is. Two runs carrying the same digests measured the same bytes, and that is
    provable by anyone holding the corpus without the corpus leaving the
    machine.

    The name is recorded too, because a digest alone is unreadable to a person
    trying to work out what a year-old result covered.
    """
    digest = hashlib.sha256()
    with open(source, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return {"filename": source.name, "bytes": source.stat().st_size, "sha256": digest.hexdigest()}


def safe_component(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value) or "comparator"


def tokens(value: str) -> list[str]:
    return re.findall(r"[\w]+|[^\w\s]", value.casefold(), flags=re.UNICODE)


def edit_distance(left: list[str], right: list[str]) -> int:
    """Small deterministic Levenshtein implementation for private gold text."""
    if len(left) < len(right):
        left, right = right, left
    row = list(range(len(right) + 1))
    for left_index, left_token in enumerate(left, 1):
        next_row = [left_index]
        for right_index, right_token in enumerate(right, 1):
            next_row.append(min(next_row[-1] + 1, row[right_index] + 1, row[right_index - 1] + (left_token != right_token)))
        row = next_row
    return row[-1]


def similarity(expected: str, actual: str) -> float | None:
    expected_tokens, actual_tokens = tokens(expected), tokens(actual)
    if not expected_tokens:
        return None
    return round(max(0.0, 1 - edit_distance(expected_tokens, actual_tokens) / len(expected_tokens)), 6)


def read_private_text(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    return path.read_text(encoding="utf-8") if path.is_file() else None


def output_contract(result: dict[str, object], expected: dict[str, object]) -> dict[str, object]:
    """Evaluate output and gold checks without writing private corpus content."""
    outputs = result.get("outputs", {})
    outputs = outputs if isinstance(outputs, dict) else {}
    requested = expected.get("required_outputs", ["machine", "markdown", "html", "ir", "chunks", "evidence", "manifest"])
    required = [item for item in requested if isinstance(item, str)] if isinstance(requested, list) else []
    missing = [item for item in required if not outputs.get(item)]
    markdown = read_private_text(outputs.get("markdown"))
    gold_markdown = read_private_text(expected.get("gold_markdown_path"))
    expected_formula = expected.get("formula")
    actual_formula = "\n".join(block.get("text", "") for block in result.get("blocks", []) if isinstance(block, dict) and block.get("type") == "formula")
    expected_tables = expected.get("table_cells")
    actual_tables: list[str] = []
    for path in outputs.get("table_csv", []) if isinstance(outputs.get("table_csv"), list) else []:
        try:
            actual_tables.append(Path(path).read_text(encoding="utf-8"))
        except OSError:
            pass
    table_score = similarity("\n".join(str(item).strip() for item in expected_tables), "\n".join(actual_tables)) if isinstance(expected_tables, list) else None
    return {"word_accuracy": similarity(gold_markdown, markdown) if gold_markdown is not None and markdown is not None else None, "formula_similarity": similarity(expected_formula, actual_formula) if isinstance(expected_formula, str) else None, "table_cell_accuracy": table_score, "required_outputs": required, "missing_outputs": missing, "unsupported_output_rate": round(len(missing) / len(required), 6) if required else None}


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Philon's local engine")
    parser.add_argument("manifest", type=Path, help="Private corpus manifest JSON")
    parser.add_argument("--output", type=Path, default=Path("bench/results/latest.json"))
    parser.add_argument("--profile", default="Balanced", choices=("Fast", "Balanced", "Verified"))
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    comparator_specs = manifest.get("comparators", {})
    if not isinstance(comparator_specs, dict):
        raise ValueError("comparators must be an object keyed by comparator name.")
    output = {"created_at": datetime.now(timezone.utc).isoformat(), "machine": {"platform": platform.platform(), "python": platform.python_version()}, "corpus": {"name": manifest.get("corpus_name"), "version": manifest.get("version")}, "profile": args.profile, "comparators": {name: comparator_version(spec) if isinstance(spec, dict) else {"status": "blocked", "error": "Comparator specification must be an object."} for name, spec in comparator_specs.items()}, "results": []}
    with tempfile.TemporaryDirectory(prefix="philon-bench-") as directory:
        root = Path(directory)
        for document in manifest.get("documents", []):
            source = Path(document["path"])
            started = time.perf_counter()
            try:
                result = engine.convert_file(source, args.profile, root / "exports", root / "cache")
                cold_milliseconds = round((time.perf_counter() - started) * 1000)
                warm_started = time.perf_counter()
                warm_result = engine.convert_file(source, args.profile, root / "exports", root / "cache")
                warm_milliseconds = round((time.perf_counter() - warm_started) * 1000)
                expected = document.get("expected", {})
                if not isinstance(expected, dict):
                    expected = {}
                pages = len(result["pages"])
                warnings = len(result["warnings"])
                comparisons = {name: run_comparator(name, spec, source, root / "comparators", str(document["id"])) if isinstance(spec, dict) else {"status": "blocked", "error": "Comparator specification must be an object."} for name, spec in comparator_specs.items()}
                contract = output_contract(result, expected)
                output["results"].append({"id": document["id"], "cohort": document.get("cohort", "unclassified"), "source": source_identity(source), "status": result["status"], "milliseconds": cold_milliseconds, "latency": {"cold_milliseconds": cold_milliseconds, "warm_milliseconds": warm_milliseconds, "warm_cache_hit": warm_result["cache_hit"], "pages_per_second_cold": round(pages / (cold_milliseconds / 1000), 4) if cold_milliseconds else None}, "pages": pages, "blocks": len(result["blocks"]), "warnings": warnings, "cache_hit": result["cache_hit"], "native_fast_path_pages": sum(page["route"]["decision"] == "native-fast-path" for page in result["pages"]), "metrics": {"uncertainty_rate": round(warnings / max(1, len(result["blocks"])), 6), "source_map_coverage": round(sum(block.get("bbox") is not None for block in result["blocks"]) / max(1, len(result["blocks"])), 6), **contract}, "comparators": comparisons, "gates": {"min_pages": pages >= expected.get("min_pages", 0), "max_warnings": warnings <= expected.get("max_warnings", float("inf")), "required_outputs": not contract["missing_outputs"]}})
            except Exception as exc:
                identity = source_identity(source) if source.is_file() else {"filename": source.name, "bytes": None, "sha256": None}
                output["results"].append({"id": document["id"], "cohort": document.get("cohort", "unclassified"), "source": identity, "status": "failed", "error": str(exc)})
    completed = [item for item in output["results"] if item["status"] != "failed"]
    durations = sorted(item["milliseconds"] for item in completed)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    output["summary"] = {"documents": len(output["results"]), "completed": len(completed), "failed": len(output["results"]) - len(completed), "median_milliseconds": durations[len(durations) // 2] if durations else None, "gate_failures": sum(not all(item["gates"].values()) for item in completed), "engine_process_peak_rss": rss, "rss_unit": "bytes on macOS, KiB on Linux"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output["summary"], indent=2))


if __name__ == "__main__":
    main()
