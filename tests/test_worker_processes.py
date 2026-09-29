from __future__ import annotations

import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.application import job_manager, worker_processes as workers
from src.application.job_repository import ActiveJobExistsError, JobRepository
from test_job_manager import _config


def _manager(tmp_path, monkeypatch):
    manager = job_manager.JobManager(
        JobRepository(tmp_path / "jobs.sqlite3"), recover_on_start=False,
        cancel_grace_seconds=0.01, terminate_grace_seconds=0.01,
    )
    manager._isolate_workers = True
    job = manager.repository.create_job("job", _config(), tmp_path / "checkpoint.jsonl")
    process = Mock(pid=4242, exitcode=23)
    process.is_alive.return_value = False
    manager.repository.record_worker_spawned("job", process.pid)
    manager._processes["job"] = process
    owner = workers.WorkerOwnership(workers.ProcessIdentity(process.pid, 123), "token")
    manager._owners["job"] = owner
    return manager, process, owner, job


@pytest.mark.parametrize("result", [False, PermissionError("/proc denied")])
def test_unverified_cleanup_keeps_admission_closed(tmp_path, monkeypatch, result):
    manager, process, _, _ = _manager(tmp_path, monkeypatch)
    cleanup = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
    monkeypatch.setattr(job_manager, "terminate_owned_processes", cleanup)
    manager.repository.update_job("job", status="completed")
    manager._watch_process("job", process)
    with pytest.raises(ActiveJobExistsError):
        manager.create_job(_config())
    assert manager._processes["job"] is process


def test_abrupt_leader_exit_cleans_descendants_before_releasing_slot(tmp_path, monkeypatch):
    manager, process, owner, _ = _manager(tmp_path, monkeypatch)
    def cleanup(actual_owner, grace):
        assert actual_owner == owner
        with pytest.raises(ActiveJobExistsError):
            manager.create_job(_config())
        return True
    monkeypatch.setattr(job_manager, "terminate_owned_processes", cleanup)
    manager._watch_process("job", process)
    assert manager._processes == manager._owners == {}
    assert manager.repository.get_job("job")["worker_exit_code"] == 23
    manager._worker_launcher = lambda _: None
    assert manager.create_job(_config())["status"] == "queued"


def test_watchdog_and_watcher_serialize_cleanup(tmp_path, monkeypatch):
    manager, process, _, _ = _manager(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    in_cleanup = threading.Lock()
    def cleanup(*_):
        assert in_cleanup.acquire(blocking=False)
        try:
            entered.set()
            assert release.wait(5)
            return True
        finally:
            in_cleanup.release()
    monkeypatch.setattr(job_manager, "terminate_owned_processes", cleanup)
    errors = []
    def run(callback):
        try:
            callback("job", process)
        except BaseException as exc:
            errors.append(exc)
    watchdog = threading.Thread(target=run, args=(manager._enforce_cancel_deadline,))
    watcher = threading.Thread(target=run, args=(manager._watch_process,))
    watchdog.start()
    assert entered.wait(5)
    watcher.start()
    with pytest.raises(ActiveJobExistsError):
        manager.create_job(_config())
    release.set()
    watchdog.join(5)
    watcher.join(5)
    assert not errors
    assert not watchdog.is_alive() and not watcher.is_alive()
    assert not manager._processes


def test_isolated_launch_waits_for_registration_before_release(tmp_path, monkeypatch):
    manager, _, owner, _ = _manager(tmp_path, monkeypatch)
    manager._processes.clear()
    manager._owners.clear()
    process = Mock(pid=owner.leader.pid)
    parent, child = Mock(), Mock()
    parent.recv.return_value = process.pid
    context = Mock()
    context.Pipe.return_value = (parent, child)
    context.Process.return_value = process
    monkeypatch.setattr(job_manager, "require_process_tracking", lambda: None)
    monkeypatch.setattr(job_manager, "register_worker", lambda *_: owner)
    manager._start_watcher = Mock()
    def send(message):
        assert message == "registered"
        assert manager._owners["job"] == owner
        assert manager.repository.get_job("job")["worker_pid"] == process.pid
    parent.send.side_effect = send
    manager._launch_isolated(context, "job")
    parent.send.assert_called_once_with("registered")
    manager._start_watcher.assert_called_once_with("job", process)


def test_failed_handshake_never_releases_worker(tmp_path, monkeypatch):
    manager, _, _, _ = _manager(tmp_path, monkeypatch)
    manager._processes.clear()
    manager._owners.clear()
    process = Mock(pid=8888)
    process.is_alive.return_value = False
    parent, child = Mock(), Mock()
    parent.poll.return_value = False
    context = Mock()
    context.Pipe.return_value = (parent, child)
    context.Process.return_value = process
    monkeypatch.setattr(job_manager, "require_process_tracking", lambda: None)
    with pytest.raises(RuntimeError, match="등록 응답"):
        manager._launch_isolated(context, "job")
    parent.send.assert_not_called()
    process.terminate.assert_called_once()
    assert not manager._processes


def test_worker_entry_does_not_run_audit_without_parent_approval(monkeypatch):
    monkeypatch.setattr(job_manager, "enter_worker_session", lambda *_: False)
    audit = Mock()
    monkeypatch.setattr(job_manager, "run_audit_job_worker", audit)
    job_manager.run_isolated_audit_job_worker("db", "job", "token", Mock())
    audit.assert_not_called()


def test_shutdown_cleans_worker_still_alive_after_terminal_result(tmp_path, monkeypatch):
    manager, process, owner, _ = _manager(tmp_path, monkeypatch)
    process.is_alive.side_effect = [True, False]
    manager.repository.update_job("job", status="completed")
    cleanup = Mock(return_value=True)
    monkeypatch.setattr(job_manager, "terminate_owned_processes", cleanup)
    manager.shutdown()
    cleanup.assert_called_once_with(owner, manager.terminate_grace_seconds)
    assert manager.repository.get_job("job")["status"] == "completed"
    with pytest.raises(RuntimeError, match="종료 중"):
        manager.create_job(_config())


def test_pid_reuse_does_not_signal_replacement(monkeypatch):
    identity = workers.ProcessIdentity(4242, 100)
    monkeypatch.setattr(workers.os, "pidfd_open", lambda _: 55, raising=False)
    close = Mock()
    send = Mock()
    monkeypatch.setattr(workers.os, "close", close)
    monkeypatch.setattr(workers.signal, "pidfd_send_signal", send, raising=False)
    monkeypatch.setattr(workers, "process_identity", lambda _: workers.ProcessIdentity(4242, 999))
    workers._signal_process(identity, signal.SIGTERM)
    send.assert_not_called()
    close.assert_called_once_with(55)


def test_forced_cleanup_escalates_to_kill_for_owned_processes_only(monkeypatch):
    owner = workers.WorkerOwnership(workers.ProcessIdentity(4242, 100), "token")
    descendant = workers.ProcessIdentity(4243, 101)
    monkeypatch.setattr(workers.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(workers, "owned_processes", Mock(side_effect=[{descendant}, {descendant}, set()]))
    send = Mock()
    monkeypatch.setattr(workers, "_signal_process", send)
    assert workers.terminate_owned_processes(owner, 0)
    assert send.call_args_list == [((descendant, signal.SIGTERM),), ((descendant, 9),)]


def test_proc_scan_finds_detached_orphans_without_touching_unrelated_processes(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    proc.mkdir()
    owner = workers.WorkerOwnership(workers.ProcessIdentity(4242, 100), "our-token")
    # The leader is gone; its numeric PID has already been reused by another job.
    for pid, started, token, state in (
        (4242, 999, "other", "S"), (4243, 101, "our-token", "S"),
        (4244, 102, "other", "S"), (4245, 103, "our-token", "Z"),
    ):
        directory = proc / str(pid)
        directory.mkdir()
        fields = [state] + ["0"] * 18 + [str(started)]
        (directory / "stat").write_text(f"{pid} (command with ) spaces) " + " ".join(fields))
        (directory / "environ").write_bytes(f"{workers.WORKER_TOKEN_ENV}={token}\0".encode())
    monkeypatch.setattr(workers, "Path", lambda value: proc / str(value).removeprefix("/proc").lstrip("/"))
    monkeypatch.setattr(workers.os, "getuid", lambda: proc.stat().st_uid, raising=False)
    assert workers.owned_processes(owner) == {workers.ProcessIdentity(4243, 101)}


def _linux_worker_fixture(token, connection, ready, ready_path, abrupt, browser):
    if not workers.enter_worker_session(token, connection):
        return
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if browser:
        from playwright.sync_api import sync_playwright
        playwright = sync_playwright().start()
        chromium = playwright.chromium.launch(args=["--disable-dev-shm-usage"])
        page = chromium.new_page()
        page.goto("data:text/html,<h1>worker cleanup</h1>")
        assert page.locator("h1").inner_text() == "worker cleanup"
    else:
        subprocess.Popen([
            sys.executable, "-c",
            "import signal,time,pathlib,sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "pathlib.Path(sys.argv[1]).touch(); time.sleep(120)", ready_path,
        ], start_new_session=True)
        deadline = time.monotonic() + 10
        while not Path(ready_path).exists():
            if time.monotonic() > deadline:
                raise RuntimeError("detached child not ready")
            time.sleep(0.01)
    ready.set()
    if abrupt:
        os._exit(23)
    time.sleep(120)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux /proc and pidfd")
@pytest.mark.parametrize("abrupt", [False, True], ids=["cancel", "leader_crash"])
@pytest.mark.parametrize("browser", [False, True], ids=["detached_subprocess", "playwright"])
def test_linux_real_worker_and_detached_descendants_are_terminated(tmp_path, abrupt, browser):
    workers.require_process_tracking()
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    ready = context.Event()
    token = uuid4().hex
    process = context.Process(target=_linux_worker_fixture, args=(
        token, child, ready, str(tmp_path / "ready"), abrupt, browser,
    ))
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    owner = None
    try:
        process.start()
        child.close()
        assert parent.poll(10)
        assert parent.recv() == process.pid
        owner = workers.register_worker(process.pid, token)
        parent.send("registered")
        assert ready.wait(20)
        if abrupt:
            process.join(10)
            assert process.exitcode == 23
        assert any(item.pid != process.pid for item in workers.owned_processes(owner))
        assert workers.terminate_owned_processes(owner, 0.5)
        process.join(5)
        assert not process.is_alive()
        assert not workers.owned_processes(owner)
        assert unrelated.poll() is None
    finally:
        if owner is not None:
            workers.terminate_owned_processes(owner, 1)
        if process.is_alive():
            process.kill()
        process.join(5)
        unrelated.kill()
        unrelated.wait(5)
        parent.close()
        child.close()
