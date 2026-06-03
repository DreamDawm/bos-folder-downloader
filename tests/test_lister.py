from types import SimpleNamespace

from bos_downloader.lister import RemoteObject, list_objects_under_prefix


class FakeClient:
    def __init__(self, items):
        self._items = items
        self.calls = []

    def list_all_objects(self, bucket_name, prefix=None):
        self.calls.append((bucket_name, prefix))
        return iter(self._items)


def test_lists_files_and_skips_pseudo_directories():
    items = [
        SimpleNamespace(key="data/", size=0),          # 伪目录,跳过
        SimpleNamespace(key="data/a.txt", size=10),
        SimpleNamespace(key="data/sub/", size=0),      # 伪目录,跳过
        SimpleNamespace(key="data/sub/b.bin", size=20),
    ]
    client = FakeClient(items)
    result = list(list_objects_under_prefix(client, "my-bucket", "data/"))
    assert result == [
        RemoteObject(key="data/a.txt", size=10),
        RemoteObject(key="data/sub/b.bin", size=20),
    ]
    assert client.calls == [("my-bucket", "data/")]


def test_empty_prefix_returns_nothing():
    client = FakeClient([])
    assert list(list_objects_under_prefix(client, "my-bucket", "empty/")) == []
