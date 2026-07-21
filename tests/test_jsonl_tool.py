"""测试 scripts/jsonl_tool.py 的各个功能。"""

import sys
import tempfile
from pathlib import Path

import pytest

# 通过 sys.path 引入 scripts 目录下的 jsonl_tool

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import jsonl_tool  # noqa: E402,I001


# ─── 辅助函数 ────────────────────────────────────────────────


def _make_jsonl(lines: list[str]) -> Path:
    """将字符串列表写入临时 JSONL 文件并返回路径。"""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8",
    )
    for line in lines:
        tmp.write(line + "\n")
    tmp.close()
    return Path(tmp.name)


# ▼▼▼ count_lines 测试 ▼▼▼


def test_count_lines_basic():
    path = _make_jsonl([
        '{"id": 1, "name": "Alice"}',
        '{"id": 2, "name": "Bob"}',
        '{"id": 3, "name": "Charlie"}',
    ])
    try:
        assert jsonl_tool.count_lines(path, quiet=True) == 3
    finally:
        path.unlink(missing_ok=True)


def test_count_lines_skips_invalid_json():
    path = _make_jsonl([
        '{"id": 1}',
        "not a json line",
        '{"id": 2}',
        "",
        '{"id": 3}',
    ])
    try:
        assert jsonl_tool.count_lines(path, quiet=True) == 3
    finally:
        path.unlink(missing_ok=True)


def test_count_lines_with_filter():
    path = _make_jsonl([
        '{"id": 1, "status": "done"}',
        '{"id": 2, "status": "pending"}',
        '{"id": 3, "status": "done"}',
        '{"id": 4, "status": "error"}',
    ])
    try:
        assert jsonl_tool.count_lines(path, "status", "done", quiet=True) == 2
    finally:
        path.unlink(missing_ok=True)


def test_count_lines_filter_no_match():
    path = _make_jsonl([
        '{"id": 1, "status": "done"}',
    ])
    try:
        assert jsonl_tool.count_lines(path, "status", "missing", quiet=True) == 0
    finally:
        path.unlink(missing_ok=True)


# ▼▼▼ head_lines 测试 ▼▼▼


def test_head_lines_basic():
    path = _make_jsonl([
        '{"id": 1}', '{"id": 2}', '{"id": 3}', '{"id": 4}', '{"id": 5}',
    ])
    try:
        result = jsonl_tool.head_lines(path, 3)
        assert len(result) == 3
        assert result[0] == {"id": 1}
        assert result[1] == {"id": 2}
        assert result[2] == {"id": 3}
    finally:
        path.unlink(missing_ok=True)


def test_head_lines_more_than_total():
    path = _make_jsonl([
        '{"id": 1}', '{"id": 2}',
    ])
    try:
        result = jsonl_tool.head_lines(path, 10)
        assert len(result) == 2
    finally:
        path.unlink(missing_ok=True)


def test_head_lines_skips_invalid():
    path = _make_jsonl([
        "bad line", '{"id": 1}', "another bad", '{"id": 2}',
    ])
    try:
        result = jsonl_tool.head_lines(path, 2)
        assert len(result) == 2
        assert result[0] == {"id": 1}
        assert result[1] == {"id": 2}
    finally:
        path.unlink(missing_ok=True)


# ▼▼▼ tail_lines 测试 ▼▼▼


def test_tail_lines_basic():
    path = _make_jsonl([
        '{"id": 1}', '{"id": 2}', '{"id": 3}', '{"id": 4}', '{"id": 5}',
    ])
    try:
        result = jsonl_tool.tail_lines(path, 2)
        assert len(result) == 2
        assert result[0] == {"id": 4}
        assert result[1] == {"id": 5}
    finally:
        path.unlink(missing_ok=True)


def test_tail_lines_more_than_total():
    path = _make_jsonl([
        '{"id": 1}', '{"id": 2}',
    ])
    try:
        result = jsonl_tool.tail_lines(path, 10)
        assert len(result) == 2
    finally:
        path.unlink(missing_ok=True)


# ▼▼▼ search_lines 测试 ▼▼▼


def test_search_lines_basic():
    path = _make_jsonl([
        '{"id": 1, "status": "ok"}',
        '{"id": 2, "status": "error"}',
        '{"id": 3, "status": "ok"}',
        '{"id": 4, "status": "error"}',
    ])
    try:
        result = jsonl_tool.search_lines(path, "status", "error")
        assert len(result) == 2
        assert result[0]["id"] == 2
        assert result[1]["id"] == 4
    finally:
        path.unlink(missing_ok=True)


def test_search_lines_with_max():
    path = _make_jsonl([
        '{"status": "ok"}',
        '{"status": "ok"}',
        '{"status": "ok"}',
    ])
    try:
        result = jsonl_tool.search_lines(path, "status", "ok", max_results=2)
        assert len(result) == 2
    finally:
        path.unlink(missing_ok=True)


def test_search_lines_no_match():
    path = _make_jsonl([
        '{"status": "ok"}',
    ])
    try:
        result = jsonl_tool.search_lines(path, "status", "missing")
        assert len(result) == 0
    finally:
        path.unlink(missing_ok=True)


def test_search_lines_missing_key():
    path = _make_jsonl([
        '{"name": "Alice"}',
    ])
    try:
        result = jsonl_tool.search_lines(path, "status", "ok")
        # key 不存在时 obj.get("status", "") 返回空字符串,不匹配 "ok"
        assert len(result) == 0
    finally:
        path.unlink(missing_ok=True)


# ▼▼▼ _parse_filter_arg 测试 ▼▼▼


def test_parse_filter_arg_valid():
    assert jsonl_tool._parse_filter_arg("status=done") == ("status", "done")
    assert jsonl_tool._parse_filter_arg("key=value=extra") == ("key", "value=extra")


def test_parse_filter_arg_no_equals():
    with pytest.raises(ValueError, match="KEY=VALUE"):
        jsonl_tool._parse_filter_arg("no_equals")


def test_parse_filter_arg_empty_key():
    with pytest.raises(ValueError, match="key 不能为空"):
        jsonl_tool._parse_filter_arg("=value")


# ▼▼▼ main() 集成测试 ▼▼▼


def test_main_count():
    path = _make_jsonl(['{"x": 1}', '{"x": 2}', '{"x": 3}'])
    try:
        result = jsonl_tool.main(["count", str(path), "--quiet"])
        assert result == 0
    finally:
        path.unlink(missing_ok=True)


def test_main_count_with_filter():
    path = _make_jsonl([
        '{"status": "done"}',
        '{"status": "pending"}',
        '{"status": "done"}',
    ])
    try:
        result = jsonl_tool.main(["count", str(path), "--filter", "status=done", "--quiet"])
        assert result == 0
    finally:
        path.unlink(missing_ok=True)


def test_main_head():
    path = _make_jsonl(['{"id": 1}', '{"id": 2}'])
    try:
        result = jsonl_tool.main(["head", str(path), "1"])
        assert result == 0
    finally:
        path.unlink(missing_ok=True)


def test_main_tail():
    path = _make_jsonl(['{"id": 1}', '{"id": 2}'])
    try:
        result = jsonl_tool.main(["tail", str(path), "1"])
        assert result == 0
    finally:
        path.unlink(missing_ok=True)


def test_main_search():
    path = _make_jsonl([
        '{"status": "ok"}',
        '{"status": "error"}',
    ])
    try:
        result = jsonl_tool.main(["search", str(path), "status=error"])
        assert result == 0
    finally:
        path.unlink(missing_ok=True)


def test_main_file_not_found():
    result = jsonl_tool.main(["count", "/nonexistent/file.jsonl", "--quiet"])
    assert result == 1


def test_main_no_command():
    result = jsonl_tool.main([])
    assert result == 1


# ▼▼▼ parse_meta 测试 ▼▼▼


def test_parse_meta_auto_detect():
    obj = {
        "doi": "10.1234/foo",
        "meta_info": '{"title": "Hello", "year": 2020}',
        "tags": ["a", "b"],
    }
    result = jsonl_tool.parse_meta(obj)
    assert result["doi"] == "10.1234/foo"
    assert result["meta_info"] == {"title": "Hello", "year": 2020}
    assert result["tags"] == ["a", "b"]


def test_parse_meta_specific_keys():
    obj = {
        "meta_info": '{"title": "Hello"}',
        "extra": '{"count": 5}',
        "not_json": "plain text",
    }
    result = jsonl_tool.parse_meta(obj, keys=["meta_info"])
    assert result["meta_info"] == {"title": "Hello"}
    assert result["extra"] == '{"count": 5}'  # 不在 keys 中,未展开
    assert result["not_json"] == "plain text"


def test_parse_meta_invalid_json_kept():
    obj = {"meta_info": "not valid json { really"}
    result = jsonl_tool.parse_meta(obj)
    assert result["meta_info"] == "not valid json { really"


def test_parse_meta_auto_detect_empty_keys():
    """keys=[] 等同于 keys=None,自动展开所有 JSON 字符串。"""
    obj = {"meta_info": '{"a": 1}'}
    result = jsonl_tool.parse_meta(obj, keys=[])
    assert result["meta_info"] == {"a": 1}


def test_parse_meta_auto_detect_array():
    obj = {"urls": '["a", "b", "c"]'}
    result = jsonl_tool.parse_meta(obj)
    assert result["urls"] == ["a", "b", "c"]


def test_parse_meta_nested():
    """嵌套 dict 中的 JSON 字符串也会被展开。"""
    obj = {
        "outer": {
            "inner_meta": '{"x": 1}',
        },
    }
    result = jsonl_tool.parse_meta(obj)
    assert result["outer"]["inner_meta"] == {"x": 1}


def test_parse_meta_keys_arg():
    assert jsonl_tool.parse_meta_keys_arg("a,b,c") == ["a", "b", "c"]
    assert jsonl_tool.parse_meta_keys_arg("  meta_info , extra ") == ["meta_info", "extra"]
    assert jsonl_tool.parse_meta_keys_arg("") == []
    assert jsonl_tool.parse_meta_keys_arg("single") == ["single"]


# ▼▼▼ main() --parse-meta 集成测试 ▼▼▼


def test_main_head_parse_meta():
    path = _make_jsonl([
        '{"id": 1, "meta": "{\\"title\\": \\"Hello\\"}"}',
    ])
    try:
        result = jsonl_tool.main(["head", str(path), "1", "--parse-meta"])
        assert result == 0
    finally:
        path.unlink(missing_ok=True)


def test_main_head_parse_meta_keys():
    path = _make_jsonl([
        '{"id": 1, "meta": "{\\"title\\": \\"Hello\\"}", "extra": "{\\"x\\": 2}"}',
    ])
    try:
        result = jsonl_tool.main([
            "head", str(path), "1", "--parse-meta-keys", "meta",
        ])
        assert result == 0
    finally:
        path.unlink(missing_ok=True)


def test_main_search_parse_meta():
    path = _make_jsonl([
        '{"status": "ok", "meta": "{\\"title\\": \\"A\\"}"}',
        '{"status": "error", "meta": "{\\"title\\": \\"B\\"}"}',
    ])
    try:
        result = jsonl_tool.main([
            "search", str(path), "status=ok", "--parse-meta",
        ])
        assert result == 0
    finally:
        path.unlink(missing_ok=True)


def test_main_tail_parse_meta():
    path = _make_jsonl([
        '{"id": 1, "meta": "{\\"title\\": \\"X\\"}"}',
    ])
    try:
        result = jsonl_tool.main(["tail", str(path), "1", "--parse-meta"])
        assert result == 0
    finally:
        path.unlink(missing_ok=True)
