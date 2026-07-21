"""命令行入口：枚举本地文件夹并多线程上传到 SFTP 服务器。"""

from __future__ import annotations

import argparse
import posixpath
import sys
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional

import paramiko
from tqdm import tqdm

from bos_downloader.config import load_sftp_config_from_env
from bos_downloader.local_walker import LocalFile, walk_local_files
from bos_downloader.sftp_client import SftpPoolClosedError, ThreadLocalSftpPool
from bos_downloader.upload_cancellation import UploadCancellation, UploadCancelledError
from bos_downloader.upload_progress import UploadProgress
from bos_downloader.uploader import RemoteDirectoryCache, upload_file

DEFAULT_WORKERS = 15
MAX_WORKERS = 64
_PROGRESS_REFRESH_SECONDS = 0.1
_CANCELLED_EXCEPTIONS = (
    UploadCancelledError,
    SftpPoolClosedError,
    EOFError,
    ConnectionError,
    TimeoutError,
    paramiko.SSHException,
)


@dataclass(frozen=True)
class UploadOutcome:
    """单个文件的上传结果。"""

    rel_path: str
    status: str
    error: Optional[str] = None


def remote_relative_path(rel_path: str, source_folder_name: str) -> str:
    """把本地相对路径映射为「源文件夹名/子路径」的远端相对路径。"""
    if rel_path.startswith("/") or rel_path.startswith("\\"):
        raise ValueError(f"相对路径 {rel_path!r} 是绝对路径段")
    if "\\" in rel_path:
        raise ValueError(f"相对路径 {rel_path!r} 含非法的反斜杠段")
    parts = rel_path.split("/")
    if ".." in parts:
        raise ValueError(f"相对路径 {rel_path!r} 含非法的 '..' 路径段")
    if source_folder_name:
        return f"{source_folder_name}/{rel_path}"
    return rel_path


def _upload_one(
    pool: ThreadLocalSftpPool,
    remote_base: str,
    source_folder: str,
    directory_cache: RemoteDirectoryCache,
    progress: UploadProgress,
    cancellation: UploadCancellation,
    local_file: LocalFile,
) -> UploadOutcome:
    rel_path = remote_relative_path(local_file.rel_path, source_folder)
    remote_path = posixpath.join(remote_base, rel_path)
    callback = progress.callback_for(rel_path, local_file.size)
    try:
        cancellation.raise_if_cancelled()
        client = pool.get()
        cancellation.raise_if_cancelled()
        status = upload_file(
            client,
            local_file.abs_path,
            remote_path,
            progress_callback=callback,
            directory_cache=directory_cache,
            expected_size=local_file.size,
        )
        return UploadOutcome(rel_path, status)
    except _CANCELLED_EXCEPTIONS as exc:
        if cancellation.is_cancelled:
            return UploadOutcome(rel_path, "cancelled")
        return UploadOutcome(rel_path, "failed", str(exc))
    except Exception as exc:  # noqa: BLE001 - 单文件失败不应中断整体
        return UploadOutcome(rel_path, "failed", str(exc))


def _collect_outcome(
    outcome: UploadOutcome, counts: Dict[str, int], progress: UploadProgress
) -> None:
    counts[outcome.status] += 1
    if outcome.status != "failed":
        return
    message = f"[失败] {outcome.rel_path}: {outcome.error}"
    try:
        tqdm.write(message, file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - UI 故障不能中断上传
        progress.record_ui_error(exc)
        print(message, file=sys.stderr)


def _update_postfix(bar, counts: Dict[str, int], progress: UploadProgress) -> None:
    try:
        bar.set_postfix(
            {
                "完成": counts["done"],
                "跳过": counts["skipped"],
                "失败": counts["failed"],
                "取消": counts["cancelled"],
            },
            refresh=False,
        )
    except Exception as exc:  # noqa: BLE001 - UI 故障不能中断上传
        progress.record_ui_error(exc)


def _submit_next(
    executor: ThreadPoolExecutor,
    files: Iterator[LocalFile],
    pending: set[Future[UploadOutcome]],
    limit: int,
    pool: ThreadLocalSftpPool,
    remote_base: str,
    source_folder: str,
    directory_cache: RemoteDirectoryCache,
    progress: UploadProgress,
    cancellation: UploadCancellation,
) -> None:
    while len(pending) < limit:
        try:
            local_file = next(files)
        except StopIteration:
            return
        pending.add(
            executor.submit(
                _upload_one,
                pool,
                remote_base,
                source_folder,
                directory_cache,
                progress,
                cancellation,
                local_file,
            )
        )


def _run_futures(
    executor: ThreadPoolExecutor,
    files: Iterable[LocalFile],
    workers: int,
    pool: ThreadLocalSftpPool,
    remote_base: str,
    source_folder: str,
    directory_cache: RemoteDirectoryCache,
    progress: UploadProgress,
    bar,
    cancellation: UploadCancellation,
) -> Dict[str, int]:
    counts = {"done": 0, "skipped": 0, "failed": 0, "cancelled": 0}
    file_iterator = iter(files)
    pending: set[Future[UploadOutcome]] = set()
    window = max(1, workers * 2)
    _submit_next(
        executor,
        file_iterator,
        pending,
        window,
        pool,
        remote_base,
        source_folder,
        directory_cache,
        progress,
        cancellation,
    )
    while pending:
        completed, pending = wait(
            pending,
            timeout=_PROGRESS_REFRESH_SECONDS,
            return_when=FIRST_COMPLETED,
        )
        progress.flush(bar)
        for future in completed:
            _collect_outcome(future.result(), counts, progress)
        _update_postfix(bar, counts, progress)
        _submit_next(
            executor,
            file_iterator,
            pending,
            window,
            pool,
            remote_base,
            source_folder,
            directory_cache,
            progress,
            cancellation,
        )
    progress.flush(bar)
    return counts


def run(
    source_dir: str,
    remote_base_override: Optional[str] = None,
    workers: int = DEFAULT_WORKERS,
) -> int:
    """枚举本地文件夹下所有文件并多线程上传，返回失败文件数。"""
    cfg = load_sftp_config_from_env()
    remote_base = remote_base_override or cfg.remote_base
    source_path = Path(source_dir).resolve()
    source_folder = source_path.name
    files = list(walk_local_files(source_path))
    if not files:
        print(f"目录 {source_dir!r} 下没有文件可上传")
        return 0

    workers = max(1, workers)
    total_bytes = sum(local_file.size for local_file in files)
    pool = ThreadLocalSftpPool(cfg)
    directory_cache = RemoteDirectoryCache()
    progress = UploadProgress(total_bytes)
    cancellation = UploadCancellation()
    print(f"共 {len(files)} 个文件，{workers} 线程并发上传到 {remote_base} ...")

    try:
        with (
            ThreadPoolExecutor(max_workers=workers) as executor,
            tqdm(
                total=total_bytes,
                unit="B",
                unit_scale=True,
                desc="已处理字节",
            ) as bar,
        ):
            counts = _run_futures(
                executor,
                files,
                workers,
                pool,
                remote_base,
                source_folder,
                directory_cache,
                progress,
                bar,
                cancellation,
            )
    finally:
        pool.close_all()

    if progress.ui_error is not None:
        print(f"警告：进度显示异常：{progress.ui_error}", file=sys.stderr)
    print(
        "上传结束："
        f"完成 {counts['done']},跳过 {counts['skipped']},失败 {counts['failed']},"
        f"取消 {counts['cancelled']}；"
        f"已处理 {progress.processed_bytes}/{total_bytes} 字节"
    )
    return counts["failed"]


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="上传本地文件夹到 SFTP 服务器")
    parser.add_argument("--src", required=True, help="要上传的本地文件夹")
    parser.add_argument("--remote-base", default=None, help="覆盖环境变量中的远端基准目录")
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"并发上传线程数(默认 {DEFAULT_WORKERS})",
    )
    args = parser.parse_args(argv)
    if args.workers > MAX_WORKERS:
        parser.error(f"--workers 不能超过 {MAX_WORKERS}")
    return run(args.src, args.remote_base, args.workers)


if __name__ == "__main__":
    raise SystemExit(main())
