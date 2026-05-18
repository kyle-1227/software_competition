# 设备检修智能辅助系统

本仓库是一个面向比赛场景的设备检修知识检索、Agent 作业辅助与 Trace 可观测系统，包含 FastAPI 后端和 Vite + React 前端。

## 当前状态

项目目前已经从早期接口骨架推进到可演示的 Agent Harness：

- 后端基于 FastAPI，提供统一 API 前缀和统一响应结构。
- `/api/query` 已接入 LangGraph Workflow、Agent Loop、Worker Dispatch、Evaluator、Guardrail、Trace 和 Memory。
- ToolRegistry 已提供 `manual_lookup`、`ai_coding`、`compliance_check` 等工具入口。
- Trace API 已支持 summary、timeline、spans、tree、analytics、metrics 和导出能力。
- 前端已改造成参考 Claude 网页版体验的三栏工作台。
- 前端包含左侧导航/历史、中间对话区、底部输入框和右侧 Artifact 工作区。
- 右侧工作区提供 SOP、证据、记录三个视图，适合展示维修指导和检索来源。

当前仍需重点生产级优化的层：

- Runtime Contract / State Machine 仍缺统一的 Step 生命周期、取消、超时、max steps 和 `RuntimeResult` 契约。
- Workflow 节点已拆到 LangGraph，但 graph 输出仍需要从 QueryResponse 构造逻辑中进一步解耦。
- Tool / Worker / Policy 已有 retry、degraded、fallback 基础能力，但审批、审计、side-effect policy 还需要统一。
- Memory 当前是进程内 session 摘要，尚未升级到 PostgreSQL / Vector DB 持久化和低置信度策略。
- Sandbox 当前是比赛演示级受限执行器，生产环境需要 Docker / Remote / 专用沙箱后端隔离。

## 生产级 Agent 当前完成度

已具备生产级基础的观察与评估层：

- Trace：支持 PostgreSQL / JSONL fallback、span、summary、timeline、tree、analytics、export。
- Eval：支持 trace 转 eval case、grader registry、失败分析和回归入口。
- Metrics：支持 degraded、fallback、cancelled、confidence、evidence 等统计信号。
- Retention / Cleanup：已有 trace retention policy、cleanup 脚本和相关测试。

仍在演进中的执行层：

- Runtime / Workflow / Tool / Memory / Sandbox 是下一阶段主线。
- 详细缺口登记见 [`docs/production_readiness_gaps.md`](docs/production_readiness_gaps.md)。
- 生产级演进路线见 [`docs/production_agent_evolution_plan.md`](docs/production_agent_evolution_plan.md)。

## 仓库结构

- `backend/`：FastAPI 后端服务、路由、服务层和测试。
- `frontend/`：Vite + React 前端页面。
- `data/`：维修手册、处理结果、索引文件和上传数据。
- `docs/`：比赛文档和交付材料。

当前已放入的手册文件：

- `data/raw/manuals/摩托车发动机维修手册.pdf`

## 前端启动

联调真实检索结果时需要先启动后端，再启动前端。只运行前端时，页面会保留 mock 展示，但无法调用 `/api/query`。

先启动后端：

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

再另开一个终端进入前端目录，安装依赖并启动开发服务器：

```bash
cd frontend
npm install
npm run dev
(npm run dev -- --open)自动打开网页
```

启动成功后，终端会显示类似下面的地址：

```text
Local: http://localhost:8001/
```

浏览器打开终端里的 `Local` 地址即可。`npm run dev` 会一直运行，这是正常现象；停止服务请在终端按 `Ctrl + C`。

如果希望启动时自动打开浏览器，可以执行：

```bash
npm run dev -- --open
```

## 前端页面说明

本次前端页面参考 Claude 网页版的工作台结构做了适配：

- 左侧：品牌区、新会话、搜索、功能导航、最近对话。
- 中间：项目切换、模式切换、推荐问题卡片、对话消息和输入框。
- 右侧：类似 Artifact 的维修工作区，可切换 SOP、证据和记录。
- 响应式：宽屏三栏展示，中等屏幕收起侧边栏文字，小屏幕纵向排列。

页面当前以比赛展示和接口联调为主，后续重点是接入更完整的 Trace 展示、运行状态和生产级告警视图。

## 后端启动

进入后端目录，安装 Python 依赖：

```bash
cd backend
pip install -r requirements.txt
```

启动后端服务：

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 接口概览

接口统一前缀：

```text
/api
```

当前已实现接口：

- `GET /`
- `GET /api/health`
- `POST /api/manuals/register`
- `POST /api/query`

统一响应结构示例：

```json
{
  "success": true,
  "data": {},
  "error": null,
  "trace_id": "uuid"
}
```

错误响应示例：

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求参数校验失败",
    "details": {}
  },
  "trace_id": "uuid"
}
```

## 测试

在仓库根目录运行：

```bash
pytest backend/tests
```

## Trace Usage Layer

Trace span coverage now feeds a small usage layer for debugging, demos, and eval review.

- Trace Summary: `app.services.tracing.summary.build_trace_summary(trace)` returns a compact structured view of span count, errors, slow spans, retrieval pages, tool degradation, LLM fallback, evaluator confidence, approval, and fail-safe signals.
- Trace Timeline: `app.services.tracing.timeline.build_trace_timeline(trace)` exports a Markdown timeline sorted by span start time, using only safe summary fields.
- Eval Failure Analysis: `app.services.tracing.analysis.analyze_eval_case_trace(case_result, trace_summary)` links failed eval cases to likely causes such as retrieval miss, missing index placeholder, tool degradation, LLM fallback, low evaluator confidence, approval gate, or fail-safe.

Safety boundary: these outputs do not include full prompts, answers, scripts, large evidence, API keys, tokens, passwords, secrets, or reasoning/thinking fields.

## Production Readiness Gaps

Open production gaps are tracked in [`docs/production_readiness_gaps.md`](docs/production_readiness_gaps.md). Current priority is Runtime / Workflow / Tool / Memory / Sandbox. Trace / Observability / Metrics / Eval already have a production-ready foundation, but still need access control, async span writing, alerting, and operational dashboards.

## Production Agent Evolution

下一阶段采用“固定 workflow + 局部自适应”的生产级 Agent 路线：

1. Commit 8：Runtime Contract + Execution State Machine。
2. Commit 9：Workflow 节点拆分 + `RuntimeResult` 输出。
3. Commit 10：Tool / Worker / Policy 完整化。
4. Commit 11：Memory 持久化 + Retrieval + Fallback。
5. Commit 12：Sandbox 安全隔离与后端插件化。
6. Commit 13：End-to-End 测试 + Operational Metrics / Eval Feedback Loop。

详细计划见 [`docs/production_agent_evolution_plan.md`](docs/production_agent_evolution_plan.md)。

## Trace Export & API

Closed traces can be exported by id for local debugging, eval failure review, frontend trace display, and competition demos.

Production trace persistence supports PostgreSQL with JSONL fallback:

```text
TRACE_BACKEND=auto        # auto | postgres | jsonl
TRACE_DATABASE_URL=postgresql://app:***@localhost:5432/software_competition
TRACE_CAPTURE_MODE=summary
```

Use `TRACE_BACKEND=postgres` to fail fast when PostgreSQL is unavailable. Leave it as `auto` for local JSONL fallback when no database URL is configured.

CLI:

```bash
python -m app.evals.export_trace --trace-id <id> --format summary
python -m app.evals.export_trace --trace-id <id> --format timeline
python -m app.evals.export_trace --trace-id <id> --format raw --pretty
```

API:

```text
GET /api/traces/{trace_id}
GET /api/traces/{trace_id}/summary
GET /api/traces/{trace_id}/timeline
GET /api/traces
GET /api/traces/{trace_id}/spans
GET /api/traces/{trace_id}/tree
GET /api/traces/{trace_id}/analytics
```

The raw export is sanitized before output and follows the same safety boundary as the summary and timeline.

## 下一步目标

1. 建立统一 `RuntimeStateFactory`、`RuntimeExecutor`、`RuntimeResultAdapter` 契约。
2. 将 LangGraph 输出改为统一 `RuntimeResult`，由 API 层适配为 `QueryResponse`。
3. 统一 Tool / Worker / Policy 的 timeout、retry、approval、audit、side-effect 规范。
4. 将 Memory 升级为可持久化、可检索、可评估、可降级的生产级层。
5. 将 Sandbox 升级为 Local / Docker / Remote 可插拔后端，并接入 ToolPolicy 与 Runtime。
