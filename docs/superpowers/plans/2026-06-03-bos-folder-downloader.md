# 百度 BOS 文件夹批量下载器 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用百度 BOS Python SDK 实现一个命令行工具,递归下载指定桶下某个文件夹(prefix)内的所有文件及子文件夹文件,带实时下载进度并支持断点续传。

**Architecture:** 分层设计——`config` 负责从环境变量/CLI 读取 AK/SK/endpoint/bucket;`client` 创建 `BosClient`;`lister` 用 SDK 的 `list_all_objects` 生成器递归枚举 prefix 下所有对象;`downloader` 用 `get_object_meta_data` 取远端大小、对比本地 `.part` 临时文件已下载字节,用 `get_object(range=(start,end))` 取剩余流以 `'ab'` 追加写入实现断点续传,完成后原子重命名;`cli` 串联以上并用 `tqdm` 展示进度。**注意:SDK 自带的 `get_object_to_file` 内部用 `'wb'` 模式会截断已下载内容,无法续传,因此续传必须走 `get_object` + 手动追加写。**

**Tech Stack:** Python 3.9+、UV 包管理、`bce-python-sdk`(`baidubce`,已安装 0.9.71)、`tqdm`(进度条)、`pytest`(测试)。

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `pyproject.toml` | UV 项目元数据与依赖声明 |
| `src/bos_downloader/__init__.py` | 包标识 |
| `src/bos_downloader/config.py` | `DownloadConfig` 数据类 + 从环境变量加载 |
| `src/bos_downloader/client.py` | 构造 `BosClient` |
| `src/bos_downloader/lister.py` | 枚举 prefix 下全部对象(过滤"伪目录"占位对象) |
| `src/bos_downloader/downloader.py` | 单文件断点续传下载 + 进度回调 |
| `src/bos_downloader/cli.py` | CLI 入口:解析参数、协调批量下载、汇总结果 |
| `tests/test_config.py` | 配置加载测试 |
| `tests/test_lister.py` | 对象枚举/过滤测试(mock client) |
| `tests/test_downloader.py` | 断点续传逻辑测试(mock client + 临时文件) |
| `tests/test_cli.py` | CLI 路径映射与协调测试 |

每个文件单一职责,变更内聚。`downloader` 是核心,独立可测;`cli` 只做编排。

---

## 运行所需的环境变量(供执行者与最终用户参考)

```
BOS_ACCESS_KEY_ID       # AK
BOS_SECRET_ACCESS_KEY   # SK
BOS_ENDPOINT            # 例如 bj.bcebos.com(按桶所在区域,不带 https://)
BOS_BUCKET              # 桶名称
```

凭证只从环境变量读取,绝不硬编码或写入仓库,避免 SK 泄露。

---
## 任务列表

### Task 1: 项目脚手架与依赖

**Files:**
- Create: `pyproject.toml`
- Create: `src/bos_downloader/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: 创建 `pyproject.toml`**

```toml
[project]
name = "bos-downloader"
version = "0.1.0"
description = "递归下载百度 BOS 指定文件夹,带进度与断点续传"
requires-python = ">=3.9"
dependencies = [
    "bce-python-sdk>=0.9.71",
    "tqdm>=4.66",
]

[project.scripts]
bos-download = "bos_downloader.cli:main"

[dependency-groups]
dev = ["pytest>=8.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/bos_downloader"]
```

- [ ] **Step 2: 创建包标识文件**

`src/bos_downloader/__init__.py`:
```python
"""百度 BOS 文件夹批量下载器。"""

__version__ = "0.1.0"
```

`tests/__init__.py`:
```python
```

- [ ] **Step 3: 同步依赖并验证导入**

Run: `uv sync && uv run python -c "import baidubce, tqdm; print('ok')"`
Expected: 输出 `ok`(确认 SDK 与 tqdm 在 uv 环境可用)

- [ ] **Step 4: 提交**

```bash
git add pyproject.toml src/bos_downloader/__init__.py tests/__init__.py
git commit -m "chore: 初始化 BOS 下载器项目脚手架与依赖"
```

### Task 2: 配置模块

**Files:**
- Create: `src/bos_downloader/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: 写失败的测试**

`tests/test_config.py`:
```python
import pytest
from bos_downloader.config import DownloadConfig, load_config_from_env


def test_load_config_from_env_reads_all_fields():
    env = {
        "BOS_ACCESS_KEY_ID": "ak-123",
        "BOS_SECRET_ACCESS_KEY": "sk-456",
        "BOS_ENDPOINT": "bj.bcebos.com",
        "BOS_BUCKET": "my-bucket",
    }
    cfg = load_config_from_env(env)
    assert cfg == DownloadConfig(
        access_key_id="ak-123",
        secret_access_key="sk-456",
        endpoint="bj.bcebos.com",
        bucket="my-bucket",
    )


def test_load_config_missing_key_raises_keyerror_with_name():
    env = {"BOS_ACCESS_KEY_ID": "ak-123"}
    with pytest.raises(KeyError) as exc:
        load_config_from_env(env)
    assert "BOS_SECRET_ACCESS_KEY" in str(exc.value)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'bos_downloader.config'`

- [ ] **Step 3: 写最小实现**

`src/bos_downloader/config.py`:
```python
"""下载配置:从环境变量加载 BOS 凭证与目标桶。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class DownloadConfig:
    access_key_id: str
    secret_access_key: str
    endpoint: str
    bucket: str


def load_config_from_env(
    env: Mapping[str, str] | None = None,
) -> DownloadConfig:
    """从环境变量映射读取配置。缺失任一必填项时抛出 KeyError(含变量名)。"""
    source = env if env is not None else os.environ
    return DownloadConfig(
        access_key_id=source["BOS_ACCESS_KEY_ID"],
        secret_access_key=source["BOS_SECRET_ACCESS_KEY"],
        endpoint=source["BOS_ENDPOINT"],
        bucket=source["BOS_BUCKET"],
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS(2 passed)

- [ ] **Step 5: 提交**

```bash
git add src/bos_downloader/config.py tests/test_config.py
git commit -m "feat: 添加从环境变量加载的下载配置"
```

### Task 3: BosClient 构造模块

**Files:**
- Create: `src/bos_downloader/client.py`
- Test: `tests/test_client.py`

- [ ] **Step 1: 写失败的测试**

`tests/test_client.py`:
```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_client.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'bos_downloader.client'`

- [ ] **Step 3: 写最小实现**

`src/bos_downloader/client.py`:
```python
"""根据配置构造百度 BOS 客户端。"""

from __future__ import annotations

from baidubce.auth.bce_credentials import BceCredentials
from baidubce.bce_client_configuration import BceClientConfiguration
from baidubce.services.bos.bos_client import BosClient

from bos_downloader.config import DownloadConfig


def create_bos_client(cfg: DownloadConfig) -> BosClient:
    """用 AK/SK/endpoint 构造一个 BosClient。"""
    credentials = BceCredentials(cfg.access_key_id, cfg.secret_access_key)
    bce_config = BceClientConfiguration(
        credentials=credentials,
        endpoint=cfg.endpoint,
    )
    return BosClient(bce_config)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_client.py -v`
Expected: PASS(1 passed)

- [ ] **Step 5: 提交**

```bash
git add src/bos_downloader/client.py tests/test_client.py
git commit -m "feat: 添加 BosClient 构造模块"
```

### Task 4: 对象枚举模块(lister)

枚举 prefix 下所有对象(含子文件夹)。SDK 的 `list_all_objects(bucket, prefix=...)` 是自动翻页生成器,**不传 delimiter** 时会递归返回所有层级对象,每项有 `.key`(str)与 `.size`(int)。需过滤掉以 `/` 结尾、size 为 0 的"伪目录"占位对象——它们不是真实文件。

**Files:**
- Create: `src/bos_downloader/lister.py`
- Test: `tests/test_lister.py`

- [ ] **Step 1: 写失败的测试**

`tests/test_lister.py`:
```python
from types import SimpleNamespace

from bos_downloader.lister import RemoteObject, list_objects_under_prefix


class FakeClient:
    def __init__(self, items):
        self._items = items
        self.calls = []

    def list_all_objects(self, bucket_name, prefix=None):
        self.calls.append((bucket_name, prefix))
        return iter(self._items)


def test_lists_files_and_skips_pseudo_directories():
    items = [
        SimpleNamespace(key="data/", size=0),          # 伪目录,跳过
        SimpleNamespace(key="data/a.txt", size=10),
        SimpleNamespace(key="data/sub/", size=0),      # 伪目录,跳过
        SimpleNamespace(key="data/sub/b.bin", size=20),
    ]
    client = FakeClient(items)
    result = list(list_objects_under_prefix(client, "my-bucket", "data/"))
    assert result == [
        RemoteObject(key="data/a.txt", size=10),
        RemoteObject(key="data/sub/b.bin", size=20),
    ]
    assert client.calls == [("my-bucket", "data/")]


def test_empty_prefix_returns_nothing():
    client = FakeClient([])
    assert list(list_objects_under_prefix(client, "my-bucket", "empty/")) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_lister.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'bos_downloader.lister'`

- [ ] **Step 3: 写最小实现**

`src/bos_downloader/lister.py`:
```python
"""枚举 BOS 桶中某 prefix 下的全部对象(递归含子文件夹)。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol


@dataclass(frozen=True)
class RemoteObject:
    key: str
    size: int


class _ListClient(Protocol):
    def list_all_objects(self, bucket_name, prefix=None): ...


def list_objects_under_prefix(
    client: _ListClient, bucket: str, prefix: str
) -> Iterator[RemoteObject]:
    """生成 prefix 下所有真实文件对象,跳过以 '/' 结尾的伪目录占位对象。"""
    for item in client.list_all_objects(bucket, prefix=prefix):
        if item.key.endswith("/"):
            continue
        yield RemoteObject(key=item.key, size=item.size)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_lister.py -v`
Expected: PASS(2 passed)

- [ ] **Step 5: 提交**

```bash
git add src/bos_downloader/lister.py tests/test_lister.py
git commit -m "feat: 添加 prefix 对象递归枚举模块"
```

### Task 5: 断点续传下载模块(downloader)— 核心

**续传策略:**
1. 用 `get_object_meta_data(bucket, key)` 取远端文件大小:`int(meta.metadata.content_length)`。
2. 目标本地路径为 `dest`,临时文件为 `dest + ".part"`。
3. 若 `.part` 已存在,其当前字节数 `local_size` 即已下载量。
   - `local_size == total` → 已下完,直接重命名收尾。
   - `local_size > total` → 本地异常(比远端还大),删除 `.part` 重新下载。
   - `0 <= local_size < total` → 从 `local_size` 断点续传。
4. 用 `client.get_object(bucket, key, range=(local_size, total - 1))` 取剩余字节流(SDK 的 range 是闭区间 `bytes=start-end`),`response.data` 是可读流;以 `'ab'` 追加写入 `.part`,边写边回调进度。
5. 写完且大小达标后,原子 `os.replace(part, dest)` 收尾。

**注意:** 不使用 SDK 的 `get_object_to_file`,因为它内部 `open(file_name, 'wb')` 会截断,破坏续传。

**Files:**
- Create: `src/bos_downloader/downloader.py`
- Test: `tests/test_downloader.py`

- [ ] **Step 1: 写失败的测试**

`tests/test_downloader.py`:
```python
from pathlib import Path
from types import SimpleNamespace

from bos_downloader.downloader import download_object


class FakeData:
    """模拟 response.data:按 chunk 产出字节的可读流。"""

    def __init__(self, payload: bytes, chunk: int = 4):
        self._payload = payload
        self._chunk = chunk
        self._pos = 0

    def read(self, n=None):
        size = self._chunk if n is None else min(n, self._chunk)
        data = self._payload[self._pos : self._pos + size]
        self._pos += len(data)
        return data

    def close(self):
        pass


class FakeClient:
    def __init__(self, full_content: bytes):
        self._full = full_content
        self.get_object_calls = []

    def get_object_meta_data(self, bucket, key):
        return SimpleNamespace(
            metadata=SimpleNamespace(content_length=str(len(self._full)))
        )

    def get_object(self, bucket, key, range=None):
        self.get_object_calls.append(range)
        start = range[0] if range else 0
        return SimpleNamespace(data=FakeData(self._full[start:]))


def test_full_download_creates_file_with_content(tmp_path: Path):
    client = FakeClient(b"hello world!")
    dest = tmp_path / "out.txt"
    download_object(client, "bkt", "data/out.txt", dest)
    assert dest.read_bytes() == b"hello world!"
    assert not (tmp_path / "out.txt.part").exists()
    assert client.get_object_calls == [(0, 11)]


def test_resume_from_partial_part_file(tmp_path: Path):
    client = FakeClient(b"hello world!")
    dest = tmp_path / "out.txt"
    # 预置已下载前 6 字节的 .part
    (tmp_path / "out.txt.part").write_bytes(b"hello ")
    download_object(client, "bkt", "data/out.txt", dest)
    assert dest.read_bytes() == b"hello world!"
    # 只请求剩余字节 6..11
    assert client.get_object_calls == [(6, 11)]


def test_already_complete_part_just_renames(tmp_path: Path):
    client = FakeClient(b"hello world!")
    dest = tmp_path / "out.txt"
    (tmp_path / "out.txt.part").write_bytes(b"hello world!")
    download_object(client, "bkt", "data/out.txt", dest)
    assert dest.read_bytes() == b"hello world!"
    # 已完整,无需再请求
    assert client.get_object_calls == []


def test_existing_dest_is_skipped(tmp_path: Path):
    client = FakeClient(b"hello world!")
    dest = tmp_path / "out.txt"
    dest.write_bytes(b"hello world!")
    download_object(client, "bkt", "data/out.txt", dest)
    assert client.get_object_calls == []


def test_progress_callback_receives_total(tmp_path: Path):
    client = FakeClient(b"hello world!")
    dest = tmp_path / "out.txt"
    seen = []
    download_object(
        client, "bkt", "data/out.txt", dest,
        progress_callback=lambda done, total: seen.append((done, total)),
    )
    assert seen[-1] == (12, 12)
    assert all(total == 12 for _, total in seen)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_downloader.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'bos_downloader.downloader'`

- [ ] **Step 3: 写最小实现**

`src/bos_downloader/downloader.py`:
```python
"""单文件断点续传下载,带进度回调。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional, Protocol

ProgressCallback = Callable[[int, int], None]

_CHUNK_SIZE = 256 * 1024


class _DownloadClient(Protocol):
    def get_object_meta_data(self, bucket, key): ...
    def get_object(self, bucket, key, range=None): ...


def _remote_size(client: _DownloadClient, bucket: str, key: str) -> int:
    meta = client.get_object_meta_data(bucket, key)
    return int(meta.metadata.content_length)


def download_object(
    client: _DownloadClient,
    bucket: str,
    key: str,
    dest: Path,
    progress_callback: Optional[ProgressCallback] = None,
) -> None:
    """下载单个对象到 dest,支持断点续传。

    若 dest 已存在则跳过。否则用 dest+'.part' 临时文件累积,
    完成后原子重命名为 dest。
    """
    dest = Path(dest)
    if dest.exists():
        return

    total = _remote_size(client, bucket, key)
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)

    local_size = part.stat().st_size if part.exists() else 0
    if local_size > total:
        # 本地损坏:比远端大,重新来过
        part.unlink()
        local_size = 0

    if progress_callback:
        progress_callback(local_size, total)

    if local_size < total:
        response = client.get_object(
            bucket, key, range=(local_size, total - 1)
        )
        stream = response.data
        downloaded = local_size
        try:
            with open(part, "ab") as f:
                while True:
                    chunk = stream.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total)
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

    os.replace(part, dest)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_downloader.py -v`
Expected: PASS(5 passed)

- [ ] **Step 5: 提交**

```bash
git add src/bos_downloader/downloader.py tests/test_downloader.py
git commit -m "feat: 添加断点续传单文件下载模块"
```

### Task 6: CLI 编排模块

**职责:** 解析命令行参数(`--prefix`、`--dest`、可选 `--bucket`),加载配置、建 client、枚举对象,逐个下载到 `dest/<相对 key>`(相对路径 = key 去掉 prefix 前缀),每个文件用 `tqdm` 显示进度,末尾汇总成功/跳过/失败数。

**本地路径映射规则:** 远端 key `data/sub/b.bin`,prefix `data/`,则相对路径 `sub/b.bin`,落地到 `dest/sub/b.bin`。需用 `os.path` 安全拼接并防止 `..` 越界。

**Files:**
- Create: `src/bos_downloader/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: 写失败的测试**

`tests/test_cli.py`:
```python
import pytest

from bos_downloader.cli import local_relative_path


def test_local_relative_path_strips_prefix():
    assert local_relative_path("data/sub/b.bin", "data/") == "sub/b.bin"


def test_local_relative_path_prefix_without_trailing_slash():
    assert local_relative_path("data/a.txt", "data") == "a.txt"


def test_local_relative_path_rejects_key_not_under_prefix():
    with pytest.raises(ValueError):
        local_relative_path("other/x.txt", "data/")


def test_local_relative_path_rejects_parent_traversal():
    with pytest.raises(ValueError):
        local_relative_path("data/../../etc/passwd", "data/")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL —`ImportError: cannot import name 'local_relative_path'`

- [ ] **Step 3: 写最小实现(第一部分:路径映射 + 进度封装)**

`src/bos_downloader/cli.py`:
```python
"""命令行入口:枚举并下载 BOS 文件夹内全部文件。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tqdm import tqdm

from bos_downloader.client import create_bos_client
from bos_downloader.config import load_config_from_env
from bos_downloader.downloader import download_object
from bos_downloader.lister import list_objects_under_prefix


def local_relative_path(key: str, prefix: str) -> str:
    """把远端 key 转成相对 prefix 的本地相对路径。

    key 必须位于 prefix 之下,否则抛 ValueError;
    并拒绝包含 '..' 段的 key,防止写出目标目录。
    """
    norm_prefix = prefix if prefix.endswith("/") or prefix == "" else prefix + "/"
    if norm_prefix and not key.startswith(norm_prefix):
        raise ValueError(f"key {key!r} 不在 prefix {prefix!r} 之下")
    rel = key[len(norm_prefix):]
    if ".." in rel.split("/"):
        raise ValueError(f"key {key!r} 含非法的 '..' 路径段")
    return rel
```

- [ ] **Step 4: 写实现(第二部分:编排 run + main 入口)**

追加到 `src/bos_downloader/cli.py` 末尾:
```python
def run(prefix: str, dest_dir: str, bucket_override: str | None = None) -> int:
    """枚举 prefix 下所有文件并逐个下载。返回失败文件数。"""
    cfg = load_config_from_env()
    bucket = bucket_override or cfg.bucket
    client = create_bos_client(cfg)
    dest_root = Path(dest_dir)

    objects = list(list_objects_under_prefix(client, bucket, prefix))
    if not objects:
        print(f"prefix {prefix!r} 下没有文件可下载")
        return 0

    print(f"共 {len(objects)} 个文件,开始下载到 {dest_root} ...")
    failures = 0
    for obj in objects:
        rel = local_relative_path(obj.key, prefix)
        dest = dest_root / rel
        if dest.exists():
            print(f"[跳过] {rel} 已存在")
            continue
        try:
            with tqdm(
                total=obj.size, unit="B", unit_scale=True,
                desc=rel, leave=False,
            ) as bar:
                last = {"n": 0}

                def cb(done: int, total: int, _last=last, _bar=bar):
                    _bar.update(done - _last["n"])
                    _last["n"] = done

                download_object(client, bucket, obj.key, dest, progress_callback=cb)
            print(f"[完成] {rel}")
        except Exception as exc:  # noqa: BLE001 - 单文件失败不应中断整体
            failures += 1
            print(f"[失败] {rel}: {exc}", file=sys.stderr)

    print(f"下载结束:成功/跳过 {len(objects) - failures},失败 {failures}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载百度 BOS 文件夹内全部文件")
    parser.add_argument("--prefix", required=True, help="要下载的文件夹前缀,如 data/")
    parser.add_argument("--dest", required=True, help="本地目标目录")
    parser.add_argument("--bucket", default=None, help="覆盖环境变量中的桶名")
    args = parser.parse_args(argv)
    return run(args.prefix, args.dest, args.bucket)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 运行 CLI 测试确认通过**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS(4 passed)

- [ ] **Step 6: 运行全部测试确认通过**

Run: `uv run pytest -v`
Expected: PASS(全部 passed,无 failure)

- [ ] **Step 7: 提交**

```bash
git add src/bos_downloader/cli.py tests/test_cli.py
git commit -m "feat: 添加 CLI 编排与 tqdm 进度展示"
```

### Task 7: 安全收尾与使用文档

确保 SK/AK 不会被提交,并提供使用说明与手动端到端验证。

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `README.md`

- [ ] **Step 1: 创建 `.gitignore`**

```gitignore
.venv/
__pycache__/
*.pyc
.env
*.part
.pytest_cache/
dist/
*.egg-info/
```

- [ ] **Step 2: 创建 `.env.example`(仅占位,不含真实密钥)**

```bash
# 复制为 .env 并填入真实值;.env 已被 .gitignore 忽略,切勿提交
export BOS_ACCESS_KEY_ID="你的AK"
export BOS_SECRET_ACCESS_KEY="你的SK"
export BOS_ENDPOINT="bj.bcebos.com"   # 按桶所在区域填写
export BOS_BUCKET="你的桶名称"
```

- [ ] **Step 3: 创建 `README.md`**

````markdown
# BOS 文件夹下载器

递归下载百度 BOS 指定文件夹内的所有文件,带进度条与断点续传。

## 安装

```bash
uv sync
```

## 配置

复制 `.env.example` 为 `.env` 填入凭证,然后:

```bash
source .env   # Windows Git Bash 同样适用
```

## 使用

```bash
uv run bos-download --prefix data/ --dest ./downloads
```

- `--prefix` 要下载的文件夹前缀(以 `/` 结尾更直观,如 `data/`)
- `--dest` 本地保存目录
- `--bucket` 可选,覆盖 `BOS_BUCKET`

中断后重复执行同一命令会自动从 `.part` 临时文件断点续传,已完成的文件会被跳过。
````

- [ ] **Step 4: 提交**

```bash
git add .gitignore .env.example README.md
git commit -m "docs: 添加使用说明与凭证安全收尾配置"
```

- [ ] **Step 5: 手动端到端验证(需真实凭证,由用户执行)**

设置好环境变量后运行:
```bash
uv run bos-download --prefix <真实文件夹前缀> --dest ./downloads
```
预期:逐个文件显示 tqdm 进度条,子文件夹结构在 `./downloads` 下还原;
Ctrl+C 中断后再次运行,应从断点继续而非重头下载。

<!-- PLAN-ANCHOR-1 -->
