"""上传取消控制器及超时看门狗。"""

from __future__ import annotations

import os
import sys
import threading
from typing import Callable, Optional


class UploadCancelledError(RuntimeError):
    """表示上传已被请求取消。"""


class UploadCancellation:
    """协调上传取消请求、清理完成通知和强制退出看门狗。"""

    _WATCHDOG_THREAD_NAME = "bos-upload-cancel-watchdog"
    _HARD_EXIT_CODE = 130

    def __init__(
        self,
        timeout_seconds: float = 5.0,
        hard_exit: Optional[Callable[[int], None]] = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._hard_exit = hard_exit if hard_exit is not None else os._exit
        self._cancelled = threading.Event()
        self._cleanup_complete = threading.Event()
        self._watchdog_started = threading.Event()
        self._state_lock = threading.Lock()

    @property
    def is_cancelled(self) -> bool:
        """返回是否已经请求取消。"""
        return self._cancelled.is_set()

    @property
    def is_cleanup_complete(self) -> bool:
        """返回上传清理是否已经完成。"""
        return self._cleanup_complete.is_set()

    def request(self) -> bool:
        """请求取消；首次请求返回 True，后续请求返回 False。"""
        with self._state_lock:
            if self._cancelled.is_set():
                return False
            self._cancelled.set()
            return True

    def raise_if_cancelled(self) -> None:
        """在已请求取消时抛出上传取消异常。"""
        if self._cancelled.is_set():
            raise UploadCancelledError("上传已取消")

    def mark_cleanup_complete(self) -> None:
        """标记上传资源清理完成。"""
        self._cleanup_complete.set()

    def start_watchdog(self) -> threading.Thread:
        """启动一次等待清理完成的超时看门狗。"""
        with self._state_lock:
            if self._watchdog_started.is_set():
                raise RuntimeError("取消看门狗已启动")
            self._watchdog_started.set()
            watchdog = threading.Thread(
                target=self._watchdog,
                name=self._WATCHDOG_THREAD_NAME,
                daemon=True,
            )
            watchdog.start()
            return watchdog

    def _watchdog(self) -> None:
        if self._cleanup_complete.wait(timeout=self._timeout_seconds):
            return
        try:
            for stream in (sys.stdout, sys.stderr):
                try:
                    stream.flush()
                except Exception:  # noqa: BLE001 - 退出前尽力刷新输出
                    continue
        finally:
            self._hard_exit(self._HARD_EXIT_CODE)
