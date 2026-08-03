import pytest

from bos_downloader.config import S3UploadConfig, load_s3_upload_config_from_env


def full_env():
    return {
        "S3_ACCESS_KEY_ID": "test-ak",
        "S3_SECRET_ACCESS_KEY": "test-sk",
        "S3_ENDPOINT": "http://s3.internal.example",
        "S3_BUCKET": "medical-dataset",
        "S3_REGION": "stack-region-1",
        "S3_ADDRESSING_STYLE": "path",
        "S3_BYPASS_PROXY": "true",
    }


def test_loads_all_s3_fields():
    assert load_s3_upload_config_from_env(full_env()) == S3UploadConfig(
        access_key_id="test-ak",
        secret_access_key="test-sk",
        endpoint="http://s3.internal.example",
        bucket="medical-dataset",
        region="stack-region-1",
        addressing_style="path",
        bypass_proxy=True,
    )


@pytest.mark.parametrize("value, expected", [("true", True), ("TRUE", True), ("false", False)])
def test_parses_bypass_proxy(value, expected):
    env = full_env()
    env["S3_BYPASS_PROXY"] = value
    assert load_s3_upload_config_from_env(env).bypass_proxy is expected


@pytest.mark.parametrize(
    "name",
    ["S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_ENDPOINT", "S3_BUCKET", "S3_REGION"],
)
def test_missing_required_s3_variable_names_the_variable(name):
    env = full_env()
    del env[name]
    with pytest.raises(KeyError, match=name):
        load_s3_upload_config_from_env(env)


def test_rejects_invalid_addressing_style():
    env = full_env()
    env["S3_ADDRESSING_STYLE"] = "mixed"
    with pytest.raises(ValueError, match="S3_ADDRESSING_STYLE"):
        load_s3_upload_config_from_env(env)


def test_rejects_invalid_bypass_proxy():
    env = full_env()
    env["S3_BYPASS_PROXY"] = "yes"
    with pytest.raises(ValueError, match="S3_BYPASS_PROXY"):
        load_s3_upload_config_from_env(env)
