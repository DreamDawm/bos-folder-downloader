"""配置:从环境变量加载 BOS 凭证(下载)与 SFTP 凭证(上传)。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv

_DEFAULT_SFTP_PORT = "22"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_S3_CONFIG_PATH = PROJECT_ROOT / "config" / "s3.yml"


@dataclass(frozen=True)
class DownloadConfig:
    access_key_id: str
    secret_access_key: str
    endpoint: str
    bucket: str


@dataclass(frozen=True)
class SftpConfig:
    host: str
    port: int
    username: str
    password: str
    remote_base: str


@dataclass(frozen=True)
class S3UploadConfig:
    access_key_id: str
    secret_access_key: str
    endpoint: str
    public_endpoint: str
    bucket: str
    region: str
    addressing_style: str
    bypass_proxy: bool
    expires_days: int


def load_config_from_env(
    env: Mapping[str, str] | None = None,
) -> DownloadConfig:
    """从环境变量映射读取配置。缺失任一必填项时抛出 KeyError(含变量名)。

    未显式传入 env 时,先自动加载项目根目录的 .env 文件(若存在),
    使配置在 Windows cmd / PowerShell / Git Bash 下都无需手动 source。
    已存在的真实环境变量优先,不会被 .env 覆盖。
    """
    if env is None:
        load_dotenv(override=False)
    source = env if env is not None else os.environ
    return DownloadConfig(
        access_key_id=source["BOS_ACCESS_KEY_ID"],
        secret_access_key=source["BOS_SECRET_ACCESS_KEY"],
        endpoint=source["BOS_ENDPOINT"],
        bucket=source["BOS_BUCKET"],
    )


def load_sftp_config_from_env(
    env: Optional[Mapping[str, str]] = None,
) -> SftpConfig:
    """从环境变量映射读取 SFTP 配置。缺失任一必填项时抛出 KeyError(含变量名)。

    与 load_config_from_env 同样,未显式传入 env 时先自动加载 .env,
    已存在的真实环境变量优先,不会被 .env 覆盖。
    SFTP_PORT 选填,缺省为 22。
    """
    if env is None:
        load_dotenv(override=False)
    source = env if env is not None else os.environ
    return SftpConfig(
        host=source["SFTP_HOST"],
        port=int(source.get("SFTP_PORT", _DEFAULT_SFTP_PORT)),
        username=source["SFTP_USERNAME"],
        password=source["SFTP_PASSWORD"],
        remote_base=source["SFTP_REMOTE_BASE"],
    )


def _require_mapping(source: Mapping, key: str, prefix: str = "") -> Mapping:
    field_name = f"{prefix}.{key}" if prefix else key
    value = source.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"配置项 {field_name} 必须存在且为映射")
    return value


def _require_string(source: Mapping, key: str, prefix: str) -> str:
    field_name = f"{prefix}.{key}"
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"配置项 {field_name} 必须是非空字符串")
    return value.strip()


def _require_bool(source: Mapping, key: str, prefix: str) -> bool:
    field_name = f"{prefix}.{key}"
    value = source.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"配置项 {field_name} 必须是布尔值 true 或 false")
    return value


def _validate_endpoint(name: str, value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"配置项 {name} 必须是有效的 HTTP 或 HTTPS 地址")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError(f"配置项 {name} 不能包含参数、查询字符串或片段")
    return value.rstrip("/")


def _load_s3_yaml(config_path: Path) -> Mapping:
    if not config_path.is_file():
        raise ValueError(f"S3 配置文件不存在: {config_path}")
    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            loaded = yaml.safe_load(config_file)
    except yaml.YAMLError as exc:
        raise ValueError(f"S3 配置文件 YAML 格式错误: {config_path}") from exc
    if not isinstance(loaded, Mapping):
        raise ValueError("S3 配置文件根节点必须是映射")
    return loaded


def load_s3_upload_config_from_env(
    env: Optional[Mapping[str, str]] = None,
    config_path: str | Path | None = None,
) -> S3UploadConfig:
    """从 .env 读取凭据，并从 YAML 读取 S3 非敏感配置。"""
    if env is None:
        load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)
    source = env if env is not None else os.environ
    yaml_path = Path(config_path) if config_path is not None else DEFAULT_S3_CONFIG_PATH
    loaded = _load_s3_yaml(yaml_path)
    s3 = _require_mapping(loaded, "s3")
    presigned_url = _require_mapping(loaded, "presigned_url")

    addressing_style = _require_string(s3, "addressing_style", "s3").lower()
    if addressing_style not in {"path", "virtual"}:
        raise ValueError("配置项 s3.addressing_style 必须是 path 或 virtual")

    expires_days = presigned_url.get("expires_days")
    if isinstance(expires_days, bool) or not isinstance(expires_days, int):
        raise ValueError("配置项 presigned_url.expires_days 必须是整数")
    if expires_days <= 0:
        raise ValueError("配置项 presigned_url.expires_days 必须大于 0")

    return S3UploadConfig(
        access_key_id=source["S3_ACCESS_KEY_ID"],
        secret_access_key=source["S3_SECRET_ACCESS_KEY"],
        endpoint=_validate_endpoint(
            "s3.endpoint", _require_string(s3, "endpoint", "s3")
        ),
        public_endpoint=_validate_endpoint(
            "s3.public_endpoint", _require_string(s3, "public_endpoint", "s3")
        ),
        bucket=_require_string(s3, "bucket", "s3"),
        region=_require_string(s3, "region", "s3"),
        addressing_style=addressing_style,
        bypass_proxy=_require_bool(s3, "bypass_proxy", "s3"),
        expires_days=expires_days,
    )
