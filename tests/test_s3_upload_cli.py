from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from bos_downloader import s3_upload_cli
from bos_downloader.s3_paths import S3UploadItem


class RecordingBar:
    instances = []

    def __init__(self, total, **kwargs):
        self.total = total
        self.n = 0
        self.postfix = {}
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def update(self, amount):
        self.n += amount

    def set_postfix(self, values, refresh=True):
        self.postfix = dict(values)

    @staticmethod
    def write(message, file=None):
        print(message, file=file)


def test_default_workers_is_eight():
    assert s3_upload_cli.DEFAULT_WORKERS == 8


def test_main_uses_eight_workers_when_option_is_omitted(monkeypatch):
    seen = []
    monkeypatch.setattr(
        s3_upload_cli,
        "run",
        lambda source, workers: seen.append((source, workers)) or 0,
    )

    assert s3_upload_cli.main(["--src", "D:/data/a.bin"]) == 0
    assert seen == [("D:/data/a.bin", 8)]


def test_main_forwards_src_and_workers(monkeypatch):
    seen = []
    monkeypatch.setattr(
        s3_upload_cli,
        "run",
        lambda source, workers: seen.append((source, workers)) or 0,
    )

    assert s3_upload_cli.main(["--src", "D:/data/a.bin", "--workers", "3"]) == 0
    assert seen == [("D:/data/a.bin", 3)]


@pytest.mark.parametrize("workers", [0, 65])
def test_main_rejects_worker_count_outside_one_to_sixty_four(workers, capsys):
    with pytest.raises(SystemExit):
        s3_upload_cli.main(["--src", "D:/data", "--workers", str(workers)])

    assert "1 到 64" in capsys.readouterr().err


def test_run_uploads_and_skips_with_summary(tmp_path, monkeypatch, capsys):
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    first.write_bytes(b"aaa")
    second.write_bytes(b"bb")
    items = [S3UploadItem(first, "data/a.bin", 3), S3UploadItem(second, "data/b.bin", 2)]
    cfg = SimpleNamespace(endpoint="http://s3.internal", bucket="bucket")
    statuses = {"data/a.bin": "done", "data/b.bin": "skipped"}
    monkeypatch.setattr(s3_upload_cli, "load_s3_upload_config_from_env", lambda: cfg)
    monkeypatch.setattr(s3_upload_cli, "create_s3_client", lambda config: object())
    monkeypatch.setattr(s3_upload_cli, "discover_upload_items", lambda source: iter(items))
    def fake_upload(client, bucket, item, callback):
        status = statuses[item.object_key]
        if status == "done":
            callback(item.size)
        return status

    monkeypatch.setattr(s3_upload_cli, "upload_s3_item", fake_upload)
    RecordingBar.instances.clear()
    monkeypatch.setattr(s3_upload_cli, "tqdm", RecordingBar)

    assert s3_upload_cli.run(str(tmp_path), workers=2) == 0

    output = capsys.readouterr()
    assert output.err.count("明文 HTTP") == 1
    assert "完成 1" in output.out
    assert "跳过 1" in output.out
    assert "失败 0" in output.out
    assert "总文件 2" in output.out
    assert RecordingBar.instances[0].n == 5


def test_run_continues_after_one_failure(tmp_path, monkeypatch, capsys):
    files = []
    for name in ("good.bin", "bad.bin"):
        path = tmp_path / name
        path.write_bytes(b"x")
        files.append(S3UploadItem(path, f"data/{name}", 1))
    monkeypatch.setattr(
        s3_upload_cli,
        "load_s3_upload_config_from_env",
        lambda: SimpleNamespace(endpoint="https://s3.internal", bucket="bucket"),
    )
    monkeypatch.setattr(s3_upload_cli, "create_s3_client", lambda config: object())
    monkeypatch.setattr(s3_upload_cli, "discover_upload_items", lambda source: iter(files))
    monkeypatch.setattr(s3_upload_cli, "tqdm", RecordingBar)

    def fake_upload(client, bucket, item, callback):
        if item.object_key.endswith("bad.bin"):
            raise RuntimeError("模拟失败")
        callback(item.size)
        return "done"

    monkeypatch.setattr(s3_upload_cli, "upload_s3_item", fake_upload)

    assert s3_upload_cli.run(str(tmp_path), workers=2) == 1

    captured = capsys.readouterr()
    assert "bad.bin" in captured.err
    assert "模拟失败" in captured.err
    assert "失败 1" in captured.out


def test_output_never_contains_credentials(tmp_path, monkeypatch, capsys):
    cfg = SimpleNamespace(
        endpoint="http://s3.internal",
        bucket="bucket",
        access_key_id="sensitive-ak",
        secret_access_key="sensitive-sk",
    )
    monkeypatch.setattr(s3_upload_cli, "load_s3_upload_config_from_env", lambda: cfg)
    monkeypatch.setattr(s3_upload_cli, "discover_upload_items", lambda source: iter(()))

    assert s3_upload_cli.run(str(tmp_path), workers=1) == 0

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "sensitive-ak" not in combined
    assert "sensitive-sk" not in combined


def test_failed_upload_output_redacts_credentials_and_authorization(
    tmp_path, monkeypatch, capsys
):
    path = tmp_path / "failed.bin"
    path.write_bytes(b"x")
    item = S3UploadItem(path, "data/failed.bin", 1)
    access_key = "unit-test-ak-marker"
    secret_key = "unit-test-sk-marker"
    authorization = (
        "Authorization: AWS4-HMAC-SHA256 "
        f"Credential={access_key}/scope, SignedHeaders=host, Signature={secret_key}"
    )
    cfg = SimpleNamespace(
        endpoint="https://s3.internal",
        bucket="bucket",
        access_key_id=access_key,
        secret_access_key=secret_key,
    )
    monkeypatch.setattr(s3_upload_cli, "load_s3_upload_config_from_env", lambda: cfg)
    monkeypatch.setattr(s3_upload_cli, "create_s3_client", lambda config: object())
    monkeypatch.setattr(s3_upload_cli, "discover_upload_items", lambda source: iter([item]))
    monkeypatch.setattr(s3_upload_cli, "tqdm", RecordingBar)
    monkeypatch.setattr(
        s3_upload_cli,
        "upload_s3_item",
        lambda *args: (_ for _ in ()).throw(
            RuntimeError(f"request failed: {access_key} {secret_key} {authorization}")
        ),
    )

    assert s3_upload_cli.run(str(tmp_path), workers=1) == 1

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert access_key not in combined
    assert secret_key not in combined
    assert "AWS4-HMAC-SHA256" not in combined
    assert "Authorization: <已遮蔽>" in captured.err


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Authorization: Bearer test-token-marker", "Authorization: <已遮蔽>"),
        ("Authorization=Bearer test-token-marker", "Authorization=<已遮蔽>"),
        (
            '{"aUtHoRiZaTiOn": "Bearer test-token-marker"}',
            '{"aUtHoRiZaTiOn": "<已遮蔽>"}',
        ),
        (
            "{'Authorization': 'Bearer test-token-marker'}",
            "{'Authorization': '<已遮蔽>'}",
        ),
    ],
)
def test_redact_error_redacts_authorization_values_without_breaking_structure(
    message, expected
):
    redacted = s3_upload_cli._redact_error(message, ())

    assert redacted == expected
    assert "test-token-marker" not in redacted


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            r'{"Authorization": "OAuth note=\"public\", token=secret-token-marker"}',
            '{"Authorization": "<已遮蔽>"}',
        ),
        (
            r"{'Authorization': 'OAuth note=\'public\', token=secret-token-marker'}",
            "{'Authorization': '<已遮蔽>'}",
        ),
    ],
)
def test_redact_error_keeps_escaped_quotes_inside_authorization_values(
    message, expected
):
    redacted = s3_upload_cli._redact_error(message, ())

    assert redacted == expected
    assert "secret-token-marker" not in redacted


def test_many_failures_return_fixed_nonzero_exit_code(tmp_path, monkeypatch, capsys):
    path = tmp_path / "failed.bin"
    path.write_bytes(b"x")
    items = [S3UploadItem(path, f"data/failed-{index}.bin", 1) for index in range(256)]
    cfg = SimpleNamespace(
        endpoint="https://s3.internal",
        bucket="bucket",
        access_key_id="unit-test-ak-marker",
        secret_access_key="unit-test-sk-marker",
    )
    monkeypatch.setattr(s3_upload_cli, "load_s3_upload_config_from_env", lambda: cfg)
    monkeypatch.setattr(s3_upload_cli, "create_s3_client", lambda config: object())
    monkeypatch.setattr(s3_upload_cli, "discover_upload_items", lambda source: iter(items))
    monkeypatch.setattr(s3_upload_cli, "tqdm", RecordingBar)
    monkeypatch.setattr(
        s3_upload_cli,
        "upload_s3_item",
        lambda *args: (_ for _ in ()).throw(RuntimeError("模拟失败")),
    )

    assert s3_upload_cli.run(str(tmp_path), workers=8) == 1
    captured = capsys.readouterr()
    assert "失败 256" in captured.out
    assert "总文件 256" in captured.out


def test_run_keeps_submission_window_at_twice_workers(tmp_path, monkeypatch):
    workers = 2
    expected_window = workers * 2
    items = [
        S3UploadItem(tmp_path / f"file-{index}.bin", f"data/file-{index}.bin", 1)
        for index in range(6)
    ]
    cfg = SimpleNamespace(endpoint="https://s3.internal", bucket="bucket")
    observed_pending_sizes = []

    class FakeExecutor:
        def __init__(self):
            self.submissions = []

        def submit(self, fn, *args):
            future = Future()
            self.submissions.append((future, args[2]))
            return future

        def shutdown(self, **kwargs):
            pass

    executor = FakeExecutor()

    def complete_pending_futures(pending, **kwargs):
        observed_pending_sizes.append(len(pending))
        completed = set(pending)
        for future, item in executor.submissions:
            if future in completed:
                future.set_result(s3_upload_cli.UploadOutcome(item.object_key, "done"))
        return completed, set()

    monkeypatch.setattr(s3_upload_cli, "load_s3_upload_config_from_env", lambda: cfg)
    monkeypatch.setattr(s3_upload_cli, "create_s3_client", lambda config: object())
    monkeypatch.setattr(s3_upload_cli, "discover_upload_items", lambda source: iter(items))
    monkeypatch.setattr(s3_upload_cli, "ThreadPoolExecutor", lambda max_workers: executor)
    monkeypatch.setattr(s3_upload_cli, "wait", complete_pending_futures)
    monkeypatch.setattr(s3_upload_cli, "tqdm", RecordingBar)

    assert s3_upload_cli.run(str(tmp_path), workers=workers) == 0

    assert max(observed_pending_sizes) == expected_window
    assert all(size <= expected_window for size in observed_pending_sizes)
    assert len(executor.submissions) == len(items)


def test_main_reports_configuration_or_source_error(monkeypatch, capsys):
    def fail(source, workers):
        raise ValueError("源路径不存在")

    monkeypatch.setattr(s3_upload_cli, "run", fail)

    assert s3_upload_cli.main(["--src", "D:/missing"]) == 2
    assert "配置或源路径错误" in capsys.readouterr().err


def test_main_propagates_cancel_exit_code(monkeypatch):
    monkeypatch.setattr(s3_upload_cli, "run", lambda source, workers: 130)

    assert s3_upload_cli.main(["--src", "D:/data"]) == 130


def test_run_returns_130_when_discovery_is_interrupted(monkeypatch, capsys):
    monkeypatch.setattr(
        s3_upload_cli,
        "load_s3_upload_config_from_env",
        lambda: SimpleNamespace(endpoint="https://s3.internal", bucket="bucket"),
    )
    monkeypatch.setattr(
        s3_upload_cli,
        "discover_upload_items",
        lambda source: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    assert s3_upload_cli.run("D:/data", workers=1) == 130
    assert "上传已取消，退出码 130" in capsys.readouterr().err


def test_run_cancellation_cancels_pending_and_stops_submissions(tmp_path, monkeypatch, capsys):
    workers = 1
    items = [
        S3UploadItem(tmp_path / f"file-{index}.bin", f"data/file-{index}.bin", 1)
        for index in range(3)
    ]
    cfg = SimpleNamespace(endpoint="https://s3.internal", bucket="bucket")
    interrupted = False

    class FakeExecutor:
        def __init__(self):
            self.shutdown_calls = []
            self.submissions = []
            self.submissions_after_interrupt = []

        def submit(self, fn, *args):
            future = Future()
            self.submissions.append(future)
            if interrupted:
                self.submissions_after_interrupt.append(future)
            return future

        def shutdown(self, **kwargs):
            self.shutdown_calls.append(kwargs)

    executor = FakeExecutor()
    monkeypatch.setattr(s3_upload_cli, "load_s3_upload_config_from_env", lambda: cfg)
    monkeypatch.setattr(s3_upload_cli, "create_s3_client", lambda config: object())
    monkeypatch.setattr(s3_upload_cli, "discover_upload_items", lambda source: iter(items))
    monkeypatch.setattr(s3_upload_cli, "ThreadPoolExecutor", lambda max_workers: executor)

    def interrupt_wait(*args, **kwargs):
        nonlocal interrupted
        interrupted = True
        raise KeyboardInterrupt

    monkeypatch.setattr(s3_upload_cli, "wait", interrupt_wait)
    monkeypatch.setattr(s3_upload_cli, "tqdm", RecordingBar)

    assert s3_upload_cli.run(str(tmp_path), workers=workers) == 130
    assert len(executor.submissions) == workers * 2
    assert not executor.submissions_after_interrupt
    assert all(future.cancelled() for future in executor.submissions)
    assert executor.shutdown_calls == [{"wait": True, "cancel_futures": True}]
    assert "退出码 130" in capsys.readouterr().err
