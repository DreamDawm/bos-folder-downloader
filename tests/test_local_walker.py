from pathlib import Path

from bos_downloader.local_walker import walk_local_files


def test_walks_all_files_across_subfolders(tmp_path: Path):
    # 造多级子目录与文件
    (tmp_path / "a.txt").write_bytes(b"aaaa")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.bin").write_bytes(b"bbbbbb")
    (tmp_path / "sub" / "deep").mkdir()
    (tmp_path / "sub" / "deep" / "c.dat").write_bytes(b"cc")

    files = list(walk_local_files(tmp_path))
    rels = {f.rel_path for f in files}

    assert rels == {"a.txt", "sub/b.bin", "sub/deep/c.dat"}


def test_rel_path_uses_posix_forward_slash(tmp_path: Path):
    # 即便在 Windows 下,rel_path 也应是正斜杠分隔
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.txt").write_bytes(b"x")

    files = list(walk_local_files(tmp_path))
    rel = files[0].rel_path

    assert "\\" not in rel
    assert rel == "sub/x.txt"


def test_size_reflects_file_bytes(tmp_path: Path):
    (tmp_path / "f.txt").write_bytes(b"12345")

    files = list(walk_local_files(tmp_path))

    assert files[0].size == 5
    assert files[0].abs_path == tmp_path / "f.txt"


def test_empty_dir_yields_nothing(tmp_path: Path):
    assert list(walk_local_files(tmp_path)) == []


def test_nonexistent_root_yields_nothing(tmp_path: Path):
    assert list(walk_local_files(tmp_path / "nope")) == []
