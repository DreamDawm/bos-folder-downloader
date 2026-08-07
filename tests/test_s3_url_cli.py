from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from bos_downloader import s3_url_cli


def client_error(code, message=None):
    return ClientError(
        {
            "Error": {"Code": code, "Message": message or code},
            "ResponseMetadata": {"HTTPStatusCode": 404 if code != "AccessDenied" else 403},
        },
        "HeadObject",
    )


class RecordingClient:
    def __init__(self, events, head_error=None):
        self.events = events
        self.head_error = head_error

    def head_object(self, **kwargs):
        self.events.append(("head", kwargs))
        if self.head_error:
            raise self.head_error
        return {"ContentLength": 123}

    def generate_presigned_url(self, operation, *, Params, ExpiresIn):
        self.events.append(("sign", operation, Params, ExpiresIn))
        return "https://s3.public.example/signed-url"


def make_config():
    return SimpleNamespace(
        access_key_id="test-ak",
        secret_access_key="test-sk",
        endpoint="http://s3.internal.example",
        public_endpoint="https://s3.public.example",
        bucket="medical-dataset",
        expires_days=2,
    )


def install_clients(monkeypatch, cfg, internal_client, public_client, events):
    monkeypatch.setattr(s3_url_cli, "load_s3_upload_config_from_env", lambda: cfg)

    def create_client(config, endpoint=None):
        events.append(("create", endpoint))
        if endpoint == cfg.endpoint:
            return internal_client
        if endpoint == cfg.public_endpoint:
            return public_client
        raise AssertionError(f"使用了未预期的 endpoint: {endpoint}")

    monkeypatch.setattr(s3_url_cli, "create_s3_client", create_client)


def test_run_checks_object_before_generating_unicode_get_link(monkeypatch, capsys):
    events = []
    cfg = make_config()
    internal_client = RecordingClient(events)
    public_client = RecordingClient(events)
    install_clients(monkeypatch, cfg, internal_client, public_client, events)

    object_key = "样例数据/英特雷真/30000中药医学知识.zip"
    assert s3_url_cli.run(object_key) == 0

    captured = capsys.readouterr()
    assert captured.out == "https://s3.public.example/signed-url\n"
    assert captured.err == ""
    assert events == [
        ("create", "http://s3.internal.example"),
        ("head", {"Bucket": "medical-dataset", "Key": object_key}),
        ("create", "https://s3.public.example"),
        (
            "sign",
            "get_object",
            {"Bucket": "medical-dataset", "Key": object_key},
            2 * 24 * 60 * 60,
        ),
    ]


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
def test_main_returns_one_without_link_when_object_is_missing(
    code, monkeypatch, capsys
):
    events = []
    cfg = make_config()
    internal_client = RecordingClient(events, head_error=client_error(code))
    public_client = RecordingClient(events)
    install_clients(monkeypatch, cfg, internal_client, public_client, events)

    object_key = "missing/不存在.zip"
    assert s3_url_cli.main(["--path", object_key]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "S3 对象不存在" in captured.err
    assert "medical-dataset" in captured.err
    assert object_key in captured.err
    assert events == [
        ("create", "http://s3.internal.example"),
        ("head", {"Bucket": "medical-dataset", "Key": object_key}),
    ]


def test_access_denied_returns_one_without_leaking_credentials(monkeypatch, capsys):
    events = []
    cfg = make_config()
    cfg.access_key_id = "sensitive-ak"
    cfg.secret_access_key = "sensitive-sk"
    error = client_error(
        "AccessDenied",
        f"拒绝访问 {cfg.access_key_id} {cfg.secret_access_key}",
    )
    install_clients(
        monkeypatch,
        cfg,
        RecordingClient(events, head_error=error),
        RecordingClient(events),
        events,
    )

    assert s3_url_cli.main(["--path", "data/a.jpg"]) == 1

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert captured.out == ""
    assert "检查 S3 对象失败" in captured.err
    assert "AccessDenied" in captured.err
    assert cfg.access_key_id not in combined
    assert cfg.secret_access_key not in combined
    assert not any(event[0] == "sign" for event in events)


def test_connection_error_returns_one_without_generating_link(monkeypatch, capsys):
    events = []
    cfg = make_config()
    error = EndpointConnectionError(endpoint_url=cfg.endpoint)
    install_clients(
        monkeypatch,
        cfg,
        RecordingClient(events, head_error=error),
        RecordingClient(events),
        events,
    )

    assert s3_url_cli.main(["--path", "data/a.jpg"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "检查 S3 对象失败" in captured.err
    assert "EndpointConnectionError" in captured.err
    assert not any(event[0] == "sign" for event in events)


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
