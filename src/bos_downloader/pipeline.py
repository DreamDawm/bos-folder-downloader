"""逐组流水线:组内并发下载 → 全部成功后并发上传 → 全部成功后删除本地。"""

from __future__ import annotations

import posixpath
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from typing import List, Optional

from bos_downloader.cli import local_relative_path
from bos_downloader.client import create_bos_client
from bos_downloader.config import load_config_from_env, load_sftp_config_from_env
from bos_downloader.downloader import download_object
from bos_downloader.grouping import group_objects_by_dir
from bos_downloader.lister import RemoteObject, list_objects_under_prefix
from bos_downloader.sftp_client import ThreadLocalSftpPool
from bos_downloader.sync_logger import setup_logger
from bos_downloader.uploader import upload_file


@dataclass(frozen=True)
class GroupResult:
    directory: str
    downloaded: int
    uploaded: int
    deleted: int
    failed: int


def _download_all(client, bucket, prefix, objects, dest_root, workers, logger):
    """并发下载组内文件,返回 (rel 列表, 失败数)。已存在则计入成功。"""
    rels: List[str] = []
    failed = 0

    def _one(obj: RemoteObject):
        rel = local_relative_path(obj.key, prefix)
        download_object(client, bucket, obj.key, dest_root / rel)
        return rel

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_one, o): o for o in objects}
        for fut in as_completed(futures):
            try:
                rels.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.error("下载失败 %s: %s", futures[fut].key, exc)
    return rels, failed


def _upload_all(pool, remote_base, rels, dest_root, workers, logger):
    """并发上传给定相对路径列表,返回失败数。"""
    failed = 0

    def _one(rel: str):
        remote_path = posixpath.join(remote_base, rel)
        client = pool.get()
        upload_file(client, dest_root / rel, remote_path)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_one, r): r for r in rels}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.error("上传失败 %s: %s", futures[fut], exc)
    return failed


def process_group(
    client,
    bucket: str,
    pool,
    prefix: str,
    remote_base: str,
    directory: str,
    objects: List[RemoteObject],
    dest_root: Path,
    dl_workers: int,
    ul_workers: int,
    logger: Logger,
) -> GroupResult:
    """处理单个目录组:下载→上传→删除。任一阶段失败则保留本地并提前返回。"""
    dest_root = Path(dest_root)
    total = len(objects)
    logger.info("[组开始] %s 共 %d 个文件", directory, total)

    rels, dl_failed = _download_all(
        client, bucket, prefix, objects, dest_root, dl_workers, logger
    )
    if dl_failed:
        logger.warning(
            "[组失败-下载] %s 下载成功 %d/%d,保留本地,跳过上传与删除",
            directory, len(rels), total,
        )
        return GroupResult(directory, len(rels), 0, 0, dl_failed)

    ul_failed = _upload_all(
        pool, remote_base, rels, dest_root, ul_workers, logger
    )
    if ul_failed:
        logger.warning(
            "[组失败-上传] %s 上传失败 %d/%d,保留本地,跳过删除",
            directory, ul_failed, total,
        )
        return GroupResult(directory, len(rels), total - ul_failed, 0, ul_failed)

    deleted = 0
    for rel in rels:
        (dest_root / rel).unlink()
        deleted += 1
    logger.info(
        "[组完成] %s 下载 %d 个,上传 %d 个,删除 %d 个",
        directory, len(rels), total, deleted,
    )
    return GroupResult(directory, len(rels), total, deleted, 0)


def run(
    prefix: str,
    dest_dir: str,
    bucket_override: Optional[str] = None,
    remote_base_override: Optional[str] = None,
    logs_dir: str = "logs",
    stamp: str = "run",
    dl_workers: int = 1,
    ul_workers: int = 5,
) -> int:
    """逐「最小子文件夹」串行执行下载→上传→删除,返回失败的组数。

    组内下载并发 dl_workers(默认 1,单线程),上传并发 ul_workers
    (默认 5)。任一组失败则保留本地、继续下一组。全程数量写入 logs。
    """
    logger = setup_logger(logs_dir, stamp)
    cfg = load_config_from_env()
    bucket = bucket_override or cfg.bucket
    client = create_bos_client(cfg)

    sftp_cfg = load_sftp_config_from_env()
    remote_base = remote_base_override or sftp_cfg.remote_base
    pool = ThreadLocalSftpPool(sftp_cfg)

    dest_root = Path(dest_dir)
    objects = list(list_objects_under_prefix(client, bucket, prefix))
    if not objects:
        logger.info("prefix %r 下没有文件可处理", prefix)
        pool.close_all()
        return 0

    groups = group_objects_by_dir(objects)
    logger.info(
        "共 %d 个文件,%d 个子文件夹组;下载并发 %d,上传并发 %d",
        len(objects), len(groups), max(1, dl_workers), max(1, ul_workers),
    )

    failed_groups = 0
    tot_dl = tot_ul = tot_del = 0
    try:
        for directory, items in groups:
            result = process_group(
                client, bucket, pool, prefix, remote_base,
                directory, items, dest_root,
                max(1, dl_workers), max(1, ul_workers), logger,
            )
            tot_dl += result.downloaded
            tot_ul += result.uploaded
            tot_del += result.deleted
            if result.failed:
                failed_groups += 1
    finally:
        pool.close_all()

    logger.info(
        "[全部结束] 组 %d 个(失败 %d 个);累计 下载 %d 个,上传 %d 个,删除 %d 个",
        len(groups), failed_groups, tot_dl, tot_ul, tot_del,
    )
    return failed_groups
