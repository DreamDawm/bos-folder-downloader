"""递归枚举本地文件夹下的全部文件(含子文件夹)。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class LocalFile:
    abs_path: Path
    rel_path: str  # 相对 root 的 POSIX 风格相对路径(以 / 分隔)
    size: int


def walk_local_files(root: Path) -> Iterator[LocalFile]:
    """用 os.walk 递归枚举 root 下所有文件,跳过目录本身。

    rel_path 统一用 as_posix() 转为正斜杠路径,使 Windows 下也能生成
    正确的 POSIX 远端路径。空目录不产出(远端目录由 ensure_remote_dir
    按需创建)。root 不存在或不是目录时不产出任何项。
    """
    root = Path(root)
    if not root.is_dir():
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            abs_path = Path(dirpath) / name
            rel_path = abs_path.relative_to(root).as_posix()
            yield LocalFile(
                abs_path=abs_path,
                rel_path=rel_path,
                size=abs_path.stat().st_size,
            )
