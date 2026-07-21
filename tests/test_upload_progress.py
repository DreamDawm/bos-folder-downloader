from __future__ import annotations

from bos_downloader.upload_progress import UploadProgress


class FakeBar:
    def __init__(self):
        self.updates = []

    def update(self, amount):
        self.updates.append(amount)


class FailingBar:
    def update(self, amount):
        raise RuntimeError("终端不可用")


def test_callback_converts_cumulative_values_to_deltas():
    progress = UploadProgress(total=100)
    callback = progress.callback_for("a.pdf", expected_size=100)

    callback(20, 100)
    callback(70, 100)
    callback(100, 100)

    bar = FakeBar()
    progress.flush(bar)
    assert bar.updates == [100]
    assert progress.processed_bytes == 100


def test_callback_ignores_duplicate_and_regressing_values():
    progress = UploadProgress(total=100)
    callback = progress.callback_for("a.pdf", expected_size=100)

    callback(60, 100)
    callback(60, 100)
    callback(40, 100)
    callback(100, 100)

    bar = FakeBar()
    progress.flush(bar)
    assert bar.updates == [100]
    assert progress.processed_bytes == 100


def test_callback_never_exceeds_expected_file_size():
    progress = UploadProgress(total=50)
    callback = progress.callback_for("a.pdf", expected_size=50)

    callback(80, 80)

    bar = FakeBar()
    progress.flush(bar)
    assert bar.updates == [50]
    assert progress.processed_bytes == 50


def test_partial_failed_transfer_keeps_only_reported_bytes():
    progress = UploadProgress(total=100)
    callback = progress.callback_for("a.pdf", expected_size=100)

    callback(25, 100)

    bar = FakeBar()
    progress.flush(bar)
    assert progress.processed_bytes == 25
    assert bar.updates == [25]


def test_ui_failure_is_recorded_without_raising():
    progress = UploadProgress(total=100)
    callback = progress.callback_for("a.pdf", expected_size=100)
    callback(100, 100)

    progress.flush(FailingBar())

    assert isinstance(progress.ui_error, RuntimeError)
    assert progress.processed_bytes == 100
