"""检查并上传单个 S3 对象。"""

from __future__ import annotations

from typing import Callable, Optional

from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

from bos_downloader.s3_paths import S3UploadItem

_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}
_TRANSFER_CONFIG = TransferConfig(use_threads=False)


class SourceFileChangedError(RuntimeError):
    """源文件在枚举完成后大小发生变化。"""


def remote_object_size(client, bucket: str, key: str) -> Optional[int]:
    """返回远端对象大小；明确不存在时返回 ``None``。"""
    try:
        response = client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in _NOT_FOUND_CODES or status == 404:
            return None
        raise
    return int(response["ContentLength"])


def upload_s3_item(
    client,
    bucket: str,
    item: S3UploadItem,
    callback: Optional[Callable[[int], None]] = None,
) -> str:
    """按大小决定跳过或覆盖上传单个本地文件。"""
    current_size = item.abs_path.stat().st_size
    if current_size != item.size:
        raise SourceFileChangedError(f"源文件大小在枚举后发生变化: {item.size} -> {current_size}")
    if remote_object_size(client, bucket, item.object_key) == item.size:
        return "skipped"
    client.upload_file(
        str(item.abs_path),
        bucket,
        item.object_key,
        Callback=callback,
        Config=_TRANSFER_CONFIG,
    )
    return "done"
