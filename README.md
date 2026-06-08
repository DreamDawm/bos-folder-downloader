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
uv run bos-download --prefix data/ --dest ./downloads              # 默认 3 线程
uv run bos-download --prefix data/ --dest ./downloads --workers 5  # 自定义并发数
```

- `--prefix` 要下载的文件夹前缀(以 `/` 结尾更直观,如 `data/`)
- `--dest` 本地保存目录
- `--bucket` 可选,覆盖 `BOS_BUCKET`
- `--workers` 可选,并发下载线程数(默认 3);同一文件不会被多个线程同时下载

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
uv run bos-upload --src D:/data/myfolder                      # 默认 3 线程
uv run bos-upload --src D:/data/myfolder --remote-base /data  # 覆盖远端基准目录
uv run bos-upload --src D:/data/myfolder --workers 5          # 自定义并发数
```

- `--src` 要上传的本地文件夹;其**最后一级文件夹名**会被保留为远端根,例如 `myfolder` 上传到 `<远端基准>/myfolder/...`
- `--remote-base` 可选,覆盖 `SFTP_REMOTE_BASE`
- `--workers` 可选,并发上传线程数(默认 3);每个线程持有**独立** SFTP 连接(paramiko 单连接非线程安全)

远端文件**已存在且大小相同则跳过**(只比大小不比内容),否则覆盖上传。重复执行同一命令时未变动的文件会全部跳过。

> 安全说明:当前用密码认证且不校验主机密钥,存在中间人风险,适用于可信内网。凭证只从 `.env` / 环境变量读取,绝不硬编码,也不会打印到日志。
