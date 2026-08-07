"""命令行入口：生成 S3 对象的外部预签名下载链接。"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from botocore.exceptions import BotoCoreError, ClientError

from bos_downloader.config import load_s3_upload_config_from_env
from bos_downloader.s3_client import create_s3_client
from bos_downloader.s3_uploader import remote_object_size

SECONDS_PER_DAY = 24 * 60 * 60


class S3ObjectNotFoundError(RuntimeError):
    """指定的桶内对象不存在。"""


def _validate_object_key(object_key: str) -> None:
    if not object_key.strip():
        raise ValueError("对象路径不能为空")
    if object_key.startswith("/"):
        raise ValueError("对象路径不能以 / 开头")
    if object_key.lower().startswith("s3://"):
        raise ValueError("对象路径必须是桶内 Key，不能使用 s3:// 地址")


def run(object_key: str) -> int:
    """为桶内对象 Key 生成预签名 GET 链接。"""
    _validate_object_key(object_key)
    config = load_s3_upload_config_from_env()
    if object_key.startswith(f"{config.bucket}/"):
        raise ValueError("对象路径不能包含配置中的桶名")

    check_client = create_s3_client(config, endpoint=config.endpoint)
    if remote_object_size(check_client, config.bucket, object_key) is None:
        raise S3ObjectNotFoundError(f"S3 对象不存在: {config.bucket}/{object_key}")

    signing_client = create_s3_client(config, endpoint=config.public_endpoint)
    url = signing_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": config.bucket, "Key": object_key},
        ExpiresIn=config.expires_days * SECONDS_PER_DAY,
    )
    print(url)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    """解析对象路径，并将可预期错误映射到退出码 2。"""
    parser = argparse.ArgumentParser(description="生成 S3 对象的外部预签名下载链接")
    parser.add_argument("--path", required=True, help="桶内对象路径，不包含桶名")
    args = parser.parse_args(argv)
    try:
        return run(args.path)
    except S3ObjectNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (ClientError, BotoCoreError) as exc:
        if isinstance(exc, ClientError):
            error_name = str(exc.response.get("Error", {}).get("Code", "")) or "ClientError"
        else:
            error_name = type(exc).__name__
        print(f"检查 S3 对象失败: {error_name}", file=sys.stderr)
        return 1
    except (KeyError, ValueError) as exc:
        print(f"配置或对象路径错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
