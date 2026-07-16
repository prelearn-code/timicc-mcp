from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from platformdirs import user_state_dir

from . import core


ACTIVE_STATUSES = {"queued", "streaming", "parsing", "validating", "cancel_requested"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
MAX_RESULT_BYTES = 2 * 1024 * 1024


class PatchJobManager:
    def __init__(self, state_dir: str | Path | None = None, max_workers: int | None = None):
        default_dir = Path(user_state_dir("codex-timicc-worker", appauthor=False))
        self.state_dir = Path(state_dir or os.environ.get("TIMICC_STATE_DIR") or default_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_dir, 0o700)
        self.db_path = self.state_dir / "jobs.sqlite3"
        workers = max_workers or int(os.environ.get("TIMICC_JOB_WORKERS", "2"))
        self.executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="timicc-job")
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    task_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    model TEXT NOT NULL,
                    allowed_paths_json TEXT NOT NULL,
                    task_sha256 TEXT NOT NULL,
                    context_sha256 TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    completed_at REAL,
                    updated_at REAL NOT NULL,
                    last_activity_at REAL,
                    last_event TEXT,
                    output_chars INTEGER NOT NULL DEFAULT 0,
                    stream_chunks INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT,
                    result_sha256 TEXT,
                    result_size_bytes INTEGER,
                    result_stored_at REAL,
                    result_read_at REAL,
                    patch_sha256 TEXT,
                    error_code TEXT,
                    error TEXT
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
            migrations = {
                "task_name": "TEXT NOT NULL DEFAULT ''",
                "started_at": "REAL",
                "completed_at": "REAL",
                "last_activity_at": "REAL",
                "output_chars": "INTEGER NOT NULL DEFAULT 0",
                "stream_chunks": "INTEGER NOT NULL DEFAULT 0",
                "result_sha256": "TEXT",
                "result_size_bytes": "INTEGER",
                "result_stored_at": "REAL",
                "result_read_at": "REAL",
                "error_code": "TEXT",
            }
            for name, declaration in migrations.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
            now = time.time()
            connection.execute(
                "UPDATE jobs SET status='failed', error_code='server_restarted', "
                "error='MCP server restarted before job completion', completed_at=?, updated_at=? "
                "WHERE status IN ('queued','streaming','parsing','validating','cancel_requested')",
                (now, now),
            )
        os.chmod(self.db_path, 0o600)

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _task_name(task_name: str, task: str) -> str:
        if not isinstance(task_name, str):
            raise ValueError("task_name must be a string")
        name = task_name.strip() or task.strip().splitlines()[0].strip()
        if not name or len(name) > 200:
            raise ValueError("task_name must contain 1 to 200 characters")
        return name

    def submit(
        self,
        *,
        task: str,
        file_context: str,
        allowed_paths: list[str],
        task_name: str = "",
        constraints: str = "",
        test_failures: str = "",
        model: str = core.DEFAULT_MODEL,
    ) -> dict[str, Any]:
        core._require_text("task", task, 30_000)
        core._require_text("file_context", file_context, core.MAX_CONTEXT_CHARS)
        name = self._task_name(task_name, task)
        allowed = sorted(core._validate_allowed_paths(allowed_paths))
        core._validate_model(model)
        if not isinstance(constraints, str) or len(constraints) > 50_000:
            raise ValueError("constraints must be a string no longer than 50,000 characters")
        if not isinstance(test_failures, str) or len(test_failures) > core.MAX_FAILURE_CHARS:
            raise ValueError("test_failures is too long")
        job_id = "tj_" + secrets.token_urlsafe(18)
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs(job_id,task_name,status,model,allowed_paths_json,task_sha256,context_sha256,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id, name, "queued", model, json.dumps(allowed), self._digest(task), self._digest(file_context), now, now),
            )
        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[job_id] = cancel_event
        self.executor.submit(
            self._run, job_id, task, file_context, allowed, constraints, test_failures, model, cancel_event
        )
        return {"job_id": job_id, "task_name": name, "status": "queued", "worker": "timicc"}

    def _update(self, job_id: str, **fields: Any) -> None:
        fields["updated_at"] = time.time()
        assignments = ", ".join(f"{name}=?" for name in fields)
        with self._connect() as connection:
            connection.execute(f"UPDATE jobs SET {assignments} WHERE job_id=?", (*fields.values(), job_id))

    def _run(
        self,
        job_id: str,
        task: str,
        file_context: str,
        allowed_paths: list[str],
        constraints: str,
        test_failures: str,
        model: str,
        cancel_event: threading.Event,
    ) -> None:
        try:
            if cancel_event.is_set():
                raise core.TimiccCancelled("cancelled before start")
            self._update(job_id, status="streaming", started_at=time.time())
            last_progress = {"event": None, "at": 0.0}

            def progress(event_type: str, output_chars: int = 0, stream_chunks: int = 0) -> None:
                now = time.monotonic()
                if event_type != last_progress["event"] or now - last_progress["at"] >= 5.0:
                    self._update(
                        job_id,
                        last_event=event_type,
                        last_activity_at=time.time(),
                        output_chars=output_chars,
                        stream_chunks=stream_chunks,
                    )
                    last_progress.update(event=event_type, at=now)

            result = core.generate_patch(
                task=task,
                file_context=file_context,
                allowed_paths=allowed_paths,
                constraints=constraints,
                test_failures=test_failures,
                model=model,
                cancel_event=cancel_event,
                progress=progress,
            )
            if cancel_event.is_set():
                raise core.TimiccCancelled("cancelled after stream completion")
            self._update(job_id, status="validating")
            serialized = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            encoded = serialized.encode("utf-8")
            if len(encoded) > MAX_RESULT_BYTES:
                raise core.TimiccError(
                    f"validated result exceeds the {MAX_RESULT_BYTES}-byte persistence limit"
                )
            now = time.time()
            self._update(
                job_id,
                status="completed",
                completed_at=now,
                result_json=serialized,
                result_sha256=hashlib.sha256(encoded).hexdigest(),
                result_size_bytes=len(encoded),
                result_stored_at=now,
                patch_sha256=result["patch_sha256"],
                error_code=None,
                error=None,
            )
        except core.TimiccCancelled as exc:
            self._update(job_id, status="cancelled", completed_at=time.time(), error_code="cancelled", error=str(exc))
        except Exception as exc:
            code = "result_too_large" if "persistence limit" in str(exc) else "worker_failed"
            self._update(job_id, status="failed", completed_at=time.time(), error_code=code, error=f"{type(exc).__name__}: {exc}"[:2000])
        finally:
            with self._lock:
                self._cancel_events.pop(job_id, None)

    def status(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT job_id,task_name,status,model,created_at,started_at,completed_at,updated_at,"
                "last_activity_at,last_event,output_chars,stream_chunks,result_sha256,result_size_bytes,"
                "result_stored_at,result_read_at,patch_sha256,error_code,error FROM jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise ValueError("unknown TIMI CC job_id")
        return dict(row)

    def result(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status,result_json,result_sha256,error_code,error FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise ValueError("unknown TIMI CC job_id")
            if row["status"] != "completed":
                return {"job_id": job_id, "status": row["status"], "ready": False, "error_code": row["error_code"], "error": row["error"]}
            raw = row["result_json"]
            if not isinstance(raw, str) or hashlib.sha256(raw.encode("utf-8")).hexdigest() != row["result_sha256"]:
                raise RuntimeError("stored TIMI CC result failed integrity validation")
            connection.execute(
                "UPDATE jobs SET result_read_at=COALESCE(result_read_at,?), updated_at=? WHERE job_id=?",
                (time.time(), time.time(), job_id),
            )
        result = json.loads(raw)
        result.update({"job_id": job_id, "status": "completed", "ready": True, "result_sha256": row["result_sha256"]})
        return result

    def cancel(self, job_id: str) -> dict[str, Any]:
        current = self.status(job_id)
        if current["status"] in TERMINAL_STATUSES:
            return current
        self._update(job_id, status="cancel_requested")
        with self._lock:
            event = self._cancel_events.get(job_id)
            if event is not None:
                event.set()
        return self.status(job_id)


_manager: PatchJobManager | None = None
_manager_lock = threading.Lock()


def get_job_manager() -> PatchJobManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = PatchJobManager()
        return _manager
