import threading
from types import SimpleNamespace

from bos_downloader import sftp_client
from bos_downloader.config import SftpConfig


def _cfg():
    return SftpConfig(
        host="10.0.0.1", port=2222, username="user",
        password="secret", remote_base="/upload",
    )


def test_open_sftp_passes_credentials(monkeypatch):
    captured = {}

    class FakeTransport:
        def __init__(self, addr):
            captured["addr"] = addr

        def connect(self, username=None, password=None):
            captured["username"] = username
            captured["password"] = password

        def close(self):
            captured["closed"] = True

    fake_sftp = SimpleNamespace()
    monkeypatch.setattr(sftp_client.paramiko, "Transport", FakeTransport)
    monkeypatch.setattr(
        sftp_client.paramiko.SFTPClient, "from_transport",
        staticmethod(lambda t: fake_sftp),
    )

    result = sftp_client.open_sftp(_cfg())

    assert captured["addr"] == ("10.0.0.1", 2222)
    assert captured["username"] == "user"
    assert captured["password"] == "secret"
    assert result is fake_sftp


def test_pool_reuses_same_client_within_thread(monkeypatch):
    created = []

    def fake_open(cfg):
        client = SimpleNamespace(closed=False)
        created.append(client)
        return client

    monkeypatch.setattr(sftp_client, "open_sftp", fake_open)
    pool = sftp_client.ThreadLocalSftpPool(_cfg())

    first = pool.get()
    second = pool.get()

    # 同一线程内复用同一连接,只建一次
    assert first is second
    assert len(created) == 1


def test_pool_creates_distinct_client_per_thread(monkeypatch):
    monkeypatch.setattr(
        sftp_client, "open_sftp",
        lambda cfg: SimpleNamespace(closed=False),
    )
    pool = sftp_client.ThreadLocalSftpPool(_cfg())
    results = {}

    def worker(name):
        results[name] = id(pool.get())

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()

    # 不同线程得到不同连接对象
    assert results["a"] != results["b"]


def test_close_all_closes_every_connection(monkeypatch):
    closed = []

    def fake_open(cfg):
        c = SimpleNamespace()
        c.close = lambda: closed.append(c)
        return c

    monkeypatch.setattr(sftp_client, "open_sftp", fake_open)
    pool = sftp_client.ThreadLocalSftpPool(_cfg())

    def worker():
        pool.get()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    pool.close_all()

    assert len(closed) == 2
