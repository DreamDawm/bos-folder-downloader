# bos-upload Ctrl+C 快速取消实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Windows 下运行的 `bos-upload` 在收到一次 `Ctrl+C` 后停止新任务、主动打断 SFTP I/O，并在最多 5 秒内以退出码 130 结束。

**Architecture:** 新增独立 `UploadCancellation` 控制器负责停止事件与 5 秒硬退出看门狗；`upload_cli` 显式管理线程池和有界 Future，并按“取消排队任务 → 关闭 SFTP → 最多等待 5 秒”的顺序清理。`ThreadLocalSftpPool` 增加幂等关闭与建连竞态防护，`open_sftp` 增加握手、认证、Channel I/O 超时及 keepalive。

**Tech Stack:** Python 3.9+、`concurrent.futures`、`threading`、Paramiko 3.x、pytest、Ruff、MyPy、Bandit。

---

## 文件职责

- Create: `src/bos_downloader/upload_cancellation.py` — 一次上传运行内的取消状态、清理完成事件与 5 秒硬退出看门狗。
- Modify: `src/bos_downloader/upload_cli.py` — 捕获 `KeyboardInterrupt`、显式关闭 Executor、取消 Future、返回 130、输出取消汇总。
- Modify: `src/bos_downloader/sftp_client.py` — 超时/keepalive、连接池关闭状态、并发建连与关闭竞态处理。
- Create: `tests/test_upload_cancellation.py` — 取消控制器和看门狗单元测试。
- Modify: `tests/test_upload_run_concurrent.py` — Ctrl+C 控制流、连接抢断顺序、有界任务停止提交和退出码测试。
- Modify: `tests/test_sftp_client.py` — Paramiko 配置及连接池取消竞态测试。
- Modify: `README.md` — Ctrl+C、5 秒退出、130 退出码及未完成文件行为。
- Modify: `docs/superpowers/specs/2026-07-21-bos-upload-cancellation-design.md` — 仅当实现阶段发现与已批准规格不一致时同步修正；通常无需修改。

### Task 1: 取消控制器与 5 秒看门狗

**Files:**
- Create: `src/bos_downloader/upload_cancellation.py`
- Create: `tests/test_upload_cancellation.py`

- [ ] **Step 1: 编写幂等取消和清理完成测试**

```python
from bos_downloader.upload_cancellation import UploadCancellation


def test_request_is_idempotent():
    cancellation = UploadCancellation(timeout_seconds=5.0)

    assert cancellation.request() is True
    assert cancellation.request() is False
    assert cancellation.is_cancelled is True


def test_mark_cleanup_complete_sets_completion_event():
    cancellation = UploadCancellation(timeout_seconds=5.0)

    cancellation.request()
    cancellation.mark_cleanup_complete()

    assert cancellation.is_cleanup_complete is True
```

- [ ] **Step 2: 运行测试并确认因模块尚不存在而失败**

Run:

```bash
uv run pytest -q tests/test_upload_cancellation.py
```

Expected: collection FAIL，包含 `ModuleNotFoundError: bos_downloader.upload_cancellation`。

- [ ] **Step 3: 实现最小取消状态 API**

```python
"""上传任务的协作式取消与硬退出看门狗。"""

from __future__ import annotations

import os
import sys
import threading
from typing import Callable, Optional

HardExit = Callable[[int], None]


class UploadCancelledError(RuntimeError):
    """上传已收到取消请求。"""


class UploadCancellation:
    def __init__(
        self,
        timeout_seconds: float = 5.0,
        hard_exit: Optional[HardExit] = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._hard_exit = hard_exit or os._exit
        self._cancelled = threading.Event()
        self._cleanup_complete = threading.Event()
        self._watchdog_started = threading.Event()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def is_cleanup_complete(self) -> bool:
        return self._cleanup_complete.is_set()

    def request(self) -> bool:
        if self._cancelled.is_set():
            return False
        self._cancelled.set()
        return True

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise UploadCancelledError("上传已取消")

    def mark_cleanup_complete(self) -> None:
        self._cleanup_complete.set()
```

- [ ] **Step 4: 运行测试并确认通过**

Run:

```bash
uv run pytest -q tests/test_upload_cancellation.py
```

Expected: `2 passed`。

- [ ] **Step 5: 编写看门狗成功解除和超时强退测试**

```python
import threading


def test_watchdog_does_not_exit_after_cleanup_completes():
    exit_codes = []
    cancellation = UploadCancellation(
        timeout_seconds=0.01,
        hard_exit=exit_codes.append,
    )

    cancellation.request()
    thread = cancellation.start_watchdog()
    cancellation.mark_cleanup_complete()
    thread.join(timeout=1)

    assert exit_codes == []
    assert thread.is_alive() is False


def test_watchdog_hard_exits_with_130_after_timeout():
    exit_codes = []
    exit_called = threading.Event()

    def fake_exit(code: int) -> None:
        exit_codes.append(code)
        exit_called.set()

    cancellation = UploadCancellation(
        timeout_seconds=0.01,
        hard_exit=fake_exit,
    )

    cancellation.request()
    thread = cancellation.start_watchdog()

    assert exit_called.wait(timeout=1)
    thread.join(timeout=1)
    assert exit_codes == [130]
```

- [ ] **Step 6: 运行看门狗测试并确认因方法缺失而失败**

Run:

```bash
uv run pytest -q tests/test_upload_cancellation.py
```

Expected: FAIL，包含 `AttributeError: 'UploadCancellation' object has no attribute 'start_watchdog'`。

- [ ] **Step 7: 实现单次启动的守护线程**

在 `UploadCancellation` 中增加：

```python
    def start_watchdog(self) -> threading.Thread:
        if self._watchdog_started.is_set():
            raise RuntimeError("取消看门狗已启动")
        self._watchdog_started.set()
        thread = threading.Thread(
            target=self._watchdog_loop,
            name="bos-upload-cancel-watchdog",
            daemon=True,
        )
        thread.start()
        return thread

    def _watchdog_loop(self) -> None:
        if self._cleanup_complete.wait(self.timeout_seconds):
            return
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            self._hard_exit(130)
```

- [ ] **Step 8: 运行取消控制器测试并确认通过**

Run:

```bash
uv run pytest -q tests/test_upload_cancellation.py
```

Expected: `4 passed`。

- [ ] **Step 9: 提交取消控制器**

```bash
git add src/bos_downloader/upload_cancellation.py tests/test_upload_cancellation.py
git commit -m "feat: 新增上传取消控制器"
```

### Task 2: SFTP 超时和 keepalive

**Files:**
- Modify: `src/bos_downloader/sftp_client.py:28-44`
- Modify: `tests/test_sftp_client.py:15-41`

- [ ] **Step 1: 扩展 FakeTransport 并编写连接参数测试**

将 `test_open_sftp_passes_credentials` 的 Fake 增加方法与记录：

```python
class FakeChannel:
    def settimeout(self, value):
        captured["channel_timeout"] = value


class FakeSftp:
    def get_channel(self):
        return FakeChannel()


class FakeTransport:
    def __init__(self, addr):
        captured["addr"] = addr
        self.auth_timeout = None

    def start_client(self, timeout=None):
        captured["connect_timeout"] = timeout

    def auth_password(self, username, password):
        captured["username"] = username
        captured["password"] = password
        captured["auth_timeout"] = self.auth_timeout

    def set_keepalive(self, interval):
        captured["keepalive"] = interval

    def close(self):
        captured["closed"] = True
```

断言：

```python
assert captured["connect_timeout"] == 10.0
assert captured["auth_timeout"] == 10.0
assert captured["channel_timeout"] == 30.0
assert captured["keepalive"] == 30
```

- [ ] **Step 2: 运行单测并确认旧实现不满足 API/断言**

Run:

```bash
uv run pytest -q tests/test_sftp_client.py::test_open_sftp_passes_credentials
```

Expected: FAIL，旧实现调用 `transport.connect(...)` 或缺少超时记录。

- [ ] **Step 3: 实现超时和 keepalive 常量**

在 `sftp_client.py` 增加：

```python
_CONNECT_TIMEOUT_SECONDS = 10.0
_AUTH_TIMEOUT_SECONDS = 10.0
_CHANNEL_TIMEOUT_SECONDS = 30.0
_KEEPALIVE_SECONDS = 30
```

将连接过程改为：

```python
transport = paramiko.Transport((cfg.host, cfg.port))
try:
    transport.start_client(timeout=_CONNECT_TIMEOUT_SECONDS)
    transport.auth_timeout = _AUTH_TIMEOUT_SECONDS
    transport.auth_password(cfg.username, cfg.password)
    transport.set_keepalive(_KEEPALIVE_SECONDS)
    sftp = paramiko.SFTPClient.from_transport(transport)
    if sftp is None:
        raise ConnectionError(f"无法建立到 {cfg.host}:{cfg.port} 的 SFTP 连接")
    sftp.get_channel().settimeout(_CHANNEL_TIMEOUT_SECONDS)
except BaseException:
    transport.close()
    raise
sftp._bos_transport = transport  # type: ignore[attr-defined]
return sftp
```

- [ ] **Step 4: 运行 SFTP client 测试并确认通过**

Run:

```bash
uv run pytest -q tests/test_sftp_client.py
```

Expected: 所有测试 PASS。

- [ ] **Step 5: 提交超时和 keepalive**

```bash
git add src/bos_downloader/sftp_client.py tests/test_sftp_client.py
git commit -m "feat: 增加SFTP连接超时与保活"
```

### Task 3: 可关闭的线程本地 SFTP 连接池

**Files:**
- Modify: `src/bos_downloader/sftp_client.py:47-77`
- Modify: `tests/test_sftp_client.py`

- [ ] **Step 1: 编写关闭幂等和关闭后禁止 get 的测试**

```python
import pytest


def test_close_all_is_idempotent(monkeypatch):
    closed = []
    client = SimpleNamespace(close=lambda: closed.append("client"))
    monkeypatch.setattr(sftp_client, "open_sftp", lambda cfg: client)
    pool = sftp_client.ThreadLocalSftpPool(_cfg())
    pool.get()

    pool.close_all()
    pool.close_all()

    assert closed == ["client"]


def test_get_rejects_new_connection_after_close(monkeypatch):
    created = []
    monkeypatch.setattr(
        sftp_client,
        "open_sftp",
        lambda cfg: created.append(cfg) or SimpleNamespace(close=lambda: None),
    )
    pool = sftp_client.ThreadLocalSftpPool(_cfg())

    pool.close_all()

    with pytest.raises(RuntimeError, match="已关闭"):
        pool.get()
    assert created == []
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
uv run pytest -q tests/test_sftp_client.py -k "idempotent or rejects_new"
```

Expected: 至少一个 FAIL；旧池允许关闭后重新建连。

- [ ] **Step 3: 实现 `_closed` 状态和统一连接关闭函数**

```python
def _close_sftp(client: paramiko.SFTPClient) -> None:
    transport = getattr(client, "_bos_transport", None)
    if transport is not None:
        transport.close()
    try:
        client.close()
    except Exception:
        if transport is None:
            raise
```

在 `__init__` 增加：

```python
self._closed = False
```

将 `close_all()` 改为：

```python
def close_all(self) -> None:
    with self._lock:
        if self._closed and not self._all:
            return
        self._closed = True
        clients = list(self._all)
        self._all.clear()
    errors = []
    for client in clients:
        try:
            _close_sftp(client)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise RuntimeError(f"关闭 SFTP 连接失败，共 {len(errors)} 个") from errors[0]
```

- [ ] **Step 4: 处理 `get()` 与关闭的双重检查竞态**

将 `get()` 改为：

```python
def get(self) -> paramiko.SFTPClient:
    client = getattr(self._local, "client", None)
    if client is not None:
        with self._lock:
            if self._closed:
                raise RuntimeError("SFTP 连接池已关闭")
        return client

    with self._lock:
        if self._closed:
            raise RuntimeError("SFTP 连接池已关闭")

    client = open_sftp(self._cfg)
    with self._lock:
        if self._closed:
            should_close = True
        else:
            self._local.client = client
            self._all.append(client)
            should_close = False
    if should_close:
        _close_sftp(client)
        raise RuntimeError("SFTP 连接池已关闭")
    return client
```

- [ ] **Step 5: 编写建连中关闭的竞态测试**

```python
def test_connection_created_during_close_is_closed(monkeypatch):
    opening = threading.Event()
    release = threading.Event()
    closed = []
    errors = []
    client = SimpleNamespace(close=lambda: closed.append("client"))

    def fake_open(cfg):
        opening.set()
        release.wait(timeout=1)
        return client

    monkeypatch.setattr(sftp_client, "open_sftp", fake_open)
    pool = sftp_client.ThreadLocalSftpPool(_cfg())

    def worker():
        try:
            pool.get()
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=worker)
    thread.start()
    assert opening.wait(timeout=1)
    pool.close_all()
    release.set()
    thread.join(timeout=1)

    assert closed == ["client"]
    assert errors == ["SFTP 连接池已关闭"]
```

- [ ] **Step 6: 运行连接池测试并确认全部通过**

Run:

```bash
uv run pytest -q tests/test_sftp_client.py
```

Expected: 所有测试 PASS，且无 `PytestUnhandledThreadExceptionWarning`。

- [ ] **Step 7: 提交连接池取消能力**

```bash
git add src/bos_downloader/sftp_client.py tests/test_sftp_client.py
git commit -m "fix: 支持取消期间关闭SFTP连接池"
```

### Task 4: 工作线程识别取消状态

**Files:**
- Modify: `src/bos_downloader/upload_cli.py:26-71`
- Modify: `tests/test_upload_run_concurrent.py`

- [ ] **Step 1: 编写已取消时不获取连接的测试**

```python
from bos_downloader.upload_cancellation import UploadCancellation


def test_upload_one_does_not_get_connection_after_cancellation(tmp_path):
    source = tmp_path / "a.pdf"
    source.write_bytes(b"pdf")
    local_file = LocalFile(source, "a.pdf", 3)
    pool = FakePool(FakeSftp())
    cancellation = UploadCancellation()
    cancellation.request()

    outcome = upload_cli._upload_one(
        pool,
        "/base",
        "folder",
        RemoteDirectoryCache(),
        UploadProgress(3),
        cancellation,
        local_file,
    )

    assert outcome.status == "cancelled"
    assert pool.get_calls == 0
```

给 `FakePool` 增加 `get_calls` 记录。

- [ ] **Step 2: 运行测试并确认参数或状态断言失败**

Run:

```bash
uv run pytest -q tests/test_upload_run_concurrent.py::test_upload_one_does_not_get_connection_after_cancellation
```

Expected: FAIL，旧 `_upload_one` 不接受 `cancellation`。

- [ ] **Step 3: 增加 cancelled 状态及停止检查**

在 `_upload_one` 参数中加入：

```python
cancellation: UploadCancellation,
```

函数开头和 `pool.get()` 后调用：

```python
try:
    cancellation.raise_if_cancelled()
    client = pool.get()
    cancellation.raise_if_cancelled()
    status = upload_file(...)
    return UploadOutcome(rel_path, status)
except UploadCancelledError:
    return UploadOutcome(rel_path, "cancelled")
except Exception as exc:
    if cancellation.is_cancelled:
        return UploadOutcome(rel_path, "cancelled")
    return UploadOutcome(rel_path, "failed", str(exc))
```

- [ ] **Step 4: 更新结果计数容纳 cancelled**

```python
counts = {"done": 0, "skipped": 0, "failed": 0, "cancelled": 0}
```

`_collect_outcome` 仅对 `failed` 输出错误；状态栏增加“取消”。

- [ ] **Step 5: 运行相关上传测试并确认通过**

Run:

```bash
uv run pytest -q tests/test_upload_run_concurrent.py
```

Expected: 所有测试 PASS。

- [ ] **Step 6: 提交工作线程取消状态**

```bash
git add src/bos_downloader/upload_cli.py tests/test_upload_run_concurrent.py
git commit -m "feat: 让上传线程响应取消状态"
```

### Task 5: 显式线程池取消流程和退出码 130

**Files:**
- Modify: `src/bos_downloader/upload_cli.py:102-254`
- Modify: `tests/test_upload_run_concurrent.py`
- Modify: `tests/test_upload_cli.py`

- [ ] **Step 1: 编写停止提交和取消 pending Future 的调度测试**

```python
def test_scheduler_stops_submitting_after_cancellation(tmp_path, monkeypatch):
    cancellation = UploadCancellation()
    submitted = []

    class CancellingExecutor:
        def submit(self, fn, *args):
            future = Future()
            submitted.append(future)
            cancellation.request()
            return future

    files = [
        LocalFile(tmp_path / f"{index}.pdf", f"{index}.pdf", 1)
        for index in range(10)
    ]
    pending = set()

    upload_cli._submit_next(
        CancellingExecutor(),
        iter(files),
        pending,
        6,
        FakePool(FakeSftp()),
        "/base",
        "folder",
        RemoteDirectoryCache(),
        UploadProgress(10),
        cancellation,
    )

    assert len(submitted) == 1
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
uv run pytest -q tests/test_upload_run_concurrent.py::test_scheduler_stops_submitting_after_cancellation
```

Expected: FAIL，旧提交器会填满窗口。

- [ ] **Step 3: 在 `_submit_next()` 每次循环前检查取消**

```python
while len(pending) < limit and not cancellation.is_cancelled:
    ...
```

并把 `cancellation` 传入 `_upload_one`。

- [ ] **Step 4: 编写 `run()` 捕获 Ctrl+C 的行为测试**

测试通过 monkeypatch 让 `_run_futures` 抛 `KeyboardInterrupt`，记录调用顺序：

```python
def test_run_ctrl_c_closes_pool_before_executor_wait(tmp_path, monkeypatch, capsys):
    source = tmp_path / "folder"
    source.mkdir()
    (source / "a.pdf").write_bytes(b"pdf")
    events = []
    pool = InterruptRecordingPool(events)
    executor = InterruptRecordingExecutor(events)

    monkeypatch.setattr(upload_cli, "ThreadLocalSftpPool", lambda cfg: pool)
    monkeypatch.setattr(upload_cli, "ThreadPoolExecutor", lambda **kwargs: executor)
    monkeypatch.setattr(
        upload_cli,
        "_run_futures",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.setattr(
        upload_cli,
        "UploadCancellation",
        lambda: UploadCancellation(timeout_seconds=1, hard_exit=lambda code: None),
    )

    result = upload_cli.run(str(source), workers=1)

    assert result == 130
    assert events.index("pool.close_all") < events.index("executor.wait")
    assert "正在取消" in capsys.readouterr().err
```

Fake Executor 明确记录：

```python
class InterruptRecordingExecutor:
    def shutdown(self, wait=True, cancel_futures=False):
        events.append("executor.wait" if wait else "executor.no_wait")
```

- [ ] **Step 5: 运行 Ctrl+C 测试并确认旧代码抛出 KeyboardInterrupt**

Run:

```bash
uv run pytest -q tests/test_upload_run_concurrent.py::test_run_ctrl_c_closes_pool_before_executor_wait
```

Expected: FAIL，`KeyboardInterrupt` 逃出或关闭顺序不符。

- [ ] **Step 6: 将 `run()` 改为显式 Executor 生命周期**

核心结构实现为：

```python
cancellation = UploadCancellation(timeout_seconds=5.0)
executor = ThreadPoolExecutor(max_workers=workers)
counts = None
try:
    with tqdm(...) as bar:
        counts = _run_futures(..., cancellation, bar)
except KeyboardInterrupt:
    print("\n收到 Ctrl+C，正在取消上传…", file=sys.stderr, flush=True)
    cancellation.request()
    watchdog = cancellation.start_watchdog()
    executor.shutdown(wait=False, cancel_futures=True)
    try:
        pool.close_all()
    except Exception as exc:
        print(f"警告：关闭 SFTP 连接时发生错误：{exc}", file=sys.stderr)
    executor.shutdown(wait=True, cancel_futures=True)
    cancellation.mark_cleanup_complete()
    watchdog.join(timeout=0.1)
    print("上传已取消，退出码 130", file=sys.stderr, flush=True)
    return 130
else:
    executor.shutdown(wait=True)
finally:
    if not cancellation.is_cancelled:
        pool.close_all()
```

注意：5 秒硬上限由看门狗覆盖 `executor.shutdown(wait=True)` 可能发生的无限等待；关闭 Transport 在等待前执行。

- [ ] **Step 7: 对 pending Future 显式调用 cancel**

让 `_run_futures` 或一个调度状态对象在 `KeyboardInterrupt` 时暴露当前 pending；推荐新增小型不可变外壳不适合，因为 pending 会变化。最小方案是让调度器接收由 `run()` 创建的共享 `pending` 集合：

```python
pending: set[Future[UploadOutcome]] = set()
```

取消函数：

```python
def _cancel_pending(pending: set[Future[UploadOutcome]]) -> None:
    for future in list(pending):
        future.cancel()
```

在 `executor.shutdown(wait=False, cancel_futures=True)` 前调用并测试所有未运行 Fake Future 的 `cancelled()` 为 True。

- [ ] **Step 8: 运行上传 run 和 CLI 测试**

Run:

```bash
uv run pytest -q tests/test_upload_run_concurrent.py tests/test_upload_cli.py
```

Expected: 所有测试 PASS。

- [ ] **Step 9: 提交主取消流程**

```bash
git add src/bos_downloader/upload_cli.py tests/test_upload_run_concurrent.py tests/test_upload_cli.py
git commit -m "fix: 支持Ctrl+C快速取消SFTP上传"
```

### Task 6: 阻塞上传抢断集成测试

**Files:**
- Modify: `tests/test_upload_run_concurrent.py`

- [ ] **Step 1: 编写阻塞 put 在 close_all 后解除的测试**

```python
class BlockingSftp(FakeSftp):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.closed = threading.Event()

    def put(self, localpath, remotepath, callback=None, confirm=True):
        self.started.set()
        self.closed.wait(timeout=2)
        raise OSError("连接已关闭")

    def close(self):
        self.closed.set()


class BlockingPool(FakePool):
    def close_all(self):
        self._client.close()
        super().close_all()
```

从测试线程运行 `upload_cli.run()`；等待 `BlockingSftp.started` 后，通过注入的 `_run_futures`/信号触发点在主调度路径抛 `KeyboardInterrupt`，断言：

```python
assert result == 130
assert client.closed.is_set()
assert worker_thread.is_alive() is False
assert elapsed < 5.0
```

使用 `time.monotonic()` 测量，但断言留出 CI 抖动，不使用小于 0.5 秒的脆弱上限。

- [ ] **Step 2: 运行测试并确认通过**

Run:

```bash
uv run pytest -q tests/test_upload_run_concurrent.py -k "ctrl_c or blocking"
```

Expected: 所有取消/阻塞测试 PASS，无残留非守护工作线程警告。

- [ ] **Step 3: 运行 `bos-sync` 回归测试**

Run:

```bash
uv run pytest -q tests/test_pipeline.py tests/test_pipeline_cli.py tests/test_sftp_client.py
```

Expected: 所有测试 PASS；`bos-sync --ul-workers` 默认值仍为 5。

- [ ] **Step 4: 提交阻塞抢断测试**

```bash
git add tests/test_upload_run_concurrent.py
git commit -m "test: 验证Ctrl+C抢断阻塞上传"
```

### Task 7: 文档和最终质量门禁

**Files:**
- Modify: `README.md:40-70`

- [ ] **Step 1: 更新 SFTP 上传说明**

在 SFTP 上传章节增加：

```markdown
按一次 `Ctrl+C` 会停止提交新文件、取消尚未开始的任务并关闭 SFTP 连接；程序最多等待 5 秒完成清理，然后以退出码 `130` 结束。强制取消时，正在上传的远端文件可能不完整；再次运行同一命令会因大小不一致而从头覆盖该文件。

连接默认启用 10 秒握手/认证超时、30 秒 SFTP I/O 超时和 30 秒 SSH keepalive。
```

- [ ] **Step 2: 运行完整测试和覆盖率**

Run:

```bash
uv run pytest --cov=src/bos_downloader --cov-report=term-missing -q
```

Expected: 0 failed，总覆盖率不低于 80%。

- [ ] **Step 3: 运行 Ruff、MyPy、Bandit 和差异检查**

Run:

```bash
uv run ruff check src tests
uvx --from 'mypy<1.19' mypy --python-version 3.9 \
  src/bos_downloader/upload_cli.py \
  src/bos_downloader/upload_cancellation.py \
  src/bos_downloader/sftp_client.py \
  --ignore-missing-imports --disable-error-code=import-untyped
uvx bandit -q -r src/bos_downloader
git diff --check
```

Expected: 全部退出码为 0。

- [ ] **Step 4: 验证所有改动文件为 LF**

```bash
python - <<'PY'
from pathlib import Path
paths = [
    Path("src/bos_downloader/upload_cancellation.py"),
    Path("src/bos_downloader/upload_cli.py"),
    Path("src/bos_downloader/sftp_client.py"),
    Path("tests/test_upload_cancellation.py"),
    Path("tests/test_upload_run_concurrent.py"),
    Path("tests/test_sftp_client.py"),
    Path("README.md"),
]
assert all(b"\r\n" not in path.read_bytes() for path in paths)
print("LF_OK")
PY
```

Expected: `LF_OK`。

- [ ] **Step 5: 启动专项代码审查**

调用：

- `ecc:code-reviewer`：线程池关闭顺序、看门狗和 Future 取消；
- `ecc:python-reviewer`：Python 3.9 类型、线程竞态、异常语义；
- `ecc:security-reviewer`：`os._exit` 使用、资源耗尽、凭证和异常输出。

修复所有 CRITICAL/HIGH 后重新运行 Steps 2-4。

- [ ] **Step 6: 提交文档**

```bash
git add README.md
git commit -m "docs: 补充SFTP上传取消说明"
```

- [ ] **Step 7: Windows 手工验收**

在经用户授权的测试目录运行：

```bash
uv run bos-upload --src D:/data/test-pdfs --workers 15
```

上传过程中按一次 `Ctrl+C`，验证：

```text
1. 立即出现“正在取消上传”；
2. 5 秒内回到提示符；
3. `$LASTEXITCODE`（PowerShell）或 `$?` 对应退出码 130；
4. Get-CimInstance Win32_Process 中不存在该命令的残留 uv/bos-upload/python 子进程；
5. 重跑同一命令能够跳过完整文件并覆盖中断文件。
```

若没有授权测试目录，明确记录“真实 SFTP 手工验收未执行”，不得宣称已完成该项。
