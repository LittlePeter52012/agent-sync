# Private Agent Hub / 私有 Agent Hub

This directory stores your desired shared Agent configuration. `agent-sync` is
the public synchronizer; this Hub is your private state. No shared capabilities
are active after initialization.

本目录保存你期望共享的 Agent 配置。`agent-sync` 是公开同步器；本 Hub 是你的
私有状态。初始化后不会自动启用任何共享能力。

## Adopt an existing Agent configuration / 采用已有 Agent 配置

```bash
agent-sync sources
# Replace TOOL with the recommended source shown above.
agent-sync sync --from TOOL --dry-run
agent-sync sync --from TOOL
```

## Start from scratch / 从零开始

1. Add Skill folders under `skills/` and list their names in `manifest.yaml`.
2. Add shared MCP definitions to `mcp/shared-servers.json`; use `${ENV_VAR}`
   placeholders instead of plaintext credentials.
3. Add optional shared instruction files under `rules/`.

然后执行：

```bash
agent-sync all
agent-sync verify --strict --scope managed
```

Optional demonstration files remain in the public agent-sync repository under
`examples/`; copy only the examples you deliberately choose.

可选演示文件仍保留在公开 agent-sync 仓库的 `examples/` 中；请只复制你明确选择
启用的样例。
