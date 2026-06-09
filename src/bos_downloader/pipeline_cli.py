"""命令行入口 bos-sync:逐子文件夹下载→上传→删除,数量写入 logs。"""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Optional

from bos_downloader.pipeline import run


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="逐子文件夹流水线:下载 BOS → SFTP 上传 → 删除本地"
    )
    parser.add_argument("--prefix", required=True, help="要处理的文件夹前缀,如 data/")
    parser.add_argument("--dest", required=True, help="本地临时落盘目录")
    parser.add_argument("--bucket", default=None, help="覆盖环境变量中的桶名")
    parser.add_argument(
        "--remote-base", default=None, help="覆盖环境变量中的远端基准目录"
    )
    parser.add_argument("--logs-dir", default="logs", help="日志目录(默认 logs)")
    parser.add_argument(
        "--dl-workers", type=int, default=1, help="组内下载并发数(默认 1)"
    )
    parser.add_argument(
        "--ul-workers", type=int, default=5, help="组内上传并发数(默认 5)"
    )
    args = parser.parse_args(argv)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return run(
        args.prefix, args.dest,
        bucket_override=args.bucket,
        remote_base_override=args.remote_base,
        logs_dir=args.logs_dir, stamp=stamp,
        dl_workers=args.dl_workers, ul_workers=args.ul_workers,
    )


if __name__ == "__main__":
    raise SystemExit(main())
