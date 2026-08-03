"""创建供上传流程使用的 S3 兼容客户端。"""

from __future__ import annotations

import boto3
from botocore.config import Config

from bos_downloader.config import S3UploadConfig


def create_s3_client(cfg: S3UploadConfig):
    """按上传配置创建一个 S3 客户端。"""
    client_config = Config(
        signature_version="s3v4",
        connect_timeout=10,
        read_timeout=60,
        retries={"max_attempts": 3, "mode": "standard"},
        proxies={} if cfg.bypass_proxy else None,
        s3={"addressing_style": cfg.addressing_style},
    )
    return boto3.client(
        "s3",
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
        endpoint_url=cfg.endpoint,
        region_name=cfg.region,
        config=client_config,
    )
