from __future__ import annotations

from pathlib import Path

import pytest

from bos_downloader import config
from bos_downloader.config import S3UploadConfig, load_s3_upload_config_from_env


def credentials():
    return {
        "S3_ACCESS_KEY_ID": "test-ak",
        "S3_SECRET_ACCESS_KEY": "test-sk",
    }


def write_config(path: Path, *, expires_days: int = 2) -> Path:
    path.write_text(
        """s3:
  endpoint: http://s3.internal.example
  public_endpoint: https://s3.public.example
  bucket: medical-dataset
  region: stack-region-1
  addressing_style: path
  bypass_proxy: true
presigned_url:
  expires_days: {expires_days}
""".format(expires_days=expires_days),
        encoding="utf-8",
        newline="\n",
    )
    return path


def test_loads_credentials_from_env_and_other_fields_from_yaml(tmp_path):
    config_path = write_config(tmp_path / "s3.yml")

    assert load_s3_upload_config_from_env(credentials(), config_path) == S3UploadConfig(
        access_key_id="test-ak",
        secret_access_key="test-sk",
        endpoint="http://s3.internal.example",
        public_endpoint="https://s3.public.example",
        bucket="medical-dataset",
        region="stack-region-1",
        addressing_style="path",
        bypass_proxy=True,
        expires_days=2,
    )


@pytest.mark.parametrize("name", ["S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"])
def test_missing_required_s3_credential_names_the_variable(tmp_path, name):
    env = credentials()
    del env[name]

    with pytest.raises(KeyError, match=name):
        load_s3_upload_config_from_env(env, write_config(tmp_path / "s3.yml"))


def test_missing_yaml_file_reports_path(tmp_path):
    missing = tmp_path / "missing.yml"

    with pytest.raises(ValueError, match="S3 配置文件不存在"):
        load_s3_upload_config_from_env(credentials(), missing)


def test_invalid_yaml_is_reported(tmp_path):
    config_path = tmp_path / "s3.yml"
    config_path.write_text("s3: [", encoding="utf-8")

    with pytest.raises(ValueError, match="YAML 格式错误"):
        load_s3_upload_config_from_env(credentials(), config_path)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("presigned_url:\n  expires_days: 2\n", "s3"),
        (
            """s3:
  endpoint: http://s3.internal.example
  public_endpoint: https://s3.public.example
  region: stack-region-1
  addressing_style: path
  bypass_proxy: true
presigned_url:
  expires_days: 2
""",
            "s3.bucket",
        ),
        (
            """s3:
  endpoint: http://s3.internal.example
  public_endpoint: https://s3.public.example
  bucket: medical-dataset
  region: stack-region-1
  addressing_style: path
  bypass_proxy: "true"
presigned_url:
  expires_days: 2
""",
            "s3.bypass_proxy",
        ),
    ],
)
def test_rejects_missing_or_invalid_yaml_fields(tmp_path, content, expected):
    config_path = tmp_path / "s3.yml"
    config_path.write_text(content, encoding="utf-8", newline="\n")

    with pytest.raises(ValueError, match=expected):
        load_s3_upload_config_from_env(credentials(), config_path)


@pytest.mark.parametrize("expires_days", [0, -1])
def test_rejects_non_positive_expiry(tmp_path, expires_days):
    with pytest.raises(ValueError, match="大于 0"):
        load_s3_upload_config_from_env(
            credentials(), write_config(tmp_path / "s3.yml", expires_days=expires_days)
        )


@pytest.mark.parametrize("expires_days", [1, 7, 30, 365])
def test_accepts_any_positive_expiry(tmp_path, expires_days):
    loaded = load_s3_upload_config_from_env(
        credentials(), write_config(tmp_path / "s3.yml", expires_days=expires_days)
    )

    assert loaded.expires_days == expires_days


def test_explicit_environment_does_not_load_dotenv(tmp_path, monkeypatch):
    monkeypatch.setattr(
        config,
        "load_dotenv",
        lambda **kwargs: pytest.fail("显式 env 不应加载 .env"),
    )

    loaded = load_s3_upload_config_from_env(
        credentials(), write_config(tmp_path / "s3.yml")
    )

    assert loaded.bucket == "medical-dataset"


def test_process_environment_has_priority_when_loading_dotenv(tmp_path, monkeypatch):
    process_env = credentials()
    for name, value in process_env.items():
        monkeypatch.setenv(name, value)
    calls = []

    def fake_load_dotenv(*, dotenv_path, override):
        calls.append((dotenv_path, override))
        monkeypatch.setenv("S3_ACCESS_KEY_ID", "dotenv-ak-marker") if override else None

    monkeypatch.setattr(config, "load_dotenv", fake_load_dotenv)

    loaded = load_s3_upload_config_from_env(
        config_path=write_config(tmp_path / "s3.yml")
    )

    assert calls == [(config.PROJECT_ROOT / ".env", False)]
    assert loaded.access_key_id == "test-ak"
