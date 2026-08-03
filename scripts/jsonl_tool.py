"""读取本地磁盘上的 JSONL 文件(每行一个 JSON 对象),支持统计、查看、搜索等操作。

使用:
    uv run python scripts/jsonl_tool.py count data.jsonl                      # 统计总行数
    uv run python scripts/jsonl_tool.py count data.jsonl --filter status=done # 按条件过滤统计
    uv run python scripts/jsonl_tool.py head data.jsonl 10                    # 前 10 行
    uv run python scripts/jsonl_tool.py head data.jsonl 10 --parse-meta
        # 前 10 行,自动展开 JSON 字符串字段
    uv run python scripts/jsonl_tool.py head data.jsonl 10 --parse-meta-keys meta_info,extra
        # 展开指定字段
    uv run python scripts/jsonl_tool.py tail data.jsonl 10                    # 后 10 行
    uv run python scripts/jsonl_tool.py search data.jsonl status=error        # 搜索匹配行
    uv run python scripts/jsonl_tool.py search data.jsonl status=error --max 50  # 限制输出行数
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


def _parse_jsonl_line(line: str) -> Optional[dict]:
    """解析一行 JSON,失败返回 None 并打印警告到 stderr。"""
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        print(f"[jsonl_tool] JSON 解析失败: {exc}", file=sys.stderr)
        return None


def count_lines(
    filepath: Path,
    filter_key: Optional[str] = None,
    filter_value: Optional[str] = None,
    quiet: bool = False,
) -> int:
    """统计 JSONL 文件中的有效行数(跳过无效 JSON 行)。

    可选 filter_key / filter_value 按字段值过滤统计。
    """
    count = 0
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = _parse_jsonl_line(line)
            if obj is None:
                continue
            if filter_key is not None and filter_value is not None:
                if str(obj.get(filter_key, "")) != filter_value:
                    continue
            count += 1
            if not quiet and count % 10_000 == 0:
                print(f"[jsonl_tool] 已扫描 {count:,} 行 ...", file=sys.stderr)
    return count


def _iter_lines(filepath: Path):
    """逐行迭代 JSONL 文件,返回解析后的 dict 列表。"""
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = _parse_jsonl_line(line)
            if obj is not None:
                yield obj


def head_lines(filepath: Path, n: int) -> list[dict]:
    """返回 JSONL 文件的前 N 个有效 JSON 对象。"""
    results = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = _parse_jsonl_line(line)
            if obj is not None:
                results.append(obj)
                if len(results) >= n:
                    break
    return results


def tail_lines(filepath: Path, n: int) -> list[dict]:
    """返回 JSONL 文件的后 N 个有效 JSON 对象。

    注意:需要将整个文件读入内存来获取尾部行;
    对于超大文件可考虑用反向 seek 优化。
    """
    all_objs = list(_iter_lines(filepath))
    return all_objs[-n:] if len(all_objs) >= n else all_objs


def search_lines(
    filepath: Path,
    key: str,
    value: str,
    max_results: int = 0,
) -> list[dict]:
    """搜索 JSONL 文件中匹配字段值的行。max_results=0 表示不限制。"""
    results = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = _parse_jsonl_line(line)
            if obj is None:
                continue
            if str(obj.get(key, "")) == value:
                results.append(obj)
                if max_results > 0 and len(results) >= max_results:
                    break
    return results


def _parse_filter_arg(filter_arg: str) -> tuple[str, str]:
    """解析 'KEY=VALUE' 格式的过滤参数。"""
    if "=" not in filter_arg:
        raise ValueError(f"过滤参数格式错误: {filter_arg!r}, 应为 KEY=VALUE")
    key, _, value = filter_arg.partition("=")
    if not key:
        raise ValueError(f"过滤参数 key 不能为空: {filter_arg!r}")
    return key, value


def parse_meta(obj: dict, keys: Optional[list[str]] = None) -> dict:
    """递归展开对象中的 JSON 字符串字段。

    若 keys 为 None 或空列表,则自动检测并展开所有 JSON 字符串字段;
    若为非空列表则仅展开指定字段。
    解析失败时保留原始字符串值。
    """
    result: dict = {}
    for k, v in obj.items():
        if isinstance(v, str):
            stripped = v.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                # 如果指定了 keys,只展开列表中的字段;否则自动展开
                if not keys or k in keys:
                    try:
                        result[k] = json.loads(stripped)
                        continue
                    except json.JSONDecodeError:
                        pass
        if isinstance(v, dict):
            result[k] = parse_meta(v, keys)
        else:
            result[k] = v
    return result


def parse_meta_keys_arg(raw: str) -> list[str]:
    """解析 --parse-meta-keys 参数:逗号分隔的字段名列表。"""
    return [k.strip() for k in raw.split(",") if k.strip()]


def _resolve_parse_meta(args: argparse.Namespace) -> Optional[list[str]]:
    """根据命令行参数解析 parse_meta 选项。

    返回:
        None — 不展开任何字段
        [] — 自动展开所有 JSON 字符串字段 (--parse-meta)
        [key1, key2] — 仅展开指定字段 (--parse-meta-keys)
    """
    if getattr(args, "parse_meta_keys", None):
        return parse_meta_keys_arg(args.parse_meta_keys)
    if getattr(args, "parse_meta", False):
        return []
    return None


def _print_results(results: list[dict], parse_meta_keys: Optional[list[str]] = None) -> None:
    """美化输出 JSON 对象列表,对象之间以空行分隔。

    若 parse_meta_keys 为 [] 表示自动展开所有 JSON 字符串字段;
    若为非空列表则仅展开指定字段。
    """
    for i, obj in enumerate(results):
        if i > 0:
            print()
        if parse_meta_keys is not None:
            obj = parse_meta(obj, parse_meta_keys if parse_meta_keys else None)
        print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="读取本地磁盘上的 JSONL 文件,支持统计、查看、搜索",
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # count
    count_parser = subparsers.add_parser("count", help="统计 JSONL 文件行数")
    count_parser.add_argument("file", help="JSONL 文件路径")
    count_parser.add_argument(
        "--filter",
        dest="filter_arg",
        metavar="KEY=VALUE",
        default=None,
        help="按字段值过滤统计(如 status=done)",
    )
    count_parser.add_argument(
        "--quiet",
        action="store_true",
        help="安静模式,不打印进度",
    )

    # head
    head_parser = subparsers.add_parser("head", help="查看前 N 行")
    head_parser.add_argument("file", help="JSONL 文件路径")
    head_parser.add_argument("n", type=int, help="查看行数")
    head_parser.add_argument(
        "--parse-meta",
        action="store_true",
        default=False,
        help="自动展开所有 JSON 字符串字段",
    )
    head_parser.add_argument(
        "--parse-meta-keys",
        metavar="KEY1,KEY2",
        default=None,
        help="仅展开指定字段(逗号分隔,如 meta_info,extra)",
    )

    # tail
    tail_parser = subparsers.add_parser("tail", help="查看后 N 行")
    tail_parser.add_argument("file", help="JSONL 文件路径")
    tail_parser.add_argument("n", type=int, help="查看行数")
    tail_parser.add_argument(
        "--parse-meta",
        action="store_true",
        default=False,
        help="自动展开所有 JSON 字符串字段",
    )
    tail_parser.add_argument(
        "--parse-meta-keys",
        metavar="KEY1,KEY2",
        default=None,
        help="仅展开指定字段(逗号分隔,如 meta_info,extra)",
    )

    # search
    search_parser = subparsers.add_parser("search", help="搜索匹配字段的行")
    search_parser.add_argument("file", help="JSONL 文件路径")
    search_parser.add_argument(
        "filter_arg",
        metavar="KEY=VALUE",
        help="搜索条件(如 status=error)",
    )
    search_parser.add_argument(
        "--max",
        dest="max_results",
        type=int,
        default=0,
        help="最大输出行数(0 表示不限制)",
    )
    search_parser.add_argument(
        "--parse-meta",
        action="store_true",
        default=False,
        help="自动展开所有 JSON 字符串字段",
    )
    search_parser.add_argument(
        "--parse-meta-keys",
        metavar="KEY1,KEY2",
        default=None,
        help="仅展开指定字段(逗号分隔,如 meta_info,extra)",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    filepath = Path(args.file)
    if not filepath.exists():
        print(f"[jsonl_tool] 文件不存在: {args.file}", file=sys.stderr)
        return 1
    if not filepath.is_file():
        print(f"[jsonl_tool] 不是文件: {args.file}", file=sys.stderr)
        return 1

    if args.command == "count":
        filter_key = None
        filter_value = None
        if args.filter_arg:
            filter_key, filter_value = _parse_filter_arg(args.filter_arg)
        total = count_lines(filepath, filter_key, filter_value, quiet=args.quiet)
        if filter_key:
            print(f"文件: {filepath}")
            print(f"过滤条件: {filter_key}={filter_value}")
            print(f"匹配行数: {total:,}")
        else:
            print(f"文件: {filepath}")
            print(f"总行数: {total:,}")

    elif args.command == "head":
        results = head_lines(filepath, args.n)
        _print_results(results, _resolve_parse_meta(args))
        print(f"\n--- 显示了 {len(results)} 行 ---", file=sys.stderr)

    elif args.command == "tail":
        results = tail_lines(filepath, args.n)
        _print_results(results, _resolve_parse_meta(args))
        print(f"\n--- 显示了 {len(results)} 行 ---", file=sys.stderr)

    elif args.command == "search":
        filter_key, filter_value = _parse_filter_arg(args.filter_arg)
        results = search_lines(filepath, filter_key, filter_value, args.max_results)
        if results:
            _print_results(results, _resolve_parse_meta(args))
            print(
                f"\n--- 找到 {len(results)} 条匹配结果 ---", file=sys.stderr,
            )
        else:
            print(f"未找到匹配 {filter_key}={filter_value!r} 的行")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
