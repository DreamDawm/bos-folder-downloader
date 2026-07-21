"""pipeline 单组与编排测试。"""
import logging
import stat
import threading
from pathlib import Path
from types import SimpleNamespace

from bos_downloader import pipeline
from bos_downloader.lister import RemoteObject


class FakeData:
    def __init__(self, payload: bytes):
        self._payload = payload
        self._pos = 0

    def read(self, n=None):
        size = len(self._payload) if n is None else n
        data = self._payload[self._pos : self._pos + size]
        self._pos += len(data)
        return data

    def close(self):
        pass


class FakeBos:
    def __init__(self, files: dict, failing_get=None):
        self._files = files
        self._failing = set(failing_get or set())
        self._lock = threading.Lock()
        self.get_calls = {}

    def get_object_meta_data(self, bucket, key):
        return SimpleNamespace(
            metadata=SimpleNamespace(content_length=str(len(self._files[key])))
        )

    def list_all_objects(self, bucket, prefix=None):
        for key, data in self._files.items():
            if prefix is None or key.startswith(prefix):
                yield SimpleNamespace(key=key, size=len(data))

    def get_object(self, bucket, key, range=None):
        with self._lock:
            self.get_calls[key] = self.get_calls.get(key, 0) + 1
        if key in self._failing:
            raise RuntimeError("模拟下载失败")
        full = self._files[key]
        if range is None:
            return SimpleNamespace(data=FakeData(full))
        s, e = range
        return SimpleNamespace(data=FakeData(full[s : e + 1]))


class FakeSftp:
    def __init__(self, failing_put=None):
        self._failing = set(failing_put or set())
        self._lock = threading.Lock()
        self.existing = {}
        self.directories = set()
        self.put_calls = {}

    def stat(self, path):
        with self._lock:
            if path in self.existing:
                return SimpleNamespace(st_size=self.existing[path])
            if path in self.directories:
                return SimpleNamespace(st_size=0, st_mode=stat.S_IFDIR | 0o755)
        raise FileNotFoundError(path)

    def lstat(self, path):
        return self.stat(path)

    def put(self, localpath, remotepath, callback=None, confirm=True):
        if remotepath in self._failing:
            raise RuntimeError("模拟上传失败")
        with self._lock:
            self.put_calls[remotepath] = self.put_calls.get(remotepath, 0) + 1
            self.existing[remotepath] = Path(localpath).stat().st_size

    def mkdir(self, path, mode=511):
        with self._lock:
            if path in self.directories:
                raise FileExistsError(path)
            self.directories.add(path)


class FakePool:
    def __init__(self, client):
        self._client = client
        self.closed = False

    def get(self):
        return self._client

    def close_all(self):
        self.closed = True


def _logger():
    lg = logging.getLogger("test_pipeline")
    lg.handlers.clear()
    lg.addHandler(logging.NullHandler())
    return lg


def test_process_group_happy_path_downloads_uploads_deletes(tmp_path: Path):
    bos = FakeBos({"data/sub/a.txt": b"aaaa", "data/sub/b.txt": b"bbbb"})
    pool = FakePool(FakeSftp())
    objs = [RemoteObject("data/sub/a.txt", 4), RemoteObject("data/sub/b.txt", 4)]

    result = pipeline.process_group(
        bos, "bkt", pool, "data/", "/base",
        directory="data/sub", objects=objs,
        dest_root=tmp_path, dl_workers=2, ul_workers=2, logger=_logger(),
    )

    assert result.downloaded == 2
    assert result.uploaded == 2
    assert result.deleted == 2
    assert result.failed == 0
    assert not (tmp_path / "data" / "sub" / "a.txt").exists()
    assert pool.get().put_calls.get("/base/data/sub/a.txt") == 1


def test_process_group_upload_failure_keeps_local(tmp_path: Path):
    bos = FakeBos({"data/sub/a.txt": b"aaaa", "data/sub/b.txt": b"bbbb"})
    pool = FakePool(FakeSftp(failing_put={"/base/data/sub/b.txt"}))
    objs = [RemoteObject("data/sub/a.txt", 4), RemoteObject("data/sub/b.txt", 4)]

    result = pipeline.process_group(
        bos, "bkt", pool, "data/", "/base",
        directory="data/sub", objects=objs,
        dest_root=tmp_path, dl_workers=2, ul_workers=2, logger=_logger(),
    )

    assert result.failed > 0
    assert result.deleted == 0
    assert (tmp_path / "data" / "sub" / "a.txt").exists()


def test_process_group_download_failure_skips_upload(tmp_path: Path):
    bos = FakeBos(
        {"data/sub/a.txt": b"aaaa", "data/sub/b.txt": b"bbbb"},
        failing_get={"data/sub/b.txt"},
    )
    sftp = FakeSftp()
    pool = FakePool(sftp)
    objs = [RemoteObject("data/sub/a.txt", 4), RemoteObject("data/sub/b.txt", 4)]

    result = pipeline.process_group(
        bos, "bkt", pool, "data/", "/base",
        directory="data/sub", objects=objs,
        dest_root=tmp_path, dl_workers=2, ul_workers=2, logger=_logger(),
    )

    assert result.failed > 0
    assert result.uploaded == 0
    assert sftp.put_calls == {}


def _patch(monkeypatch, bos, sftp):
    pool = FakePool(sftp)
    monkeypatch.setattr(
        pipeline, "load_config_from_env",
        lambda: SimpleNamespace(bucket="bkt"),
    )
    monkeypatch.setattr(pipeline, "create_bos_client", lambda cfg: bos)
    monkeypatch.setattr(
        pipeline, "load_sftp_config_from_env",
        lambda: SimpleNamespace(remote_base="/base"),
    )
    monkeypatch.setattr(pipeline, "ThreadLocalSftpPool", lambda cfg: pool)
    return pool


def test_run_processes_groups_serially_and_counts(tmp_path, monkeypatch):
    bos = FakeBos({
        "data/a.txt": b"aaaa",
        "data/sub/b.txt": b"bbbb",
        "data/sub/c.txt": b"cccc",
    })
    sftp = FakeSftp()
    pool = _patch(monkeypatch, bos, sftp)

    failures = pipeline.run(
        "data/", str(tmp_path / "dl"),
        logs_dir=str(tmp_path / "logs"), stamp="20260608-130000",
        dl_workers=1, ul_workers=5,
    )

    assert failures == 0
    assert pool.get().put_calls.get("/base/data/a.txt") == 1
    assert pool.get().put_calls.get("/base/data/sub/b.txt") == 1
    assert not (tmp_path / "dl" / "data" / "a.txt").exists()
    assert not (tmp_path / "dl" / "data" / "sub" / "b.txt").exists()
    assert pool.closed is True
    log_text = (tmp_path / "logs" / "bos-sync-20260608-130000.log").read_text("utf-8")
    assert "下载 3 个" in log_text
    assert "上传 3 个" in log_text
    assert "删除 3 个" in log_text


def test_run_failed_group_keeps_local_and_continues(tmp_path, monkeypatch):
    bos = FakeBos({"data/a.txt": b"aaaa", "data/sub/b.txt": b"bbbb"})
    sftp = FakeSftp(failing_put={"/base/data/sub/b.txt"})
    _patch(monkeypatch, bos, sftp)

    failures = pipeline.run(
        "data/", str(tmp_path / "dl"),
        logs_dir=str(tmp_path / "logs"), stamp="20260608-140000",
        dl_workers=1, ul_workers=2,
    )

    assert failures == 1
    assert not (tmp_path / "dl" / "data" / "a.txt").exists()
    assert (tmp_path / "dl" / "data" / "sub" / "b.txt").exists()


def test_run_returns_zero_when_no_objects(tmp_path, monkeypatch):
    bos = FakeBos({})
    _patch(monkeypatch, bos, FakeSftp())

    failures = pipeline.run(
        "data/", str(tmp_path / "dl"),
        logs_dir=str(tmp_path / "logs"), stamp="20260608-150000",
    )
    assert failures == 0
