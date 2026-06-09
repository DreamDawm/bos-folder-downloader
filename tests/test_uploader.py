from pathlib import Path
from types import SimpleNamespace

from bos_downloader.uploader import (
    ensure_remote_dir,
    remote_file_size,
    upload_file,
)


class FakeSftp:
    """模拟 SFTP 客户端:existing 映射 远端路径 -> 大小。"""

    def __init__(self, existing=None):
        self.existing = dict(existing or {})
        self.put_calls = []  # (localpath, remotepath)
        self.made_dirs = []

    def stat(self, path):
        if path in self.existing:
            return SimpleNamespace(st_size=self.existing[path])
        raise IOError("No such file")

    def put(self, localpath, remotepath, callback=None, confirm=True):
        self.put_calls.append((localpath, remotepath))
        # 模拟上传后远端出现该文件
        self.existing[remotepath] = Path(localpath).stat().st_size

    def mkdir(self, path, mode=511):
        if path in self.made_dirs:
            raise IOError("已存在")
        self.made_dirs.append(path)


def test_uploads_when_remote_absent(tmp_path: Path):
    local = tmp_path / "f.txt"
    local.write_bytes(b"hello")
    client = FakeSftp()

    status = upload_file(client, local, "/base/myfolder/f.txt")

    assert status == "done"
    assert client.put_calls == [(str(local), "/base/myfolder/f.txt")]
    # 父目录被逐级创建
    assert "/base/myfolder" in client.made_dirs


def test_skips_when_remote_same_size(tmp_path: Path):
    local = tmp_path / "f.txt"
    local.write_bytes(b"hello")
    client = FakeSftp(existing={"/base/f.txt": 5})

    status = upload_file(client, local, "/base/f.txt")

    assert status == "skipped"
    assert client.put_calls == []


def test_overwrites_when_remote_different_size(tmp_path: Path):
    local = tmp_path / "f.txt"
    local.write_bytes(b"hello")
    client = FakeSftp(existing={"/base/f.txt": 999})

    status = upload_file(client, local, "/base/f.txt")

    assert status == "done"
    assert client.put_calls == [(str(local), "/base/f.txt")]


def test_remote_file_size_returns_none_when_absent():
    client = FakeSftp()
    assert remote_file_size(client, "/nope") is None


def test_remote_file_size_returns_size_when_present():
    client = FakeSftp(existing={"/x": 42})
    assert remote_file_size(client, "/x") == 42


def test_ensure_remote_dir_creates_each_level():
    client = FakeSftp()
    ensure_remote_dir(client, "/a/b/c")
    assert client.made_dirs == ["/a", "/a/b", "/a/b/c"]


def test_ensure_remote_dir_swallows_existing():
    client = FakeSftp()
    client.made_dirs.append("/a")  # /a 已存在,mkdir 会抛 IOError
    # 不应抛出
    ensure_remote_dir(client, "/a/b")
    assert "/a/b" in client.made_dirs


def test_progress_callback_called_on_skip(tmp_path: Path):
    local = tmp_path / "f.txt"
    local.write_bytes(b"hello")
    client = FakeSftp(existing={"/base/f.txt": 5})
    seen = []

    upload_file(
        client, local, "/base/f.txt",
        progress_callback=lambda done, total: seen.append((done, total)),
    )

    assert seen[-1] == (5, 5)
