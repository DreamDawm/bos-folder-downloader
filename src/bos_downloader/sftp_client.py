"""建立 SFTP 连接,并为多线程上传提供每线程独立连接。

paramiko 的单个 SFTPClient / Transport channel 不是线程安全的:多线程
并发 put / stat 会因共享请求序号与 packetizer 而数据串扰或抛异常。因此
ThreadLocalSftpPool 用 threading.local() 为每个工作线程惰性建立独立连接,
线程内复用,close_all() 统一回收,避免连接泄漏。
"""

from __future__ import annotations

import socket
import threading
from typing import List, Optional, cast

import paramiko

from bos_downloader.config import SftpConfig

SSH_HANDSHAKE_TIMEOUT = 10.0
SSH_AUTH_TIMEOUT = 10.0
SFTP_CHANNEL_TIMEOUT = 30.0
SSH_KEEPALIVE_INTERVAL = 30


def _best_effort_close(resource: object) -> None:
    """尽力关闭资源。"""
    close = getattr(resource, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        return


def open_sftp(cfg: SftpConfig) -> paramiko.SFTPClient:
    """用密码认证建立 Transport 并返回 SFTPClient。

    安全权衡:此处用密码认证且不校验主机密钥(无 known_hosts),存在中间人
    风险。生产环境应改用密钥认证或校验主机指纹。凭证绝不打印到日志或异常。

    transport 的引用挂到返回对象上,close() 时一并关闭底层连接。
    """
    sock = socket.create_connection(
        (cfg.host, cfg.port),
        timeout=SSH_HANDSHAKE_TIMEOUT,
    )
    transport = None
    try:
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=SSH_HANDSHAKE_TIMEOUT)
        transport.auth_timeout = SSH_AUTH_TIMEOUT
        transport.auth_password(cfg.username, cfg.password)
        transport.set_keepalive(SSH_KEEPALIVE_INTERVAL)
        transport.channel_timeout = SFTP_CHANNEL_TIMEOUT
        sftp = paramiko.SFTPClient.from_transport(transport)
        if sftp is None:
            raise ConnectionError(f"无法建立到 {cfg.host}:{cfg.port} 的 SFTP 连接")
        channel = sftp.get_channel()
        if channel is None:
            raise ConnectionError("SFTP channel unavailable")
        channel.settimeout(SFTP_CHANNEL_TIMEOUT)
    except Exception:
        if transport is None:
            _best_effort_close(sock)
        else:
            _best_effort_close(transport)
            _best_effort_close(sock)
        raise

    # 持有 transport 以便 close 时一并关闭(from_transport 不会主动关 transport)
    sftp._bos_transport = transport  # type: ignore[attr-defined]
    return sftp


class ThreadLocalSftpPool:
    """每线程一个独立 SFTPClient,解决 paramiko 单连接非线程安全问题。"""

    def __init__(self, cfg: SftpConfig) -> None:
        self._cfg = cfg
        self._local = threading.local()
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._all: List[paramiko.SFTPClient] = []
        self._opening = 0
        self._closed = False

    def get(self) -> paramiko.SFTPClient:
        """返回当前线程的 SFTPClient,首次访问时建连。"""
        with self._condition:
            if self._closed:
                raise RuntimeError("SFTP 连接池已关闭")
            client = cast(
                Optional[paramiko.SFTPClient],
                getattr(self._local, "client", None),
            )
            if client is not None:
                return client
            self._opening += 1

        try:
            client = open_sftp(self._cfg)
            with self._condition:
                if not self._closed:
                    self._local.client = client
                    self._all.append(client)
                    return client

            cleanup_error = None
            try:
                self._close_sftp(client)
            except Exception as exc:
                cleanup_error = exc
            with self._condition:
                if cleanup_error is not None:
                    self._all.append(client)
            if cleanup_error is not None:
                raise RuntimeError("SFTP 连接池已关闭") from cleanup_error
            raise RuntimeError("SFTP 连接池已关闭")
        finally:
            with self._condition:
                self._opening -= 1
                self._condition.notify_all()

    @staticmethod
    def _close_sftp(client: paramiko.SFTPClient) -> None:
        """先关闭底层 Transport,再关闭 SFTPClient。"""
        transport = getattr(client, "_bos_transport", None)
        first_error = None
        if transport is not None:
            try:
                transport.close()
            except Exception as exc:
                first_error = exc
        try:
            client.close()
        except Exception as exc:
            if first_error is None:
                first_error = exc
        if first_error is not None:
            raise first_error

    def close_all(self) -> None:
        """关闭所有已建立的连接(在 run() 的 finally 中调用)。"""
        with self._condition:
            if self._closed and not self._all and self._opening == 0:
                return
            self._closed = True
            while self._opening:
                self._condition.wait()
            clients = list(self._all)
            self._all.clear()

        first_error = None
        failed_clients: List[paramiko.SFTPClient] = []
        for client in clients:
            try:
                self._close_sftp(client)
            except Exception as exc:
                failed_clients.append(client)
                if first_error is None:
                    first_error = exc

        with self._condition:
            self._all.extend(failed_clients)
        if first_error is not None:
            raise RuntimeError("关闭 SFTP 连接失败") from first_error
