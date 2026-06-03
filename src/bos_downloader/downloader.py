"""单文件断点续传下载,带进度回调。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional, Protocol

ProgressCallback = Callable[[int, int], None]

_CHUNK_SIZE = 256 * 1024


class _DownloadClient(Protocol):
    def get_object_meta_data(self, bucket, key): ...
    def get_object(self, bucket, key, range=None): ...


def _remote_size(client: _DownloadClient, bucket: str, key: str) -> int:
    meta = client.get_object_meta_data(bucket, key)
    return int(meta.metadata.content_length)


def download_object(
    client: _DownloadClient,
    bucket: str,
    key: str,
    dest: Path,
    progress_callback: Optional[ProgressCallback] = None,
) -> None:
    """下载单个对象到 dest,支持断点续传。

    若 dest 已存在则跳过。否则用 dest+'.part' 临时文件累积,
    完成后原子重命名为 dest。
    """
    dest = Path(dest)
    if dest.exists():
        return

    total = _remote_size(client, bucket, key)
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)

    local_size = part.stat().st_size if part.exists() else 0
    if local_size > total:
        part.unlink()
        local_size = 0

    if progress_callback:
        progress_callback(local_size, total)

    if local_size < total:
        response = client.get_object(
            bucket, key, range=(local_size, total - 1)
        )
        stream = response.data
        downloaded = local_size
        try:
            with open(part, "ab") as f:
                while True:
                    chunk = stream.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

    if not part.exists():
        part.touch()
    os.replace(part, dest)
