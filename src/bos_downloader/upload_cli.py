"""命令行入口:枚举本地文件夹并多线程上传到 SFTP 服务器。"""

from __future__ import annotations

import argparse
import posixpath
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Tuple

from tqdm import tqdm

from bos_downloader.config import load_sftp_config_from_env
from bos_downloader.local_walker import LocalFile, walk_local_files
from bos_downloader.sftp_client import ThreadLocalSftpPool
from bos_downloader.uploader import upload_file

DEFAULT_WORKERS = 5


def remote_relative_path(rel_path: str, source_folder_name: str) -> str:
    """把本地相对路径映射为「源文件夹名/子路径」的远端相对路径。

    本地 D:/data/myfolder 上传时保留 myfolder 这一级,使远端结构与来源
    同名。rel_path 已是相对 myfolder 根的 POSIX 路径。

    路径安全:拒绝 '..' 段、拒绝以 / 或 \\ 开头(绝对路径)、拒绝含反斜杠,
    镜像下载侧 local_relative_path 的防遍历校验,防止逃出远端基准目录。
    """
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
    lf: LocalFile,
) -> Tuple[str, str]:
    """上传单个文件,返回 (相对路径, 状态)。状态为 skipped/done/failed。

    在工作线程内执行,从 pool 取当前线程独立连接。远端父目录由 upload_file
    内的 ensure_remote_dir 幂等创建。
    """
    rel = remote_relative_path(lf.rel_path, source_folder)
    remote_path = posixpath.join(remote_base, rel)
    try:
        client = pool.get()
        status = upload_file(client, lf.abs_path, remote_path)
        return rel, status
    except Exception as exc:  # noqa: BLE001 - 单文件失败不应中断整体
        tqdm.write(f"[失败] {rel}: {exc}", file=sys.stderr)
        return rel, "failed"


def run(
    source_dir: str,
    remote_base_override: Optional[str] = None,
    workers: int = DEFAULT_WORKERS,
) -> int:
    """枚举本地文件夹下所有文件并多线程上传。返回失败文件数。

    远端保留来源文件夹名:本地 <source_dir> 的最后一级文件夹名作为远端
    根。每个文件作为一个任务提交给线程池;每线程持有独立 SFTP 连接。
    """
    cfg = load_sftp_config_from_env()
    remote_base = remote_base_override or cfg.remote_base
    source_path = Path(source_dir).resolve()
    source_folder = source_path.name

    files = list(walk_local_files(source_path))
    if not files:
        print(f"目录 {source_dir!r} 下没有文件可上传")
        return 0

    workers = max(1, workers)
    pool = ThreadLocalSftpPool(cfg)

    print(f"共 {len(files)} 个文件,{workers} 线程并发上传到 {remote_base} ...")
    failures = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex, tqdm(
            total=len(files), unit="个", desc="总进度",
        ) as bar:
            futures = [
                ex.submit(_upload_one, pool, remote_base, source_folder, lf)
                for lf in files
            ]
            for future in as_completed(futures):
                rel, status = future.result()
                if status == "failed":
                    failures += 1
                elif status == "skipped":
                    tqdm.write(f"[跳过] {rel} 已存在且同大小")
                else:
                    tqdm.write(f"[完成] {rel}")
                bar.update(1)
    finally:
        pool.close_all()

    print(f"上传结束:成功/跳过 {len(files) - failures},失败 {failures}")
    return failures


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="上传本地文件夹到 SFTP 服务器")
    parser.add_argument("--src", required=True, help="要上传的本地文件夹")
    parser.add_argument(
        "--remote-base", default=None, help="覆盖环境变量中的远端基准目录"
    )
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS,
        help=f"并发上传线程数(默认 {DEFAULT_WORKERS})",
    )
    args = parser.parse_args(argv)
    return run(args.src, args.remote_base, args.workers)


if __name__ == "__main__":
    raise SystemExit(main())
