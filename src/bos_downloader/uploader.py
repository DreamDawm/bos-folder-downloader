"""单文件 SFTP 上传：已存在且同大小则跳过，否则覆盖上传。"""

from __future__ import annotations

import errno
import posixpath
import stat
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Callable, Dict, Iterator, Optional, Protocol, Set

ProgressCallback = Callable[[int, int], None]


class SourceFileChangedError(RuntimeError):
    """源文件在枚举后发生大小变化。"""


class _UploadClient(Protocol):
    def stat(self, path): ...
    def lstat(self, path): ...
    def put(self, localpath, remotepath, callback=None, confirm=True): ...
    def mkdir(self, path, mode=511): ...


def _is_remote_symlink(client: _UploadClient, remote_path: str) -> bool:
    try:
        attrs = client.lstat(remote_path)
    except FileNotFoundError:
        return False
    mode = getattr(attrs, "st_mode", None)
    return mode is not None and stat.S_ISLNK(mode)


def remote_file_size(client: _UploadClient, remote_path: str) -> Optional[int]:
    """远端普通文件存在则返回字节大小，不存在返回 None。"""
    try:
        attrs = client.lstat(remote_path)
    except FileNotFoundError:
        return None
    mode = getattr(attrs, "st_mode", None)
    if mode is not None and stat.S_ISLNK(mode):
        raise OSError(f"拒绝覆盖远端符号链接：{remote_path}")
    return int(attrs.st_size)


def _directory_prefixes(remote_dir: str) -> Iterator[str]:
    parts = remote_dir.strip("/").split("/")
    prefix = "/" if remote_dir.startswith("/") else ""
    for part in parts:
        if not part:
            continue
        prefix = posixpath.join(prefix, part) if prefix else part
        yield prefix


def _is_remote_directory(client: _UploadClient, remote_dir: str) -> bool:
    try:
        attrs = client.lstat(remote_dir)
    except OSError:
        return False
    mode = getattr(attrs, "st_mode", None)
    return mode is None or stat.S_ISDIR(mode)


def _ensure_directory_level(client: _UploadClient, remote_dir: str) -> None:
    try:
        client.mkdir(remote_dir)
        return
    except OSError as exc:
        is_exists_error = isinstance(exc, FileExistsError) or exc.errno == errno.EEXIST
        if not is_exists_error and exc.errno is not None:
            raise
        if not _is_remote_directory(client, remote_dir):
            raise


def ensure_remote_dir(client: _UploadClient, remote_dir: str) -> None:
    """按父目录到子目录的顺序创建远端目录。"""
    if not remote_dir or remote_dir == "/":
        return
    for prefix in _directory_prefixes(remote_dir):
        _ensure_directory_level(client, prefix)


class RemoteDirectoryCache:
    """一次上传任务内共享的线程安全远端目录缓存。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready: Set[str] = set()
        self._inflight: Dict[str, Future[None]] = {}

    def ensure(self, client: _UploadClient, remote_dir: str) -> None:
        """仅创建尚未确认的目录，并合并同一路径的并发请求。"""
        if not remote_dir or remote_dir == "/":
            return
        for prefix in _directory_prefixes(remote_dir):
            self._ensure_level(client, prefix)

    def _ensure_level(self, client: _UploadClient, remote_dir: str) -> None:
        with self._lock:
            if remote_dir in self._ready:
                return
            future = self._inflight.get(remote_dir)
            if future is not None:
                is_owner = False
            else:
                is_owner = True
                future = Future()
                self._inflight[remote_dir] = future

        if not is_owner:
            future.result()
            return

        try:
            _ensure_directory_level(client, remote_dir)
        except BaseException as exc:
            with self._lock:
                future.set_exception(exc)
                self._inflight.pop(remote_dir, None)
            raise
        else:
            with self._lock:
                self._ready.add(remote_dir)
                future.set_result(None)
                self._inflight.pop(remote_dir, None)


def upload_file(
    client: _UploadClient,
    local_path: Path,
    remote_path: str,
    progress_callback: Optional[ProgressCallback] = None,
    directory_cache: Optional[RemoteDirectoryCache] = None,
    expected_size: Optional[int] = None,
) -> str:
    """上传单个文件到 remote_path，返回 ``skipped`` 或 ``done``。"""
    local_path = Path(local_path)
    local_size = local_path.stat().st_size
    if expected_size is not None and local_size != expected_size:
        raise SourceFileChangedError(
            f"源文件大小在枚举后发生变化：{expected_size} -> {local_size}"
        )

    if remote_file_size(client, remote_path) == local_size:
        if progress_callback:
            progress_callback(local_size, local_size)
        return "skipped"

    parent = posixpath.dirname(remote_path)
    if directory_cache is None:
        ensure_remote_dir(client, parent)
    else:
        directory_cache.ensure(client, parent)
    if _is_remote_symlink(client, remote_path):
        raise OSError(f"拒绝覆盖远端符号链接：{remote_path}")
    client.put(
        str(local_path),
        remote_path,
        callback=progress_callback,
        confirm=True,
    )
    return "done"
