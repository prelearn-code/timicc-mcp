# TIMI CC MCP Patch Worker

一个可本地安装、通过 STDIO 运行的 MCP 服务。它把调用方明确提供的代码上下文发送到 TIMI CC 的 OpenAI-compatible Responses 接口，并返回经过本地路径校验的 Git unified diff；它不会扫描或修改你的仓库。

> 非 TIMI CC 官方项目。外部模型调用会发送数据并可能产生费用，请先阅读 [SECURITY.md](SECURITY.md)。

## 功能

- 同步生成或审查候选补丁：`generate_patch`、`review_patch`
- 后台长任务：`submit_patch_job`、`get_patch_job`、`get_patch_result`、`cancel_patch_job`
- 本地能力查询：`get_capabilities`（不调用 API）
- Responses SSE 流式接收，避免长请求期间完全无活动
- 严格限制补丁只能修改 `allowed_paths` 中的路径
- 拒绝二进制补丁、越权路径和超限响应
- 只持久化任务摘要、哈希、聚合进度和已校验结果，不持久化完整提示词、文件上下文或推理内容

## 安装

需要 Python 3.10 或更高版本。

```bash
git clone https://github.com/prelearn-code/timicc-mcp.git
cd timicc-mcp
python -m venv .venv
```

Linux/macOS：

```bash
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e .
export TIMICC_API_KEY="your-key"
```

Windows PowerShell：

```powershell
.venv\Scripts\python.exe -m pip install -U pip
.venv\Scripts\python.exe -m pip install -e .
$env:TIMICC_API_KEY = "your-key"
```

不要把真实密钥写进 Git、TOML 示例或命令历史。推荐通过操作系统的密钥管理或在启动 MCP 客户端前设置环境变量。

## 接入 Codex

将 [config.example.toml](config.example.toml) 复制到 `~/.codex/config.toml`，并替换 Python 的绝对路径。Windows 的 `command` 应改为类似：

```toml
command = "C:/absolute/path/timicc-mcp/.venv/Scripts/python.exe"
```

重启 Codex 后运行 `codex mcp list`，或在 Codex 中输入 `/mcp`。示例使用 `env_vars` 转发已存在的环境变量，并将工具审批设为 `prompt`，因为每次模型调用都可能发送代码并产生费用。

其他支持 STDIO 的 MCP 客户端可用相同入口启动：

```text
/absolute/path/.venv/bin/python -m timicc_worker.server
```

## 配置

| 环境变量 | 用途 | 默认值 |
| --- | --- | --- |
| `TIMICC_API_KEY` | API 密钥（也兼容 `OPENAI_API_KEY`） | 无 |
| `TIMICC_BASE_URL` | Responses API 地址 | `https://timicc.com` |
| `TIMICC_MODELS` | 额外允许的模型 ID，逗号分隔 | 无 |
| `TIMICC_STATE_DIR` | 后台任务数据库目录 | 操作系统的用户 state 目录 |
| `TIMICC_JOB_WORKERS` | 后台并发数 | `2` |

默认允许 `gpt-5.6-sol`、`gpt-5.5` 和 `gpt-5.4`。如果兼容网关提供其他 `gpt-*` 模型，可通过 `TIMICC_MODELS` 显式添加。

## 开发与验证

测试不会调用真实 API：

```bash
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest -q
.venv/bin/python -m build
```

GitHub Actions 会在 Linux、Windows 和 macOS 的 Python 3.10/3.13 上运行相同验证。

## 安全模型

返回的补丁始终是不可信候选内容。调用方仍需审查完整 diff、应用补丁并运行格式化、lint、构建和测试。`allowed_paths` 只能约束输出补丁路径，不能识别输入中是否包含敏感数据。
