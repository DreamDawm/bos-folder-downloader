"""统计 BOS 桶(或指定 prefix)下的文件数量。

使用:
    uv run python scripts/count_objects.py                  # 统计 .env 里 BOS_BUCKET 整桶
    uv run python scripts/count_objects.py data/2024/       # 只统计 prefix = data/2024/ 下

读取凭证:项目根目录的 .env(BOS_ACCESS_KEY_ID / BOS_SECRET_ACCESS_KEY / BOS_ENDPOINT / BOS_BUCKET)。
伪目录占位对象(以 '/' 结尾的 key)会被跳过,只计数真实文件。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 把 src 加入 sys.path,使脚本可以直接 `uv run python scripts/...` 调用,
# 而不依赖项目以开发模式安装到当前解释器。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from bos_downloader.client import create_bos_client  # noqa: E402
from bos_downloader.config import load_config_from_env  # noqa: E402


def count_objects(
    client,
    bucket: str,
    prefix: str,
    show_progress: bool = True,
) -> int:
    """枚举 bucket 中 prefix 下的真实文件并返回总数。

    client 由调用方构造并传入,便于测试注入 Fake 与避免重复加载配置。
    """
    count = 0
    for item in client.list_all_objects(bucket, prefix=prefix or None):
        # 跳过伪目录占位对象(与 lister.py 行为一致)
        if item.key.endswith("/"):
            continue
        count += 1
        if show_progress and count % 10_000 == 0:
            # 进度提示到 stderr,不污染 stdout 的纯数字输出
            print(f"[count_objects] 已枚举 {count:,} 个对象 ...", file=sys.stderr)
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="统计 BOS 桶或指定 prefix 下的文件数量",
    )
    parser.add_argument(
        "prefix",
        nargs="?",
        default="",
        help="要统计的 prefix(例如 'data/2024/');留空则统计整桶",
    )
    parser.add_argument(
        "--bucket",
        default=None,
        help="覆盖 .env 中的 BOS_BUCKET;缺省使用 .env 中的值",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="安静模式,只输出最终数字,不打印进度",
    )
    args = parser.parse_args(argv)

    cfg = load_config_from_env()
    client = create_bos_client(cfg)
    bucket = args.bucket or cfg.bucket
    prefix = args.prefix

    if prefix and not prefix.endswith("/"):
        # 提示用户:prefix 通常以 '/' 结尾才是"文件夹"语义,否则会把 'data' 匹配到 'data_backup'
        print(
            f"[count_objects] 提示:prefix '{prefix}' 未以 '/' 结尾,将按字面前缀匹配",
            file=sys.stderr,
        )

    total = count_objects(client, bucket, prefix, show_progress=not args.quiet)

    # 结果输出到 stdout,便于管道消费
    if prefix:
        print(f"桶: {bucket}")
        print(f"prefix: {prefix}")
        print(f"文件数量: {total:,}")
    else:
        print(f"桶: {bucket}")
        print(f"文件数量: {total:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
