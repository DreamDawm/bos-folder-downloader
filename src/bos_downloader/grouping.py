"""把 BOS 对象按 key 的直接父目录分组,作为「最小子文件夹」流水线单位。"""

from __future__ import annotations

import posixpath
from typing import Dict, List, Tuple

from bos_downloader.lister import RemoteObject


def group_objects_by_dir(
    objects: List[RemoteObject],
) -> List[Tuple[str, List[RemoteObject]]]:
    """按 posixpath.dirname(key) 分组,保持首次出现顺序。

    返回 [(目录, [该目录直接下的对象, ...]), ...]。同一目录的对象
    聚成一组,组间按目录首次出现的先后排列(便于日志可读、可复现)。
    """
    groups: List[Tuple[str, List[RemoteObject]]] = []
    index: Dict[str, List[RemoteObject]] = {}
    for obj in objects:
        directory = posixpath.dirname(obj.key)
        if directory not in index:
            bucket: List[RemoteObject] = []
            index[directory] = bucket
            groups.append((directory, bucket))
        index[directory].append(obj)
    return groups
