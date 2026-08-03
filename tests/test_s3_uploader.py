from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from bos_downloader.s3_paths import S3UploadItem
from bos_downloader.s3_uploader import SourceFileChangedError, upload_s3_item


def client_error(code, status=400):
    return ClientError(
        {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "HeadObject",
    )


class FakeS3:
    def __init__(self, head=None, head_error=None):
        self.head = head
        self.head_error = head_error
        self.uploads = []

    def head_object(self, **kwargs):
        if self.head_error:
            raise self.head_error
        return {"ContentLength": self.head}

    def upload_file(self, filename, bucket, key, Callback=None, Config=None):
        self.uploads.append((filename, bucket, key, Config.use_threads))
        if Callback:
            Callback(Path(filename).stat().st_size)


def make_item(tmp_path, content=b"abc"):
    path = tmp_path / "a.bin"
    path.write_bytes(content)
    return S3UploadItem(path, "data/a.bin", len(content))


def test_same_size_is_skipped(tmp_path):
    item = make_item(tmp_path)
    client = FakeS3(head=3)
    assert upload_s3_item(client, "bucket", item) == "skipped"
    assert client.uploads == []


def test_different_size_is_overwritten_without_nested_threads(tmp_path):
    item = make_item(tmp_path)
    client = FakeS3(head=2)
    assert upload_s3_item(client, "bucket", item) == "done"
    assert client.uploads == [(str(item.abs_path), "bucket", "data/a.bin", False)]


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
def test_not_found_is_uploaded(tmp_path, code):
    item = make_item(tmp_path)
    client = FakeS3(head_error=client_error(code, 404))
    assert upload_s3_item(client, "bucket", item) == "done"


def test_permission_error_is_not_treated_as_missing(tmp_path):
    item = make_item(tmp_path)
    client = FakeS3(head_error=client_error("AccessDenied", 403))
    with pytest.raises(ClientError):
        upload_s3_item(client, "bucket", item)


def test_changed_source_is_rejected(tmp_path):
    item = make_item(tmp_path, b"a")
    item.abs_path.write_bytes(b"changed")
    with pytest.raises(SourceFileChangedError, match="源文件大小"):
        upload_s3_item(FakeS3(head=None), "bucket", item)
