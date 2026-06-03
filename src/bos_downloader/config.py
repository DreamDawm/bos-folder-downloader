"""下载配置:从环境变量加载 BOS 凭证与目标桶。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from dotenv import load_dotenv


@dataclass(frozen=True)
class DownloadConfig:
    access_key_id: str
    secret_access_key: str
    endpoint: str
    bucket: str


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
