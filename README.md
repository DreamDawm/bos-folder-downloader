# BOS 文件夹下载器

递归下载百度 BOS 指定文件夹内的所有文件,带进度条与断点续传。

## 安装

```bash
uv sync
```

## 配置

复制 `.env.example` 为 `.env` 并填入凭证即可,程序启动时会自动加载它,**无需手动设置环境变量**:

```bash
# Windows / macOS / Linux 通用
copy .env.example .env   # Windows cmd
# 或 cp .env.example .env # macOS / Linux / Git Bash
```

然后编辑 `.env` 填入真实的 AK / SK / endpoint / 桶名。

> 也可以不使用 `.env`,改为在系统里设置同名环境变量(`BOS_ACCESS_KEY_ID` 等);
> 已存在的真实环境变量优先级高于 `.env`。

## 使用

```bash
uv run bos-download --prefix data/ --dest ./downloads              # 默认单线程
uv run bos-download --prefix data/ --dest ./downloads --workers 5  # 自定义并发数
```

- `--prefix` 要下载的文件夹前缀(以 `/` 结尾更直观,如 `data/`)
- `--dest` 本地保存目录
- `--bucket` 可选,覆盖 `BOS_BUCKET`
- `--workers` 可选,并发下载线程数(默认 1,单线程);同一文件不会被多个线程同时下载

中断后重复执行同一命令会自动从 `.part` 临时文件断点续传,已完成的文件会被跳过。

## SFTP 上传

把本地指定文件夹(含全部子文件夹)递归上传到远程 SFTP 服务器,远端保留与本地**同名**的文件夹/子文件夹结构。

先在 `.env` 中填入 SFTP 配置(占位见 `.env.example`):

```
SFTP_HOST=10.75.13.59
SFTP_PORT=22
SFTP_USERNAME=你的SFTP用户名
SFTP_PASSWORD=你的SFTP密码
SFTP_REMOTE_BASE=/upload
```

然后:

```bash
uv run bos-upload --src D:/data/myfolder                       # 默认 15 线程
uv run bos-upload --src D:/data/myfolder --remote-base /data   # 覆盖远端基准目录
uv run bos-upload --src D:/data/myfolder --workers 15           # 自定义并发数
```

- `--src` 要上传的本地文件夹;其**最后一级文件夹名**会被保留为远端根,例如 `myfolder` 上传到 `<远端基准>/myfolder/...`
- `--remote-base` 可选,覆盖 `SFTP_REMOTE_BASE`
- `--workers` 可选,并发上传线程数(默认 15,最大 64);每个线程持有**独立** SFTP 连接(paramiko 单连接非线程安全)

上传时按已处理字节显示总进度和实时速度;远端已存在且大小相同的文件也会计入已处理字节。为减少 10 万级小文件场景下的终端刷新开销,成功和跳过不再逐文件输出,失败仍逐文件输出,结束时统一汇总完成、跳过、失败及已处理字节。上传期间不要修改源目录中的文件。

同一次 `bos-upload` 运行会缓存已确认的远端目录,避免每个文件重复执行多级 `mkdir`。

远端文件**已存在且大小相同则跳过**(只比大小不比内容),否则覆盖上传。重复执行同一命令时未变动的文件会全部跳过。

> 安全说明:当前用密码认证且不校验主机密钥,存在中间人风险,适用于可信内网。凭证只从 `.env` / 环境变量读取,绝不硬编码,也不会打印到日志。

## S3 上传

`s3-upload` 将本地文件或文件夹递归上传到 S3 兼容存储。AK/SK 属于敏感凭据,保存在 `.env` 或系统环境变量中,真实环境变量的优先级高于 `.env`:

```dotenv
S3_ACCESS_KEY_ID=你的S3_AK
S3_SECRET_ACCESS_KEY=你的S3_SK
```

其他 S3 配置统一保存在 `config/s3.yml`:

```yaml
s3:
  endpoint: http://s3-internal.stack-region-1.cloud.bjaidata.cn
  public_endpoint: https://s3.bjdataxxq.cn
  bucket: medical-dataset
  region: cn-north-1
  addressing_style: path
  bypass_proxy: true

presigned_url:
  expires_days: 2
```

```bash
uv run s3-upload --src D:/data/images/a.jpg
uv run s3-upload --src D:/data/images
uv run s3-upload --src D:/data/images --workers 8
```

- `--src` 支持单个文件或文件夹;文件夹会递归上传全部文件。
- `--workers` 可选,并发上传数默认 `8`,取值范围为 `1` 到 `64`。
- 上传文件夹时保留来源文件夹名,例如 `D:/data/一脉阳光` 下的 `2026/result.csv` 上传为对象 `一脉阳光/2026/result.csv`,不会包含上级目录 `data`。
- 上传单个文件时保留其直接父目录名,例如 `D:/data2/国中康建/test.jpg` 上传为对象 `国中康建/test.jpg`。
- 文件夹内同名且大小相同的对象会跳过;大小不同则覆盖上传。
- 命令不提供 `--prefix`,不能额外指定对象 Key 前缀。

> 安全说明:当前内网 Endpoint 使用明文 HTTP,凭据签名和数据不会被 TLS 加密。请只在可信内网使用;生产环境应优先配置受信任证书的 HTTPS Endpoint。凭据仅保存在 `.env` 或环境变量中,不要提交真实密钥。

## S3 外部访问链接

`s3-url` 为桶内对象生成 SigV4 预签名 GET 下载链接。对象路径不包含桶名,不能以 `/` 开头;公网域名、桶名和有效天数均读取 `config/s3.yml`:

```bash
uv run s3-url --path "样例数据/英特雷真/30000中药医学知识.zip"
```

命令成功时仅向标准输出打印完整 URL,方便复制或在脚本中使用。`presigned_url.expires_days` 必须是正整数,程序不限制最大天数;实际有效期是否受限由 S3 兼容存储服务决定。链接生成过程不会请求 S3,因此不会检查对象是否存在。

## 流水线(下载→上传→删除)

逐「最小子文件夹」串行处理:每个目录的文件**全部下载完**立即 SFTP 上传,
**上传成功后删除**本地该组文件,再继续下一个目录。全程数量写入 `logs/`。

```bash
uv run bos-sync --prefix data/ --dest ./tmp                       # 下载 1 线程,上传 5 线程
uv run bos-sync --prefix data/ --dest ./tmp --dl-workers 3 --ul-workers 8
```

- `--prefix` 要处理的文件夹前缀
- `--dest` 本地临时落盘目录(上传成功后该组文件会被删除)
- `--bucket` / `--remote-base` 可选,分别覆盖桶名与远端基准目录
- `--dl-workers` 组内下载并发(默认 1)
- `--ul-workers` 组内上传并发(默认 5)
- `--logs-dir` 日志目录(默认 `logs/`)

**失败处理**:某个目录组内任一文件下载或上传失败,则**保留**该组本地文件、
记录到日志,并继续处理下一组;命令退出码为失败的组数。日志含每组与累计的
「下载 N 个 / 上传 N 个 / 删除 N 个」便于校对。

## JSONL 工具

读取本地磁盘上的 JSONL 文件(每行一个 JSON 对象),支持统计、查看、搜索等操作:

```bash
uv run python scripts/jsonl_tool.py count data.jsonl                        # 统计总行数
uv run python scripts/jsonl_tool.py count data.jsonl --filter status=done   # 按条件过滤统计
uv run python scripts/jsonl_tool.py head data.jsonl 10                      # 前 10 行
uv run python scripts/jsonl_tool.py head data.jsonl 10 --parse-meta         # 自动展开 JSON 字符串字段
uv run python scripts/jsonl_tool.py head data.jsonl 10 --parse-meta-keys meta_info  # 仅展开指定字段
uv run python scripts/jsonl_tool.py tail data.jsonl 10                      # 后 10 行
uv run python scripts/jsonl_tool.py search data.jsonl status=error          # 搜索匹配行
uv run python scripts/jsonl_tool.py search data.jsonl status=error --max 50 # 限制输出行数
```

- `--parse-meta` 自动检测并递归展开所有 JSON 字符串字段,便于查看内嵌元数据
- `--parse-meta-keys KEY1,KEY2` 仅展开指定字段,其余 JSON 字符串保持原样
