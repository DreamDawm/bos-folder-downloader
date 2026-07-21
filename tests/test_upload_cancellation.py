from __future__ import annotations

import threading
from typing import List, Union

import pytest
from bos_downloader.upload_cancellation import UploadCancellation, UploadCancelledError

EventRecord = Union[str, int]


def test_request_is_idempotent_and_marks_upload_cancelled() -> None:
    cancellation = UploadCancellation(hard_exit=lambda _: None)

    assert cancellation.request() is True
    assert cancellation.is_cancelled is True
    assert cancellation.request() is False
    assert cancellation.is_cancelled is True


def test_mark_cleanup_complete_sets_completion_state() -> None:
    cancellation = UploadCancellation(hard_exit=lambda _: None)

    assert cancellation.is_cleanup_complete is False
    cancellation.mark_cleanup_complete()

    assert cancellation.is_cleanup_complete is True


def test_raise_if_cancelled_raises_only_after_request() -> None:
    cancellation = UploadCancellation(hard_exit=lambda _: None)

    cancellation.raise_if_cancelled()
    cancellation.request()

    with pytest.raises(UploadCancelledError, match="上传已取消"):
        cancellation.raise_if_cancelled()


def test_watchdog_does_not_exit_when_cleanup_finishes_early() -> None:
    exit_called = threading.Event()
    cancellation = UploadCancellation(
        timeout_seconds=0.05,
        hard_exit=lambda _: exit_called.set(),
    )
    cancellation.mark_cleanup_complete()

    cancellation.start_watchdog()

    assert not exit_called.wait(timeout=0.2)


def test_watchdog_flushes_streams_before_hard_exit_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: List[EventRecord] = []
    exit_called = threading.Event()

    class FakeStream:
        def __init__(self, name: str) -> None:
            self.name = name

        def flush(self) -> None:
            events.append(self.name)

    def fake_hard_exit(code: int) -> None:
        events.append(code)
        exit_called.set()

    from bos_downloader import upload_cancellation

    monkeypatch.setattr(upload_cancellation.sys, "stdout", FakeStream("stdout"))
    monkeypatch.setattr(upload_cancellation.sys, "stderr", FakeStream("stderr"))
    cancellation = UploadCancellation(timeout_seconds=0.01, hard_exit=fake_hard_exit)

    cancellation.start_watchdog()

    assert exit_called.wait(timeout=0.2)
    assert events == ["stdout", "stderr", 130]


def test_start_watchdog_rejects_duplicate_start() -> None:
    cancellation = UploadCancellation(
        timeout_seconds=0.05,
        hard_exit=lambda _: None,
    )
    cancellation.mark_cleanup_complete()

    cancellation.start_watchdog()

    with pytest.raises(RuntimeError, match="取消看门狗已启动"):
        cancellation.start_watchdog()
