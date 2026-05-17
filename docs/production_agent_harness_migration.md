# 生产级 Agent Harness 改造蓝图

目标：将当前“比赛演示级工作流”升级为可落地、可审计、可评测、可安全执行的生产级 Agent Harness。

本蓝图以当前仓库的实际代码为基础：`backend/app/services/agent_harness_lc.py`、`graph/graph_builder.py`、`retriever.py`、`manual_indexer.py`、`tool_registry.py`、`guardrails/*`、`evaluator_optimizer.py`、`sandbox.py`、`trace_store.py`、`memory_store.py`。

参考方向：OpenAI Agents SDK/Responses API 的工具、guardrails、handoff、tracing、evals 思路；Anthropic《Building Effective Agents》中的 prompt chaining、routing、parallelization、orchestrator-workers、evaluator-optimizer 模式；Anthropic MCP 的工具标准化思想。

---

## 1. 当前架构定位

当前系统已经不是简单 mock，而是初版 Agentic Workflow：

```text
query
  -> input_guardrail
  -> memory_load
  -> orchestrator
  -> worker_executor
       -> fault_triage / sop_guidance / ai_coding
  -> bounded_agent_loop
       -> retrieval_retry / approval / clarification / fail_safe
  -> evaluator_optimizer
  -> output_guardrail
  -> trace + memory_save + finalize
```

现状优点：

- 已使用 LangGraph 组织 Harness DAG。
- 已有 Orchestrator-Workers 雏形。
- 已有 Bounded Agent Loop、retrieval retry、approval、clarification、fail-safe。
- 已有 RAG、reranker、HyDE/query rewriter 接口。
- 已有 input/output guardrail、evaluator-optimizer、trace、memory、sandbox。

核心短板：

- 手册 ingestion 没闭环，`register_manual()` 还没有真正解析 PDF、分块、构建索引。
- RAG fallback 和 answer fallback 中存在硬编码维修知识，影响 groundedness。
- Orchestrator 更像 intent router，不是真正 planner。
- ToolRegistry 不是生产级工具协议层，缺权限、预算、审批、版本、结构化错误。
- Sandbox 明确是演示级，不是强隔离执行环境。
- Guardrail 主要靠关键词与简单 LLM 判断，缺 tool guardrail / pre-exec guardrail。
- Evaluator 没有离线 eval dataset 与 CI 回归。
- Memory 是进程内字典，不适合多实例或长期会话。
- Trace 已记录，但还没有形成 observability + eval flywheel。

---

## 2. 目标架构

生产级 Harness 应升级为：

```text
API Gateway
  -> Auth / tenant / rate limit
  -> Run Manager
      -> Conversation State Store
      -> Trace Session
      -> Policy Engine
      -> Planner
          -> Task Plan
          -> Risk Classification
          -> Tool Budget
          -> Human Approval Plan
      -> Executor
          -> Tool Broker
          -> RAG Tool
          -> Coding Tool
          -> Sandbox Service
          -> Compliance Tool
      -> Evaluator
          -> Groundedness
          -> Retrieval Quality
          -> Safety
          -> Task Success
      -> Guardrails
          -> input guardrail
          -> plan guardrail
          -> tool guardrail
          -> output guardrail
      -> Finalizer
          -> cited answer
          -> trace id
          -> audit metadata
```

关键原则：

1. 证据必须来自可追溯来源。
2. 模型不能直接决定高风险动作是否执行。
3. 工具调用必须经过 policy、budget、timeout、schema validation。
4. 执行环境必须隔离。
5. 每一次运行都必须可审计、可复现、可评测。
6. 低置信度或高风险必须进入人工确认或 fail-safe。

---

## 3. P0：先修可信证据链

### 3.1 完整接入手册入库流程

新增或重构：

```text
backend/app/services/ingestion/
  pdf_loader.py
  ocr_loader.py
  layout_parser.py
  chunker.py
  index_builder.py
  index_registry.py
```

目标接口：

```python
class ManualIngestionPipeline:
    async def ingest(file_path: str, manual_id: str, device_model: str | None) -> IngestionResult:
        ...
```

输出：

```json
{
  "manual_id": "...",
  "source_file": "...",
  "page_count": 41,
  "chunk_count": 320,
  "index_id": "manual_xxx_v1",
  "embedding_model": "...",
  "chunks_sha256": "...",
  "status": "ready"
}
```

改造点：

- `manual_indexer.py` 中的 `register_manual()` 不再只返回“等待索引构建”，而是触发 ingestion job。
- `manual_vector_indexer.py` 改为支持多 manual、多 device_model、多版本 index。
- `retriever.py` 必须基于 `manual_id/device_model/index_version` 检索。

### 3.2 移除硬编码维修参数

必须修改：

- `backend/app/services/answer_generation.py`
- `backend/app/services/retriever.py`

规则：

- fallback answer 不得写死页码、火花塞间隙、气门间隙等参数。
- 没有 evidence 时只能回答“证据不足，需要补充手册或型号”。
- 有 evidence 时只允许引用 evidence 中出现的页码、章节、参数、片段。

---

## 4. P1：升级 Planner，而不是只做路由

当前 `orchestrator.py` 主要是选择 worker。生产级应输出结构化 TaskPlan。

新增：

```text
backend/app/schemas/plan.py
backend/app/services/planner.py
backend/app/services/policy_engine.py
```

建议 schema：

```python
class TaskPlan(BaseModel):
    goal: str
    intent: str
    risk_level: Literal["low", "medium", "high"]
    evidence_required: bool
    human_approval_required: bool
    tool_budget: ToolBudget
    subtasks: list[SubTask]
    stop_conditions: list[str]
```

Planner 输出后，先进入 `plan_guardrail`：

```text
orchestrator_node
  -> plan_guardrail_node
  -> worker_executor_node
```

这样可以在执行工具前拦截风险，而不是等工具执行后再补救。

---

## 5. P1：Tool Broker 替换简单 ToolRegistry

当前 `ToolRegistry` 可保留，但其上层要增加 `ToolBroker`：

```text
backend/app/services/tools/broker.py
backend/app/services/tools/contracts.py
backend/app/services/tools/policy.py
```

ToolBroker 负责：

- 参数 schema validation。
- 工具权限判断。
- 工具调用预算。
- timeout / retry / circuit breaker。
- 结构化错误。
- tool-level trace span。
- human approval gate。
- tool result verifier。

建议执行流：

```text
Agent asks tool
  -> ToolBroker.validate_schema
  -> PolicyEngine.authorize
  -> ApprovalGate.check
  -> BudgetManager.reserve
  -> tool.execute
  -> ResultVerifier.check
  -> TraceStore.record
  -> return ToolResult
```

关键：`ai_coding` 工具只能生成代码，不能声明 `execution_allowed=True`。执行权限必须由 PolicyEngine + SandboxGate 决定。

---

## 6. P1：Guardrail 分层

当前有 input/output guardrail，但生产级应拆成四层：

```text
input_guardrail
plan_guardrail
工具调用前 tool_guardrail
output_guardrail
```

新增：

```text
backend/app/services/guardrails/plan_guard.py
backend/app/services/guardrails/tool_guard.py
backend/app/services/guardrails/prompt_injection_guard.py
backend/app/services/guardrails/evidence_guard.py
```

重点规则：

- 不允许模型基于外部文档中的指令改变系统规则。
- 不允许未审批执行高风险工具。
- 不允许输出未被 evidence 支撑的页码、参数、维修结论。
- 不允许把 tool 输出中的非可信文本当系统指令。

---

## 7. P1：建立 Eval Dataset 和 CI 回归

新增：

```text
evals/
  datasets/
    retrieval_cases.jsonl
    safety_cases.jsonl
    tool_routing_cases.jsonl
    answer_groundedness_cases.jsonl
  graders/
    retrieval_grader.py
    groundedness_grader.py
    safety_grader.py
  run_eval.py
```

至少覆盖：

- 检索是否命中正确页码。
- answer 是否引用了 evidence。
- 高风险问题是否进入 approval/fail-safe。
- 没证据时是否拒绝编造。
- ai_coding 是否经过 sandbox gate。
- prompt injection 是否被拦截。

CI 中加入：

```bash
pytest
python evals/run_eval.py --dataset evals/datasets/retrieval_cases.jsonl
python evals/run_eval.py --dataset evals/datasets/safety_cases.jsonl
```

---

## 8. P2：Memory 和 Trace 生产化

### 8.1 Memory

替换进程内 dict：

```text
backend/app/services/memory/
  repository.py
  summarizer.py
  schemas.py
```

建议先 SQLite，后续 PostgreSQL：

- session 表
- message 表
- memory_summary 表
- tenant/user 字段
- retention 策略

### 8.2 Trace

现有 `TraceStore` 可以保留，但要补：

- run_id / user_id / session_id / model_version / prompt_version / index_version
- latency / token usage / cost
- tool error taxonomy
- eval_result
- export API
- dashboard 页面

Trace 不只是日志，而是 eval 数据来源。

---

## 9. P2：Sandbox 强隔离

当前 `sandbox.py` 明确是演示级。生产级建议：

```text
backend/app/services/sandbox/
  policy.py
  docker_executor.py
  local_executor.py
  result_verifier.py
```

默认策略：

- 禁止网络。
- 限制 CPU / memory / time。
- 只读根文件系统。
- 临时工作目录。
- 非 root 用户。
- shell 默认关闭。
- 高风险脚本必须人工审批。

本地开发可保留旧 executor，但生产配置必须切换为容器 executor。

---

## 10. 推荐实施顺序

### Sprint 1：可信 RAG

- 移除硬编码 answer fallback。
- 接入真实 ingestion pipeline。
- 支持 manual_id/index_version。
- 加 retrieval eval。

### Sprint 2：Planner + Policy

- 新增 TaskPlan schema。
- Orchestrator 输出 TaskPlan。
- 增加 plan_guardrail。
- 增加 PolicyEngine。

### Sprint 3：Tool Broker

- ToolRegistry 外包一层 ToolBroker。
- 工具调用统一通过 broker。
- 加 tool budget、approval、structured error。
- ai_coding 执行权限迁移到 PolicyEngine。

### Sprint 4：Eval + Observability

- 建 evals 目录。
- 加 groundedness/safety/routing/retrieval eval。
- Trace 增加 run metadata。
- 低置信度 trace 自动回流 eval dataset。

### Sprint 5：Production Runtime

- Memory 持久化。
- Sandbox 容器化。
- 增加 auth/rate limit/tenant。
- 增加 deployment profile：dev/demo/prod。

---

## 11. 可直接给 Codex 的总 Prompt

```text
你现在在 kyle-1227/software_competition 仓库中工作。目标是将当前比赛演示级 Agentic Workflow 升级为生产级 Agent Harness。请严格基于现有代码演进，不要推倒重写。

第一阶段只做 P0 + P1 的最小闭环：

1. 移除 answer_generation.py 和 retriever.py 中所有硬编码维修参数、页码、固定部件知识。没有 evidence 时必须返回证据不足；有 evidence 时只能引用 evidence 中存在的信息。
2. 将 manual_indexer.register_manual 改造为真实 ingestion pipeline 的入口：新增 ingestion 模块，至少支持从已存在的 manual_chunks.jsonl 构建 index，并为未来 PDF/OCR 预留接口。
3. 引入 manual_id/index_version/index_meta，Retriever 检索结果必须携带 manual_id、chunk_id、page、source、index_version。
4. 新增 TaskPlan schema 和 Planner/PolicyEngine 雏形，让 Orchestrator 不只返回 workers，而是返回 goal、risk_level、subtasks、tool_budget、human_approval_required、stop_conditions。
5. 新增 plan_guardrail_node，在 worker_executor_node 之前执行。高风险且证据不足的任务进入 approval/fail-safe。
6. 新增 ToolBroker，先包装现有 ToolRegistry，不破坏原有工具。所有 manual_lookup、compliance_check、ai_coding 调用逐步改为通过 ToolBroker。
7. ai_coding 工具不再返回 execution_allowed=True；是否执行由 PolicyEngine + SandboxExecutor 决定。
8. 新增 evals/ 目录，加入最小 retrieval、groundedness、safety、tool_routing 测试样例和 pytest/CLI runner。
9. 所有新增逻辑必须有单元测试；不能破坏现有 API schema。
10. 每一步保留 trace metadata：run_id、trace_id、manual_id、index_version、tool_name、risk_level、approval_required、fallback_used。

请按小步提交，优先保证现有测试通过，再逐步替换调用链。
```
