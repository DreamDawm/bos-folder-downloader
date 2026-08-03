from pathlib import Path

import pytest

from bos_downloader.s3_paths import discover_upload_items, object_key_for_path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"D:\\data\\images\\a.jpg", "data/images/a.jpg"),
        ("D:/data/images/a.jpg", "data/images/a.jpg"),
        ("/data/images/a.jpg", "data/images/a.jpg"),
    ],
)
def test_object_key_strips_only_drive_and_root(raw, expected):
    assert object_key_for_path(Path(raw)) == expected


def test_discovers_one_file(tmp_path):
    source = tmp_path / "one.txt"
    source.write_bytes(b"abc")

    items = list(discover_upload_items(source))

    assert len(items) == 1
    assert items[0].abs_path == source.resolve()
    assert items[0].object_key.endswith("one.txt")
    assert items[0].size == 3


def test_discovers_folder_recursively(tmp_path):
    source = tmp_path / "images"
    (source / "nested").mkdir(parents=True)
    (source / "a.jpg").write_bytes(b"a")
    (source / "nested" / "b.jpg").write_bytes(b"bb")

    keys = {item.object_key for item in discover_upload_items(source)}

    assert any(key.endswith("images/a.jpg") for key in keys)
    assert any(key.endswith("images/nested/b.jpg") for key in keys)


def test_empty_folder_produces_no_items(tmp_path):
    source = tmp_path / "empty"
    source.mkdir()

    assert list(discover_upload_items(source)) == []


def test_missing_source_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="不存在"):
        list(discover_upload_items(tmp_path / "missing"))


def test_empty_object_key_is_rejected():
    with pytest.raises(ValueError, match="对象 Key"):
        object_key_for_path(Path("/"))
