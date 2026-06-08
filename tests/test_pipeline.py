"""pipeline 单组与编排测试。"""
import logging
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
        self.put_calls = {}

    def stat(self, path):
        with self._lock:
            if path in self.existing:
                return SimpleNamespace(st_size=self.existing[path])
        raise IOError("No such file")

    def put(self, localpath, remotepath, callback=None, confirm=True):
        if remotepath in self._failing:
            raise RuntimeError("模拟上传失败")
        with self._lock:
            self.put_calls[remotepath] = self.put_calls.get(remotepath, 0) + 1
            self.existing[remotepath] = Path(localpath).stat().st_size

    def mkdir(self, path, mode=511):
        raise IOError("已存在")


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
