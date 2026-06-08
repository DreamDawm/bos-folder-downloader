import pytest

from bos_downloader.upload_cli import remote_relative_path


def test_keeps_source_folder_name():
    # 保留来源文件夹名:子路径前缀上 myfolder
    assert remote_relative_path("sub/b.bin", "myfolder") == "myfolder/sub/b.bin"


def test_top_level_file_keeps_folder():
    assert remote_relative_path("a.txt", "myfolder") == "myfolder/a.txt"


def test_empty_source_folder_keeps_rel_only():
    assert remote_relative_path("a.txt", "") == "a.txt"


def test_rejects_parent_traversal():
    with pytest.raises(ValueError):
        remote_relative_path("../etc/passwd", "myfolder")


def test_rejects_absolute_remainder():
    with pytest.raises(ValueError):
        remote_relative_path("/etc/passwd", "myfolder")


def test_rejects_backslash_segment():
    with pytest.raises(ValueError):
        remote_relative_path("sub\\evil", "myfolder")
