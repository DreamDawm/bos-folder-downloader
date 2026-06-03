from pathlib import Path
from types import SimpleNamespace

from bos_downloader.downloader import download_object


class FakeData:
    """模拟 response.data:按 chunk 产出字节的可读流。"""

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


class FakeClient:
    def __init__(self, full_content: bytes):
        self._full = full_content
        self.get_object_calls = []

    def get_object_meta_data(self, bucket, key):
        return SimpleNamespace(
            metadata=SimpleNamespace(content_length=str(len(self._full)))
        )

    def get_object(self, bucket, key, range=None):
        self.get_object_calls.append(range)
        if range is None:
            return SimpleNamespace(data=FakeData(self._full))
        start, end = range[0], range[1]
        return SimpleNamespace(data=FakeData(self._full[start : end + 1]))


def test_full_download_creates_file_with_content(tmp_path: Path):
    client = FakeClient(b"hello world!")
    dest = tmp_path / "out.txt"
    download_object(client, "bkt", "data/out.txt", dest)
    assert dest.read_bytes() == b"hello world!"
    assert not (tmp_path / "out.txt.part").exists()
    assert client.get_object_calls == [(0, 11)]


def test_resume_from_partial_part_file(tmp_path: Path):
    client = FakeClient(b"hello world!")
    dest = tmp_path / "out.txt"
    (tmp_path / "out.txt.part").write_bytes(b"hello ")
    download_object(client, "bkt", "data/out.txt", dest)
    assert dest.read_bytes() == b"hello world!"
    assert client.get_object_calls == [(6, 11)]


def test_already_complete_part_just_renames(tmp_path: Path):
    client = FakeClient(b"hello world!")
    dest = tmp_path / "out.txt"
    (tmp_path / "out.txt.part").write_bytes(b"hello world!")
    download_object(client, "bkt", "data/out.txt", dest)
    assert dest.read_bytes() == b"hello world!"
    assert client.get_object_calls == []


def test_existing_dest_is_skipped(tmp_path: Path):
    client = FakeClient(b"hello world!")
    dest = tmp_path / "out.txt"
    dest.write_bytes(b"hello world!")
    download_object(client, "bkt", "data/out.txt", dest)
    assert client.get_object_calls == []


def test_progress_callback_receives_total(tmp_path: Path):
    client = FakeClient(b"hello world!")
    dest = tmp_path / "out.txt"
    seen = []
    download_object(
        client, "bkt", "data/out.txt", dest,
        progress_callback=lambda done, total: seen.append((done, total)),
    )
    assert seen[-1] == (12, 12)
    assert all(total == 12 for _, total in seen)


def test_empty_object_creates_empty_file(tmp_path: Path):
    client = FakeClient(b"")
    dest = tmp_path / "empty.txt"
    download_object(client, "bkt", "empty", dest)
    assert dest.exists()
    assert dest.read_bytes() == b""
    assert not (tmp_path / "empty.txt.part").exists()
