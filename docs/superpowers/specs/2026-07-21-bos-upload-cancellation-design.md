# bos-upload Ctrl+C 快速取消设计

## 背景

`bos-upload` 使用多个工作线程执行 Paramiko SFTP 上传。Python 只在主线程处理 `KeyboardInterrupt`，而 `ThreadPoolExecutor` 上下文退出默认执行 `shutdown(wait=True)`。当前顺序会先等待工作线程，再由 `finally` 关闭 SFTP 连接；当工作线程阻塞在 `put/stat/mkdir` 时，`Ctrl+C` 因而无法及时结束进程。

目标是在 Windows 下首次按下 `Ctrl+C` 后立即停止新工作、主动打断 SFTP I/O，并在最多 5 秒内以退出码 130 结束。正常上传及 `bos-sync` 的现有行为不得退化。

## 取消语义

- 首次 `Ctrl+C` 是取消请求，不把已取消文件计为普通上传失败。
- 收到请求后不再提交新文件；已排队但尚未运行的 Future 全部取消。
- 立即关闭所有现有 SFTP client 和底层 Transport，以打断工作线程中的阻塞 I/O。
- 最多等待 5 秒完成线程和连接清理。
- 5 秒内清理完成时正常返回 130；超过 5 秒时刷新错误输出并执行进程级强制退出，退出码仍为 130。
- 已完成文件保持不变。强退时正在上传的远端文件可能不完整；下次运行通过大小不一致从头覆盖。

## 架构

### UploadCancellation

新增一次 `run()` 生命周期内的取消控制器，职责包括：

- 持有 `threading.Event` 停止标志；
- 提供 `request()` 幂等地发布取消；
- 提供 `raise_if_cancelled()` 或等价检查，让调度边界和工作线程拒绝开始新工作；
- 管理 5 秒硬退出看门狗；
- 正常清理完成后解除看门狗，避免误杀已完成进程。

看门狗使用守护线程等待取消事件后的固定期限。若主流程在期限内报告清理完成，线程直接退出；否则调用 `os._exit(130)`。这是最后兜底，仅用于 Python/Paramiko 工作线程无法停止的情况。

### 有界调度器

`upload_cli` 不再依赖 `ThreadPoolExecutor` 的 `with` 自动关闭：

1. 显式创建 Executor；
2. 有界窗口提交任务前检查取消状态；
3. 主线程捕获 `KeyboardInterrupt`；
4. 发布取消；
5. 对 pending Future 调用 `cancel()`；
6. 调用 `executor.shutdown(wait=False, cancel_futures=True)`，避免主线程先阻塞等待；
7. 关闭 SFTP 连接以抢断 I/O；
8. 在剩余 5 秒预算内等待运行中的 Future/线程结束；
9. 清理完成后标记看门狗完成并返回 130。

Python 3.9 支持 `cancel_futures=True`，符合项目最低版本。

### ThreadLocalSftpPool

扩展为可取消、线程安全且幂等的连接池：

- 增加 `_closed` 状态；
- `get()` 在建连前、建连后加入 `_all` 前分别检查 `_closed`，解决取消与惰性建连竞态；
- 取消后调用 `get()` 会抛出明确的连接池已关闭异常；
- 若连接在取消期间刚建立，则立即关闭，不允许进入池；
- `close_all()` 原子设置关闭状态、取出连接列表并逐一关闭；重复调用无副作用；
- 优先关闭底层 Transport，确保其关联 Channel 被关闭，再容错关闭 SFTP client；单个关闭异常不能阻止其他连接清理。

`pipeline` 继续复用该池；正常路径兼容，取消能力不改变其返回值和日志语义。

## Paramiko 超时与 keepalive

连接采用代码内保守默认常量，不新增环境变量：

- TCP/SSH 握手超时：10 秒；
- 认证超时：10 秒；
- SFTP Channel I/O 超时：30 秒；
- SSH keepalive：30 秒。

`open_sftp()` 使用显式 socket/Transport 初始化或等价 API，确保握手与认证阶段也有上限；成功创建 SFTP client 后，通过 `get_channel().settimeout(30)` 设置 Channel I/O 超时，并对 Transport 调用 `set_keepalive(30)`。

这些超时是异常网络下的常规故障边界；Ctrl+C 仍以主动关闭 Transport 为主要抢断机制，不等待超时自然发生。

## 控制流与输出

正常完成：

```text
run → 调度/上传 → shutdown(wait=True) → close_all → 汇总 → 原返回码
```

Ctrl+C：

```text
KeyboardInterrupt
  → 输出“收到 Ctrl+C，正在取消…”
  → request cancellation / 启动 5 秒看门狗
  → 停止提交 + cancel pending
  → shutdown(wait=False, cancel_futures=True)
  → pool.close_all() 抢断 SFTP
  → 最多等待剩余预算
  → 输出取消汇总
  → 清理完成标记
  → return 130
```

取消日志只包含汇总，不逐文件打印由连接关闭引起的异常，避免 15 个工作线程产生噪声。若正常上传发生非取消异常，仍沿用现有逐文件失败行为。

## 错误处理

- `KeyboardInterrupt` 不被 `_upload_one()` 的 `except Exception` 吞掉，因为它继承自 `BaseException`；由主线程统一处理。
- 工作线程在取消已发布后遇到的连接关闭异常转换为 `cancelled` 状态，不计入 `failed`。
- 连接池关闭过程收集/忽略单连接的关闭错误，继续关闭其余连接；必要时只输出一条清理警告。
- 进程级强退前刷新 stdout/stderr。`os._exit(130)` 不执行后续 Python 清理，因此只在 5 秒期限耗尽时使用。

## 测试设计

### upload_cli

- 调度器收到取消后不再提交新任务；
- pending Future 被取消；
- SFTP pool 在 Executor 等待前关闭；
- 阻塞上传在连接关闭后退出，`run()` 返回 130；
- 取消导致的工作线程异常不计为普通失败；
- 看门狗在 5 秒内完成时不会调用 `os._exit`；
- 期限耗尽时调用 `os._exit(130)`；测试必须注入假时钟/假退出函数，绝不真的终止 pytest；
- 正常上传、跳过、失败、进度和有界 Future 窗口测试继续通过。

### sftp_client

- `close_all()` 幂等；
- 关闭后 `get()` 拒绝创建连接；
- 建连与关闭竞态中，新连接被立即关闭且不会泄漏；
- `open_sftp()` 配置握手、认证、Channel timeout 与 keepalive；
- 关闭一个 client 失败时，其余连接仍会清理。

### CLI 与集成

- `main()` 传播取消退出码 130；
- `bos-sync` 现有测试保持通过；
- Windows 手工验收：运行包含阻塞/限速上传的任务，按一次 `Ctrl+C`，确认 5 秒内返回提示符且无残留 `bos-upload/python` 子进程。

## 验证门禁

- `uv run ruff check src tests`
- `uv run pytest -q`
- `uv run pytest --cov=src/bos_downloader --cov-report=term-missing`，覆盖率不低于 80%
- Python 3.9 MyPy 检查改动模块
- Bandit 扫描
- Windows 实际进程树验收；不在未授权远端目录执行测试上传

## 非目标与已知限制

- 不实现跨进程上传恢复；未完成文件仍由下次运行按大小差异重传。
- 不将整个上传器重构为多进程。
- 不保证 `os._exit` 兜底路径执行普通 `finally`、缓冲区刷新以外的清理。
- 标准 SFTP 无原子 no-follow 写入；可信内网及远端目录不被恶意并发替换的既有威胁模型保持不变。
