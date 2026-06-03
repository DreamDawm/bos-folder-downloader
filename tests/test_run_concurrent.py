"""run() 多线程下载编排的测试。"""

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from bos_downloader import cli


class FakeData:
    def __init__(self, payload: bytes, chunk: int = 4):
        self._payload = payload
        self._chunk = chunk
        self._pos = 0

    def read(self, n=None):
        size = self._chunk if n is None else min(n, self._chunk)
        data = self._payload[self._pos : self._pos + size]
        self._pos += len(data)
        return data

    def close(self):
        pass


class FakeListItem(SimpleNamespace):
    pass


class FakeClient:
    """记录每个 key 的下载次数与并发安全的多文件 Fake。"""

    def __init__(self, files: dict[str, bytes]):
        self._files = files
        self._lock = threading.Lock()
        self.get_calls: dict[str, int] = {}

    def list_all_objects(self, bucket_name, prefix=None):
        for key, content in self._files.items():
            yield FakeListItem(key=key, size=len(content))

    def get_object_meta_data(self, bucket, key):
        return SimpleNamespace(
            metadata=SimpleNamespace(content_length=str(len(self._files[key])))
        )

    def get_object(self, bucket, key, range=None):
        with self._lock:
            self.get_calls[key] = self.get_calls.get(key, 0) + 1
        full = self._files[key]
        if range is None:
            return SimpleNamespace(data=FakeData(full))
        start, end = range[0], range[1]
        return SimpleNamespace(data=FakeData(full[start : end + 1]))


@pytest.fixture
def patched(monkeypatch):
    """让 run() 使用注入的 FakeClient,而非真实 BosClient。"""

    def _install(client: FakeClient):
        monkeypatch.setattr(cli, "load_config_from_env",
                            lambda: SimpleNamespace(bucket="bkt"))
        monkeypatch.setattr(cli, "create_bos_client", lambda cfg: client)

    return _install


def test_run_downloads_all_files_across_subfolders(tmp_path: Path, patched):
    client = FakeClient({
        "data/a.txt": b"aaaa",
        "data/sub/b.txt": b"bbbbbb",
        "data/sub/deep/c.bin": b"cccccccc",
    })
    patched(client)

    failures = cli.run("data/", str(tmp_path), workers=3)

    assert failures == 0
    assert (tmp_path / "data" / "a.txt").read_bytes() == b"aaaa"
    assert (tmp_path / "data" / "sub" / "b.txt").read_bytes() == b"bbbbbb"
    assert (tmp_path / "data" / "sub" / "deep" / "c.bin").read_bytes() == b"cccccccc"


def test_run_downloads_each_file_exactly_once(tmp_path: Path, patched):
    client = FakeClient({f"data/f{i}.txt": b"x" * (i + 1) for i in range(20)})
    patched(client)

    cli.run("data/", str(tmp_path), workers=4)

    # 每个文件恰好被下载一次,绝不会有两个线程同时下同一文件
    assert all(count == 1 for count in client.get_calls.values())
    assert len(client.get_calls) == 20


def test_run_skips_existing_files(tmp_path: Path, patched):
    client = FakeClient({"data/a.txt": b"aaaa", "data/b.txt": b"bbbb"})
    patched(client)
    existing = tmp_path / "data" / "a.txt"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"aaaa")

    cli.run("data/", str(tmp_path), workers=2)

    # 已存在的文件被跳过,不会再发起下载
    assert "data/a.txt" not in client.get_calls
    assert client.get_calls.get("data/b.txt") == 1


def test_run_counts_failures_without_aborting(tmp_path: Path, patched):
    client = FakeClient({"data/ok.txt": b"ok", "data/bad.txt": b"bad"})
    patched(client)

    original_get = client.get_object

    def flaky_get(bucket, key, range=None):
        if key == "data/bad.txt":
            raise RuntimeError("模拟下载失败")
        return original_get(bucket, key, range=range)

    client.get_object = flaky_get

    failures = cli.run("data/", str(tmp_path), workers=2)

    # 一个失败不影响另一个成功
    assert failures == 1
    assert (tmp_path / "data" / "ok.txt").read_bytes() == b"ok"


def test_run_returns_zero_when_no_objects(tmp_path: Path, patched):
    client = FakeClient({})
    patched(client)

    assert cli.run("data/", str(tmp_path), workers=3) == 0
