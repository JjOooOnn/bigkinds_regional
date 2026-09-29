"""Linux server worker ownership, including Playwright's detached browsers.

Signals use pidfds instead of numeric killpg: a re-used PID/group must never
target the API, Xvfb, another job, or an unrelated process.
"""
from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

WORKER_TOKEN_ENV = "BIGKINDS_WORKER_TOKEN"


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    started: int


@dataclass(frozen=True)
class WorkerOwnership:
    leader: ProcessIdentity
    token: str


def require_process_tracking() -> None:
    if not (hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal")):
        raise RuntimeError("Linux 서버 worker 정리에는 pidfd를 지원하는 Python/kernel이 필요합니다.")
    fd = os.pidfd_open(os.getpid())
    os.close(fd)
    if not Path("/proc/self/stat").is_file():
        raise RuntimeError("Linux 서버 worker 정리에는 /proc가 필요합니다.")


def process_identity(pid: int) -> ProcessIdentity | None:
    try:
        # comm may contain spaces and parentheses; fields after its last ')' start at state.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] in {"Z", "X"}:
            return None
        return ProcessIdentity(pid, int(fields[19]))
    except (FileNotFoundError, ProcessLookupError):
        return None


def register_worker(pid: int, token: str) -> WorkerOwnership:
    identity = process_identity(pid)
    if identity is None or pid == os.getpid() or os.getpgid(pid) != pid or os.getsid(pid) != pid:
        raise RuntimeError("worker의 독립 session/process group 등록에 실패했습니다.")
    return WorkerOwnership(identity, token)


def owned_processes(owner: WorkerOwnership) -> set[ProcessIdentity]:
    owned = set()
    if process_identity(owner.leader.pid) == owner.leader:
        owned.add(owner.leader)
    marker = f"{WORKER_TOKEN_ENV}={owner.token}".encode()
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        pid = int(directory.name)
        if pid == os.getpid() or pid == owner.leader.pid:
            continue
        try:
            if directory.stat().st_uid != os.getuid():
                continue
            identity = process_identity(pid)
            if identity and marker in (directory / "environ").read_bytes().split(b"\0"):
                # Re-check after reading environ, before registering the process.
                if process_identity(pid) == identity:
                    owned.add(identity)
        except (FileNotFoundError, ProcessLookupError):
            continue
        # Permission errors must block admission, not silently declare cleanup done.
    return owned


def _signal_process(identity: ProcessIdentity, sig: int) -> None:
    try:
        fd = os.pidfd_open(identity.pid)
    except ProcessLookupError:
        return
    try:
        if identity.pid != os.getpid() and process_identity(identity.pid) == identity:
            try:
                signal.pidfd_send_signal(fd, sig)
            except ProcessLookupError:
                pass
    finally:
        os.close(fd)


def terminate_owned_processes(owner: WorkerOwnership, grace_seconds: float) -> bool:
    """Return true only after all live owned processes disappear; never reap API children here."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        deadline = time.monotonic() + grace_seconds
        while True:
            processes = owned_processes(owner)
            if not processes:
                return True
            for identity in processes:
                _signal_process(identity, sig)
            if time.monotonic() >= deadline:
                break
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    return not owned_processes(owner)


def enter_worker_session(token: str, connection) -> bool:
    """No driver/browser may start until the parent has recorded this owner."""
    try:
        os.setsid()
        os.environ[WORKER_TOKEN_ENV] = token
        connection.send(os.getpid())
        return connection.poll(15.0) and connection.recv() == "registered"
    finally:
        connection.close()
