import pytest

from bos_downloader import upload_cli
from bos_downloader.upload_cli import DEFAULT_WORKERS, remote_relative_path


def test_default_upload_workers_is_fifteen():
    assert DEFAULT_WORKERS == 15


def test_main_forwards_explicit_workers(monkeypatch):
    seen = []
    monkeypatch.setattr(
        upload_cli,
        "run",
        lambda source, remote, workers: seen.append((source, remote, workers)) or 0,
    )

    result = upload_cli.main(["--src", "D:/data/myfolder", "--workers", "7"])

    assert result == 0
    assert seen == [("D:/data/myfolder", None, 7)]



def test_main_rejects_workers_above_limit(capsys):
    with pytest.raises(SystemExit):
        upload_cli.main(["--src", "D:/data/myfolder", "--workers", "65"])

    assert "不能超过 64" in capsys.readouterr().err


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
