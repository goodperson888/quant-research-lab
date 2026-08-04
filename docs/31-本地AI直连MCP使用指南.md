# 本地 AI 直连 MCP 使用指南

状态：商业 MVP 已实现
适用：Codex、Claude Code/Claude Desktop，以及其他支持 MCP stdio 的现代本地 AI 客户端

## 1. 它解决什么问题

Quant Research Lab 同时保留两条本地 AI 路径：

```text
网页 → Local Connector → Codex CLI
本地 AI 对话 → MCP stdio → Quant Lab 领域服务
```

第一条适合用户从网页提交策略；第二条适合用户直接在 Codex 或 Claude 对话中说“研究这个
策略”。两条路径共用同一 ResearchSession、StrategyDraft、Baseline、Job、Artifact、
ResearchHandoff 和 append-only audit，不创建第二套研究数据。

MCP Server 由客户安装包内的编译运行器按需启动。它不要求客户获得 Git 仓库或 Python
源码，也不开放公网端口。

## 2. 首次连接 Codex

1. 安装并启动 Quant Research Lab；
2. 打开“本地设置 → 本地 AI 直连”；
3. 点击“复制安装命令”；
4. 在终端执行；
5. 重启 Codex；
6. 用页面提供的检查命令确认 `quant-research-lab` 已存在。

开发环境对应命令类似：

```bash
codex mcp add quant-research-lab -- \
  /absolute/project/.venv/bin/python \
  -m quant_lab.interfaces.mcp.server
```

商业安装包会根据真实安装位置生成命令，macOS 和 Windows 路径不需要用户手写。重复添加
前可先运行：

```bash
codex mcp remove quant-research-lab
```

## 3. 连接 Claude 或其他客户端

设置页会生成当前机器的 MCP JSON，例如：

```json
{
  "mcpServers": {
    "quant-research-lab": {
      "command": "/Applications/Quant Research Lab.app/Contents/MacOS/QuantResearchLab",
      "args": [
        "--role",
        "mcp",
        "--data-home",
        "/Users/customer/Library/Application Support/QuantResearchLab"
      ]
    }
  }
}
```

把它放入客户端自己的 MCP 配置入口并重启客户端。不同厂商的配置文件位置可能变化，
以对应客户端当前文档为准。

## 4. 在本地 AI 中如何使用

用户可以直接说：

> 帮我研究一个 ETH 15 分钟 EMA20/EMA60 交叉策略，ATR 止损。先梳理歧义，不要直接回测。

AI 应按以下顺序调用：

1. `start_strategy_research`：保存原始策略；
2. 在对话中解释交易语义和歧义；
3. `propose_strategy_formalization`：保存结构化提案，等待用户确认；
4. 用户明确确认后调用 `confirm_strategy_formalization`；
5. 用户第二次明确批准后调用 `freeze_strategy_baseline`；
6. 用户批准 exact Baseline subject 后调用 `authorize_research_to_viability`；
7. 用 `get_job` 或 `get_research_session` 查看真实任务、停止原因和下一步。

只说“我已经分析好了”不代表程序执行完成。AI 必须读取真实 Job、Report、Artifact 或
Handoff，才能声称某个研究阶段已经运行。

## 5. 当前 MCP 工具

| 工具 | 类型 | 作用 |
|---|---|---|
| `quant_lab_status` | 只读 | 授权、数据位置和安全边界 |
| `list_research_sessions` | 只读 | 列出研究会话 |
| `get_research_session` | 只读 | 会话、Draft、版本、预算、Job、Handoff |
| `get_job` | 只读 | Job 状态和日志 |
| `start_strategy_research` | 写入 | 保存原文和创建会话 |
| `propose_strategy_formalization` | 写入 | 保存 AI 提案，不替用户确认 |
| `confirm_strategy_formalization` | 审批 | 记录用户对结构化规则的明确确认 |
| `freeze_strategy_baseline` | 审批 | 冻结不可覆盖的 Baseline v0 |
| `authorize_research_to_viability` | 审批 | 有预算地排队到 Viability |

参数批量搜索、候选 Diff、最终保留测试和 dry-run 后续按真实用户路径逐步增加，不能通过
通用 Shell 或自由 HTTP 工具绕过。

## 6. 安全与授权

MCP Server：

- 只使用本机 SQLite、Artifact 和领域服务；
- 只提供固定工具，不提供 `run_shell`、任意文件路径或任意 HTTP；
- 不读取或保存交易所密钥；
- 没有 live trade、自动 production 或自动 Git 工具；
- 许可证到期后仍允许读取，禁止创建新研究；
- 商业许可证必须包含 `local_ai` 或 `all` 功能；
- 最终保留测试、参数搜索和 dry-run 不包含在“研究到 Viability”授权内。

Skill/提示词只负责告诉 AI 如何编排；真正的许可证、Baseline 不可覆盖、审批、预算和 Job
白名单由后端执行。

## 7. 主程序是否必须运行

MCP stdio 进程可以读取和建档，但确定性 Worker 由 Quant Research Lab 主程序托管。要让
排队回测继续执行，应保持主程序运行。若主程序关闭，Job 会保留在 SQLite 中，重新启动后
继续由 Worker 领取，不会伪装完成。

## 8. 常见问题

### AI 中没有出现工具

1. 确认客户端支持 MCP stdio；
2. 执行设置页的检查命令；
3. 重启 AI 客户端；
4. 检查安装路径是否移动；
5. macOS 首次运行先正常打开一次应用。

### 可以自动发现所有本地 AI 吗

不能，也不应偷偷扫描或接管所有客户端。产品可以检测已知 Codex 路径并生成配置，但每个
AI 客户端都需要客户主动添加一次 MCP Server。

### 是否会连接当前打开的旧对话

不会。添加 MCP 后，新旧对话是否加载工具由 AI 客户端决定。MCP 只提供工具，不读取其他
对话历史。

### 客户能否看到源码

商业包调用编译后的本地运行器。MCP 客户端能看到工具名称、参数 Schema 和返回的研究
结果，但不需要获得项目 Python/TypeScript 源码或许可证私钥。
