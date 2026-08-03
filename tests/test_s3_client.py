from bos_downloader import s3_client
from bos_downloader.config import S3UploadConfig


def make_config(bypass_proxy=True):
    return S3UploadConfig(
        access_key_id="test-ak",
        secret_access_key="test-sk",
        endpoint="http://s3.internal.example",
        bucket="medical-dataset",
        region="stack-region-1",
        addressing_style="path",
        bypass_proxy=bypass_proxy,
    )


def test_client_uses_sigv4_path_style_and_empty_proxy(monkeypatch):
    captured = {}
    sentinel = object()

    def fake_client(service, **kwargs):
        captured["service"] = service
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(s3_client.boto3, "client", fake_client)

    assert s3_client.create_s3_client(make_config()) is sentinel
    assert captured["service"] == "s3"
    assert captured["endpoint_url"] == "http://s3.internal.example"
    assert captured["region_name"] == "stack-region-1"
    assert captured["config"].signature_version == "s3v4"
    assert captured["config"].s3 == {"addressing_style": "path"}
    assert captured["config"].proxies == {}


def test_client_allows_environment_proxy_when_bypass_is_false(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        s3_client.boto3,
        "client",
        lambda service, **kwargs: captured.update(kwargs),
    )

    s3_client.create_s3_client(make_config(bypass_proxy=False))

    assert captured["config"].proxies is None
