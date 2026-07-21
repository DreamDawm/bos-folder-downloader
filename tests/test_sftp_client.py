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


def test_open_sftp_closes_transport_when_channel_is_missing(monkeypatch):
    transport = FakeTransport()
    fake_sftp = SimpleNamespace(get_channel=lambda: None)
    _install_fakes(monkeypatch, transport, fake_sftp)

    with pytest.raises(ConnectionError, match="SFTP channel unavailable"):
        sftp_client.open_sftp(_cfg())

    assert transport.close_calls == 1


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


def test_close_all_is_idempotent_and_marks_pool_closed(monkeypatch):
    close_calls = []

    def fake_open(cfg):
        client = SimpleNamespace()
        client.close = lambda: close_calls.append(client)
        return client

    monkeypatch.setattr(sftp_client, "open_sftp", fake_open)
    pool = sftp_client.ThreadLocalSftpPool(_cfg())
    pool.get()

    pool.close_all()
    pool.close_all()

    assert pool._closed is True
    assert pool._all == []
    assert len(close_calls) == 1


def test_get_after_close_does_not_open_a_connection(monkeypatch):
    open_calls = []

    def fake_open(cfg):
        open_calls.append(cfg)
        return SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(sftp_client, "open_sftp", fake_open)
    pool = sftp_client.ThreadLocalSftpPool(_cfg())
    pool.close_all()

    with pytest.raises(RuntimeError, match="^SFTP 连接池已关闭$"):
        pool.get()

    assert open_calls == []


def test_get_rejects_existing_thread_local_client_after_close(monkeypatch):
    open_calls = []

    def fake_open(cfg):
        client = SimpleNamespace(close=lambda: None)
        open_calls.append(client)
        return client

    monkeypatch.setattr(sftp_client, "open_sftp", fake_open)
    pool = sftp_client.ThreadLocalSftpPool(_cfg())
    client = pool.get()
    pool.close_all()

    with pytest.raises(RuntimeError, match="^SFTP 连接池已关闭$"):
        pool.get()

    assert open_calls == [client]


def test_close_all_closes_new_client_when_connection_races_with_close(
    monkeypatch,
):
    open_started = threading.Event()
    release_open = threading.Event()
    created = []
    thread_errors = []

    def fake_open(cfg):
        client = SimpleNamespace(close_calls=0)

        def close():
            client.close_calls += 1

        client.close = close
        created.append(client)
        open_started.set()
        assert release_open.wait(timeout=2)
        return client

    monkeypatch.setattr(sftp_client, "open_sftp", fake_open)
    pool = sftp_client.ThreadLocalSftpPool(_cfg())

    def worker():
        try:
            pool.get()
        except Exception as exc:
            thread_errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    assert open_started.wait(timeout=2)

    close_errors = []
    close_returned = threading.Event()

    def close_worker():
        try:
            pool.close_all()
        except Exception as exc:
            close_errors.append(exc)
        finally:
            close_returned.set()

    close_thread = threading.Thread(target=close_worker)
    close_thread.start()
    assert not close_returned.wait(timeout=0.1)
    release_open.set()
    thread.join(timeout=2)
    close_thread.join(timeout=2)

    assert not thread.is_alive()
    assert not close_thread.is_alive()
    assert close_errors == []
    assert len(thread_errors) == 1
    assert str(thread_errors[0]) == "SFTP 连接池已关闭"
    assert created[0].close_calls == 1
    assert pool._all == []


def test_close_all_attempts_other_clients_after_one_close_fails(monkeypatch):
    first_ready = threading.Event()
    second_can_connect = threading.Event()
    created = []
    thread_errors = []
    first_close_error = RuntimeError("first close failed")

    def fake_open(cfg):
        client = SimpleNamespace(close_calls=0)
        if not created:
            client.close_error = first_close_error
        else:
            client.close_error = None
        created.append(client)
        if len(created) == 1:
            first_ready.set()

        def close():
            client.close_calls += 1
            if client.close_error is not None:
                raise client.close_error

        client.close = close
        return client

    monkeypatch.setattr(sftp_client, "open_sftp", fake_open)
    pool = sftp_client.ThreadLocalSftpPool(_cfg())

    def first_worker():
        try:
            pool.get()
        except Exception as exc:
            thread_errors.append(exc)

    def second_worker():
        try:
            assert first_ready.wait(timeout=2)
            second_can_connect.set()
            pool.get()
        except Exception as exc:
            thread_errors.append(exc)

    first_thread = threading.Thread(target=first_worker)
    second_thread = threading.Thread(target=second_worker)
    first_thread.start()
    second_thread.start()
    assert second_can_connect.wait(timeout=2)
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert thread_errors == []
    assert len(created) == 2

    with pytest.raises(RuntimeError) as exc_info:
        pool.close_all()

    assert str(exc_info.value) == "关闭 SFTP 连接失败"
    assert exc_info.value.__cause__ is first_close_error
    assert [client.close_calls for client in created] == [1, 1]


def test_close_all_closes_transport_before_client(monkeypatch):
    events = []
    transport = SimpleNamespace(close=lambda: events.append("transport"))
    client = SimpleNamespace(
        _bos_transport=transport,
        close=lambda: events.append("client"),
    )

    monkeypatch.setattr(sftp_client, "open_sftp", lambda cfg: client)
    pool = sftp_client.ThreadLocalSftpPool(_cfg())
    pool.get()

    pool.close_all()

    assert events == ["transport", "client"]


def test_close_all_closes_client_when_transport_close_fails(monkeypatch):
    events = []
    transport_error = RuntimeError("transport close failed")

    def close_transport():
        events.append("transport")
        raise transport_error

    transport = SimpleNamespace(close=close_transport)
    client = SimpleNamespace(
        _bos_transport=transport,
        close=lambda: events.append("client"),
    )

    monkeypatch.setattr(sftp_client, "open_sftp", lambda cfg: client)
    pool = sftp_client.ThreadLocalSftpPool(_cfg())
    pool.get()

    with pytest.raises(RuntimeError) as exc_info:
        pool.close_all()

    assert str(exc_info.value) == "关闭 SFTP 连接失败"
    assert exc_info.value.__cause__ is transport_error
    assert events == ["transport", "client"]


def test_close_all_retries_failed_client_without_reclosing_successful_clients(
    monkeypatch,
):
    creation_lock = threading.Lock()
    clients = []

    def fake_open(cfg):
        with creation_lock:
            client = SimpleNamespace(close_calls=0)
            clients.append(client)

        def close():
            client.close_calls += 1
            if client is clients[0] and client.close_calls == 1:
                raise RuntimeError("retry close failed")

        client.close = close
        return client

    monkeypatch.setattr(sftp_client, "open_sftp", fake_open)
    pool = sftp_client.ThreadLocalSftpPool(_cfg())

    threads = [threading.Thread(target=pool.get) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert len(clients) == 2

    with pytest.raises(RuntimeError, match="^关闭 SFTP 连接失败$"):
        pool.close_all()

    assert clients[0].close_calls == 1
    assert clients[1].close_calls == 1
    assert pool._all == [clients[0]]

    pool.close_all()
    assert clients[0].close_calls == 2
    assert clients[1].close_calls == 1
    assert pool._all == []

    pool.close_all()
    assert clients[0].close_calls == 2
    assert clients[1].close_calls == 1


def test_racing_client_cleanup_failure_is_retriable(monkeypatch):
    open_started = threading.Event()
    release_open = threading.Event()
    thread_errors = []
    cleanup_error = RuntimeError("new client cleanup failed")
    client = SimpleNamespace(close_calls=0, should_fail=True)

    def close():
        client.close_calls += 1
        if client.should_fail:
            raise cleanup_error

    client.close = close

    def fake_open(cfg):
        open_started.set()
        assert release_open.wait(timeout=2)
        return client

    monkeypatch.setattr(sftp_client, "open_sftp", fake_open)
    pool = sftp_client.ThreadLocalSftpPool(_cfg())

    def worker():
        try:
            pool.get()
        except Exception as exc:
            thread_errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    assert open_started.wait(timeout=2)

    close_errors = []
    close_returned = threading.Event()

    def close_worker():
        try:
            pool.close_all()
        except Exception as exc:
            close_errors.append(exc)
        finally:
            close_returned.set()

    close_thread = threading.Thread(target=close_worker)
    close_thread.start()
    assert not close_returned.wait(timeout=0.1)
    release_open.set()
    thread.join(timeout=2)
    close_thread.join(timeout=2)

    assert not thread.is_alive()
    assert not close_thread.is_alive()
    assert len(thread_errors) == 1
    assert str(thread_errors[0]) == "SFTP 连接池已关闭"
    assert thread_errors[0].__cause__ is cleanup_error
    assert len(close_errors) == 1
    assert str(close_errors[0]) == "关闭 SFTP 连接失败"
    assert close_errors[0].__cause__ is cleanup_error
    assert pool._all == [client]

    client.should_fail = False
    pool.close_all()
    assert client.close_calls == 3
    assert pool._all == []


def test_open_sftp_preserves_original_error_when_cleanup_also_fails(monkeypatch):
    transport = FakeTransport()
    original_error = TimeoutError("handshake failed")
    cleanup_error = OSError("transport cleanup failed")
    transport.start_error = original_error

    def fail_close():
        raise cleanup_error

    transport.close = fail_close
    _, fake_socket = _install_fakes(monkeypatch, transport, FakeSftp())

    with pytest.raises(TimeoutError) as exc_info:
        sftp_client.open_sftp(_cfg())

    assert exc_info.value is original_error
    assert fake_socket.close_calls == 1


def test_close_all_waits_for_opening_connection_before_return(monkeypatch):
    open_started = threading.Event()
    release_open = threading.Event()
    close_returned = threading.Event()
    created = []
    get_errors = []
    close_errors = []

    def fake_open(cfg):
        client = SimpleNamespace(close_calls=0)

        def close():
            client.close_calls += 1

        client.close = close
        created.append(client)
        open_started.set()
        assert release_open.wait(timeout=2)
        return client

    monkeypatch.setattr(sftp_client, "open_sftp", fake_open)
    pool = sftp_client.ThreadLocalSftpPool(_cfg())

    def get_worker():
        try:
            pool.get()
        except Exception as exc:
            get_errors.append(exc)

    def close_worker():
        try:
            pool.close_all()
        except Exception as exc:
            close_errors.append(exc)
        finally:
            close_returned.set()

    get_thread = threading.Thread(target=get_worker)
    close_thread = threading.Thread(target=close_worker)
    get_thread.start()
    assert open_started.wait(timeout=2)
    close_thread.start()

    assert not close_returned.wait(timeout=0.1)
    release_open.set()
    get_thread.join(timeout=2)
    close_thread.join(timeout=2)

    assert not get_thread.is_alive()
    assert not close_thread.is_alive()
    assert len(get_errors) == 1
    assert str(get_errors[0]) == "SFTP 连接池已关闭"
    assert close_errors == []
    assert created[0].close_calls == 1


def test_close_all_waits_for_opening_cleanup_failure_and_keeps_client(
    monkeypatch,
):
    open_started = threading.Event()
    release_open = threading.Event()
    close_returned = threading.Event()
    get_errors = []
    close_errors = []
    cleanup_error = RuntimeError("new client cleanup failed")
    client = SimpleNamespace(close_calls=0, should_fail=True)

    def close():
        client.close_calls += 1
        if client.should_fail:
            raise cleanup_error

    client.close = close

    def fake_open(cfg):
        open_started.set()
        assert release_open.wait(timeout=2)
        return client

    monkeypatch.setattr(sftp_client, "open_sftp", fake_open)
    pool = sftp_client.ThreadLocalSftpPool(_cfg())

    def get_worker():
        try:
            pool.get()
        except Exception as exc:
            get_errors.append(exc)

    def close_worker():
        try:
            pool.close_all()
        except Exception as exc:
            close_errors.append(exc)
        finally:
            close_returned.set()

    get_thread = threading.Thread(target=get_worker)
    close_thread = threading.Thread(target=close_worker)
    get_thread.start()
    assert open_started.wait(timeout=2)
    close_thread.start()

    assert not close_returned.wait(timeout=0.1)
    release_open.set()
    get_thread.join(timeout=2)
    close_thread.join(timeout=2)

    assert not get_thread.is_alive()
    assert not close_thread.is_alive()
    assert len(get_errors) == 1
    assert get_errors[0].__cause__ is cleanup_error
    assert len(close_errors) == 1
    assert str(close_errors[0]) == "关闭 SFTP 连接失败"
    assert close_errors[0].__cause__ is cleanup_error
    assert client.close_calls == 2
    assert pool._all == [client]

    client.should_fail = False
    pool.close_all()
    assert client.close_calls == 3
    assert pool._all == []


def test_get_after_close_raises_specific_pool_error(monkeypatch):
    monkeypatch.setattr(
        sftp_client,
        "open_sftp",
        lambda cfg: SimpleNamespace(close=lambda: None),
    )
    pool = sftp_client.ThreadLocalSftpPool(_cfg())
    pool.close_all()

    with pytest.raises(sftp_client.SftpPoolClosedError):
        pool.get()
