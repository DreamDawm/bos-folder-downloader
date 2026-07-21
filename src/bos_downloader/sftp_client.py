"""建立 SFTP 连接,并为多线程上传提供每线程独立连接。

paramiko 的单个 SFTPClient / Transport channel 不是线程安全的:多线程
并发 put / stat 会因共享请求序号与 packetizer 而数据串扰或抛异常。因此
ThreadLocalSftpPool 用 threading.local() 为每个工作线程惰性建立独立连接,
线程内复用,close_all() 统一回收,避免连接泄漏。
"""

from __future__ import annotations

import threading
from typing import List, Protocol

import paramiko

from bos_downloader.config import SftpConfig

SSH_HANDSHAKE_TIMEOUT = 10.0
SSH_AUTH_TIMEOUT = 10.0
SFTP_CHANNEL_TIMEOUT = 30.0
SSH_KEEPALIVE_INTERVAL = 30


class SftpLike(Protocol):
    """上传所需的最小 SFTP 接口,便于测试注入 Fake。"""

    def stat(self, path): ...
    def put(self, localpath, remotepath, callback=None, confirm=True): ...
    def mkdir(self, path, mode=511): ...
    def close(self): ...


def open_sftp(cfg: SftpConfig) -> paramiko.SFTPClient:
    """用密码认证建立 Transport 并返回 SFTPClient。

    安全权衡:此处用密码认证且不校验主机密钥(无 known_hosts),存在中间人
    风险。生产环境应改用密钥认证或校验主机指纹。凭证绝不打印到日志或异常。

    transport 的引用挂到返回对象上,close() 时一并关闭底层连接。
    """
    transport = paramiko.Transport((cfg.host, cfg.port))
    try:
        transport.start_client(timeout=SSH_HANDSHAKE_TIMEOUT)
        transport.auth_timeout = SSH_AUTH_TIMEOUT
        transport.auth_password(cfg.username, cfg.password)
        transport.set_keepalive(SSH_KEEPALIVE_INTERVAL)
        sftp = paramiko.SFTPClient.from_transport(transport)
        if sftp is None:
            raise ConnectionError(f"无法建立到 {cfg.host}:{cfg.port} 的 SFTP 连接")
        sftp.get_channel().settimeout(SFTP_CHANNEL_TIMEOUT)
    except Exception:
        transport.close()
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
        self._all: List[paramiko.SFTPClient] = []

    def get(self) -> paramiko.SFTPClient:
        """返回当前线程的 SFTPClient,首次访问时建连。"""
        client = getattr(self._local, "client", None)
        if client is None:
            client = open_sftp(self._cfg)
            self._local.client = client
            with self._lock:
                self._all.append(client)
        return client

    def close_all(self) -> None:
        """关闭所有已建立的连接(在 run() 的 finally 中调用)。"""
        with self._lock:
            clients = list(self._all)
            self._all.clear()
        for client in clients:
            transport = getattr(client, "_bos_transport", None)
            try:
                client.close()
            finally:
                if transport is not None:
                    transport.close()
