from __future__ import annotations

import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from bos_downloader.uploader import (
    RemoteDirectoryCache,
    SourceFileChangedError,
    ensure_remote_dir,
    remote_file_size,
    upload_file,
)


class FakeRemote:
    def __init__(self, existing=None):
        self.existing = dict(existing or {})
        self.directories = set()
        self.mkdir_calls = []
        self.mkdir_failures = {}
        self.lock = threading.Lock()


class FakeSftp:
    """模拟共享同一远端文件系统的独立 SFTP 连接。"""

    def __init__(self, remote=None):
        self.remote = remote or FakeRemote()
        self.put_calls = []

    def stat(self, path):
        with self.remote.lock:
            if path in self.remote.existing:
                return SimpleNamespace(st_size=self.remote.existing[path])
            if path in self.remote.directories:
                return SimpleNamespace(st_size=0, st_mode=stat.S_IFDIR | 0o755)
        raise FileNotFoundError(path)

    def lstat(self, path):
        return self.stat(path)

    def put(self, localpath, remotepath, callback=None, confirm=True):
        self.put_calls.append((localpath, remotepath))
        size = Path(localpath).stat().st_size
        if callback:
            callback(size // 2, size)
            callback(size, size)
        with self.remote.lock:
            self.remote.existing[remotepath] = size

    def mkdir(self, path, mode=511):
        with self.remote.lock:
            self.remote.mkdir_calls.append(path)
            failure = self.remote.mkdir_failures.get(path)
            if failure:
                raise failure
            if path in self.remote.directories:
                raise FileExistsError(path)
            self.remote.directories.add(path)


class PermissionStatSftp(FakeSftp):
    def stat(self, path):
        raise PermissionError(path)


def test_uploads_when_remote_absent(tmp_path: Path):
    local = tmp_path / "f.txt"
    local.write_bytes(b"hello")
    client = FakeSftp()

    status = upload_file(client, local, "/base/myfolder/f.txt")

    assert status == "done"
    assert client.put_calls == [(str(local), "/base/myfolder/f.txt")]
    assert "/base/myfolder" in client.remote.directories


def test_skips_when_remote_same_size(tmp_path: Path):
    local = tmp_path / "f.txt"
    local.write_bytes(b"hello")
    client = FakeSftp(FakeRemote(existing={"/base/f.txt": 5}))

    status = upload_file(client, local, "/base/f.txt")

    assert status == "skipped"
    assert client.put_calls == []


def test_overwrites_when_remote_different_size(tmp_path: Path):
    local = tmp_path / "f.txt"
    local.write_bytes(b"hello")
    client = FakeSftp(FakeRemote(existing={"/base/f.txt": 999}))

    status = upload_file(client, local, "/base/f.txt")

    assert status == "done"
    assert client.put_calls == [(str(local), "/base/f.txt")]


def test_remote_file_size_returns_none_when_absent():
    assert remote_file_size(FakeSftp(), "/nope") is None


def test_remote_file_size_returns_size_when_present():
    client = FakeSftp(FakeRemote(existing={"/x": 42}))
    assert remote_file_size(client, "/x") == 42


class SymlinkFileSftp(FakeSftp):
    def lstat(self, path):
        return SimpleNamespace(st_size=5, st_mode=stat.S_IFLNK | 0o777)


def test_remote_file_size_rejects_symbolic_link_target():
    with pytest.raises(OSError):
        remote_file_size(SymlinkFileSftp(), "/linked.pdf")


def test_remote_file_size_propagates_permission_errors():
    with pytest.raises(PermissionError):
        remote_file_size(PermissionStatSftp(), "/restricted")


def test_ensure_remote_dir_creates_each_level():
    client = FakeSftp()

    ensure_remote_dir(client, "/a/b/c")

    assert client.remote.mkdir_calls == ["/a", "/a/b", "/a/b/c"]


def test_ensure_remote_dir_accepts_existing_directories():
    remote = FakeRemote()
    remote.directories.add("/a")
    client = FakeSftp(remote)

    ensure_remote_dir(client, "/a/b")

    assert "/a/b" in remote.directories


def test_ensure_remote_dir_accepts_generic_mkdir_failure_for_existing_directory():
    remote = FakeRemote()
    remote.directories.add("/a")
    remote.mkdir_failures["/a"] = OSError("Failure")
    client = FakeSftp(remote)

    ensure_remote_dir(client, "/a")


class SymlinkDirectorySftp(FakeSftp):
    def mkdir(self, path, mode=511):
        raise OSError("Failure")

    def stat(self, path):
        return SimpleNamespace(st_size=0, st_mode=stat.S_IFDIR | 0o755)

    def lstat(self, path):
        return SimpleNamespace(st_size=0, st_mode=stat.S_IFLNK | 0o777)


def test_ensure_remote_dir_rejects_symbolic_link_directory():
    with pytest.raises(OSError):
        ensure_remote_dir(SymlinkDirectorySftp(), "/linked")


def test_ensure_remote_dir_propagates_permission_errors():
    remote = FakeRemote()
    remote.mkdir_failures["/a"] = PermissionError("denied")

    with pytest.raises(PermissionError):
        ensure_remote_dir(FakeSftp(remote), "/a/b")


def test_directory_cache_avoids_repeated_mkdir_across_clients():
    remote = FakeRemote()
    cache = RemoteDirectoryCache()
    clients = [FakeSftp(remote) for _ in range(12)]

    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(lambda client: cache.ensure(client, "/a/b/c"), clients))

    assert remote.mkdir_calls.count("/a") == 1
    assert remote.mkdir_calls.count("/a/b") == 1
    assert remote.mkdir_calls.count("/a/b/c") == 1


class BlockingFailureSftp(FakeSftp):
    def __init__(self, remote, failure_published, release_failure):
        super().__init__(remote)
        self.failure_published = failure_published
        self.release_failure = release_failure

    def mkdir(self, path, mode=511):
        with self.remote.lock:
            self.remote.mkdir_calls.append(path)
        self.failure_published.set()
        self.release_failure.wait(timeout=2)
        raise PermissionError("denied")


def test_directory_cache_does_not_start_new_owner_before_failure_is_published():
    remote = FakeRemote()
    cache = RemoteDirectoryCache()
    failure_published = threading.Event()
    release_failure = threading.Event()
    first_client = BlockingFailureSftp(remote, failure_published, release_failure)
    second_client = FakeSftp(remote)
    errors = []

    def attempt(client):
        try:
            cache.ensure(client, "/a")
        except PermissionError as exc:
            errors.append(exc)

    first_thread = threading.Thread(target=attempt, args=(first_client,))
    first_thread.start()
    assert failure_published.wait(timeout=2)

    second_thread = threading.Thread(target=attempt, args=(second_client,))
    second_thread.start()
    release_failure.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert len(remote.mkdir_calls) == 1
    assert len(errors) == 2


class RecordingInflight(dict):
    def __init__(self):
        super().__init__()
        self.removed_before_published = []

    def pop(self, key, default=None):
        future = self[key]
        self.removed_before_published.append(not future.done())
        return super().pop(key, default)


def test_directory_cache_publishes_failure_before_removing_inflight():
    remote = FakeRemote()
    remote.mkdir_failures["/a"] = PermissionError("denied")
    cache = RemoteDirectoryCache()
    inflight = RecordingInflight()
    cache._inflight = inflight

    with pytest.raises(PermissionError):
        cache.ensure(FakeSftp(remote), "/a")

    assert inflight.removed_before_published == [False]


def test_directory_cache_retries_after_failed_creation():
    remote = FakeRemote()
    remote.mkdir_failures["/a"] = PermissionError("denied")
    cache = RemoteDirectoryCache()

    with pytest.raises(PermissionError):
        cache.ensure(FakeSftp(remote), "/a")

    del remote.mkdir_failures["/a"]
    cache.ensure(FakeSftp(remote), "/a")
    assert remote.mkdir_calls == ["/a", "/a"]


def test_upload_uses_directory_cache(tmp_path: Path):
    remote = FakeRemote()
    client = FakeSftp(remote)
    cache = RemoteDirectoryCache()
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    upload_file(client, first, "/base/folder/first.txt", directory_cache=cache)
    upload_file(client, second, "/base/folder/second.txt", directory_cache=cache)

    assert remote.mkdir_calls == ["/base", "/base/folder"]


def test_upload_rejects_changed_source_size(tmp_path: Path):
    local = tmp_path / "f.txt"
    local.write_bytes(b"hello")

    with pytest.raises(SourceFileChangedError):
        upload_file(FakeSftp(), local, "/base/f.txt", expected_size=4)


def test_progress_callback_called_on_skip(tmp_path: Path):
    local = tmp_path / "f.txt"
    local.write_bytes(b"hello")
    client = FakeSftp(FakeRemote(existing={"/base/f.txt": 5}))
    seen = []

    upload_file(
        client,
        local,
        "/base/f.txt",
        progress_callback=lambda done, total: seen.append((done, total)),
    )

    assert seen[-1] == (5, 5)


def test_progress_callback_is_forwarded_during_upload(tmp_path: Path):
    local = tmp_path / "f.txt"
    local.write_bytes(b"123456")
    seen = []

    upload_file(
        FakeSftp(),
        local,
        "/base/f.txt",
        progress_callback=lambda done, total: seen.append((done, total)),
    )

    assert seen == [(3, 6), (6, 6)]
