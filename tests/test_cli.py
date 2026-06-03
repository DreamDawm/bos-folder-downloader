import pytest

from bos_downloader.cli import local_relative_path


def test_local_relative_path_strips_prefix():
    assert local_relative_path("data/sub/b.bin", "data/") == "sub/b.bin"


def test_local_relative_path_prefix_without_trailing_slash():
    assert local_relative_path("data/a.txt", "data") == "a.txt"


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
