"""配置写入 logs/ 的文件日志,同时回显到控制台,便于数量校对。"""

from __future__ import annotations

import logging
from pathlib import Path

_LOGGER_NAME = "bos_sync"


def setup_logger(logs_dir: str, stamp: str) -> logging.Logger:
    """在 logs_dir 下建 bos-sync-<stamp>.log,返回配置好的 logger。

    同一进程内重复调用会先清空旧 handler,避免重复写入。stamp 由调用方
    传入(而非内部取当前时间),便于测试与外部对齐文件名。
    """
    logs_path = Path(logs_dir)
    logs_path.mkdir(parents=True, exist_ok=True)
    log_file = logs_path / f"bos-sync-{stamp}.log"

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    return logger
