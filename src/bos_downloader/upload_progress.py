"""多线程上传的聚合字节进度。"""

from __future__ import annotations

import threading
from typing import Callable, Optional, Protocol


class _ProgressBar(Protocol):
    def update(self, amount: int) -> None: ...


class UploadProgress:
    """把每个文件的累计回调聚合为一次上传任务的字节进度。"""

    def __init__(self, total: int) -> None:
        self.total = max(0, total)
        self._lock = threading.Lock()
        self._processed_bytes = 0
        self._pending_delta = 0
        self._ui_error: Optional[Exception] = None

    @property
    def processed_bytes(self) -> int:
        with self._lock:
            return self._processed_bytes

    @property
    def ui_error(self) -> Optional[Exception]:
        with self._lock:
            return self._ui_error

    def record_ui_error(self, exc: Exception) -> None:
        """记录首个 UI 故障，后续上传继续执行。"""
        with self._lock:
            if self._ui_error is None:
                self._ui_error = exc

    def callback_for(
        self, _file_id: str, expected_size: int
    ) -> Callable[[int, int], None]:
        """返回单文件回调，将 Paramiko 累计值转换为非重复增量。"""
        upper_bound = max(0, expected_size)
        previous = 0
        callback_lock = threading.Lock()

        def callback(transferred: int, _reported_total: int) -> None:
            nonlocal previous
            with callback_lock:
                current = max(previous, min(max(0, int(transferred)), upper_bound))
                delta = current - previous
                previous = current
            if delta:
                self._record(delta)

        return callback

    def _record(self, delta: int) -> None:
        with self._lock:
            remaining = max(0, self.total - self._processed_bytes)
            accepted = min(delta, remaining)
            self._processed_bytes += accepted
            self._pending_delta += accepted

    def flush(self, bar: _ProgressBar) -> None:
        """把待显示增量提交给进度条；UI 故障不影响上传任务。"""
        with self._lock:
            if self._ui_error is not None or self._pending_delta == 0:
                return
            delta = self._pending_delta
            self._pending_delta = 0
        try:
            bar.update(delta)
        except Exception as exc:  # noqa: BLE001 - UI 故障必须与上传隔离
            self.record_ui_error(exc)
