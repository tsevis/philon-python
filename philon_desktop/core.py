"""Persistent local application services for the Qt desktop host.

The conversion engine remains the source of truth for preflight, evidence and
exports. This module owns only desktop state: SQLite history, recoverable
queues, user preferences, and collision-safe copying of completed exports.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from engine import philon_engine as engine

PROFILES = ("Fast", "Balanced", "Verified")
OUTPUTS = ("machine", "markdown", "html", "ir", "chunks", "evidence", "table_csv", "assets", "manifest", "marker_json")
DEFAULT_OUTPUTS = tuple(output for output in OUTPUTS if output != "marker_json")
TERMINAL_STATES = {"completed", "completed_with_warnings", "failed", "cancelled"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_data_dir() -> Path:
    """Keep Philon state in the standard local macOS application location."""
    # An explicit override is useful for offline test/release sandboxes and is
    # never a network setting. Normal desktop launches use Application Support.
    override = os.environ.get("PHILON_DATA_DIR")
    return Path(override).expanduser() if override else Path.home() / "Library" / "Application Support" / "Philon Python"


class LocalStore:
    """SQLite state with short transactions and explicit interruption recovery."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_data_dir()).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.root / "philon.sqlite3"
        self._lock = threading.RLock()
        self._migrate()
        self.recover_interrupted_batches()

    @contextmanager
    def connection(self):
        with self._lock:
            connection = sqlite3.connect(self.database_path)
            connection.row_factory = sqlite3.Row
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _migrate(self) -> None:
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    status TEXT NOT NULL,
                    documents INTEGER NOT NULL,
                    warnings INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_jobs (
                    id TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    cache_policy TEXT NOT NULL,
                    outputs TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_state TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_items (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    result TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(batch_id) REFERENCES batch_jobs(id)
                );
                CREATE INDEX IF NOT EXISTS batch_items_by_batch ON batch_items(batch_id, status);
            """)

    def recover_interrupted_batches(self) -> None:
        recovered = utc_now()
        with self.connection() as db:
            db.execute("UPDATE batch_items SET status='queued', error='Recovered after an interrupted Philon session.', updated_at=? WHERE status='running'", (recovered,))
            db.execute("UPDATE batch_jobs SET status='queued', requested_state=NULL, updated_at=? WHERE status='running'", (recovered,))

    def setting(self, key: str, default: Any = None) -> Any:
        with self.connection() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def save_setting(self, key: str, value: Any) -> None:
        with self.connection() as db:
            db.execute("INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (key, json.dumps(value), utc_now()))

    def store_job(self, payload: dict[str, Any]) -> None:
        results = payload.get("results", [])
        warnings = sum(len(item.get("warnings", [])) for item in results if isinstance(item, dict))
        status = "completed_with_warnings" if warnings or payload.get("failures") else "completed"
        with self.connection() as db:
            db.execute("INSERT INTO jobs(id,created_at,profile,status,documents,warnings,payload) VALUES(?,?,?,?,?,?,?)", (payload["id"], payload["created_at"], payload["profile"], status, len(results), warnings, json.dumps(payload, ensure_ascii=False)))

    def list_jobs(self) -> list[dict[str, Any]]:
        """Return lightweight library rows; full conversions load on demand."""
        with self.connection() as db:
            rows = db.execute("SELECT id, created_at, profile, status, documents, warnings FROM jobs ORDER BY created_at DESC LIMIT 50").fetchall()
        return [dict(row) for row in rows]

    def clear_jobs(self) -> int:
        """Remove every library record; exported files are never touched."""
        with self.connection() as db:
            removed = db.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()["count"]
            db.execute("DELETE FROM jobs")
        return int(removed)

    def job_payload(self, job_id: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError("This local conversion is no longer available in the library.")
        return json.loads(row["payload"])

    def create_batch(self, paths: Iterable[str], profile: str, cache_policy: str, outputs: Iterable[str]) -> str:
        batch_id, created = str(uuid.uuid4()), utc_now()
        unique_paths = list(dict.fromkeys(str(Path(path)) for path in paths))
        with self.connection() as db:
            db.execute("INSERT INTO batch_jobs(id,profile,cache_policy,outputs,status,requested_state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (batch_id, profile, cache_policy, json.dumps(list(outputs)), "queued", None, created, created))
            db.executemany("INSERT INTO batch_items(id,batch_id,source_path,status,error,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", [(str(uuid.uuid4()), batch_id, path, "queued", None, None, created, created) for path in unique_paths])
        return batch_id

    def append_batch_items(self, batch_id: str, paths: Iterable[str]) -> None:
        existing = {item["source_path"] for item in self.batch_items(batch_id)}
        created = utc_now()
        additions = [str(Path(path)) for path in paths if str(Path(path)) not in existing]
        with self.connection() as db:
            db.executemany("INSERT INTO batch_items(id,batch_id,source_path,status,error,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", [(str(uuid.uuid4()), batch_id, path, "queued", None, None, created, created) for path in additions])
            db.execute("UPDATE batch_jobs SET updated_at=? WHERE id=?", (created, batch_id))

    def latest_batch_id(self) -> str | None:
        with self.connection() as db:
            row = db.execute("SELECT id FROM batch_jobs ORDER BY created_at DESC LIMIT 1").fetchone()
        return row["id"] if row else None

    def batch(self, batch_id: str) -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute("SELECT * FROM batch_jobs WHERE id=?", (batch_id,)).fetchone()
        if not row:
            raise ValueError("The selected batch no longer exists.")
        return {**dict(row), "outputs": json.loads(row["outputs"])}

    def batch_items(self, batch_id: str) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM batch_items WHERE batch_id=? ORDER BY created_at", (batch_id,)).fetchall()
        return [{**dict(row), "result": json.loads(row["result"]) if row["result"] else None} for row in rows]

    def set_item_state(self, item_id: str, state: str) -> None:
        if state not in {"queued", "paused", "cancelled"}:
            raise ValueError("Queue state must be queued, paused, or cancelled.")
        with self.connection() as db:
            row = db.execute("SELECT status FROM batch_items WHERE id=?", (item_id,)).fetchone()
            if not row:
                raise ValueError("The selected queue item no longer exists.")
            if row["status"] == "running":
                raise ValueError("A running item is finished atomically; pause the batch after this document instead.")
            db.execute("UPDATE batch_items SET status=?, updated_at=? WHERE id=?", (state, utc_now(), item_id))

    def request_batch_state(self, batch_id: str, state: str | None) -> None:
        if state not in {None, "paused", "cancelled"}:
            raise ValueError("Batch request must be paused, cancelled, or cleared.")
        with self.connection() as db:
            db.execute("UPDATE batch_jobs SET requested_state=?, updated_at=? WHERE id=?", (state, utc_now(), batch_id))

    def mark_item_running(self, item_id: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE batch_items SET status='running', error=NULL, updated_at=? WHERE id=?", (utc_now(), item_id))

    def finish_item(self, item_id: str, result: dict[str, Any] | None, error: str | None) -> None:
        state = result.get("status", "completed") if result else "failed"
        with self.connection() as db:
            db.execute("UPDATE batch_items SET status=?, error=?, result=?, updated_at=? WHERE id=?", (state, error, json.dumps(result, ensure_ascii=False) if result else None, utc_now(), item_id))

    def finish_batch(self, batch_id: str) -> str:
        items = self.batch_items(batch_id)
        requested = self.batch(batch_id).get("requested_state")
        if requested == "paused":
            state = "paused"
        elif requested == "cancelled":
            with self.connection() as db:
                db.execute("UPDATE batch_items SET status='cancelled', updated_at=? WHERE batch_id=? AND status IN ('queued','paused')", (utc_now(), batch_id))
            state = "cancelled"
        elif any(item["status"] in {"queued", "paused", "running"} for item in items):
            state = "queued"
        else:
            state = "completed"
        with self.connection() as db:
            db.execute("UPDATE batch_jobs SET status=?, requested_state=NULL, updated_at=? WHERE id=?", (state, utc_now(), batch_id))
        return state


class PhilonService:
    """Thread-safe facade used by both UI actions and package-level tests."""

    def __init__(self, root: Path | None = None) -> None:
        self.store = LocalStore(root)
        self.workspace_dir = self.store.root
        bundled_helper = Path(__file__).resolve().parents[1] / "engine" / "dist" / "philon-vision-ocr"
        engine.VISION_HELPER = bundled_helper if bundled_helper.exists() else None

    def preferences(self) -> dict[str, Any]:
        return self.store.setting("preferences", {"profile": "Balanced", "cache_policy": "use", "outputs": list(DEFAULT_OUTPUTS), "enabled_model_ids": []})

    def save_preferences(self, values: dict[str, Any]) -> None:
        profile = values.get("profile", "Balanced")
        outputs = values.get("outputs", list(DEFAULT_OUTPUTS))
        enabled_model_ids = values.get("enabled_model_ids", [])
        valid_models = isinstance(enabled_model_ids, list) and all(isinstance(item, str) for item in enabled_model_ids)
        if profile not in PROFILES or values.get("cache_policy", "use") not in {"use", "refresh", "bypass"} or not isinstance(outputs, list) or not outputs or not valid_models:
            raise ValueError("Preferences contain an unsupported profile, cache policy, model list, or empty output selection.")
        self.store.save_setting("preferences", values)

    def preflight(self, paths: Iterable[str], progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        return engine.action_preflight({"config": {"input_paths": list(paths)}}, progress)

    def parse_pages(self, text: str) -> tuple[int, ...] | None:
        """Read a page selection the way the engine will, so the interface can
        refuse a malformed one before a job is started rather than after."""
        return engine.parse_page_selection(text)

    def convert(self, paths: Iterable[str], profile: str, cache_policy: str, outputs: Iterable[str], progress: Callable[[dict[str, Any]], None] | None = None, pages: tuple[int, ...] | None = None) -> dict[str, Any]:
        if profile not in PROFILES:
            raise ValueError("Profile must be Fast, Balanced, or Verified.")
        config = {"input_paths": list(paths), "profile": profile, "workspace_dir": str(self.workspace_dir), "cache_policy": cache_policy, "outputs": list(outputs)}
        if pages:
            config["pages"] = list(pages)
        payload = engine.action_convert({"config": config}, progress)
        self.store.store_job(payload)
        return payload

    def create_batch(self, paths: Iterable[str], profile: str, cache_policy: str, outputs: Iterable[str]) -> str:
        return self.store.create_batch(paths, profile, cache_policy, outputs)

    def run_batch(self, batch_id: str, progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        batch = self.store.batch(batch_id)
        with self.store.connection() as db:
            db.execute("UPDATE batch_jobs SET status='running', updated_at=? WHERE id=?", (utc_now(), batch_id))
        completed, failures = [], []
        queued = [item for item in self.store.batch_items(batch_id) if item["status"] == "queued"]
        total = max(1, len(queued))
        for index, item in enumerate(queued):
            requested = self.store.batch(batch_id).get("requested_state")
            if requested in {"paused", "cancelled"}:
                break
            if item["status"] != "queued":
                continue
            self.store.mark_item_running(item["id"])
            if progress:
                progress({"current": index + 1, "total": total, "percent": round(index / total * 100), "stage": "starting", "message": f"Starting {Path(item['source_path']).name}", "source_path": item["source_path"]})
            try:
                def report_file(update: dict[str, Any], *, index: int = index) -> None:
                    if not progress:
                        return
                    file_percent = int(update.get("percent", 0))
                    progress({**update, "current": index + 1, "total": total, "percent": round((index + file_percent / 100) / total * 100)})
                reply = engine.action_convert({"config": {"input_paths": [item["source_path"]], "profile": batch["profile"], "workspace_dir": str(self.workspace_dir), "cache_policy": batch["cache_policy"], "outputs": batch["outputs"]}}, report_file)
                if reply["results"]:
                    result = reply["results"][0]
                    self.store.finish_item(item["id"], result, None)
                    completed.append(result)
                elif reply["failures"]:
                    error = reply["failures"][0]["error"]
                    self.store.finish_item(item["id"], None, error)
                    failures.extend(reply["failures"])
            except Exception as exc:
                self.store.finish_item(item["id"], None, str(exc))
                failures.append({"source_path": item["source_path"], "error": str(exc)})
            if progress:
                progress({"current": index + 1, "total": total, "percent": round((index + 1) / total * 100), "stage": "complete", "message": f"Finished {Path(item['source_path']).name}", "source_path": item["source_path"]})
        state = self.store.finish_batch(batch_id)
        payload = {"id": str(uuid.uuid4()), "profile": batch["profile"], "local_only": True, "results": completed, "failures": failures, "created_at": utc_now(), "batch_id": batch_id, "batch_status": state}
        if completed or failures:
            self.store.store_job(payload)
        return payload

    def apply_review(self, ir_path: str, block_id: str, action: str, text: str | None = None, candidate_index: int | None = None) -> dict[str, Any]:
        return engine.action_review({"ir_path": ir_path, "block_id": block_id, "review_action": action, "text": text, "candidate_index": candidate_index})

    def request_repair(self, ir_path: str, block_id: str, repair_mode: str, progress: Callable[[dict[str, Any]], None] | None = None, enabled_model_ids: list[str] | None = None) -> dict[str, Any]:
        request: dict[str, Any] = {"ir_path": ir_path, "block_id": block_id, "repair_mode": repair_mode}
        if enabled_model_ids is not None:
            request["enabled_model_ids"] = list(enabled_model_ids)
        return engine.action_repair(request, progress)

    def models(self) -> dict[str, Any]:
        return engine.model_status()

    def diagnostics(self) -> dict[str, Any]:
        return {"engine": engine.ENGINE_VERSION, "local_only": True, "database": str(self.store.database_path), "model_packs": engine.model_status()["packs"], "review_actions": ["accept", "edit", "restore_candidate", "rerun_region", "ignore_warning"]}

    def export_conversion(self, ir_path: str, destination: str, progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        source = Path(ir_path).resolve().parent
        target_root = Path(destination).expanduser().resolve()
        if not source.is_dir() or not target_root.is_dir():
            raise ValueError("Choose an existing local destination and a completed conversion bundle.")
        base, target, index = engine.safe_slug(source.name), target_root / engine.safe_slug(source.name), 2
        while target.exists():
            target = target_root / f"{base}-{index}"
            index += 1
        target.mkdir()
        sources = [item for item in source.rglob("*") if not item.is_symlink() and item.is_file()]
        copied = 0
        for index, item in enumerate(sources, start=1):
            if item.is_symlink() or not item.is_file():
                continue
            relative = item.relative_to(source)
            final = target / relative
            final.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, final)
            copied += 1
            if progress:
                progress({"current": index, "total": len(sources), "percent": round(index / max(1, len(sources)) * 100), "stage": "copying", "message": f"Copying {item.name}", "source_path": str(item)})
        return {"export_path": str(target), "files": copied}
