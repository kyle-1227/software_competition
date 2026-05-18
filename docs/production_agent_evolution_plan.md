# 生产级 Agent 进化路线

本文档用于指导当前 Agent Harness 从比赛可演示形态升级为生产级可维护架构。路线遵循“固定 workflow + 局部自适应”：graph 拓扑保持稳定，节点内部允许 fallback、degraded、retry、tool 选择和策略调整。

## 核心原则

- 固定 Workflow：LangGraph 拓扑保持清晰稳定，避免把运行时决策扩散成不可追踪的动态图。
- 局部自适应：节点内部处理 retry、fallback、degraded、approval、tool selection 和低置信度策略。
- 模块化复用：Runtime、Tool、Memory、Sandbox、Trace 分层明确，接口稳定后再替换内部实现。
- 生产级可落地：每次执行都必须具备 Trace、Metrics、Eval，可追踪、可回溯、可重放。
- 安全优先：任何 fallback / degraded / low confidence 都必须显式进入 RuntimeResult、RuntimeEvent 和 Trace。

## 层级优化顺序

| 顺序 | 层级 | 优化目标 | 当前差距 |
|---|---|---|---|
| 1 | Agent Runtime Contract / State Machine | `RuntimeStateFactory` / `RuntimeExecutor` / `RuntimeResultAdapter` | 缺统一 Step 生命周期、取消、超时、max steps 和 RuntimeResult |
| 2 | LangGraph / Workflow 节点拆分 | Intake / Guardrail / Memory / Orchestrator / Worker / Loop / Eval / Trace / Finalize | 节点已拆分，但 graph 输出仍需统一 RuntimeResult |
| 3 | Tool / Worker / Policy | Policy / Approval / Timeout / Retry / Audit / Side-effect | retry 与 degraded 已有基础，审批、审计和副作用策略仍分散 |
| 4 | Memory 层 | 持久化 / Retrieval / Eval / Fallback / 低置信度策略 | 当前是进程内窗口和摘要，缺生产持久化与检索评估 |
| 5 | Sandbox 层 | Local / Docker / Remote 后端隔离 | 当前是演示级受限执行器，缺强隔离和资源治理 |
| 6 | Trace / Observability / Metrics / Eval | 完整 Trace、Synthetic Span、Degraded / Fallback / Eval Dataset | 已具备生产级基础，继续扩展 alerting 和 operational dashboard |
| 7 | API / CLI / Dependency | FastAPI / DI / CLI wrapper | API 可用，CLI wrapper、依赖注入和部署配置需继续收敛 |

## 阶段计划

### Commit 8: Runtime Contract + Execution State Machine

目标：把 Agent 执行入口从“直接构造 dict state”升级为稳定 Runtime 契约。

改动范围：

- 新增或规划 `RuntimeStateFactory`：负责从 `QueryRequest` 构造标准运行时 state。
- 新增或规划 `RuntimeExecutor`：负责执行 graph，统一处理 timeout、cancel、max steps 和异常。
- 新增或规划 `RuntimeResultAdapter`：负责把 `RuntimeResult` 转成 `QueryResponse`。
- 明确 Step 生命周期：pending、running、success、error、degraded、cancelled。

验收标准：

- 每次请求都有唯一 runtime id / trace id / session id 关联。
- Runtime 能记录开始、结束、失败、取消、超时和降级原因。
- API 层不再直接理解 graph 内部临时字段。

### Commit 9: Workflow 节点拆分 + RuntimeResult 输出

目标：保持 LangGraph 拓扑固定，让每个节点输出统一局部结果，最终 graph 输出 `RuntimeResult`。

改动范围：

- 收敛 Intake / Guardrail / Memory / Orchestrator / Worker / Loop / Eval / Trace / Finalize 的节点职责。
- 节点内部只更新受控 state 字段，不直接拼装 API response。
- `finalize_node` 输出标准 `RuntimeResult`，再由 adapter 转换为 `QueryResponse`。
- 条件分支继续覆盖 retry、approval、clarification、fail-safe 和 answer regeneration。

验收标准：

- graph 输出结构稳定，与 API schema 解耦。
- 每个节点都有 span，且 span metadata 不包含完整 prompt、answer、script、secret 或 reasoning。
- Workflow 测试覆盖正常路径、guardrail blocked、retrieval retry、approval、clarification、fail-safe。

### Commit 10: Tool / Worker / Policy 完整化

目标：统一工具和 worker 的调用规范，确保安全、可靠、可审计。

改动范围：

- 扩展 `ToolResult` 或新增 `RuntimeEvent`，统一 success、error、duration、attempt、degraded、fallback、side_effect。
- 新增或规划 `ToolPolicy`：统一 approval、timeout、retry、audit、side-effect 限制。
- Worker 输出与 Tool 输出对齐，避免每个 worker 自行定义状态碎片。
- 所有工具调用必须进入 Trace，并能被 Eval 和 Metrics 识别。

验收标准：

- transient failure 会按策略 retry，超过预算后显式 degraded。
- 高风险工具调用必须进入 approval 或 fail-safe。
- Metrics 能统计 tool success rate、retry count、degraded rate 和 fallback rate。

### Commit 11: Memory 持久化 + Retrieval + Fallback

目标：把 Memory 从进程内上下文升级为生产级可持久化记忆层。

改动范围：

- 设计 PostgreSQL session memory 表和可选 Vector DB 检索接口。
- 支持 CRUD、retrieval、summary、eval feedback 和 low confidence 标记。
- 明确写入策略：哪些内容可写、哪些必须脱敏、哪些只保留摘要。
- Memory fallback / degraded / retrieval miss 必须进入 RuntimeEvent 和 Trace。

验收标准：

- 进程重启后 session memory 可恢复。
- 低置信度回答不会被静默写入长期记忆。
- Memory retrieval 可被 Eval 评估，并能解释命中或未命中原因。

### Commit 12: Sandbox 安全隔离与后端插件化

目标：将比赛演示级 Sandbox 替换为可插拔生产执行层。

改动范围：

- 设计 `SandboxBackend` 接口，支持 Local、Docker、Remote。
- 为每个后端统一 timeout、stdout/stderr 截断、资源限制、网络限制、文件系统限制。
- Sandbox 调用接入 `ToolPolicy`，根据风险决定拒绝、审批或执行。
- 所有执行结果封装为 `RuntimeEvent` / `ToolResult` 并进入 Trace。

验收标准：

- Shell 默认拒绝，Python / SQL 仍受策略限制。
- Docker / Remote 后端能阻断越权文件、网络和长时间执行。
- Sandbox timeout、blocked、degraded、execution error 都有可观测指标。

### Commit 13: End-to-End 测试 + Operational Metrics / Eval Feedback Loop

目标：把前面各层串成可回归、可观测、可持续优化的生产闭环。

改动范围：

- 增加端到端测试：API -> Runtime -> Workflow -> Tool -> Memory -> Sandbox -> Trace -> Eval。
- 建立 degraded / fallback / low confidence / approval / fail-safe 的回归数据集。
- 将失败 trace 写入 eval candidate，经人工确认后进入 regression dataset。
- 扩展 operational dashboard 指标：latency、error rate、retry rate、degraded rate、fallback rate、confidence、evidence coverage。

验收标准：

- 任一生产级关键路径变更都能通过 E2E 和 trace regression 验证。
- 失败样本能进入 Eval 回流，不再只停留在日志或人工记录。
- dashboard 能区分 Runtime、Tool、Memory、Sandbox、Trace、Eval 的主要失败来源。

## 成员分工

| 阶段 | 同学 A：后端与 Agent | 同学 B：数据知识库与模型 | 同学 C：前端与文档 |
|---|---|---|---|
| Commit 8 | Runtime Contract、Execution State Machine、API adapter | 配合确认 retrieval / memory 对 Runtime 的输入输出需求 | 更新 README、接口说明、演示脚本 |
| Commit 9 | LangGraph 节点职责收敛、RuntimeResult 输出、Workflow 测试 | 配合 worker 所需 evidence / retrieval 字段 | 更新架构图、Trace 展示说明 |
| Commit 10 | ToolRegistry、WorkerDispatcher、Policy 接口和测试 | ToolPolicy、retrieval tool、eval tool、工具评测数据 | 更新工具调用展示、审计说明 |
| Commit 11 | Memory API、Runtime 集成、测试 | PostgreSQL / Vector DB、retrieval、fallback、low confidence 策略 | 更新产品说明和记忆能力展示 |
| Commit 12 | Sandbox 接口、ToolPolicy 集成、API 错误处理 | Docker / Remote backend 方案、资源限制评测 | 更新安全执行说明和演示素材 |
| Commit 13 | E2E 测试、Metrics 接口、CI 回归 | Eval dataset、failure taxonomy、评测报告 | Operational dashboard、提交文档、PPT / 视频 |

## 默认假设

- 本路线不修改现有 public API，除非对应阶段明确进入实现。
- `RuntimeStateFactory`、`RuntimeExecutor`、`RuntimeResultAdapter`、`RuntimeResult`、`RuntimeEvent`、`ToolPolicy` 均为后续计划接口名。
- Trace / Eval / Metrics / Retention / Cleanup 已具备生产级基础，但访问控制、异步写入、告警和 dashboard 仍作为增强项继续推进。
