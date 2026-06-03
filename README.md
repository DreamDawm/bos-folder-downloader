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
uv run bos-download --prefix data/ --dest ./downloads
```

- `--prefix` 要下载的文件夹前缀(以 `/` 结尾更直观,如 `data/`)
- `--dest` 本地保存目录
- `--bucket` 可选,覆盖 `BOS_BUCKET`

中断后重复执行同一命令会自动从 `.part` 临时文件断点续传,已完成的文件会被跳过。
