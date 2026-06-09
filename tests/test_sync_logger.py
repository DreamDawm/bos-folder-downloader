"""sync_logger 落地与计数行为测试。"""
from pathlib import Path

from bos_downloader.sync_logger import setup_logger


def test_repeated_setup_does_not_leak_handlers(tmp_path: Path):
    setup_logger(str(tmp_path / "logs"), stamp="20260608-120000")
    logger = setup_logger(str(tmp_path / "logs"), stamp="20260608-120001")
    assert len(logger.handlers) == 2
    logger.info("第二次配置后仍可写 上传 1 个")
    for h in logger.handlers:
        h.flush()
    second = tmp_path / "logs" / "bos-sync-20260608-120001.log"
    assert "上传 1 个" in second.read_text(encoding="utf-8")


def test_creates_log_file_and_writes(tmp_path: Path):
    logger = setup_logger(str(tmp_path / "logs"), stamp="20260608-120000")
    logger.info("组 data/sub 下载 3 个,上传 3 个,删除 3 个")
    for h in logger.handlers:
        h.flush()
    log_file = tmp_path / "logs" / "bos-sync-20260608-120000.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "下载 3 个" in content
    assert "删除 3 个" in content
