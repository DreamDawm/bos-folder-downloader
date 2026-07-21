"""run() 多线程上传编排的测试。"""

from __future__ import annotations

import stat
import threading
from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from bos_downloader import upload_cli


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
    def __init__(self, client):
        self._client = client
        self.closed = False

    def get(self):
        return self._client

    def close_all(self):
        self.closed = True


class RecordingBar:
    instances = []

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
        upload_cli, "load_sftp_config_from_env",
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
        def __exit__(self, exc_type, exc, traceback):
            events.append("executor")
            return super().__exit__(exc_type, exc, traceback)

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


def test_silences_done_and_skipped_but_keeps_summary(
    tmp_path: Path, monkeypatch, capsys
):
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
