"""将本地路径归一化为 S3 对象 Key，并枚举待上传文件。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Union

from bos_downloader.local_walker import walk_local_files


@dataclass(frozen=True)
class S3UploadItem:
    """不可变的单个 S3 上传文件描述。"""

    abs_path: Path
    object_key: str
    size: int


def object_key_for_path(path: Path) -> str:
    """将 Windows 或 POSIX 绝对路径转换为安全的 POSIX 对象 Key。"""
    raw = str(path).replace("\\", "/")
    raw = re.sub(r"^[A-Za-z]:", "", raw).lstrip("/")
    parts = [part for part in raw.split("/") if part and part != "."]
    if not parts or ".." in parts:
        raise ValueError(f"无法从路径 {path!s} 生成安全的对象 Key")
    return "/".join(parts)


def discover_upload_items(source: Union[str, Path]) -> Iterator[S3UploadItem]:
    """验证 source，并递归生成其下所有待上传文件。"""
    source_path = Path(source).expanduser().resolve()
    if source_path.is_file():
        yield S3UploadItem(
            abs_path=source_path,
            object_key=object_key_for_path(source_path),
            size=source_path.stat().st_size,
        )
        return
    if not source_path.is_dir():
        raise ValueError(f"源路径不存在或不是普通文件/文件夹: {source}")
    for local_file in walk_local_files(source_path):
        yield S3UploadItem(
            abs_path=local_file.abs_path,
            object_key=object_key_for_path(local_file.abs_path),
            size=local_file.size,
        )
