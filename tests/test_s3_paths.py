import pytest

from bos_downloader.s3_paths import build_object_key, discover_upload_items


@pytest.mark.parametrize(
    "relative_path",
    [
        "",
        "/absolute/file.txt",
        r"\absolute\file.txt",
        r"nested\file.txt",
        "../file.txt",
        "nested/../file.txt",
        "D:/data/file.txt",
        "nested//file.txt",
    ],
)
def test_build_object_key_rejects_unsafe_relative_path(relative_path):
    with pytest.raises(ValueError, match="对象 Key"):
        build_object_key(relative_path, "source")


def test_build_object_key_uses_only_relative_path_without_source_folder():
    assert build_object_key("one.txt") == "one.txt"


@pytest.mark.parametrize(
    "source_folder_name",
    ["/source", r"\source", "parent/source", r"parent\source", "..", "D:"],
)
def test_build_object_key_rejects_unsafe_source_folder(source_folder_name):
    with pytest.raises(ValueError):
        build_object_key("one.txt", source_folder_name)


def test_discovers_one_file_under_its_parent_folder(tmp_path):
    source_dir = tmp_path / "国中康建"
    source_dir.mkdir()
    source = source_dir / "test.jpg"
    source.write_bytes(b"abc")

    items = list(discover_upload_items(source))

    assert len(items) == 1
    assert items[0].abs_path == source.resolve()
    assert items[0].object_key == "国中康建/test.jpg"
    assert items[0].size == 3


def test_discovers_folder_recursively_under_source_folder_name(tmp_path):
    source = tmp_path / "一脉阳光"
    (source / "2026").mkdir(parents=True)
    (source / "a.jpg").write_bytes(b"a")
    (source / "2026" / "result.csv").write_bytes(b"bb")

    keys = {item.object_key for item in discover_upload_items(source)}

    assert keys == {"一脉阳光/a.jpg", "一脉阳光/2026/result.csv"}


def test_empty_folder_produces_no_items(tmp_path):
    source = tmp_path / "empty"
    source.mkdir()

    assert list(discover_upload_items(source)) == []


def test_missing_source_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="不存在"):
        list(discover_upload_items(tmp_path / "missing"))
