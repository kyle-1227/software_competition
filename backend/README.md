# 后端说明

后端基于 FastAPI，负责设备检修 Agent 的 API、工作流编排、工具调用、记忆、沙箱执行、Trace、Eval 和 Metrics。

## 当前能力

- API 层：提供统一 `/api` 前缀、健康检查、手册注册、查询、Trace 查询和导出接口。
- Workflow 层：`AgentHarness` 通过 LangGraph `StateGraph(HarnessState)` 编排固定拓扑。
- Agent Loop：支持有限 retry、clarification、approval、fail-safe 和答案再生成。
- Worker 层：通过 Orchestrator 分发 `fault_triage`、`sop_guidance`、`ai_coding` 等 worker。
- Tool 层：`ToolRegistry` 统一注册 `manual_lookup`、`ai_coding`、`compliance_check`。
- Memory 层：`MemoryStore` 支持 session 级历史窗口和摘要压缩。
- Sandbox 层：`SandboxExecutor` 支持受限 Python / 只读 SQL，默认拒绝 Shell。
- Trace / Eval / Metrics：支持 span、summary、timeline、tree、analytics、eval dataset、metrics 和 retention / cleanup。

## 当前生产级差距

后端已经具备可演示、可测试、可观测的 Agent Harness，但以下执行层还没有达到完整生产级：

| 层级 | 当前状态 | 生产级差距 |
|---|---|---|
| Runtime Contract / State Machine | `AgentHarness` 直接构造初始 state 并调用 graph | 需要统一 `RuntimeStateFactory`、`RuntimeExecutor`、`RuntimeResultAdapter`，明确 step 生命周期、取消、超时和 max steps |
| LangGraph / Workflow | 已拆分 intake、guardrail、memory、orchestrator、worker、loop、eval、trace、finalize | graph 输出仍需统一为 `RuntimeResult`，避免节点直接面向 `QueryResponse` |
| Tool / Worker / Policy | 已有 `ToolResult`、retry、degraded、fallback 基础能力 | 需要统一 approval、timeout、audit、side-effect policy 和 `RuntimeEvent` 封装 |
| Memory | 当前为进程内 session history + summary | 需要 PostgreSQL / Vector DB 持久化、retrieval、eval、fallback、low confidence 策略 |
| Sandbox | 当前为比赛演示级受限执行器 | 需要 Local / Docker / Remote 后端隔离、资源限制、side-effect 控制和审计 |
| API / CLI / Dependency | FastAPI、SSE、Trace API 已可用 | CLI wrapper 需要瘦身，依赖注入和运行配置还需按生产部署收敛 |

Trace / Observability / Metrics / Eval 已具备生产级基础，后续主要增强 access control、async span writer、alerting、operational dashboard 和 eval feedback loop。

## 运行后端

进入后端目录并安装依赖：

```bash
cd backend
pip install -r requirements.txt
```

启动开发服务：

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 关键环境变量

```text
APP_ENV=development
API_PREFIX=/api

TRACE_BACKEND=auto        # auto | postgres | jsonl
TRACE_DATABASE_URL=postgresql://app:***@localhost:5432/software_competition
TRACE_CAPTURE_MODE=summary

DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro

SILICONFLOW_API_KEY=
EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5
RERANKER_ENABLED=true
```

`TRACE_BACKEND=postgres` 用于生产环境 fail fast；`TRACE_BACKEND=auto` 允许本地在无数据库时回落到 JSONL。

## 测试

在仓库根目录运行：

```bash
pytest backend/tests
```

Trace / Eval / Metrics 相关测试集中在：

- `backend/tests/test_trace_*.py`
- `backend/tests/test_traces_api.py`
- `backend/tests/test_eval_metrics.py`
- `backend/tests/test_export_trace_eval_cases.py`

Agent Runtime / Workflow / Tool / Memory / Sandbox 相关测试集中在：

- `backend/tests/test_agent_harness.py`
- `backend/tests/test_graph_*.py`
- `backend/tests/test_agent_loop_*.py`
- `backend/tests/test_worker_dispatcher.py`
- `backend/tests/test_tools.py`
- `backend/tests/test_memory_store.py`
- `backend/tests/test_sandbox.py`

## 后续生产级优化入口

详细路线见 [`../docs/production_agent_evolution_plan.md`](../docs/production_agent_evolution_plan.md)。

当前优先级：

1. Runtime Contract + Execution State Machine。
2. Workflow 节点拆分收敛到统一 `RuntimeResult` 输出。
3. Tool / Worker / Policy 完整化。
4. Memory 持久化 + Retrieval + Fallback。
5. Sandbox 安全隔离与后端插件化。
6. End-to-End 测试 + Operational Metrics / Eval Feedback Loop。
