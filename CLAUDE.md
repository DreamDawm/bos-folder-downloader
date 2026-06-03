# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

百度 BOS(对象存储)文件夹下载器:给定桶内某个 prefix(文件夹),递归下载其下所有文件与子文件夹文件,带 tqdm 实时进度,支持断点续传。命令行入口为 `bos-download`。

## 常用命令

包管理用 **UV**(非 pip / venv 手工管理):

```bash
uv sync                          # 安装依赖(含 dev 组的 pytest)
uv run pytest                    # 跑全部测试
uv run pytest tests/test_downloader.py            # 单个测试文件
uv run pytest tests/test_cli.py::test_local_relative_path_keeps_prefix_folder  # 单个测试
uv run bos-download --prefix data/ --dest ./downloads   # 运行下载器(默认 3 线程)
uv run bos-download --prefix data/ --dest ./downloads --bucket 其他桶名  # 覆盖默认桶
uv run bos-download --prefix data/ --dest ./downloads --workers 5  # 自定义并发线程数
```

凭证通过环境变量或 `.env` 提供(`python-dotenv` 自动加载,跨平台,无需 `source`):

```bash
cp .env.example .env    # Windows: copy .env.example .env,然后填入真实 AK/SK/endpoint/桶名
```

`.env` 必填键:`BOS_ACCESS_KEY_ID`、`BOS_SECRET_ACCESS_KEY`、`BOS_ENDPOINT`、`BOS_BUCKET`。

## 架构

分层管线,每层单一职责,层间用 `typing.Protocol` 做鸭子类型解耦(便于测试时注入 Fake,而非依赖真实 BosClient)。数据流:

```
cli.run() → lister.list_objects_under_prefix() → 逐个 downloader.download_object()
              ↑ client.create_bos_client() 提供 BosClient
              ↑ config.load_config_from_env() 提供凭证
```

- `config.py` — 从环境变量/`.env` 读取凭证,返回 frozen dataclass `DownloadConfig`。`load_config_from_env()` 在未显式传 env 时自动 `load_dotenv(override=False)`(真实环境变量优先于 `.env`)。
- `client.py` — 仅负责用 `DownloadConfig` 构造 `BosClient`。
- `lister.py` — 用 `list_all_objects`(无 delimiter = 递归)枚举 prefix 下全部对象,跳过以 `/` 结尾的伪目录占位对象。
- `downloader.py` — 单文件断点续传核心。
- `cli.py` — argparse 入口 + tqdm 进度编排 + 远端 key → 本地相对路径映射。

## 关键设计决策(改动前务必理解)

**断点续传不能用 SDK 的 `get_object_to_file`**:它内部 `open(file_name, 'wb')` 会截断已下载内容,破坏续传。因此用 `get_object(range=(start, total-1))` 拉取剩余字节,以 `'ab'` 追加写入 `dest+'.part'` 临时文件,完成后 `os.replace()` 原子重命名为最终文件。

**BOS range 是闭区间**:`range=(start, end)` 含 end 字节,所以请求剩余内容时用 `total - 1` 作为 end。测试里的 FakeClient 必须用 `data[start:end+1]` 才能正确模拟。

**已存在即跳过**:`dest` 已存在则直接 return(整体续传的粒度在文件级)。`.part` 比远端还大时视为损坏,删除重下。空文件(total==0)需先 `part.touch()` 再 replace,否则 `os.replace` 抛 FileNotFoundError。

**本地路径保留来源文件夹名**:`cli.local_relative_path(key, prefix)` 会保留 prefix 的最后一级文件夹名,使下载结果落在 `dest/<最后一级文件夹>/...` 而非直接铺在 dest 根目录(例:prefix `a/b/data/` 下的 `a/b/data/x.txt` → `dest/data/x.txt`)。

**路径遍历防护(安全,勿删)**:`local_relative_path` 拒绝 `..` 路径段、拒绝解析出的绝对路径、拒绝反斜杠,防止恶意 key 写到 dest 之外。

## 约束

- 凭证(AK/SK)只从环境变量 / `.env` 读取,绝不硬编码,绝不打印到日志或异常信息中。
- `.env` 已被 `.gitignore` 忽略,绝不提交;`.env.example` 只放占位值。
- 兼容 Python 3.9+:模块统一 `from __future__ import annotations`,类型注解用 `Optional[...]` 而非 `... | None` 的运行期形式。

