"""根据配置构造百度 BOS 客户端。"""

from __future__ import annotations

from baidubce.auth.bce_credentials import BceCredentials
from baidubce.bce_client_configuration import BceClientConfiguration
from baidubce.services.bos.bos_client import BosClient

from bos_downloader.config import DownloadConfig


def create_bos_client(cfg: DownloadConfig) -> BosClient:
    """用 AK/SK/endpoint 构造一个 BosClient。"""
    credentials = BceCredentials(cfg.access_key_id, cfg.secret_access_key)
    bce_config = BceClientConfiguration(
        credentials=credentials,
        endpoint=cfg.endpoint,
    )
    return BosClient(bce_config)
