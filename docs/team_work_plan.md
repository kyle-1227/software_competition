# 生产级优化成员分工

本文件是根目录 `team_work_plan.md` 的 docs 交付摘要，聚焦 Commit 8 到 Commit 13 的生产级 Agent 优化。成员命名沿用同学 A / B / C。

## 总体职责

| 成员 | 主责方向 | 重点产出 |
|---|---|---|
| 同学 A | 后端与 Agent Runtime | Runtime Contract、Execution State Machine、LangGraph Workflow、API / SSE / CLI adapter、Agent Loop、后端测试 |
| 同学 B | 数据知识库与模型执行层 | Tool Policy、Memory 持久化、Retrieval、Sandbox 后端、Eval 数据集、failure taxonomy |
| 同学 C | 前端与文档 | README、架构文档、生产缺口文档、产品说明、演示材料、前端 Trace / Metrics 展示 |

## 阶段分工

| 阶段 | 目标 | 同学 A | 同学 B | 同学 C |
|---|---|---|---|---|
| Commit 8 | Runtime Contract + Execution State Machine | 定义 `RuntimeStateFactory`、`RuntimeExecutor`、`RuntimeResultAdapter`；接入 timeout、cancel、max steps | 确认 retrieval / memory / sandbox 对 Runtime 的输入输出需求 | 更新 README、架构说明和演示脚本 |
| Commit 9 | Workflow 节点拆分 + RuntimeResult 输出 | 收敛 LangGraph 节点职责；让 graph 输出 `RuntimeResult`；补 workflow 测试 | 对齐 evidence、tool、memory 字段，避免 worker 输出漂移 | 更新架构图、Trace 页面说明和文档截图 |
| Commit 10 | Tool / Worker / Policy 完整化 | 统一 ToolRegistry、WorkerDispatcher、policy adapter 和错误处理 | 设计 `ToolPolicy`、工具评测数据、retry / degraded / side-effect 策略 | 展示工具调用、审批、审计和 degraded 状态 |
| Commit 11 | Memory 持久化 + Retrieval + Fallback | Memory API 与 Runtime 集成；补 session / fallback 测试 | PostgreSQL / Vector DB 方案、retrieval、low confidence 写入策略 | 更新产品说明、记忆能力说明和测试用例 |
| Commit 12 | Sandbox 安全隔离与后端插件化 | 定义 `SandboxBackend` 接口；接入 ToolPolicy 和 Runtime | Docker / Remote backend、资源限制、网络限制、沙箱评测 | 更新安全执行说明、演示素材和风险边界 |
| Commit 13 | E2E 测试 + Operational Metrics / Eval Feedback Loop | 端到端测试、Metrics 接口、CI 回归 | Eval regression dataset、失败归因、评测报告 | Operational dashboard、提交文档、PPT 和视频 |

## 协作规则

- Runtime / Workflow 优先，Tool / Memory / Sandbox 的输入输出必须向 Runtime Contract 对齐。
- 所有 fallback、degraded、low confidence、approval、fail-safe 都必须进入 Trace 和 Metrics。
- 接口字段变更由同学 A 先更新 schema 和样例 JSON，再通知同学 B / C。
- 检索、Memory、Sandbox 的行为变更由同学 B 提供前后对比样例或 eval 结果。
- 文档、PPT、演示视频由同学 C 汇总，但技术细节由对应负责人确认。

## 当前优先级

1. Commit 8：先统一 Runtime Contract，避免后续层继续耦合 `QueryResponse`。
2. Commit 9：让 Workflow 输出稳定 `RuntimeResult`。
3. Commit 10：统一 Tool / Worker / Policy，保证安全、可靠、可追踪。
4. Commit 11-12：补 Memory 持久化和 Sandbox 强隔离。
5. Commit 13：用 E2E、Metrics 和 Eval 回流形成生产级闭环。
