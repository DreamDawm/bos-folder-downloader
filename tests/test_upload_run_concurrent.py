"""run() 多线程上传编排的测试。"""

import threading
from pathlib import Path
from types import SimpleNamespace

from bos_downloader import upload_cli


class FakeSftp:
    """线程安全的多文件 Fake:记录每个远端路径的 put 次数。"""

    def __init__(self, existing=None, failing=None):
        self.existing = dict(existing or {})
        self.failing = set(failing or set())
        self._lock = threading.Lock()
        self.put_calls = {}
        self.made_dirs = set()

    def stat(self, path):
        with self._lock:
            if path in self.existing:
                return SimpleNamespace(st_size=self.existing[path])
        raise IOError("No such file")

    def put(self, localpath, remotepath, callback=None, confirm=True):
        if remotepath in self.failing:
            raise RuntimeError("模拟上传失败")
        with self._lock:
            self.put_calls[remotepath] = self.put_calls.get(remotepath, 0) + 1
            self.existing[remotepath] = Path(localpath).stat().st_size

    def mkdir(self, path, mode=511):
        with self._lock:
            if path in self.made_dirs:
                raise IOError("已存在")
            self.made_dirs.add(path)


class FakePool:
    def __init__(self, client):
        self._client = client
        self.closed = False

    def get(self):
        return self._client

    def close_all(self):
        self.closed = True


def _patch(monkeypatch, client):
    """让 run() 使用注入的 FakeSftp 与 FakePool。"""
    pool = FakePool(client)
    monkeypatch.setattr(
        upload_cli, "load_sftp_config_from_env",
        lambda: SimpleNamespace(remote_base="/base"),
    )
    monkeypatch.setattr(upload_cli, "ThreadLocalSftpPool", lambda cfg: pool)
    return pool


def _make_tree(root: Path):
    (root / "a.txt").write_bytes(b"aaaa")
    (root / "sub").mkdir()
    (root / "sub" / "b.txt").write_bytes(b"bbbbbb")
    (root / "sub" / "deep").mkdir()
    (root / "sub" / "deep" / "c.bin").write_bytes(b"cccccccc")


def test_uploads_all_files_to_correct_remote_paths(tmp_path: Path, monkeypatch):
    src = tmp_path / "myfolder"
    src.mkdir()
    _make_tree(src)
    client = FakeSftp()
    pool = _patch(monkeypatch, client)

    failures = upload_cli.run(str(src), workers=3)

    assert failures == 0
    # 远端保留 myfolder 这一级,且子目录结构完整
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
    # a.txt 远端已存在且同大小(4 字节)
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
