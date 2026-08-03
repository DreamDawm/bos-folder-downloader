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


def test_submit_window_never_exceeds_twice_workers():
    submitted = []

    class FakeExecutor:
        def submit(self, fn, *args):
            future = object()
            submitted.append(future)
            return future

    iterator = iter([SimpleNamespace() for _ in range(10)])
    pending = set()

    s3_upload_cli._submit_until_full(
        FakeExecutor(), iterator, pending, 4, object(), "bucket", object()
    )

    assert len(pending) == 4
    assert len(submitted) == 4


def test_main_reports_configuration_or_source_error(monkeypatch, capsys):
    def fail(source, workers):
        raise ValueError("源路径不存在")

    monkeypatch.setattr(s3_upload_cli, "run", fail)

    assert s3_upload_cli.main(["--src", "D:/missing"]) == 2
    assert "配置或源路径错误" in capsys.readouterr().err


def test_main_propagates_cancel_exit_code(monkeypatch):
    monkeypatch.setattr(s3_upload_cli, "run", lambda source, workers: 130)

    assert s3_upload_cli.main(["--src", "D:/data"]) == 130


def test_run_returns_130_and_cleans_up_once_when_wait_is_interrupted(tmp_path, monkeypatch, capsys):
    source = tmp_path / "a.bin"
    source.write_bytes(b"a")
    item = S3UploadItem(source, "data/a.bin", 1)
    cfg = SimpleNamespace(endpoint="https://s3.internal", bucket="bucket")

    class FakeExecutor:
        def __init__(self):
            self.shutdown_calls = []

        def submit(self, fn, *args):
            return Future()

        def shutdown(self, **kwargs):
            self.shutdown_calls.append(kwargs)

    executor = FakeExecutor()
    monkeypatch.setattr(s3_upload_cli, "load_s3_upload_config_from_env", lambda: cfg)
    monkeypatch.setattr(s3_upload_cli, "create_s3_client", lambda config: object())
    monkeypatch.setattr(s3_upload_cli, "discover_upload_items", lambda source: iter([item]))
    monkeypatch.setattr(s3_upload_cli, "ThreadPoolExecutor", lambda max_workers: executor)
    monkeypatch.setattr(
        s3_upload_cli,
        "wait",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.setattr(s3_upload_cli, "tqdm", RecordingBar)

    assert s3_upload_cli.run(str(source), workers=1) == 130
    assert executor.shutdown_calls == [{"wait": True, "cancel_futures": True}]
    assert "退出码 130" in capsys.readouterr().err
