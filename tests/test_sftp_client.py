import threading
from types import SimpleNamespace

import pytest

from bos_downloader import sftp_client
from bos_downloader.config import SftpConfig


class FakeSocket:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


class FakeChannel:
    def __init__(self, error=None, events=None):
        self.error = error
        self.events = events
        self.timeouts = []

    def settimeout(self, timeout):
        self.timeouts.append(timeout)
        if self.events is not None:
            self.events.append(("channel_settimeout", timeout))
        if self.error is not None:
            raise self.error


class FakeSftp:
    def __init__(self, channel=None, events=None):
        self.channel = channel or FakeChannel(events=events)
        self.events = events
        self.get_channel_calls = 0

    def get_channel(self):
        self.get_channel_calls += 1
        if self.events is not None:
            self.events.append(("get_channel",))
        return self.channel


class FakeTransport:
    def __init__(self, events=None):
        self.socket = None
        self.events = events
        self.start_client_timeouts = []
        self._auth_timeout = None
        self._channel_timeout = None
        self.credentials = []
        self.keepalive_intervals = []
        self.close_calls = 0
        self.start_error = None
        self.auth_error = None
        self.keepalive_error = None

    @property
    def auth_timeout(self):
        return self._auth_timeout

    @auth_timeout.setter
    def auth_timeout(self, timeout):
        self._auth_timeout = timeout
        if self.events is not None:
            self.events.append(("auth_timeout", timeout))

    @property
    def channel_timeout(self):
        return self._channel_timeout

    @channel_timeout.setter
    def channel_timeout(self, timeout):
        self._channel_timeout = timeout
        if self.events is not None:
            self.events.append(("channel_timeout", timeout))

    def start_client(self, timeout=None):
        self.start_client_timeouts.append(timeout)
        if self.events is not None:
            self.events.append(("start_client", timeout))
        if self.start_error is not None:
            raise self.start_error

    def auth_password(self, username, password):
        self.credentials.append((username, password))
        if self.events is not None:
            self.events.append(("auth_password", username, password))
        if self.auth_error is not None:
            raise self.auth_error

    def set_keepalive(self, interval):
        self.keepalive_intervals.append(interval)
        if self.events is not None:
            self.events.append(("keepalive", interval))
        if self.keepalive_error is not None:
            raise self.keepalive_error

    def close(self):
        self.close_calls += 1


def _cfg():
    return SftpConfig(
        host="10.0.0.1",
        port=2222,
        username="user",
        password="secret",
        remote_base="/upload",
    )


def _install_fakes(
    monkeypatch,
    transport,
    fake_sftp,
    from_transport=None,
    fake_socket=None,
    transport_factory=None,
):
    fake_socket = fake_socket or FakeSocket()
    connection = {}

    def create_connection(addr, timeout=None):
        connection["addr"] = addr
        connection["timeout"] = timeout
        return fake_socket

    def create_transport(sock):
        transport.socket = sock
        return transport

    monkeypatch.setattr(sftp_client.socket, "create_connection", create_connection)
    monkeypatch.setattr(
        sftp_client.paramiko,
        "Transport",
        transport_factory or create_transport,
    )
    if from_transport is None:

        def from_transport_factory(candidate):
            if transport.events is not None:
                transport.events.append(("from_transport", candidate))
            return fake_sftp

        factory = from_transport_factory
    else:
        factory = from_transport
    monkeypatch.setattr(
        sftp_client.paramiko.SFTPClient,
        "from_transport",
        staticmethod(factory),
    )
    return connection, fake_socket


def test_open_sftp_configures_timeouts_keepalive_and_credentials(monkeypatch):
    events = []
    transport = FakeTransport(events=events)
    fake_sftp = FakeSftp(events=events)
    connection, fake_socket = _install_fakes(monkeypatch, transport, fake_sftp)

    result = sftp_client.open_sftp(_cfg())

    assert result is fake_sftp
    assert connection == {"addr": ("10.0.0.1", 2222), "timeout": 10.0}
    assert transport.socket is fake_socket
    assert transport.start_client_timeouts == [10.0]
    assert transport.auth_timeout == 10.0
    assert transport.channel_timeout == 30.0
    assert transport.credentials == [("user", "secret")]
    assert transport.keepalive_intervals == [30]
    assert fake_sftp.channel.timeouts == [30.0]
    assert fake_sftp.get_channel_calls == 1
    assert result._bos_transport is transport
    assert transport.close_calls == 0
    assert events == [
        ("start_client", 10.0),
        ("auth_timeout", 10.0),
        ("auth_password", "user", "secret"),
        ("keepalive", 30),
        ("channel_timeout", 30.0),
        ("from_transport", transport),
        ("get_channel",),
        ("channel_settimeout", 30.0),
    ]


def test_open_sftp_closes_socket_when_transport_construction_fails(monkeypatch):
    fake_socket = FakeSocket()

    def fail_transport(sock):
        assert sock is fake_socket
        raise RuntimeError("transport construction failed")

    connection, returned_socket = _install_fakes(
        monkeypatch,
        FakeTransport(),
        FakeSftp(),
        fake_socket=fake_socket,
        transport_factory=fail_transport,
    )

    with pytest.raises(RuntimeError, match="transport construction failed"):
        sftp_client.open_sftp(_cfg())

    assert connection == {"addr": ("10.0.0.1", 2222), "timeout": 10.0}
    assert returned_socket.close_calls == 1


def test_open_sftp_closes_transport_when_handshake_fails(monkeypatch):
    transport = FakeTransport()
    transport.start_error = TimeoutError("handshake timed out")
    _install_fakes(monkeypatch, transport, FakeSftp())

    with pytest.raises(TimeoutError, match="handshake timed out"):
        sftp_client.open_sftp(_cfg())

    assert transport.close_calls == 1
    assert transport.credentials == []


def test_open_sftp_closes_transport_when_authentication_fails(monkeypatch):
    transport = FakeTransport()
    transport.auth_error = PermissionError("authentication failed")
    _install_fakes(monkeypatch, transport, FakeSftp())

    with pytest.raises(PermissionError, match="authentication failed"):
        sftp_client.open_sftp(_cfg())

    assert transport.close_calls == 1
    assert transport.credentials == [("user", "secret")]


def test_open_sftp_closes_transport_when_from_transport_fails(monkeypatch):
    transport = FakeTransport()

    def fail_from_transport(_transport):
        raise RuntimeError("SFTP channel creation failed")

    _install_fakes(monkeypatch, transport, FakeSftp(), fail_from_transport)

    with pytest.raises(RuntimeError, match="SFTP channel creation failed"):
        sftp_client.open_sftp(_cfg())

    assert transport.close_calls == 1


def test_open_sftp_closes_transport_when_from_transport_returns_none(monkeypatch):
    transport = FakeTransport()
    _install_fakes(monkeypatch, transport, FakeSftp(), lambda _transport: None)

    with pytest.raises(ConnectionError) as exc_info:
        sftp_client.open_sftp(_cfg())

    assert transport.close_calls == 1
    assert "secret" not in str(exc_info.value)


def test_open_sftp_closes_transport_when_channel_configuration_fails(monkeypatch):
    transport = FakeTransport()
    fake_sftp = FakeSftp(FakeChannel(error=OSError("channel timeout setup failed")))
    _install_fakes(monkeypatch, transport, fake_sftp)

    with pytest.raises(OSError, match="channel timeout setup failed"):
        sftp_client.open_sftp(_cfg())

    assert transport.close_calls == 1


def test_open_sftp_closes_transport_when_keepalive_configuration_fails(monkeypatch):
    transport = FakeTransport()
    transport.keepalive_error = OSError("keepalive setup failed")
    _install_fakes(monkeypatch, transport, FakeSftp())

    with pytest.raises(OSError, match="keepalive setup failed"):
        sftp_client.open_sftp(_cfg())

    assert transport.close_calls == 1


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
        sftp_client,
        "open_sftp",
        lambda cfg: SimpleNamespace(closed=False),
    )
    pool = sftp_client.ThreadLocalSftpPool(_cfg())
    results = {}

    def worker(name):
        results[name] = id(pool.get())

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

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
