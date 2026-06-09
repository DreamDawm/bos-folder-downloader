"""命令行入口:枚举并多线程下载 BOS 文件夹内全部文件。"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from bos_downloader.client import create_bos_client
from bos_downloader.config import load_config_from_env
from bos_downloader.downloader import download_object
from bos_downloader.lister import list_objects_under_prefix

DEFAULT_WORKERS = 1


def local_relative_path(key: str, prefix: str) -> str:
    """把远端 key 转成本地相对路径,保留来源文件夹名。

    从 prefix 下载的文件会落在 "prefix 最后一级文件夹/..." 下,而非直接
    铺到目标根目录。例如 prefix="a/b/data/" 时 key "a/b/data/x.txt" 映射为
    "data/x.txt"。空 prefix(整桶下载)则保留完整 key。

    key 必须位于 prefix 之下,否则抛 ValueError;
    并拒绝包含 '..'、绝对路径或反斜杠的 key,防止写出目标目录。
    """
    norm_prefix = prefix if prefix.endswith("/") or prefix == "" else prefix + "/"
    if norm_prefix and not key.startswith(norm_prefix):
        raise ValueError(f"key {key!r} 不在 prefix {prefix!r} 之下")
    rel = key[len(norm_prefix):]
    parts = rel.split("/")
    if ".." in parts:
        raise ValueError(f"key {key!r} 含非法的 '..' 路径段")
    # 拒绝绝对路径:rel 以 / 或 \ 开头,或含 Windows 反斜杠段,
    # 否则 dest_root / rel 会丢弃 dest_root 写到目录之外
    if rel.startswith("/") or rel.startswith("\\"):
        raise ValueError(f"key {key!r} 解析出绝对路径段")
    if "\\" in rel:
        raise ValueError(f"key {key!r} 含非法的反斜杠路径段")
    # 保留 prefix 的最后一级文件夹名作为本地根
    base_folder = norm_prefix.rstrip("/").split("/")[-1]
    if base_folder:
        return f"{base_folder}/{rel}"
    return rel


def _download_one(client, bucket: str, obj, prefix: str, dest_root: Path) -> tuple[str, str]:
    """下载单个对象,返回 (相对路径, 状态)。状态为 skipped/done/failed。

    在工作线程内执行。每个文件有独立的 dest 与 .part 临时文件,
    任务队列中每个 key 仅出现一次,因此不会有两个线程下载同一文件。
    """
    rel = local_relative_path(obj.key, prefix)
    dest = dest_root / rel
    if dest.exists():
        return rel, "skipped"
    try:
        download_object(client, bucket, obj.key, dest)
        return rel, "done"
    except Exception as exc:  # noqa: BLE001 - 单文件失败不应中断整体
        tqdm.write(f"[失败] {rel}: {exc}", file=sys.stderr)
        return rel, "failed"


def run(
    prefix: str,
    dest_dir: str,
    bucket_override: str | None = None,
    workers: int = DEFAULT_WORKERS,
) -> int:
    """枚举 prefix 下所有文件并多线程下载。返回失败文件数。

    每个文件作为一个任务提交给线程池;文件级断点续传逻辑在
    download_object 中,任务粒度保证同一文件不会被多个线程并发下载。
    """
    cfg = load_config_from_env()
    bucket = bucket_override or cfg.bucket
    client = create_bos_client(cfg)
    dest_root = Path(dest_dir)

    objects = list(list_objects_under_prefix(client, bucket, prefix))
    if not objects:
        print(f"prefix {prefix!r} 下没有文件可下载")
        return 0

    workers = max(1, workers)
    print(f"共 {len(objects)} 个文件,{workers} 线程并发下载到 {dest_root} ...")
    failures = 0
    with ThreadPoolExecutor(max_workers=workers) as pool, tqdm(
        total=len(objects), unit="个", desc="总进度",
    ) as bar:
        futures = [
            pool.submit(_download_one, client, bucket, obj, prefix, dest_root)
            for obj in objects
        ]
        for future in as_completed(futures):
            rel, status = future.result()
            if status == "failed":
                failures += 1
            elif status == "skipped":
                tqdm.write(f"[跳过] {rel} 已存在")
            else:
                tqdm.write(f"[完成] {rel}")
            bar.update(1)

    print(f"下载结束:成功/跳过 {len(objects) - failures},失败 {failures}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载百度 BOS 文件夹内全部文件")
    parser.add_argument("--prefix", required=True, help="要下载的文件夹前缀,如 data/")
    parser.add_argument("--dest", required=True, help="本地目标目录")
    parser.add_argument("--bucket", default=None, help="覆盖环境变量中的桶名")
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS,
        help=f"并发下载线程数(默认 {DEFAULT_WORKERS})",
    )
    args = parser.parse_args(argv)
    return run(args.prefix, args.dest, args.bucket, args.workers)


if __name__ == "__main__":
    raise SystemExit(main())
