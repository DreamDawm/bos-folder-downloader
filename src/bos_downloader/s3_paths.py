"""将本地路径归一化为 S3 对象 Key，并枚举待上传文件。"""

from __future__ import annotations

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


def _validated_relative_parts(value: str) -> list[str]:
    """校验相对 POSIX 路径并返回组成对象 Key 的路径段。"""
    has_drive = len(value) >= 2 and value[0].isalpha() and value[1] == ":"
    if not value or value.startswith(("/", "\\")) or "\\" in value or has_drive:
        raise ValueError(f"无法从相对路径 {value!r} 生成安全的对象 Key")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"无法从相对路径 {value!r} 生成安全的对象 Key")
    return parts


def build_object_key(relative_path: str, source_folder_name: str = "") -> str:
    """生成“来源文件夹名/相对路径”格式的安全 S3 对象 Key。"""
    relative_parts = _validated_relative_parts(relative_path)
    if not source_folder_name:
        return "/".join(relative_parts)

    source_parts = _validated_relative_parts(source_folder_name)
    if len(source_parts) != 1:
        raise ValueError(f"来源文件夹名 {source_folder_name!r} 不是单一路径段")
    return "/".join([*source_parts, *relative_parts])


def discover_upload_items(source: Union[str, Path]) -> Iterator[S3UploadItem]:
    """验证 source，并递归生成其下所有待上传文件。"""
    source_path = Path(source).expanduser().resolve()
    if source_path.is_file():
        yield S3UploadItem(
            abs_path=source_path,
            object_key=build_object_key(source_path.name, source_path.parent.name),
            size=source_path.stat().st_size,
        )
        return
    if not source_path.is_dir():
        raise ValueError(f"源路径不存在或不是普通文件/文件夹: {source}")
    for local_file in walk_local_files(source_path):
        yield S3UploadItem(
            abs_path=local_file.abs_path,
            object_key=build_object_key(local_file.rel_path, source_path.name),
            size=local_file.size,
        )
