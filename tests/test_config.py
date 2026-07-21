import pytest

from bos_downloader.config import DownloadConfig, load_config_from_env


def test_load_config_from_env_reads_all_fields():
    env = {
        "BOS_ACCESS_KEY_ID": "ak-123",
        "BOS_SECRET_ACCESS_KEY": "sk-456",
        "BOS_ENDPOINT": "bj.bcebos.com",
        "BOS_BUCKET": "my-bucket",
    }
    cfg = load_config_from_env(env)
    assert cfg == DownloadConfig(
        access_key_id="ak-123",
        secret_access_key="sk-456",
        endpoint="bj.bcebos.com",
        bucket="my-bucket",
    )


def test_load_config_missing_key_raises_keyerror_with_name():
    env = {"BOS_ACCESS_KEY_ID": "ak-123"}
    with pytest.raises(KeyError) as exc:
        load_config_from_env(env)
    assert "BOS_SECRET_ACCESS_KEY" in str(exc.value)
