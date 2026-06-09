"""pipeline_cli 参数解析与转发测试。"""
from bos_downloader import pipeline_cli


def test_main_forwards_args(monkeypatch):
    captured = {}

    def fake_run(prefix, dest_dir, bucket_override=None,
                 remote_base_override=None, logs_dir="logs",
                 stamp="run", dl_workers=1, ul_workers=5):
        captured.update(
            prefix=prefix, dest_dir=dest_dir,
            dl_workers=dl_workers, ul_workers=ul_workers,
            logs_dir=logs_dir,
        )
        return 0

    monkeypatch.setattr(pipeline_cli, "run", fake_run)

    rc = pipeline_cli.main([
        "--prefix", "data/", "--dest", "./dl",
        "--dl-workers", "1", "--ul-workers", "5",
    ])

    assert rc == 0
    assert captured["prefix"] == "data/"
    assert captured["dest_dir"] == "./dl"
    assert captured["dl_workers"] == 1
    assert captured["ul_workers"] == 5
    assert captured["logs_dir"] == "logs"
