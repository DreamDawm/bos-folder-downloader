from baidubce.services.bos.bos_client import BosClient

from bos_downloader.client import create_bos_client
from bos_downloader.config import DownloadConfig


def test_create_bos_client_returns_bosclient_with_endpoint():
    cfg = DownloadConfig(
        access_key_id="ak-123",
        secret_access_key="sk-456",
        endpoint="bj.bcebos.com",
        bucket="my-bucket",
    )
    client = create_bos_client(cfg)
    assert isinstance(client, BosClient)
    # endpoint 在 SDK 内部被转为 bytes 存储
    assert client.config.endpoint == b"bj.bcebos.com"
