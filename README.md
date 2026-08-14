# DSSP Mock

一个可配置的 DiffScope Synthesis Platform（DSSP）歌声合成 Mock。项目使用 FastAPI，提供：

- 与参考 OpenAPI 对齐并统一加上 `/v1` 前缀的元数据与合成端点；
- 一个无需外部 CDN 的中文管理界面；
- 同一进程内彼此隔离、监听不同 IP/端口的多个 Mock 实例；
- 参数依赖 DAG 编辑与校验；
- 由 singer `mock_key` 确定性生成的头像、背景、示例旋律和音色；
- 100 Hz（可配置）参数生成、44.1 kHz 单声道 WAV 合成；
- data URL 与共享内存 HTTP 资源服务两种媒体响应方式；
- JSON 配置持久化、实例生命周期控制和请求/响应日志；
- RFC 9457 风格的 `application/problem+json` 错误响应。

## 启动

项目要求 Python 3.11+ 和 `uv`。依赖安装在项目的 `.venv` 中：

```powershell
uv sync --extra dev
uv run dssp-mock --config config.local.json
```

默认地址：

- 管理界面：<http://127.0.0.1:7860>
- 默认 Mock：<http://127.0.0.1:13711/v1/info>
- 共享临时资源服务：切换任一实例到 HTTP 媒体模式后启用，默认监听 `127.0.0.1:7861`

首次运行会创建持久化配置 `config.local.json`。可通过命令行覆盖管理端监听地址：

```powershell
uv run dssp-mock --host 0.0.0.0 --port 9000 --log-level info
```

## API 行为

实现的端点为：

```text
GET  /v1/info
GET  /v1/arch
GET  /v1/arch/{arch_id}
GET  /v1/singer
GET  /v1/arch/{arch_id}/singer
GET  /v1/arch/{arch_id}/singer/{singer_id}
GET  /v1/arch/{arch_id}/singer/{singer_id}/avatar
GET  /v1/arch/{arch_id}/singer/{singer_id}/background
GET  /v1/arch/{arch_id}/singer/{singer_id}/demo_audio
POST /v1/env_tag
POST /v1/synth/pronunciation
POST /v1/synth/phoneme
POST /v1/synth/duration
POST /v1/synth/parameter
POST /v1/synth/audio
```

`GET /v1/info` 返回 `{"dssp":{"api_version":1}}`。请求中的 `stream` 字段为兼容 OpenAPI 而保留，但按本项目约定始终忽略；所有合成响应都是普通 JSON，不输出 NDJSON。

FastAPI 生成的交互文档位于每个实例的 `/docs`。

歌手元数据中的 `languages` 是以语言代码为键的对象；每种语言均返回显示名称
`name` 和用于新建音符的默认歌词 `default_lyric`。管理界面可逐项配置这些值，
`default_language` 必须是其中一个语言代码。

### 参数 retake

- 不带 `retake` 的参数只作为输入或依赖，不出现在参数合成结果中。
- 带 `retake` 的 INDIRECT 参数会返回完整的重采样曲线；指定区间被确定性生成值替换，区间外保留输入重采样值。
- `retake.position` 和 `retake.length` 按该输入参数自身采样率下的点索引解释。
- DIRECT 参数不能 retake；INDIRECT 参数 retake 时必须提供全部依赖。
- pitch 输出固定限制到 `[0, 12800]`；其它参数分别使用管理界面中配置的最小值/最大值（默认 `[-1000, 1000]`）。输入允许越界，但不允许 NaN/Infinity。

### 合成步骤延迟

每个 Mock 实例可分别配置 pronunciation、phoneme、duration、parameter 和 audio
五个合成步骤的额外响应延迟。配置单位为毫秒，默认均为 `0`；延迟发生在请求完成校验和生成后、发送响应前。

### 多歌手 mix

`mix` 的每一帧包含前 `N-1` 个歌手的权重，最后一个歌手的权重为 `1 - sum(frame)`。每帧长度、值域和总和都会进行业务校验；多个歌手必须属于同一个 `mix_group`。

### 媒体

data URL 模式直接在 JSON 中返回 PNG/WAV data URL。HTTP 模式把内容放入共享资源服务的内存存储，达到实例配置的 TTL 后返回 404；资源不会写入磁盘。若服务通过代理或容器暴露，请配置 `resource_public_base_url`，以便返回客户端可访问的地址。

Mock 不限制同时执行的合成请求数量。请求体仍限制为 32 MiB，输入数组与内存资源仓库也保留容量上限。管理端默认只监听环回地址；若主动暴露到网络，请在可信反向代理后增加认证与来源限制。

## 架构

```text
web/                 原生 HTML/CSS/JS 管理视图与 SVG DAG
api/control.py       管理 API
api/mock.py          DSSP /v1 API
domain/              持久化配置与 OpenAPI 请求模型
repositories/        原子 JSON 配置仓储
services/            校验、确定性随机、参数/音频、媒体、日志
runtime/             多 Uvicorn listener 生命周期管理
```

Mock 实例的元数据配置彼此独立。配置保存使用临时文件加原子替换；媒体资源和请求日志只保存在内存中。

## 测试

```powershell
uv run pytest
uv run ruff check .
```
