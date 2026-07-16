import time

from timicc_worker import core, jobs


def wait_for_terminal(manager, job_id):
    for _ in range(100):
        status = manager.status(job_id)["status"]
        if status in jobs.TERMINAL_STATUSES:
            return status
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_background_job_completes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        core,
        "generate_patch",
        lambda **kwargs: {
            "summary": "ok",
            "patch": "",
            "tests": [],
            "assumptions": [],
            "risks": [],
            "model": "gpt-5.6-sol",
            "worker": "timicc",
            "changed_files": [],
            "patch_sha256": "0" * 64,
        },
    )
    manager = jobs.PatchJobManager(tmp_path, max_workers=1)
    submitted = manager.submit(task="change", file_context="context", allowed_paths=["a.py"])
    assert wait_for_terminal(manager, submitted["job_id"]) == "completed"
    result = manager.result(submitted["job_id"])
    assert result["ready"] is True
    assert result["summary"] == "ok"


def test_background_job_can_be_cancelled(monkeypatch, tmp_path):
    def fake_generate(**kwargs):
        event = kwargs["cancel_event"]
        while not event.is_set():
            time.sleep(0.01)
        raise core.TimiccCancelled("cancelled in test")

    monkeypatch.setattr(core, "generate_patch", fake_generate)
    manager = jobs.PatchJobManager(tmp_path, max_workers=1)
    submitted = manager.submit(task="change", file_context="context", allowed_paths=["a.py"])
    manager.cancel(submitted["job_id"])
    assert wait_for_terminal(manager, submitted["job_id"]) == "cancelled"
    assert manager.result(submitted["job_id"])["ready"] is False


def test_restart_marks_active_jobs_failed(tmp_path):
    manager = jobs.PatchJobManager(tmp_path, max_workers=1)
    now = time.time()
    with manager._connect() as connection:
        connection.execute(
            "INSERT INTO jobs(job_id,status,model,allowed_paths_json,task_sha256,context_sha256,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("tj_stale", "streaming", "gpt-5.6-sol", "[]", "a", "b", now, now),
        )
    restarted = jobs.PatchJobManager(tmp_path, max_workers=1)
    assert restarted.status("tj_stale")["status"] == "failed"


def test_result_is_persisted_with_name_and_integrity(monkeypatch, tmp_path):
    monkeypatch.setattr(
        core,
        "generate_patch",
        lambda **kwargs: {
            "summary": "persisted",
            "patch": "",
            "tests": [],
            "assumptions": [],
            "risks": [],
            "model": "gpt-5.6-sol",
            "worker": "timicc",
            "changed_files": [],
            "patch_sha256": "0" * 64,
        },
    )
    manager = jobs.PatchJobManager(tmp_path, max_workers=1)
    submitted = manager.submit(task="change", task_name="named task", file_context="context", allowed_paths=["a.py"])
    assert wait_for_terminal(manager, submitted["job_id"]) == "completed"
    assert manager.result(submitted["job_id"])["summary"] == "persisted"
    assert manager.result(submitted["job_id"])["summary"] == "persisted"
    status = manager.status(submitted["job_id"])
    assert status["task_name"] == "named task"
    assert status["result_size_bytes"] > 0
    assert status["result_read_at"] is not None


def test_result_larger_than_two_mib_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(
        core,
        "generate_patch",
        lambda **kwargs: {
            "summary": "x" * (jobs.MAX_RESULT_BYTES + 1),
            "patch": "",
            "tests": [],
            "assumptions": [],
            "risks": [],
            "model": "gpt-5.6-sol",
            "worker": "timicc",
            "changed_files": [],
            "patch_sha256": "0" * 64,
        },
    )
    manager = jobs.PatchJobManager(tmp_path, max_workers=1)
    submitted = manager.submit(task="change", file_context="context", allowed_paths=["a.py"])
    assert wait_for_terminal(manager, submitted["job_id"]) == "failed"
    assert manager.status(submitted["job_id"])["error_code"] == "result_too_large"
