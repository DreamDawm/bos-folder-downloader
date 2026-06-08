"""单文件 SFTP 上传:已存在且同大小则跳过,否则覆盖上传。"""

from __future__ import annotations

import posixpath
from pathlib import Path
from typing import Callable, Optional, Protocol

ProgressCallback = Callable[[int, int], None]


class _UploadClient(Protocol):
    def stat(self, path): ...
    def put(self, localpath, remotepath, callback=None, confirm=True): ...
    def mkdir(self, path, mode=511): ...


def remote_file_size(client: _UploadClient, remote_path: str) -> Optional[int]:
    """远端文件存在则返回字节大小,不存在返回 None。

    SFTP stat 对不存在的路径抛 IOError(FileNotFoundError 是其子类),
    捕获后判定为不存在。
    """
    try:
        return int(client.stat(remote_path).st_size)
    except IOError:
        return None


def ensure_remote_dir(client: _UploadClient, remote_dir: str) -> None:
    """mkdir -p 语义:逐级创建 remote_dir 的每一层目录。

    SFTP 无原生 mkdir -p。按 '/' 累积前缀对每段尝试 mkdir,已存在段
    会抛 IOError,吞掉即可(幂等)。空段(如开头的绝对路径分隔)跳过。
    """
    if not remote_dir or remote_dir == "/":
        return
    parts = remote_dir.strip("/").split("/")
    prefix = "/" if remote_dir.startswith("/") else ""
    for part in parts:
        if not part:
            continue
        prefix = posixpath.join(prefix, part) if prefix else part
        try:
            client.mkdir(prefix)
        except IOError:
            # 目录已存在(或并发创建竞态),幂等吞掉
            pass


def upload_file(
    client: _UploadClient,
    local_path: Path,
    remote_path: str,
    progress_callback: Optional[ProgressCallback] = None,
) -> str:
    """上传单个文件到 remote_path,返回 'skipped' 或 'done'。

    远端已存在且大小与本地相同则跳过(只比大小不比内容)。否则先确保
    远端父目录存在,再 put 覆盖上传。
    """
    local_path = Path(local_path)
    local_size = local_path.stat().st_size

    if remote_file_size(client, remote_path) == local_size:
        if progress_callback:
            progress_callback(local_size, local_size)
        return "skipped"

    parent = posixpath.dirname(remote_path)
    ensure_remote_dir(client, parent)
    client.put(
        str(local_path),
        remote_path,
        callback=progress_callback,
        confirm=True,
    )
    return "done"
