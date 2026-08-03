# Task 6 Report: 独立 S3 上传 CLI 与有界并发

## 实现

- 新增 `src/bos_downloader/s3_upload_cli.py`，提供 `UploadOutcome`、`run()` 与 `main()`。
- 新增 `s3-upload` 控制台脚本，参数为 `--src` 与 `--workers`；默认 8，允许范围 1 到 64。
- 使用 `ThreadPoolExecutor(max_workers=workers)` 执行文件级上传，并将未完成任务窗口限制为 `2 * workers`。
- 每个项目通过 `UploadProgress.increment_callback_for()` 聚合字节进度；远端同大小跳过时补齐该文件的全部字节。
- 单文件异常转为失败结果，持续处理剩余文件；完成后输出完成、跳过、失败和已处理字节汇总。
- HTTP endpoint 仅在每次 `run()` 中警告一次；所有输出均未包含配置凭据。
- `KeyboardInterrupt` 取消 pending future，调用一次 `shutdown(wait=True, cancel_futures=True)`，并返回 130。
- `KeyError` 与 `ValueError`（配置或源路径）由 CLI 映射为退出码 2。

## RED / GREEN

1. RED：新增 `tests/test_s3_upload_cli.py` 后执行 `uv run pytest tests/test_s3_upload_cli.py -q`，因 `bos_downloader.s3_upload_cli` 尚不存在，在收集阶段以 `ImportError` 失败。
2. GREEN：实现 CLI 和脚本注册。首次测试发现上传替身未模拟 boto3 成功路径的进度回调，导致它错误地只记录跳过文件的字节；已将替身改为在 `done` 时调用回调，随后 `11 passed in 0.26s`。

## 覆盖的行为

- 默认并发、CLI 参数转发和 1–64 边界。
- HTTP 单次警告、成功与跳过汇总、跳过的完整进度。
- 单文件失败继续执行与错误输出。
- 输出不泄漏 Access Key 或 Secret Key。
- 有界提交窗口、配置/源路径错误退出码 2、取消退出码 130 以及取消时仅一次清理。
- 所有网络边界均以受控替身替换；测试未访问网络。

## 验证输出

- `uv run pytest tests/test_s3_upload_cli.py tests/test_s3_uploader.py tests/test_upload_progress.py -q`：`27 passed in 0.31s`。
- `uv run pytest -q`：`230 passed in 1.51s`。
- `uv run ruff check .`：`All checks passed!`。
- `uv lock --check`：成功，解析 43 个包；`uv.lock` 无变更。
- `uv run s3-upload --help`：成功，显示 `--src` 和 `--workers`，未显示 `--prefix`。
- `git diff --check`：成功。

## 文件与提交

- 修改：`pyproject.toml`。
- 新增：`src/bos_downloader/s3_upload_cli.py`、`tests/test_s3_upload_cli.py`。
- 新增：本报告。
- `uv.lock` 预先包含 boto3 依赖图且未发生变化，因此未暂存或重生成。
- 提交主题：`功能: 新增S3上传命令`。

## 自审与 concerns

- SFTP CLI、其默认并发和参数均未修改。
- 对 `upload_s3_item()` 返回的未知状态按单文件失败处理，避免汇总循环崩溃。
- 文件级窗口包含正在运行和排队任务，最大为 `2 * workers`；运行线程数独立受 `workers` 限制。
- 简报示例对 `RecordingBar.n` 的断言是文件数 `2`，但该进度条的单位为字节且需求要求 skipped 计入完整进度；测试采用准确的总字节 `5`。
