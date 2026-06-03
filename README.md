# BOS 文件夹下载器

递归下载百度 BOS 指定文件夹内的所有文件,带进度条与断点续传。

## 安装

```bash
uv sync
```

## 配置

复制 `.env.example` 为 `.env` 填入凭证,然后:

```bash
source .env
```

## 使用

```bash
uv run bos-download --prefix data/ --dest ./downloads
```

- `--prefix` 要下载的文件夹前缀(以 `/` 结尾更直观,如 `data/`)
- `--dest` 本地保存目录
- `--bucket` 可选,覆盖 `BOS_BUCKET`

中断后重复执行同一命令会自动从 `.part` 临时文件断点续传,已完成的文件会被跳过。
