"""group_objects_by_dir 分组行为测试。"""
from bos_downloader.grouping import group_objects_by_dir
from bos_downloader.lister import RemoteObject


def test_groups_by_direct_parent_dir():
    objs = [
        RemoteObject("data/a.txt", 1),
        RemoteObject("data/sub/b.txt", 2),
        RemoteObject("data/sub/deep/c.bin", 3),
        RemoteObject("data/sub/deep/d.bin", 4),
    ]
    groups = group_objects_by_dir(objs)
    as_dict = {d: [o.key for o in items] for d, items in groups}
    assert as_dict["data"] == ["data/a.txt"]
    assert as_dict["data/sub"] == ["data/sub/b.txt"]
    assert as_dict["data/sub/deep"] == [
        "data/sub/deep/c.bin",
        "data/sub/deep/d.bin",
    ]


def test_empty_input_returns_empty():
    assert group_objects_by_dir([]) == []


def test_group_order_is_stable_by_first_appearance():
    objs = [
        RemoteObject("x/2.txt", 1),
        RemoteObject("y/1.txt", 1),
        RemoteObject("x/3.txt", 1),
    ]
    dirs = [d for d, _ in group_objects_by_dir(objs)]
    assert dirs == ["x", "y"]
