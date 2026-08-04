"""命令行入口：将本地文件或目录并发上传到 S3 兼容存储。"""

from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Dict, Iterator, Optional, Set
from urllib.parse import urlparse

from tqdm import tqdm

from bos_downloader.config import load_s3_upload_config_from_env
from bos_downloader.s3_client import create_s3_client
from bos_downloader.s3_paths import S3UploadItem, discover_upload_items
from bos_downloader.s3_uploader import upload_s3_item
from bos_downloader.upload_progress import UploadProgress

DEFAULT_WORKERS = 8
MAX_WORKERS = 64
_PROGRESS_REFRESH_SECONDS = 0.1


@dataclass(frozen=True)
class UploadOutcome:
    """单个 S3 对象的上传结果。"""

    object_key: str
    status: str
    error: Optional[str] = None


def _upload_one(
    client, bucket: str, item: S3UploadItem, progress: UploadProgress
) -> UploadOutcome:
    """上传一个对象，将单项异常转换为可汇总的失败结果。"""
    callback = progress.increment_callback_for(item.object_key, item.size)
    try:
        status = upload_s3_item(client, bucket, item, callback)
        if status == "skipped":
            callback(item.size)
        if status not in {"done", "skipped"}:
            return UploadOutcome(item.object_key, "failed", f"未知上传状态: {status}")
        return UploadOutcome(item.object_key, status)
    except Exception as exc:  # noqa: BLE001 - 单文件失败不应中断整个批次
        return UploadOutcome(item.object_key, "failed", str(exc))


def _submit_until_full(
    executor,
    items: Iterator[S3UploadItem],
    pending: Set[Future[UploadOutcome]],
    limit: int,
    client,
    bucket: str,
    progress: UploadProgress,
) -> None:
    """将待处理窗口填满；窗口限制未开始和已开始的总文件任务数。"""
    while len(pending) < limit:
        try:
            item = next(items)
        except StopIteration:
            return
        pending.add(executor.submit(_upload_one, client, bucket, item, progress))


def _redact_error(message: str, secrets: tuple[str, ...]) -> str:
    redacted = message
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "<已遮蔽>")
    redacted = re.sub(
        r'''(?i)(authorization["']\s*[:=]\s*)(["'])(?:\\[^\r\n]|(?!\2)[^\\\r\n])*\2''',
        r"\1\2<已遮蔽>\2",
        redacted,
    )
    return re.sub(
        r"(?i)(authorization\s*[:=]\s*)[^\r\n]*",
        r"\1<已遮蔽>",
        redacted,
    )


def _collect_outcome(
    outcome: UploadOutcome,
    counts: Dict[str, int],
    secrets: tuple[str, ...],
) -> None:
    counts[outcome.status] += 1
    if outcome.status == "failed":
        error = _redact_error(outcome.error or "未知错误", secrets)
        tqdm.write(f"[失败] {outcome.object_key}: {error}", file=sys.stderr)


def _cancel_pending(pending: Set[Future[UploadOutcome]]) -> None:
    for future in pending:
        future.cancel()


def _is_plain_http(endpoint: str) -> bool:
    return urlparse(endpoint).scheme.lower() == "http"


def run(source: str, workers: int = DEFAULT_WORKERS) -> int:
    """并发上传 source 下的文件，返回固定状态码或取消退出码。"""
    pending: Set[Future[UploadOutcome]] = set()
    executor = None

    try:
        if not 1 <= workers <= MAX_WORKERS:
            raise ValueError(f"--workers 必须在 1 到 {MAX_WORKERS} 之间")

        config = load_s3_upload_config_from_env()
        items = list(discover_upload_items(source))
        if _is_plain_http(config.endpoint):
            print(
                "警告: S3 Endpoint 使用明文 HTTP，凭据签名和数据不会被 TLS 加密",
                file=sys.stderr,
            )
        if not items:
            print(f"路径 {source!r} 下没有文件可上传")
            return 0

        client = create_s3_client(config)
        progress = UploadProgress(sum(item.size for item in items))
        counts = {"done": 0, "skipped": 0, "failed": 0}
        secrets = (
            getattr(config, "access_key_id", ""),
            getattr(config, "secret_access_key", ""),
        )
        executor = ThreadPoolExecutor(max_workers=workers)
        item_iterator = iter(items)
        window = workers * 2
        with tqdm(
            total=progress.total,
            unit="B",
            unit_scale=True,
            desc="已处理字节",
        ) as bar:
            _submit_until_full(
                executor,
                item_iterator,
                pending,
                window,
                client,
                config.bucket,
                progress,
            )
            while pending:
                completed, remaining = wait(
                    pending,
                    timeout=_PROGRESS_REFRESH_SECONDS,
                    return_when=FIRST_COMPLETED,
                )
                pending.clear()
                pending.update(remaining)
                progress.flush(bar)
                for future in completed:
                    _collect_outcome(future.result(), counts, secrets)
                bar.set_postfix(
                    {"完成": counts["done"], "跳过": counts["skipped"], "失败": counts["failed"]},
                    refresh=False,
                )
                _submit_until_full(
                    executor,
                    item_iterator,
                    pending,
                    window,
                    client,
                    config.bucket,
                    progress,
                )
            progress.flush(bar)
    except KeyboardInterrupt:
        _cancel_pending(pending)
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        print("上传已取消，退出码 130", file=sys.stderr)
        return 130
    except BaseException:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        if executor is not None:
            executor.shutdown(wait=True)

    print(
        "上传结束："
        f"完成 {counts['done']},跳过 {counts['skipped']},失败 {counts['failed']}；"
        f"总文件 {len(items)}；"
        f"已处理 {progress.processed_bytes}/{progress.total} 字节"
    )
    return 1 if counts["failed"] else 0


def main(argv: Optional[list[str]] = None) -> int:
    """解析参数并将可预期的配置、源路径错误映射到退出码 2。"""
    parser = argparse.ArgumentParser(description="上传本地文件或文件夹到 S3 兼容桶")
    parser.add_argument("--src", required=True, help="要上传的本地文件或文件夹")
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"文件并发数(默认 {DEFAULT_WORKERS})",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= MAX_WORKERS:
        parser.error(f"--workers 必须在 1 到 {MAX_WORKERS} 之间")
    try:
        return run(args.src, args.workers)
    except (KeyError, ValueError) as exc:
        print(f"配置或源路径错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
