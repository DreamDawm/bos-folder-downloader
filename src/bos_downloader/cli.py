"""命令行入口:枚举并下载 BOS 文件夹内全部文件。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tqdm import tqdm

from bos_downloader.client import create_bos_client
from bos_downloader.config import load_config_from_env
from bos_downloader.downloader import download_object
from bos_downloader.lister import list_objects_under_prefix


def local_relative_path(key: str, prefix: str) -> str:
    """把远端 key 转成相对 prefix 的本地相对路径。

    key 必须位于 prefix 之下,否则抛 ValueError;
    并拒绝包含 '..' 段的 key,防止写出目标目录。
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
    return rel


def run(prefix: str, dest_dir: str, bucket_override: str | None = None) -> int:
    """枚举 prefix 下所有文件并逐个下载。返回失败文件数。"""
    cfg = load_config_from_env()
    bucket = bucket_override or cfg.bucket
    client = create_bos_client(cfg)
    dest_root = Path(dest_dir)

    objects = list(list_objects_under_prefix(client, bucket, prefix))
    if not objects:
        print(f"prefix {prefix!r} 下没有文件可下载")
        return 0

    print(f"共 {len(objects)} 个文件,开始下载到 {dest_root} ...")
    failures = 0
    for obj in objects:
        rel = local_relative_path(obj.key, prefix)
        dest = dest_root / rel
        if dest.exists():
            print(f"[跳过] {rel} 已存在")
            continue
        try:
            with tqdm(
                total=obj.size, unit="B", unit_scale=True,
                desc=rel, leave=False,
            ) as bar:
                last = {"n": 0}

                def cb(done: int, total: int, _last=last, _bar=bar):
                    _bar.update(done - _last["n"])
                    _last["n"] = done

                download_object(client, bucket, obj.key, dest, progress_callback=cb)
            print(f"[完成] {rel}")
        except Exception as exc:  # noqa: BLE001 - 单文件失败不应中断整体
            failures += 1
            print(f"[失败] {rel}: {exc}", file=sys.stderr)

    print(f"下载结束:成功/跳过 {len(objects) - failures},失败 {failures}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载百度 BOS 文件夹内全部文件")
    parser.add_argument("--prefix", required=True, help="要下载的文件夹前缀,如 data/")
    parser.add_argument("--dest", required=True, help="本地目标目录")
    parser.add_argument("--bucket", default=None, help="覆盖环境变量中的桶名")
    args = parser.parse_args(argv)
    return run(args.prefix, args.dest, args.bucket)


if __name__ == "__main__":
    raise SystemExit(main())
