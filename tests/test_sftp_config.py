from bos_downloader.config import load_sftp_config_from_env

import pytest


def _full_env():
    return {
        "SFTP_HOST": "10.0.0.1",
        "SFTP_PORT": "2222",
        "SFTP_USERNAME": "user",
        "SFTP_PASSWORD": "secret",
        "SFTP_REMOTE_BASE": "/upload",
    }


def test_loads_all_fields_with_port_as_int():
    cfg = load_sftp_config_from_env(_full_env())
    assert cfg.host == "10.0.0.1"
    assert cfg.port == 2222
    assert isinstance(cfg.port, int)
    assert cfg.username == "user"
    assert cfg.password == "secret"
    assert cfg.remote_base == "/upload"


def test_port_defaults_to_22_when_missing():
    env = _full_env()
    del env["SFTP_PORT"]
    cfg = load_sftp_config_from_env(env)
    assert cfg.port == 22


def test_missing_password_raises_keyerror_with_name():
    env = _full_env()
    del env["SFTP_PASSWORD"]
    with pytest.raises(KeyError, match="SFTP_PASSWORD"):
        load_sftp_config_from_env(env)


def test_missing_host_raises_keyerror_with_name():
    env = _full_env()
    del env["SFTP_HOST"]
    with pytest.raises(KeyError, match="SFTP_HOST"):
        load_sftp_config_from_env(env)
