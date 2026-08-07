from types import SimpleNamespace

import pytest

from bos_downloader import s3_url_cli


class RecordingClient:
    def __init__(self):
        self.calls = []

    def generate_presigned_url(self, operation, *, Params, ExpiresIn):
        self.calls.append((operation, Params, ExpiresIn))
        return "https://s3.public.example/signed-url"


def make_config():
    return SimpleNamespace(
        public_endpoint="https://s3.public.example",
        bucket="medical-dataset",
        expires_days=2,
    )


def test_run_generates_get_link_for_unicode_object_key(monkeypatch, capsys):
    client = RecordingClient()
    cfg = make_config()
    monkeypatch.setattr(s3_url_cli, "load_s3_upload_config_from_env", lambda: cfg)
    monkeypatch.setattr(
        s3_url_cli,
        "create_s3_client",
        lambda config, endpoint: client,
    )

    object_key = "样例数据/英特雷真/30000中药医学知识.zip"
    assert s3_url_cli.run(object_key) == 0

    captured = capsys.readouterr()
    assert captured.out == "https://s3.public.example/signed-url\n"
    assert captured.err == ""
    assert client.calls == [
        (
            "get_object",
            {"Bucket": "medical-dataset", "Key": object_key},
            2 * 24 * 60 * 60,
        )
    ]


def test_run_uses_public_endpoint(monkeypatch):
    cfg = make_config()
    endpoints = []
    client = RecordingClient()
    monkeypatch.setattr(s3_url_cli, "load_s3_upload_config_from_env", lambda: cfg)
    monkeypatch.setattr(
        s3_url_cli,
        "create_s3_client",
        lambda config, endpoint: endpoints.append(endpoint) or client,
    )

    assert s3_url_cli.run("data/a.jpg") == 0
    assert endpoints == ["https://s3.public.example"]


@pytest.mark.parametrize("object_key", ["", "   ", "/data/a.jpg", "s3://bucket/data/a.jpg"])
def test_run_rejects_empty_or_absolute_object_key(object_key):
    with pytest.raises(ValueError, match="对象路径"):
        s3_url_cli.run(object_key)


def test_run_rejects_path_containing_configured_bucket(monkeypatch):
    monkeypatch.setattr(
        s3_url_cli,
        "load_s3_upload_config_from_env",
        make_config,
    )

    with pytest.raises(ValueError, match="不能包含配置中的桶名"):
        s3_url_cli.run("medical-dataset/data/a.jpg")


def test_main_requires_path():
    with pytest.raises(SystemExit) as exc:
        s3_url_cli.main([])

    assert exc.value.code == 2


def test_main_reports_configuration_or_path_error(monkeypatch, capsys):
    monkeypatch.setattr(
        s3_url_cli,
        "run",
        lambda object_key: (_ for _ in ()).throw(ValueError("模拟配置错误")),
    )

    assert s3_url_cli.main(["--path", "data/a.jpg"]) == 2
    assert "配置或对象路径错误" in capsys.readouterr().err
