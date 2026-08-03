# S3 文件与文件夹上传设计

## 背景

项目当前提供百度 BOS 下载、SFTP 上传和下载到 SFTP 的流水线。现需在不改变
现有 `bos-upload` SFTP 行为的前提下，增加独立的 S3 兼容存储上传命令。
目标服务通过内网 HTTP Endpoint 提供 S3 API，使用 boto3 和 AWS Signature V4。

## 目标

- 新增独立命令 `s3-upload`，支持上传一个普通文件或递归上传一个文件夹。
- 对象 Key 保留用户给出的源路径层级，仅去掉 Windows 盘符或 POSIX 根分隔符。
- 上传前比较同名对象大小；大小相同跳过，大小不同则覆盖。
- 支持文件级并发、汇总进度、失败统计和 Ctrl+C 取消。
- 从 `.env` 或进程环境变量加载 S3 配置，绝不硬编码或输出真实凭据。
- 保持现有 `bos-upload`、`bos-download`、`bos-sync` 行为不变。

## 非目标

- 不给新命令增加远端 `--prefix` 参数。
- 不把 SFTP 与 S3 重构为统一上传后端。
- 不创建代表空目录的零字节 S3 对象。
- 不实现对象删除、同步删除、下载或双向同步。
- 不在自动化测试中访问真实 S3 服务。

## 命令行接口

```text
s3-upload --src <文件或文件夹> [--workers N]
```

- `--src` 必填，可指向普通文件或文件夹。
- `--workers` 可选，默认 `8`，取值范围为 `1..64`。
- 上传全部成功或跳过时返回 `0`。
- 任一文件上传失败时输出失败数量并返回非零退出码。
- 配置错误或源路径错误时输出明确的中文错误并返回非零退出码。
- 用户按下 Ctrl+C 时停止提交新任务，清理线程池并返回 `130`。

## 环境变量

`.env.example` 与本地 `.env` 增加以下结构。示例文件只使用占位凭据；本地
`.env` 已由 `.gitignore` 忽略，也不得提交。

```dotenv
S3_ACCESS_KEY_ID=你的S3_AK
S3_SECRET_ACCESS_KEY=你的S3_SK
S3_ENDPOINT=http://s3-internal.stack-region-1.cloud.bjaidata.cn
S3_BUCKET=medical-dataset
S3_REGION=stack-region-1
S3_ADDRESSING_STYLE=path
S3_BYPASS_PROXY=true
```

加载规则沿用项目现有约定：只有调用方未显式传入环境映射时才加载 `.env`，
并且已存在的进程环境变量优先于 `.env`。`S3_ADDRESSING_STYLE` 仅接受 `path`
或 `virtual`；`S3_BYPASS_PROXY` 接受不区分大小写的 `true` 或 `false`。

## 路径到对象 Key 的映射

对象 Key 由源文件的绝对路径生成：

- `D:/data/images/a.jpg` 映射为 `data/images/a.jpg`。
- `D:\data\images\a.jpg` 映射为 `data/images/a.jpg`。
- `/data/images/a.jpg` 映射为 `data/images/a.jpg`。
- 上传文件夹 `D:/data/images` 时，其中的所有文件都保留
  `data/images/...` 这一完整层级。

实现必须使用 `pathlib` 解析路径，将分隔符统一为 `/`，去掉 Windows 盘符和
开头的根分隔符。生成的 Key 不得为空，不得包含 `.` 或 `..` 路径段。符号链接
不作为独立目录递归；本地枚举保持 `os.walk` 的现有行为。

## 架构与组件

### 配置与客户端

`bos_downloader.config` 增加不可变的 `S3UploadConfig` 和
`load_s3_upload_config_from_env()`。客户端工厂在单独模块中构造 boto3 S3
客户端，配置以下行为：

- `region_name` 来自 `S3_REGION`。
- 签名版本固定为 `s3v4`。
- addressing style 来自 `S3_ADDRESSING_STYLE`。
- 使用 boto3 标准重试模式和有界连接、读取超时。
- `S3_BYPASS_PROXY=true` 时传入空代理配置，避免请求进入系统代理。

当 Endpoint 使用 `http://` 时，CLI 在首次发起上传前打印一次明文传输警告。
警告和异常消息不得包含 AK、SK 或完整 Authorization 请求头。

### 源文件枚举与 Key 生成

单独的路径模块负责两件事：验证 `--src` 是普通文件或文件夹，以及生成包含
本地绝对路径、对象 Key 和文件大小的上传项。单文件生成一项；文件夹使用项目
现有本地枚举逻辑递归生成文件项。空文件夹生成零项，并以成功状态结束。

### 单文件上传

单文件上传模块接收 boto3 客户端、桶名和上传项：

1. 调用 `head_object` 查询目标对象。
2. 若响应中的 `ContentLength` 与本地枚举大小相同，返回 `skipped`。
3. `head_object` 返回明确的 `404`、`NoSuchKey` 或 `NotFound` 时视为对象不存在。
4. 其他鉴权、连接或服务错误原样作为该文件失败，不误判为不存在。
5. 对不存在或大小不同的对象调用 boto3 托管上传，允许覆盖同名对象。
6. 上传前再次读取文件大小；若与枚举大小不同，则拒绝上传并报告源文件已变化。

boto3 托管上传使用 `TransferConfig(use_threads=False)`。文件级线程池负责并发，
避免每个文件再创建一组内部线程。托管上传仍负责大文件分片和失败清理。

### CLI 调度与进度

CLI 创建最多 `workers` 个线程，采用有界任务窗口，避免大目录一次性创建全部
Future。每个文件完成后记录 `done`、`skipped` 或 `failed`。进度按已处理字节
累计：实际上传通过 boto3 callback 增量更新，跳过文件一次性计入完整大小。

成功和跳过不逐文件刷屏；失败逐文件写入标准错误。结束时统一输出完成、跳过、
失败、总文件数以及已处理字节。一个文件失败不取消其他文件。

## 错误处理与取消

- 缺少配置变量时，错误消息指出缺失变量名。
- `S3_BYPASS_PROXY` 或 `S3_ADDRESSING_STYLE` 值非法时，在创建客户端前失败。
- 源路径不存在或不是普通文件/文件夹时，在访问网络前失败。
- `head_object` 的权限错误、无效 AK、签名错误和非“不存在”服务错误均计为失败。
- 上传中的网络错误由 boto3 标准重试处理；最终失败后记录该文件并继续。
- Ctrl+C 后停止提交新文件，取消尚未开始的 Future，等待已运行任务结束或失败，
  关闭进度条并返回 `130`。

## 测试策略

测试使用伪造 boto3 客户端和临时文件，不访问真实网络：

- 配置测试覆盖所有字段、`.env` 优先级、缺失字段和布尔/枚举校验。
- 客户端测试覆盖 region、SigV4、path style 和代理绕过配置。
- 路径测试覆盖 Windows 路径、POSIX 路径、单文件、递归文件夹和非法空 Key。
- 上传器测试覆盖同大小跳过、不同大小覆盖、对象不存在、权限错误和源文件变化。
- CLI 测试覆盖默认/非法 workers、HTTP 警告、空目录、部分失败、汇总和退出码。
- 并发测试验证任务窗口受限、每个文件只上传一次以及 Ctrl+C 返回 `130`。
- 输出测试验证标准输出和标准错误不包含测试 AK/SK。

## 文档与兼容性

- `pyproject.toml` 注册 `s3-upload = "bos_downloader.s3_upload_cli:main"`。
- `.env.example` 记录完整的 S3 配置结构。
- `README.md` 增加单文件、文件夹、并发、路径映射和 HTTP 安全警告示例。
- Python 版本下限保持 `>=3.9`，所有文本文件使用 LF。
- 已存在的 boto3 依赖变更属于本功能实施的一部分，但实施时必须保留工作区中
  用户对 `pyproject.toml` 和 `uv.lock` 的现有修改。

## 验收标准

- `uv run s3-upload --src D:/data/images` 能把普通文件映射到
  `data/images/...` 并上传到配置的桶。
- `uv run s3-upload --src D:/data/images/a.jpg` 只上传该文件，Key 为
  `data/images/a.jpg`。
- 同名同大小对象被跳过；不同大小对象被覆盖。
- 一个文件失败不会阻止其他文件处理，命令最终返回非零状态。
- 现有下载、SFTP 上传和流水线测试继续通过。
- 仓库文件和程序输出均不包含真实 S3 AK/SK。
