"""run() 多线程上传编排的测试。"""

from __future__ import annotations

import socket
import stat
import threading
from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import paramiko
import pytest

from bos_downloader import upload_cli
from bos_downloader.local_walker import LocalFile
from bos_downloader.sftp_client import SftpPoolClosedError
from bos_downloader.upload_cancellation import UploadCancellation
from bos_downloader.upload_progress import UploadProgress
from bos_downloader.uploader import RemoteDirectoryCache


class FakeSftp:
    """线程安全的多文件 Fake：记录远端文件、目录和上传调用。"""

    def __init__(self, existing=None, failing=None):
        self.existing = dict(existing or {})
        self.failing = set(failing or set())
        self._lock = threading.Lock()
        self.put_calls = {}
        self.made_dirs = set()
        self.mkdir_calls = []

    def stat(self, path):
        with self._lock:
            if path in self.existing:
                return SimpleNamespace(st_size=self.existing[path])
            if path in self.made_dirs:
                return SimpleNamespace(st_size=0, st_mode=stat.S_IFDIR | 0o755)
        raise FileNotFoundError(path)

    def lstat(self, path):
        return self.stat(path)

    def put(self, localpath, remotepath, callback=None, confirm=True):
        if remotepath in self.failing:
            raise RuntimeError("模拟上传失败")
        size = Path(localpath).stat().st_size
        if callback:
            callback(size // 2, size)
            callback(size, size)
        with self._lock:
            self.put_calls[remotepath] = self.put_calls.get(remotepath, 0) + 1
            self.existing[remotepath] = size

    def mkdir(self, path, mode=511):
        with self._lock:
            self.mkdir_calls.append(path)
            if path in self.made_dirs:
                raise FileExistsError(path)
            self.made_dirs.add(path)


class FakePool:
    def __init__(self, client, on_get=None):
        self._client = client
        self._on_get = on_get
        self._lock = threading.Lock()
        self.get_calls = 0
        self.closed = False

    def get(self):
        with self._lock:
            self.get_calls += 1
        if self._on_get is not None:
            self._on_get()
        return self._client

    def close_all(self):
        self.closed = True


class RecordingBar:
    instances: list["RecordingBar"] = []

    def __init__(self, total, **kwargs):
        self.total = total
        self.kwargs = kwargs
        self.n = 0
        self.postfix = {}
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def update(self, amount):
        self.n += amount

    def set_postfix(self, values, refresh=True):
        self.postfix = dict(values)

    @staticmethod
    def write(message, file=None):
        print(message, file=file)


class FailingPostfixBar(RecordingBar):
    def set_postfix(self, values, refresh=True):
        raise RuntimeError("终端状态栏不可用")


class FailingWriteBar(RecordingBar):
    @staticmethod
    def write(message, file=None):
        raise RuntimeError("终端日志不可用")


def _patch(monkeypatch, client, record_bar=False):
    """让 run() 使用注入的 FakeSftp 与 FakePool。"""
    pool = FakePool(client)
    monkeypatch.setattr(
        upload_cli,
        "load_sftp_config_from_env",
        lambda: SimpleNamespace(remote_base="/base"),
    )
    monkeypatch.setattr(upload_cli, "ThreadLocalSftpPool", lambda cfg: pool)
    if record_bar:
        RecordingBar.instances.clear()
        monkeypatch.setattr(upload_cli, "tqdm", RecordingBar)
    return pool


def _make_tree(root: Path):
    (root / "a.txt").write_bytes(b"aaaa")
    (root / "sub").mkdir()
    (root / "sub" / "b.txt").write_bytes(b"bbbbbb")
    (root / "sub" / "deep").mkdir()
    (root / "sub" / "deep" / "c.bin").write_bytes(b"cccccccc")


def test_fails_when_source_size_changes_after_discovery(tmp_path: Path, monkeypatch):
    src = tmp_path / "myfolder"
    src.mkdir()
    changed = src / "changed.pdf"
    changed.write_bytes(b"x")
    client = FakeSftp()
    _patch(monkeypatch, client, record_bar=True)
    discovered = list(upload_cli.walk_local_files(src))
    changed.write_bytes(b"x" * 1024)
    monkeypatch.setattr(upload_cli, "walk_local_files", lambda root: iter(discovered))

    failures = upload_cli.run(str(src), workers=1)

    assert failures == 1
    assert "/base/myfolder/changed.pdf" not in client.put_calls


def test_uploads_all_files_to_correct_remote_paths(tmp_path: Path, monkeypatch):
    src = tmp_path / "myfolder"
    src.mkdir()
    _make_tree(src)
    client = FakeSftp()
    pool = _patch(monkeypatch, client)

    failures = upload_cli.run(str(src), workers=3)

    assert failures == 0
    assert client.put_calls.get("/base/myfolder/a.txt") == 1
    assert client.put_calls.get("/base/myfolder/sub/b.txt") == 1
    assert client.put_calls.get("/base/myfolder/sub/deep/c.bin") == 1
    assert pool.closed is True


def test_uploads_each_file_exactly_once(tmp_path: Path, monkeypatch):
    src = tmp_path / "myfolder"
    src.mkdir()
    for i in range(20):
        (src / f"f{i}.txt").write_bytes(b"x" * (i + 1))
    client = FakeSftp()
    _patch(monkeypatch, client)

    upload_cli.run(str(src), workers=4)

    assert all(count == 1 for count in client.put_calls.values())
    assert len(client.put_calls) == 20


def test_skips_existing_same_size(tmp_path: Path, monkeypatch):
    src = tmp_path / "myfolder"
    src.mkdir()
    (src / "a.txt").write_bytes(b"aaaa")
    (src / "b.txt").write_bytes(b"bbbb")
    client = FakeSftp(existing={"/base/myfolder/a.txt": 4})
    _patch(monkeypatch, client)

    upload_cli.run(str(src), workers=2)

    assert "/base/myfolder/a.txt" not in client.put_calls
    assert client.put_calls.get("/base/myfolder/b.txt") == 1


def test_counts_failures_without_aborting(tmp_path: Path, monkeypatch):
    src = tmp_path / "myfolder"
    src.mkdir()
    (src / "ok.txt").write_bytes(b"ok")
    (src / "bad.txt").write_bytes(b"bad")
    client = FakeSftp(failing={"/base/myfolder/bad.txt"})
    pool = _patch(monkeypatch, client)

    failures = upload_cli.run(str(src), workers=2)

    assert failures == 1
    assert client.put_calls.get("/base/myfolder/ok.txt") == 1
    assert pool.closed is True


def test_remote_base_override(tmp_path: Path, monkeypatch):
    src = tmp_path / "myfolder"
    src.mkdir()
    (src / "a.txt").write_bytes(b"aaaa")
    client = FakeSftp()
    _patch(monkeypatch, client)

    upload_cli.run(str(src), remote_base_override="/custom", workers=1)

    assert client.put_calls.get("/custom/myfolder/a.txt") == 1


def test_returns_zero_when_empty(tmp_path: Path, monkeypatch):
    src = tmp_path / "myfolder"
    src.mkdir()
    client = FakeSftp()
    _patch(monkeypatch, client)

    assert upload_cli.run(str(src), workers=3) == 0


def test_executor_is_closed_before_sftp_pool(tmp_path: Path, monkeypatch):
    src = tmp_path / "myfolder"
    src.mkdir()
    (src / "ok.txt").write_bytes(b"ok")
    events = []

    class RecordingExecutor(RealThreadPoolExecutor):
        def shutdown(self, wait=True, *, cancel_futures=False):
            events.append("executor")
            return super().shutdown(wait=wait, cancel_futures=cancel_futures)

    class RecordingPool(FakePool):
        def close_all(self):
            events.append("pool")
            super().close_all()

    pool = RecordingPool(FakeSftp())
    monkeypatch.setattr(
        upload_cli, "load_sftp_config_from_env", lambda: SimpleNamespace(remote_base="/base")
    )
    monkeypatch.setattr(upload_cli, "ThreadLocalSftpPool", lambda cfg: pool)
    monkeypatch.setattr(upload_cli, "ThreadPoolExecutor", RecordingExecutor)
    monkeypatch.setattr(upload_cli, "tqdm", RecordingBar)

    upload_cli.run(str(src), workers=1)

    assert events == ["executor", "pool"]


def test_run_limits_submitted_futures_to_worker_window(tmp_path: Path, monkeypatch):
    src = tmp_path / "myfolder"
    src.mkdir()
    for index in range(40):
        (src / f"f{index}.txt").write_bytes(b"x")
    _patch(monkeypatch, FakeSftp(), record_bar=True)
    observed_batch_sizes = []
    real_wait = upload_cli.wait

    def recording_wait(futures, **kwargs):
        observed_batch_sizes.append(len(futures))
        return real_wait(futures, **kwargs)

    monkeypatch.setattr(upload_cli, "wait", recording_wait)

    upload_cli.run(str(src), workers=3)

    assert max(observed_batch_sizes) <= 6


def test_uses_total_bytes_and_aggregates_upload_callbacks(tmp_path: Path, monkeypatch):
    src = tmp_path / "myfolder"
    src.mkdir()
    _make_tree(src)
    _patch(monkeypatch, FakeSftp(), record_bar=True)

    upload_cli.run(str(src), workers=3)

    bar = RecordingBar.instances[-1]
    assert bar.total == 18
    assert bar.n == 18
    assert bar.kwargs["unit"] == "B"
    assert bar.kwargs["unit_scale"] is True


def test_silences_done_and_skipped_but_keeps_summary(tmp_path: Path, monkeypatch, capsys):
    src = tmp_path / "myfolder"
    src.mkdir()
    (src / "done.txt").write_bytes(b"done")
    (src / "skip.txt").write_bytes(b"skip")
    client = FakeSftp(existing={"/base/myfolder/skip.txt": 4})
    _patch(monkeypatch, client, record_bar=True)

    upload_cli.run(str(src), workers=2)

    captured = capsys.readouterr()
    assert "[完成]" not in captured.out
    assert "[跳过]" not in captured.out
    assert "完成 1,跳过 1,失败 0" in captured.out


def test_keeps_per_file_failure_log_and_summary(tmp_path: Path, monkeypatch, capsys):
    src = tmp_path / "myfolder"
    src.mkdir()
    (src / "bad.txt").write_bytes(b"bad")
    client = FakeSftp(failing={"/base/myfolder/bad.txt"})
    _patch(monkeypatch, client, record_bar=True)

    failures = upload_cli.run(str(src), workers=1)

    captured = capsys.readouterr()
    assert failures == 1
    assert "[失败] myfolder/bad.txt: 模拟上传失败" in captured.err
    assert "完成 0,跳过 0,失败 1" in captured.out


def test_progress_postfix_failure_does_not_abort_upload(tmp_path: Path, monkeypatch):
    src = tmp_path / "myfolder"
    src.mkdir()
    (src / "ok.txt").write_bytes(b"ok")
    _patch(monkeypatch, FakeSftp())
    FailingPostfixBar.instances.clear()
    monkeypatch.setattr(upload_cli, "tqdm", FailingPostfixBar)

    assert upload_cli.run(str(src), workers=1) == 0


def test_failure_log_ui_error_does_not_abort_remaining_files(tmp_path: Path, monkeypatch):
    src = tmp_path / "myfolder"
    src.mkdir()
    (src / "bad.txt").write_bytes(b"bad")
    (src / "ok.txt").write_bytes(b"ok")
    client = FakeSftp(failing={"/base/myfolder/bad.txt"})
    _patch(monkeypatch, client)
    FailingWriteBar.instances.clear()
    monkeypatch.setattr(upload_cli, "tqdm", FailingWriteBar)

    assert upload_cli.run(str(src), workers=1) == 1
    assert client.put_calls.get("/base/myfolder/ok.txt") == 1


def test_directory_cache_avoids_repeating_parent_mkdir(tmp_path: Path, monkeypatch):
    src = tmp_path / "myfolder"
    src.mkdir()
    for index in range(20):
        (src / f"f{index}.txt").write_bytes(b"x")
    client = FakeSftp()
    _patch(monkeypatch, client, record_bar=True)

    upload_cli.run(str(src), workers=5)

    assert client.mkdir_calls.count("/base") == 1
    assert client.mkdir_calls.count("/base/myfolder") == 1


def _make_local_file(tmp_path: Path) -> LocalFile:
    local_path = tmp_path / "a.txt"
    local_path.write_bytes(b"data")
    return LocalFile(local_path, "a.txt", 4)


def _upload_one(
    pool: Any, cancellation: UploadCancellation, local_file: LocalFile
) -> upload_cli.UploadOutcome:
    return upload_cli._upload_one(
        pool,
        "/base",
        "myfolder",
        RemoteDirectoryCache(),
        UploadProgress(local_file.size),
        cancellation,
        local_file,
    )


def test_upload_one_does_not_get_connection_when_pre_cancelled(tmp_path: Path):
    local_file = _make_local_file(tmp_path)
    cancellation = UploadCancellation()
    cancellation.request()
    pool = FakePool(FakeSftp())

    outcome = _upload_one(pool, cancellation, local_file)

    assert outcome.status == "cancelled"
    assert pool.get_calls == 0


def test_upload_one_checks_cancellation_after_get_before_upload(tmp_path: Path, monkeypatch):
    local_file = _make_local_file(tmp_path)
    cancellation = UploadCancellation()
    pool = FakePool(FakeSftp(), on_get=cancellation.request)
    upload_calls = []
    monkeypatch.setattr(
        upload_cli,
        "upload_file",
        lambda *args, **kwargs: upload_calls.append((args, kwargs)),
    )

    outcome = _upload_one(pool, cancellation, local_file)

    assert outcome.status == "cancelled"
    assert pool.get_calls == 1
    assert upload_calls == []


def test_upload_one_returns_cancelled_when_connection_closes_after_cancel(
    tmp_path: Path,
):
    local_file = _make_local_file(tmp_path)
    cancellation = UploadCancellation()

    def get_and_close():
        cancellation.request()
        raise ConnectionError("连接已关闭")

    pool = FakePool(FakeSftp(), on_get=get_and_close)

    outcome = _upload_one(pool, cancellation, local_file)

    assert outcome.status == "cancelled"
    assert outcome.error is None


def test_upload_one_returns_failed_for_uncancelled_exception(tmp_path: Path, monkeypatch):
    local_file = _make_local_file(tmp_path)
    cancellation = UploadCancellation()
    pool = FakePool(FakeSftp())
    monkeypatch.setattr(
        upload_cli,
        "upload_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("模拟上传失败")),
    )

    outcome = _upload_one(pool, cancellation, local_file)

    assert outcome.status == "failed"
    assert outcome.error == "模拟上传失败"


def test_collect_cancelled_counts_without_failure_log(capsys):
    counts = {"done": 0, "skipped": 0, "failed": 0, "cancelled": 0}
    outcome = upload_cli.UploadOutcome("myfolder/a.txt", "cancelled", "上传已取消")

    upload_cli._collect_outcome(outcome, counts, UploadProgress(0))

    captured = capsys.readouterr()
    assert counts == {"done": 0, "skipped": 0, "failed": 0, "cancelled": 1}
    assert "[失败]" not in captured.err


def test_postfix_includes_cancelled_count():
    bar = RecordingBar(total=0)
    counts = {"done": 1, "skipped": 2, "failed": 3, "cancelled": 4}

    upload_cli._update_postfix(bar, counts, UploadProgress(0))

    assert bar.postfix == {"完成": 1, "跳过": 2, "失败": 3, "取消": 4}


def test_upload_one_keeps_permission_error_after_cancellation(tmp_path: Path, monkeypatch):
    local_file = _make_local_file(tmp_path)
    cancellation = UploadCancellation()
    pool = FakePool(FakeSftp())

    def fail_after_request(*args, **kwargs):
        cancellation.request()
        raise PermissionError("权限拒绝")

    monkeypatch.setattr(upload_cli, "upload_file", fail_after_request)

    outcome = _upload_one(pool, cancellation, local_file)

    assert outcome.status == "failed"
    assert outcome.error == "权限拒绝"


@pytest.mark.parametrize(
    "error",
    [
        ConnectionError("连接关闭"),
        TimeoutError("请求超时"),
        socket.timeout("socket 超时"),
    ],
)
def test_upload_one_converts_cancelled_io_errors(tmp_path: Path, monkeypatch, error):
    local_file = _make_local_file(tmp_path)
    cancellation = UploadCancellation()
    pool = FakePool(FakeSftp())

    def fail_after_request(*args, **kwargs):
        cancellation.request()
        raise error

    monkeypatch.setattr(upload_cli, "upload_file", fail_after_request)

    outcome = _upload_one(pool, cancellation, local_file)

    assert outcome.status == "cancelled"
    assert outcome.error is None


@pytest.mark.parametrize(
    "error",
    [
        paramiko.AuthenticationException("认证失败"),
        paramiko.BadHostKeyException(
            "host",
            SimpleNamespace(get_base64=lambda: "actual"),
            SimpleNamespace(get_base64=lambda: "expected"),
        ),
        paramiko.ChannelException(1, "通道失败"),
    ],
)
def test_upload_one_keeps_paramiko_protocol_errors_after_cancellation(
    tmp_path: Path, monkeypatch, error
):
    local_file = _make_local_file(tmp_path)
    cancellation = UploadCancellation()
    pool = FakePool(FakeSftp())

    def fail_after_request(*args, **kwargs):
        cancellation.request()
        raise error

    monkeypatch.setattr(upload_cli, "upload_file", fail_after_request)

    outcome = _upload_one(pool, cancellation, local_file)

    assert outcome.status == "failed"
    assert outcome.error == str(error)


def test_upload_one_converts_closed_pool_after_cancellation(tmp_path: Path):
    local_file = _make_local_file(tmp_path)
    cancellation = UploadCancellation()

    def get_from_closed_pool():
        cancellation.request()
        raise SftpPoolClosedError("SFTP 连接池已关闭")

    pool = FakePool(FakeSftp(), on_get=get_from_closed_pool)

    outcome = _upload_one(pool, cancellation, local_file)

    assert outcome.status == "cancelled"
    assert outcome.error is None


def test_submit_next_stops_filling_window_after_cancellation():
    class FakeFuture:
        pass

    class RecordingExecutor:
        def __init__(self):
            self.submitted = []

        def submit(self, *args):
            self.submitted.append(args)
            cancellation.request()
            return FakeFuture()

    cancellation = UploadCancellation()
    executor = RecordingExecutor()
    pending = set()
    files = iter(
        [
            LocalFile(Path("one.txt"), "one.txt", 1),
            LocalFile(Path("two.txt"), "two.txt", 1),
        ]
    )

    upload_cli._submit_next(
        executor,
        files,
        pending,
        4,
        FakePool(FakeSftp()),
        "/base",
        "myfolder",
        RemoteDirectoryCache(),
        UploadProgress(2),
        cancellation,
    )

    assert len(executor.submitted) == 1
    assert len(pending) == 1


def test_cancel_pending_calls_cancel_on_every_future():
    class FakeFuture:
        def __init__(self):
            self.cancel_calls = 0

        def cancel(self):
            self.cancel_calls += 1
            return True

    futures = {FakeFuture(), FakeFuture(), FakeFuture()}

    upload_cli._cancel_pending(futures)

    assert [future.cancel_calls for future in futures] == [1, 1, 1]


def test_run_keyboard_interrupt_cleans_up_in_required_order(tmp_path: Path, monkeypatch, capsys):
    src = tmp_path / "myfolder"
    src.mkdir()
    (src / "a.txt").write_bytes(b"a")
    events = []

    class FakeFuture:
        def cancel(self):
            events.append("cancel pending")
            return True

    class FakeWatchdog:
        def join(self, timeout=None):
            events.append(("watchdog join", timeout))

    class FakeCancellation:
        is_cancelled = False

        def request(self):
            events.append("request")
            self.is_cancelled = True
            return True

        def start_watchdog(self):
            events.append("watchdog")
            return FakeWatchdog()

        def mark_cleanup_complete(self):
            events.append("cleanup complete")

    class FakeExecutor:
        def __init__(self, max_workers):
            assert max_workers == 1

        def shutdown(self, wait=True, *, cancel_futures=False):
            events.append(("shutdown", wait, cancel_futures))

    class RecordingPool(FakePool):
        def close_all(self):
            events.append("pool close")
            super().close_all()

    cancellation = FakeCancellation()
    pool = RecordingPool(FakeSftp())
    monkeypatch.setattr(
        upload_cli,
        "load_sftp_config_from_env",
        lambda: SimpleNamespace(remote_base="/base"),
    )
    monkeypatch.setattr(upload_cli, "ThreadLocalSftpPool", lambda cfg: pool)
    monkeypatch.setattr(upload_cli, "ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(upload_cli, "UploadCancellation", lambda: cancellation)
    monkeypatch.setattr(upload_cli, "tqdm", RecordingBar)

    def raise_keyboard_interrupt(*args, **kwargs):
        kwargs["pending"].add(FakeFuture())
        raise KeyboardInterrupt

    monkeypatch.setattr(upload_cli, "_run_futures", raise_keyboard_interrupt)

    result = upload_cli.run(str(src), workers=1)

    assert result == 130
    assert events[:7] == [
        "request",
        "watchdog",
        "cancel pending",
        ("shutdown", False, True),
        "pool close",
        ("shutdown", True, True),
        "cleanup complete",
    ]
    assert events[7][0] == "watchdog join"
    assert events[7][1] < 1
    captured = capsys.readouterr()
    assert "收到 Ctrl+C，正在取消上传…" in captured.err
    assert "上传已取消，退出码 130" in captured.err
    assert "关闭 SFTP 连接失败" not in captured.err


def test_run_keyboard_interrupt_returns_130_when_pool_close_fails(
    tmp_path: Path, monkeypatch, capsys
):
    src = tmp_path / "myfolder"
    src.mkdir()
    (src / "a.txt").write_bytes(b"a")
    events = []

    class FakeWatchdog:
        def join(self, timeout=None):
            events.append("watchdog join")

    class FakeCancellation:
        def request(self):
            events.append("request")

        def start_watchdog(self):
            events.append("watchdog")
            return FakeWatchdog()

        def mark_cleanup_complete(self):
            events.append("cleanup complete")

    class FakeExecutor:
        def __init__(self, max_workers):
            pass

        def shutdown(self, wait=True, *, cancel_futures=False):
            events.append(("shutdown", wait, cancel_futures))

    class FailingPool(FakePool):
        def close_all(self):
            events.append("pool close")
            raise RuntimeError("模拟关闭失败")

    cancellation = FakeCancellation()
    pool = FailingPool(FakeSftp())
    monkeypatch.setattr(
        upload_cli,
        "load_sftp_config_from_env",
        lambda: SimpleNamespace(remote_base="/base"),
    )
    monkeypatch.setattr(upload_cli, "ThreadLocalSftpPool", lambda cfg: pool)
    monkeypatch.setattr(upload_cli, "ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(upload_cli, "UploadCancellation", lambda: cancellation)
    monkeypatch.setattr(upload_cli, "tqdm", RecordingBar)
    monkeypatch.setattr(
        upload_cli,
        "_run_futures",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    result = upload_cli.run(str(src), workers=1)

    assert result == 130
    assert events == [
        "request",
        "watchdog",
        ("shutdown", False, True),
        "pool close",
        ("shutdown", True, True),
        "pool close",
        "watchdog join",
    ]
    captured = capsys.readouterr()
    assert captured.err.count("警告：关闭 SFTP 连接失败") == 1


def test_run_normal_path_shuts_executor_before_pool(tmp_path: Path, monkeypatch):
    src = tmp_path / "myfolder"
    src.mkdir()
    (src / "a.txt").write_bytes(b"a")
    events = []

    class FakeExecutor:
        def __init__(self, max_workers):
            pass

        def shutdown(self, wait=True, *, cancel_futures=False):
            events.append(("shutdown", wait, cancel_futures))

    class RecordingPool(FakePool):
        def close_all(self):
            events.append("pool close")
            super().close_all()

    pool = RecordingPool(FakeSftp())
    monkeypatch.setattr(
        upload_cli,
        "load_sftp_config_from_env",
        lambda: SimpleNamespace(remote_base="/base"),
    )
    monkeypatch.setattr(upload_cli, "ThreadLocalSftpPool", lambda cfg: pool)
    monkeypatch.setattr(upload_cli, "ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(upload_cli, "tqdm", RecordingBar)
    monkeypatch.setattr(
        upload_cli,
        "_run_futures",
        lambda *args, **kwargs: {"done": 1, "skipped": 0, "failed": 0, "cancelled": 0},
    )

    assert upload_cli.run(str(src), workers=1) == 0
    assert events == [("shutdown", True, False), "pool close"]


class CleanupWatchdog:
    def __init__(self, events):
        self.events = events
        self.active = True

    def join(self, timeout=None):
        self.events.append(("watchdog join", timeout))


class CleanupCancellation:
    def __init__(self, events):
        self.events = events
        self.cleanup_marked = False
        self.watchdog = CleanupWatchdog(events)

    def request(self):
        self.events.append("request")

    def start_watchdog(self):
        self.events.append("watchdog")
        return self.watchdog

    def mark_cleanup_complete(self):
        self.events.append("cleanup complete")
        self.cleanup_marked = True
        self.watchdog.active = False


class CleanupExecutor:
    def __init__(self, events, shutdown_error=None):
        self.events = events
        self.shutdown_error = shutdown_error

    def shutdown(self, wait=True, *, cancel_futures=False):
        self.events.append(("shutdown", wait, cancel_futures))
        if self.shutdown_error is not None:
            raise self.shutdown_error


class SequenceClosePool:
    def __init__(self, events, errors):
        self.events = events
        self.errors = list(errors)
        self.close_calls = 0

    def close_all(self):
        self.close_calls += 1
        self.events.append(("pool close", self.close_calls))
        error = self.errors.pop(0) if self.errors else None
        if error is not None:
            raise error


def test_cancel_upload_retries_pool_after_executor_wait(tmp_path: Path, capsys):
    events = []
    cancellation = CleanupCancellation(events)
    executor = CleanupExecutor(events)
    pool = SequenceClosePool(events, [RuntimeError("首次关闭失败")])

    result = upload_cli._cancel_upload(executor, pool, cancellation, set())

    assert result == 130
    assert pool.close_calls == 2
    assert cancellation.cleanup_marked is True
    assert cancellation.watchdog.active is False
    assert events == [
        "request",
        "watchdog",
        ("shutdown", False, True),
        ("pool close", 1),
        ("shutdown", True, True),
        ("pool close", 2),
        "cleanup complete",
        ("watchdog join", 0.1),
    ]
    assert "关闭 SFTP 连接失败" not in capsys.readouterr().err


def test_cancel_upload_leaves_watchdog_active_after_second_pool_failure(capsys):
    events = []
    cancellation = CleanupCancellation(events)
    executor = CleanupExecutor(events)
    pool = SequenceClosePool(
        events,
        [RuntimeError("首次关闭失败"), RuntimeError("重试关闭失败")],
    )

    result = upload_cli._cancel_upload(executor, pool, cancellation, set())

    assert result == 130
    assert pool.close_calls == 2
    assert cancellation.cleanup_marked is False
    assert cancellation.watchdog.active is True
    assert events == [
        "request",
        "watchdog",
        ("shutdown", False, True),
        ("pool close", 1),
        ("shutdown", True, True),
        ("pool close", 2),
        ("watchdog join", 0.1),
    ]
    captured = capsys.readouterr()
    assert captured.err.count("警告：关闭 SFTP 连接失败") == 1
    assert "重试关闭失败" in captured.err


def test_non_keyboard_interrupt_cleanup_attempts_both_and_reraises_original(
    tmp_path: Path, monkeypatch
):
    src = tmp_path / "myfolder"
    src.mkdir()
    (src / "a.txt").write_bytes(b"a")
    events = []
    primary_error = RuntimeError("上传主异常")
    executor = CleanupExecutor(events, RuntimeError("executor关闭异常"))
    pool = SequenceClosePool(events, [])

    def raise_primary(*args, **kwargs):
        raise primary_error

    monkeypatch.setattr(
        upload_cli,
        "load_sftp_config_from_env",
        lambda: SimpleNamespace(remote_base="/base"),
    )
    monkeypatch.setattr(upload_cli, "ThreadLocalSftpPool", lambda cfg: pool)
    monkeypatch.setattr(upload_cli, "ThreadPoolExecutor", lambda max_workers: executor)
    monkeypatch.setattr(upload_cli, "tqdm", RecordingBar)
    monkeypatch.setattr(upload_cli, "_run_futures", raise_primary)

    with pytest.raises(RuntimeError) as raised:
        upload_cli.run(str(src), workers=1)

    assert raised.value is primary_error
    assert events == [("shutdown", True, False), ("pool close", 1)]
