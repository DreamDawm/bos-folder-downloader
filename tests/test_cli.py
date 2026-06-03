import pytest

from bos_downloader.cli import local_relative_path


def test_local_relative_path_keeps_prefix_folder():
    # 保留来源文件夹名:从 data/ 下载应落在 data/ 下,而非直接铺到根目录
    assert local_relative_path("data/sub/b.bin", "data/") == "data/sub/b.bin"


def test_local_relative_path_prefix_without_trailing_slash():
    assert local_relative_path("data/a.txt", "data") == "data/a.txt"


def test_local_relative_path_multilevel_prefix_keeps_last_folder():
    # 多级前缀只保留下载的那一级文件夹,不带上层路径
    assert local_relative_path("a/b/data/x.txt", "a/b/data/") == "data/x.txt"


def test_local_relative_path_empty_prefix_keeps_full_key():
    # 整桶下载(空前缀)时保留完整 key
    assert local_relative_path("top/x.txt", "") == "top/x.txt"


def test_local_relative_path_rejects_key_not_under_prefix():
    with pytest.raises(ValueError):
        local_relative_path("other/x.txt", "data/")


def test_local_relative_path_rejects_parent_traversal():
    with pytest.raises(ValueError):
        local_relative_path("data/../../etc/passwd", "data/")


def test_local_relative_path_rejects_absolute_remainder():
    # key 去掉 prefix 后以 / 开头,会让 dest_root / rel 丢弃 dest_root
    with pytest.raises(ValueError):
        local_relative_path("data//etc/passwd", "data/")


def test_local_relative_path_rejects_backslash_absolute():
    # Windows 反斜杠绝对路径段也应拒绝
    with pytest.raises(ValueError):
        local_relative_path("data/\\\\server\\share", "data/")
