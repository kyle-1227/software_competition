# Production Readiness Gaps

本文件用于持续登记当前 Agent Harness 各架构层尚未达到生产级的根因问题。

项目目标不是比赛演示，而是长期可维护、可部署、可观测、可测试、可迁移、可审计的生产级 Agent Harness。后续发现任何架构层存在生产级缺口，都应追加到本文档，而不是散落在临时 issue、提交说明或代码注释里。

## 使用原则

- 只记录根因问题，不记录临时现象。
- 每个问题必须说明影响、当前状态、目标状态和验收标准。
- PostgreSQL 是生产持久化目标；JSONL / InMemory 只能作为明确 fallback。
- fallback 必须显式记录 `fallback_used`、`degraded` 和原因。
- 不允许为快速通过测试而降低生产约束。
- 涉及 API key、token、password、secret、reasoning、chain-of-thought、完整 prompt、完整 answer、完整 script 的问题必须按安全风险处理。

## 记录模板

```md
### GAP-<layer>-<number>: <问题标题>

- Layer: Trace / Memory / RAG / ToolBroker / Sandbox / Guardrail / Eval / API / Frontend
- Priority: P0 / P1 / P2
- Status: open / in_progress / mitigated / closed
- Owner: TBD
- Current state:
- Production target:
- Risk:
- Required work:
- Acceptance criteria:
```

## Execution Layers

### GAP-RUNTIME-001: Runtime Contract 与 State Machine 尚未统一

- Layer: Runtime
- Priority: P0
- Status: open
- Owner: 同学 A
- Current state: `AgentHarness` 直接从 `QueryRequest` 构造 dict state，并调用 LangGraph `ainvoke()`；Step 生命周期、取消、超时、max steps 和最终结果适配分散在 harness、graph node 和 API schema 之间。
- Production target: 引入 `RuntimeStateFactory`、`RuntimeExecutor`、`RuntimeResultAdapter` 和统一 `RuntimeResult`，由 Runtime 层负责执行生命周期、超时、取消、错误归一化和结果适配。
- Risk: API 层、Workflow 层和 Trace 层继续耦合，后续扩展 Tool、Memory、Sandbox 时容易出现字段漂移和不可回放执行。
- Required work:
  - 定义标准 runtime id / trace id / session id / request metadata。
  - 定义 Step 状态：pending、running、success、error、degraded、cancelled。
  - 将 max steps、timeout、cancel、exception mapping 收敛到 `RuntimeExecutor`。
  - 将 `QueryResponse` 构造移动到 `RuntimeResultAdapter`。
- Acceptance criteria:
  - API 层只接收 `RuntimeResult` 并适配响应，不读取 graph 临时字段。
  - 超时、取消、异常、degraded 都能进入 RuntimeResult 和 Trace。
  - 测试覆盖 success、timeout、cancelled、max steps exceeded、node error。

### GAP-WORKFLOW-001: Workflow 输出契约仍未完全从 QueryResponse 解耦

- Layer: Planner / Orchestrator / Workflow
- Priority: P0
- Status: open
- Owner: 同学 A
- Current state: LangGraph 已拆成 intake、guardrail、memory、orchestrator、worker、loop、eval、trace、memory_save、finalize 等节点，但 `finalize_node` 仍直接拼装面向 API 的 response dict。
- Production target: Graph 只输出 `RuntimeResult`，节点只负责局部 state update 和 RuntimeEvent，API response 由 adapter 生成。
- Risk: 节点内部容易混入 API 展示字段，导致 Workflow 难以复用到 CLI、batch eval、SSE replay 或离线回放。
- Required work:
  - 明确每个节点的输入字段、输出字段和错误语义。
  - 将 `finalize_node` 从 API response builder 改成 runtime finalizer。
  - 将 retry、approval、clarification、fail-safe 的路径输出统一为 RuntimeResult outcome。
  - 保持 graph 拓扑固定，只允许节点内部局部自适应。
- Acceptance criteria:
  - 同一个 graph 结果可被 API、CLI、eval runner 复用。
  - Workflow 测试覆盖所有条件分支。
  - Trace timeline 能稳定展示每个节点的进入、退出、失败和降级原因。

### GAP-TOOL-001: Tool / Worker / Policy 缺少统一生产调用规范

- Layer: ToolBroker / Tool Runtime / Worker / Policy
- Priority: P0
- Status: open
- Owner: 同学 B
- Current state: `ToolRegistry` 已有 `ToolResult`，Agent Loop 已有 retry 和 degraded 基础能力；但 timeout、approval、audit、side-effect、ToolPolicy 与 Worker 输出尚未形成统一契约。
- Production target: 工具和 worker 都通过统一 policy 调用，输出封装为 `ToolResult` / `RuntimeEvent`，并完整进入 Trace、Metrics 和 Eval。
- Risk: 高风险工具可能绕过审批；不同 worker 的错误和降级字段不一致；工具副作用难以审计。
- Required work:
  - 定义 `ToolPolicy`：timeout、retry、approval、audit、side-effect、allowed environments。
  - 扩展 `ToolResult` 或新增 `RuntimeEvent` 字段：attempt、retry_count、fallback_used、degraded、side_effect、policy_decision。
  - 统一 WorkerDispatcher 和 ToolRegistry 的错误处理。
  - 将 tool policy outcome 写入 span metadata 和 metrics。
- Acceptance criteria:
  - 任一工具失败都能区分 transient、policy blocked、timeout、degraded。
  - 高风险工具调用必须进入 approval 或 fail-safe。
  - Metrics 可统计 tool success rate、timeout rate、retry count、degraded rate。

### GAP-MEMORY-001: Memory 仍是进程内窗口，缺少生产持久化与评估

- Layer: Memory
- Priority: P1
- Status: open
- Owner: 同学 B
- Current state: `MemoryStore` 使用进程内 dict 保存 session history，并在窗口达到阈值时做 fallback summary；重启后记忆丢失，缺少 retrieval、eval、fallback 和 low confidence 写入策略。
- Production target: Memory 层支持 PostgreSQL / Vector DB 持久化、CRUD、retrieval、summary、eval feedback、fallback 和低置信度策略。
- Risk: 多实例部署无法共享上下文；错误答案可能进入长期记忆；低置信度和 degraded memory retrieval 无法追踪。
- Required work:
  - 设计 session memory 表和可选向量索引。
  - 定义写入策略：完整内容、摘要、脱敏字段、禁止写入字段。
  - 增加 memory retrieval span、fallback span、low confidence marker。
  - 接入 eval dataset，评估 memory 命中与错误引用。
- Acceptance criteria:
  - 进程重启后 session memory 可恢复。
  - 低置信度、fallback、degraded 回答不会静默写入长期记忆。
  - Memory retrieval miss / hit / degraded 能在 Trace 和 Metrics 中查看。

### GAP-SANDBOX-001: Sandbox 仍是演示级受限执行器

- Layer: Sandbox
- Priority: P0
- Status: open
- Owner: 同学 B
- Current state: `SandboxExecutor` 通过 AST 检查、危险词、临时目录、超时和只读 SQL 降低风险；代码注释已明确它不是强隔离沙箱。
- Production target: Sandbox 层支持 Local / Docker / Remote 后端插件化，并与 `ToolPolicy` / Runtime 集成，具备资源限制、网络限制、文件系统限制、审计和可观测指标。
- Risk: 演示级限制不能承诺生产隔离；Python 子进程和 SQLite 执行缺少容器边界；副作用治理依赖静态检查。
- Required work:
  - 定义 `SandboxBackend` 接口和执行结果 schema。
  - 增加 Docker / Remote backend，限制 CPU、内存、网络、文件系统和执行时长。
  - 将 sandbox policy decision 写入 Trace 和 Metrics。
  - 增加 blocked、timeout、execution error、degraded 测试。
- Acceptance criteria:
  - Shell 默认拒绝，Python / SQL 受后端和 policy 双层限制。
  - 生产后端能阻断越权文件、网络和长时间执行。
  - Sandbox 失败不会导致 Agent 编造结果，而是进入 approval、clarification 或 fail-safe。

### GAP-API-CLI-001: API / CLI / Dependency 边界还需生产化收敛

- Layer: API / CLI / Dependency
- Priority: P1
- Status: open
- Owner: 同学 A
- Current state: FastAPI、SSE、Trace API 和导出 CLI 已可用，但 CLI、API adapter、dependency injection 和生产配置边界还未完全与 Runtime Contract 对齐。
- Production target: API 保持 RESTful / SSE / Metrics 支持，CLI wrapper 只做参数解析和结果输出，依赖注入统一装配 Runtime、Tool、Memory、Sandbox、Trace。
- Risk: API 与 CLI 可能分别走不同执行路径；部署配置分散；生产环境依赖替换成本高。
- Required work:
  - 将 API、SSE、CLI 统一接入 `RuntimeExecutor`。
  - 梳理依赖注入入口，明确 singleton / request scoped service。
  - 为生产环境配置增加 startup validation。
  - 保持 CLI wrapper 瘦身，不承载业务逻辑。
- Acceptance criteria:
  - API、SSE、CLI 对同一请求生成一致 RuntimeResult。
  - 生产必需配置缺失时启动失败或清晰 degraded。
  - 测试覆盖 API 与 CLI 的执行路径一致性。

## Trace / Observability / Metrics / Eval Layer

Trace / Observability / Metrics / Eval 已具备生产级基础：当前系统支持 span、summary、timeline、tree、analytics、metrics、eval dataset、retention 和 cleanup。以下条目记录的是继续增强项，重点是高并发写入、访问控制、数据生命周期、安全审计和 eval 回流。

### GAP-TRACE-001: Span 写入仍为同步逐条写入

- Layer: Trace
- Priority: P1
- Status: open
- Owner: TBD
- Current state: `TraceStore.add_span()` 在主请求路径中同步调用 repository 写入。PostgreSQL 不可用时会记录 `last_error/degraded` 并 fail-open，但高并发下每个 span 仍会带来数据库往返开销。
- Production target: Trace 写入应支持有界异步队列或批量写入，并具备背压、flush、shutdown drain、失败重试和可观测指标。
- Risk: 请求延迟受数据库抖动影响；高 QPS 下数据库连接压力增加；span 写入峰值可能拖慢 Agent 主流程。
- Required work:
  - 设计 `TraceWriteBuffer` 或 background writer。
  - 限制队列大小，队列满时显式降级并记录 drop count。
  - 支持批量 `save_span_batch()`。
  - 暴露 queue depth、dropped span count、flush error count。
- Acceptance criteria:
  - Agent 主流程不直接等待每个 span 的 PostgreSQL round trip。
  - shutdown 时可 drain 未写入 span。
  - `/api/traces/health` 能展示 writer 状态和 drop/degraded 原因。
  - 测试覆盖队列满、写入失败、shutdown flush。

### GAP-TRACE-002: Migration 机制仍是轻量内置版本

- Layer: Trace
- Priority: P1
- Status: mitigated
- Owner: TBD
- Current state: 已有 `trace_schema_migrations`、幂等 `ALTER TABLE ADD COLUMN IF NOT EXISTS`、CHECK constraint 和索引创建，但 migration 仍内置在 repository 代码中。
- Production target: 数据库 schema 迁移应有独立 migration runner、版本文件、checksum 校验、升级/回滚策略和部署前检查。
- Risk: 随字段增多，内置 SQL 难以审计；多环境升级时不易追踪 schema drift；失败回滚策略不够清晰。
- Required work:
  - 引入 Alembic 或项目内 migration runner。
  - 将当前 v1 schema 拆成版本化 migration 文件。
  - 增加 migration dry-run / status 命令。
  - 在部署流程中加入 migration health check。
- Acceptance criteria:
  - 新环境可一键初始化 schema。
  - 旧环境可按版本顺序升级。
  - migration 失败时错误清晰且不会产生半完成状态。
  - CI 覆盖 migration 从空库到最新版本。

### GAP-TRACE-003: Analytics 分类规则仍需要真实样本校准

- Layer: Trace
- Priority: P1
- Status: open
- Owner: TBD
- Current state: `FailureType` 已稳定，且 retrieval / reranker / LLM / tool / sandbox / guardrail / approval / evaluator / memory / repository 优先于 `fallback_degraded`。但分类规则仍主要基于 span 名称和摘要字段。
- Production target: Failure taxonomy 应基于真实 eval/线上失败样本持续校准，形成可回归的 failure dataset，并能解释分类置信度。
- Risk: 复杂失败链路可能被误归因；根因推荐不够精确；eval 回流数据质量受影响。
- Required work:
  - 用 `trace_to_eval_case()` 生成 trace regression cases。
  - 建立 `trace_failure_cases.jsonl` 数据集。
  - 为每类 FailureType 增加正负样例。
  - 增加分类置信度或 matched_signals 输出。
- Acceptance criteria:
  - 每个 FailureType 至少有一个回归样例。
  - retrieval miss、tool degradation、LLM fallback、approval、fail-safe 等常见失败可稳定归因。
  - 分类结果变更会触发测试失败或报告差异。

### GAP-TRACE-004: Trace API 缺少生产级访问控制与审计

- Layer: Trace API
- Priority: P0
- Status: open
- Owner: TBD
- Current state: `/api/traces/*` 已支持 raw、summary、timeline、spans、tree、analytics、health，但没有按 user/tenant/session 做访问控制，也没有查询审计。
- Production target: Trace 查询必须经过认证授权，只允许用户查看自己有权限的 trace，并记录谁在什么时候读取了哪些 trace。
- Risk: Trace 中即使已脱敏，也可能包含业务上下文、设备信息和执行链路，未授权访问会造成审计风险。
- Required work:
  - 接入 auth/user/tenant context。
  - `agent_traces` 增加或严格使用 `user_id/session_id` 查询边界。
  - Trace API 增加权限过滤。
  - 增加 trace access audit log。
- Acceptance criteria:
  - 未认证请求不能读取 trace。
  - 用户不能读取其他 tenant/user 的 trace。
  - 每次 raw/tree/spans/timeline 查询都有审计记录。
  - 测试覆盖越权访问。

### GAP-TRACE-005: Trace 列表缺少分页游标和保留策略

- Layer: Trace API / Trace Repository
- Priority: P1
- Status: open
- Owner: TBD
- Current state: `/api/traces` 支持 `limit/session_id/status`，PostgreSQL list 已返回聚合字段，但还没有 cursor pagination、时间范围过滤、retention、归档或删除策略。
- Production target: Trace 列表应支持稳定分页、时间范围、状态过滤、保留周期、冷热归档和安全删除。
- Risk: trace 数据量增长后列表查询和存储成本不可控；审计数据和调试数据缺乏生命周期管理。
- Required work:
  - 增加 `created_before/created_after/cursor` 查询。
  - 为高频过滤字段建立复合索引。
  - 制定 retention policy。
  - 增加归档或删除脚本。
- Acceptance criteria:
  - 大量 trace 下列表查询延迟可控。
  - 分页稳定且不会漏/重复。
  - 过期 trace 可按策略归档或删除。

### GAP-TRACE-006: Trace 存储缺少加密与字段级安全策略

- Layer: Trace Repository / Security
- Priority: P0
- Status: open
- Owner: TBD
- Current state: 入库前已通过 serializer 脱敏，capture mode 已生效；但数据库层没有字段级加密、密钥轮换、敏感字段扫描任务。
- Production target: 即使 serializer 漏放大文本或敏感值，数据库层也应有二次防线和周期性扫描。
- Risk: 代码回归或新 span 字段可能误写敏感内容；数据库备份中可能保留不应长期保存的上下文。
- Required work:
  - 增加写入前敏感内容扫描。
  - 对高风险字段考虑应用层加密或数据库加密策略。
  - 增加周期性 trace redaction audit job。
  - 将 redaction 违规计入 observability/alert。
- Acceptance criteria:
  - 构造敏感 payload 时 PostgreSQL raw 数据不包含真实敏感值。
  - 定期扫描能发现并报告违规 trace。
  - redaction 回归测试覆盖 prompt/answer/script/evidence/reasoning/token/password。

### GAP-TRACE-007: Trace 与 Eval 回流仍未闭环

- Layer: Trace / Eval
- Priority: P1
- Status: open
- Owner: TBD
- Current state: 已有 `trace_to_eval_case(trace)`，但尚未接入自动写入 eval regression dataset，也没有人工确认/标注流程。
- Production target: 低置信度、失败、降级、approval、fail-safe trace 应能进入 eval 候选池，经人工确认后成为回归样例。
- Risk: 线上失败无法系统性沉淀，类似问题可能反复出现。
- Required work:
  - 新增 trace regression candidate writer。
  - 定义 `evals/datasets/trace_regression_cases.jsonl` schema。
  - 增加人工审核状态。
  - CI 中加入 trace regression dataset。
- Acceptance criteria:
  - 失败 trace 可生成候选 eval case。
  - 人工确认后进入固定回归集。
  - 回归集能在 CI 中稳定运行。

## 后续追加分区

后续发现其他架构层生产级缺口时，按以下分区继续追加：

- RAG / Ingestion Layer
- Guardrail / Policy Layer
- API / Auth / Tenant Layer
- Frontend Observability Layer
- Operational Dashboard / Alerting Layer

每个新增条目必须使用本文档的记录模板，并尽量关联到具体文件、测试和验收标准。
