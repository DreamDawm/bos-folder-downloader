"""枚举 BOS 桶中某 prefix 下的全部对象(递归含子文件夹)。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol


@dataclass(frozen=True)
class RemoteObject:
    key: str
    size: int


class _ListClient(Protocol):
    def list_all_objects(self, bucket_name, prefix=None): ...


def list_objects_under_prefix(
    client: _ListClient, bucket: str, prefix: str
) -> Iterator[RemoteObject]:
    """生成 prefix 下所有真实文件对象,跳过以 '/' 结尾的伪目录占位对象。"""
    for item in client.list_all_objects(bucket, prefix=prefix):
        if item.key.endswith("/"):
            continue
        yield RemoteObject(key=item.key, size=item.size)
