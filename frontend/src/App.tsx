import { FormEvent, useState } from "react";

type IconName =
  | "attach"
  | "book"
  | "check"
  | "chevron"
  | "clipboard"
  | "file"
  | "filter"
  | "message"
  | "panel"
  | "plus"
  | "search"
  | "send"
  | "settings"
  | "upload"
  | "wrench";

type ArtifactTab = "sop" | "evidence" | "log";
type JsonMap = Record<string, unknown>;

interface ApiEnvelope<T> {
  success: boolean;
  data: T | null;
  error: {
    code: string;
    message: string;
    details?: unknown;
  } | null;
  trace_id: string;
}

interface EvidenceItem {
  source: string;
  page: number | null;
  snippet: string;
  score: number | null;
  metadata: JsonMap;
}

interface PlanStep {
  step: string;
  status: string;
}

interface ToolCallItem {
  tool_name: string;
  input: JsonMap;
  output: JsonMap | JsonMap[] | string | null;
  status: string;
  duration_ms: number | null;
}

interface EvaluationResult {
  is_safe: boolean;
  is_compliant: boolean;
  confidence: number;
  issues: string[];
}

interface SandboxResult {
  language: string;
  allowed: boolean;
  return_code: number | null;
  stdout: string;
  stderr: string;
  error: string | null;
  duration_ms: number | null;
}

interface AICodingResult {
  language?: string;
  script?: string;
  explanation?: string;
  warnings?: string[];
  sandbox_result?: SandboxResult | JsonMap | null;
  [key: string]: unknown;
}

interface QueryResponse {
  answer: string;
  plan: PlanStep[];
  evidence: EvidenceItem[];
  tool_calls: ToolCallItem[];
  evaluation: EvaluationResult | null;
  trace_id: string | null;
  sop: string[];
  memory: JsonMap[];
  ai_coding: AICodingResult | null;
  llm_usage: JsonMap | null;
  llm_model: string | null;
}

const conversations: Array<{
  title: string;
  meta: string;
  active?: boolean;
}> = [
  { title: "怠速不稳和回火排查", meta: "2 分钟前", active: true },
  { title: "机油压力灯异常", meta: "今天 14:20" },
  { title: "冷车启动困难", meta: "昨天" },
  { title: "气门间隙复检", meta: "周二" },
];

const navItems: Array<{
  icon: IconName;
  label: string;
  active?: boolean;
  count?: string;
}> = [
  { icon: "message", label: "对话", active: true, count: "12" },
  { icon: "book", label: "手册库", count: "3" },
  { icon: "clipboard", label: "工单" },
  { icon: "panel", label: "Artifacts" },
];

const promptCards: Array<{
  title: string;
  detail: string;
  prompt: string;
  accent: string;
}> = [
  {
    title: "怠速不稳",
    detail: "生成检查顺序和证据页",
    prompt: "热车后怠速不稳，排气管偶尔回火，应该先检查哪里？",
    accent: "brick",
  },
  {
    title: "启动困难",
    detail: "定位燃油、点火、压缩链路",
    prompt: "冷车启动困难，启动机转速正常但发动机不着车，请给出排查流程。",
    accent: "sage",
  },
  {
    title: "生成脚本",
    detail: "调用 AI Coding 和沙箱",
    prompt: "生成 SQL 脚本检查诊断记录",
    accent: "blue",
  },
];

const evidenceItems = [
  { page: "P.36", title: "怠速调整与混合气检查", confidence: "92%" },
  { page: "P.58", title: "火花塞颜色与点火弱化判断", confidence: "88%" },
  { page: "P.74", title: "进气歧管漏气检查", confidence: "84%" },
];

const sopSteps = [
  "确认怠速转速是否低于手册标准区间。",
  "检查进气歧管、化油器接口和真空管是否漏气。",
  "拆检火花塞，记录颜色、积碳和电极间隙。",
  "复测点火正时，必要时调整混合气螺钉。",
];

const fallbackAssistantText =
  "我会先按进气漏气、点火偏弱、怠速调整偏差三个方向缩小范围。目前证据更指向进气系统密封和火花塞状态，建议不要先拆化油器总成。";

function Icon({ name }: { name: IconName }) {
  const props = {
    width: 18,
    height: 18,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };

  switch (name) {
    case "attach":
      return (
        <svg {...props}>
          <path d="M21 11.5 12.7 19.8a6 6 0 0 1-8.5-8.5l8.9-8.9a4 4 0 1 1 5.7 5.7L9.9 17a2 2 0 1 1-2.8-2.8l8.3-8.3" />
        </svg>
      );
    case "book":
      return (
        <svg {...props}>
          <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v17H6.5A2.5 2.5 0 0 1 4 17.5v-12Z" />
          <path d="M4 17.5A2.5 2.5 0 0 1 6.5 15H20" />
        </svg>
      );
    case "check":
      return (
        <svg {...props}>
          <path d="m5 12 4 4L19 6" />
        </svg>
      );
    case "chevron":
      return (
        <svg {...props}>
          <path d="m9 18 6-6-6-6" />
        </svg>
      );
    case "clipboard":
      return (
        <svg {...props}>
          <path d="M9 4h6l1 2h3v15H5V6h3l1-2Z" />
          <path d="M9 12h6" />
          <path d="M9 16h4" />
        </svg>
      );
    case "file":
      return (
        <svg {...props}>
          <path d="M6 3h8l4 4v14H6V3Z" />
          <path d="M14 3v5h4" />
          <path d="M9 13h6" />
          <path d="M9 17h4" />
        </svg>
      );
    case "filter":
      return (
        <svg {...props}>
          <path d="M4 6h16" />
          <path d="M7 12h10" />
          <path d="M10 18h4" />
        </svg>
      );
    case "message":
      return (
        <svg {...props}>
          <path d="M4 5h16v11H8l-4 4V5Z" />
        </svg>
      );
    case "panel":
      return (
        <svg {...props}>
          <path d="M4 5h16v14H4V5Z" />
          <path d="M14 5v14" />
        </svg>
      );
    case "plus":
      return (
        <svg {...props}>
          <path d="M12 5v14" />
          <path d="M5 12h14" />
        </svg>
      );
    case "search":
      return (
        <svg {...props}>
          <circle cx="11" cy="11" r="6" />
          <path d="m16 16 4 4" />
        </svg>
      );
    case "send":
      return (
        <svg {...props}>
          <path d="M21 3 10 14" />
          <path d="m21 3-7 18-4-7-7-4 18-7Z" />
        </svg>
      );
    case "settings":
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="3" />
          <path d="M12 2v3" />
          <path d="M12 19v3" />
          <path d="m4.2 4.2 2.1 2.1" />
          <path d="m17.7 17.7 2.1 2.1" />
          <path d="M2 12h3" />
          <path d="M19 12h3" />
          <path d="m4.2 19.8 2.1-2.1" />
          <path d="m17.7 6.3 2.1-2.1" />
        </svg>
      );
    case "upload":
      return (
        <svg {...props}>
          <path d="M12 16V4" />
          <path d="m7 9 5-5 5 5" />
          <path d="M4 16v4h16v-4" />
        </svg>
      );
    case "wrench":
      return (
        <svg {...props}>
          <path d="M14.7 6.3a4.5 4.5 0 0 0 5.1 5.1L11 20.2 6.8 16l8.8-8.8Z" />
          <path d="m6.8 16-3 3 1.2 1.2 3-3" />
        </svg>
      );
    default:
      return null;
  }
}

function formatJson(value: unknown) {
  if (value === null || value === undefined) {
    return "null";
  }

  if (typeof value === "string") {
    return value;
  }

  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatConfidence(confidence?: number | null) {
  if (typeof confidence !== "number" || Number.isNaN(confidence)) {
    return 91;
  }

  const normalized = confidence <= 1 ? confidence * 100 : confidence;
  return Math.max(0, Math.min(100, Math.round(normalized)));
}

function formatScore(score?: number | null) {
  if (typeof score !== "number" || Number.isNaN(score)) {
    return "未知";
  }

  const normalized = score <= 1 ? score * 100 : score;
  return `${Math.round(normalized)}%`;
}

function evidencePageLabel(page?: number | null) {
  return page === null || page === undefined ? "P.-" : `P.${page}`;
}

function hasMetadata(metadata: JsonMap) {
  return Object.keys(metadata).length > 0;
}

function App() {
  const [draft, setDraft] = useState(promptCards[0].prompt);
  const [submittedPrompt, setSubmittedPrompt] = useState(promptCards[0].prompt);
  const [activeTab, setActiveTab] = useState<ArtifactTab>("sop");
  const [response, setResponse] = useState<QueryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const confidence = formatConfidence(response?.evaluation?.confidence);
  const displayedSop = response?.sop?.length ? response.sop : sopSteps;
  const assistantText = loading
    ? "正在调用 Harness..."
    : error
      ? error
      : response?.answer || fallbackAssistantText;
  const inlineEvidence = response?.evidence?.length
    ? response.evidence.slice(0, 2)
    : null;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextPrompt = draft.trim();

    if (!nextPrompt || loading) {
      return;
    }

    setSubmittedPrompt(nextPrompt);
    setError(null);
    setLoading(true);

    try {
      const httpResponse = await fetch("/api/query", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          question: nextPrompt,
          device_name: "摩托车发动机",
          session_id: "demo-session",
        }),
      });
      const envelope = (await httpResponse.json()) as ApiEnvelope<QueryResponse>;

      if (!httpResponse.ok || !envelope.success || !envelope.data) {
        throw new Error(envelope.error?.message || `请求失败：${httpResponse.status}`);
      }

      setResponse(envelope.data);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "调用 Harness 失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="workspace">
      <aside className="sidebar" aria-label="主导航">
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true">
            M
          </div>
          <div className="brand-copy">
            <span>Maintenance</span>
            <strong>Agent</strong>
          </div>
        </div>

        <button className="new-chat-button" type="button">
          <Icon name="plus" />
          <span>新会话</span>
        </button>

        <label className="sidebar-search">
          <Icon name="search" />
          <input aria-label="搜索" placeholder="搜索对话、手册、工单" />
        </label>

        <nav className="nav-stack" aria-label="功能">
          {navItems.map((item) => (
            <button
              className={`nav-item${item.active ? " is-active" : ""}`}
              key={item.label}
              type="button"
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
              {item.count ? <small>{item.count}</small> : null}
            </button>
          ))}
        </nav>

        <section className="conversation-list" aria-labelledby="recent-title">
          <div className="section-heading">
            <h2 id="recent-title">最近</h2>
            <button aria-label="筛选最近对话" className="icon-button" type="button">
              <Icon name="filter" />
            </button>
          </div>
          {conversations.map((conversation) => (
            <button
              className={`conversation-item${
                conversation.active ? " is-active" : ""
              }`}
              key={conversation.title}
              type="button"
            >
              <span>{conversation.title}</span>
              <small>{conversation.meta}</small>
            </button>
          ))}
        </section>

        <div className="sidebar-footer">
          <div className="user-avatar" aria-hidden="true">
            L
          </div>
          <div>
            <span>维修组</span>
            <small>比赛演示环境</small>
          </div>
          <button aria-label="设置" className="icon-button" type="button">
            <Icon name="settings" />
          </button>
        </div>
      </aside>

      <main className="chat-shell">
        <header className="chat-topbar">
          <button className="project-chip" type="button">
            <Icon name="wrench" />
            <span>摩托车发动机维修</span>
            <Icon name="chevron" />
          </button>

          <div className="mode-switch" aria-label="模式">
            <button className="is-active" type="button">
              诊断
            </button>
            <button type="button">检索</button>
            <button type="button">复核</button>
          </div>
        </header>

        <section className="welcome-block">
          <p className="eyebrow">Equipment Maintenance Agent</p>
          <h1>今天要排查哪类故障？</h1>
          <div className="prompt-grid">
            {promptCards.map((card) => (
              <button
                className={`prompt-card accent-${card.accent}`}
                key={card.title}
                onClick={() => setDraft(card.prompt)}
                type="button"
              >
                <span>{card.title}</span>
                <small>{card.detail}</small>
              </button>
            ))}
          </div>
        </section>

        <section className="thread" aria-label="对话内容">
          <article className="message-row is-user">
            <div className="message-bubble">{submittedPrompt}</div>
          </article>

          <article className="message-row is-assistant">
            <div className="assistant-avatar" aria-hidden="true">
              A
            </div>
            <div className="assistant-message">
              <p className={error ? "assistant-error" : undefined}>{assistantText}</p>
              <div className="inline-evidence">
                {inlineEvidence
                  ? inlineEvidence.map((item, index) => (
                      <button key={`${item.source}-${index}`} type="button">
                        <Icon name="file" />
                        <span>{evidencePageLabel(item.page)}</span>
                      </button>
                    ))
                  : evidenceItems.slice(0, 2).map((item) => (
                      <button key={item.page} type="button">
                        <Icon name="file" />
                        <span>{item.page}</span>
                      </button>
                    ))}
              </div>
            </div>
          </article>
        </section>

        <form className="composer" onSubmit={handleSubmit}>
          <div className="composer-input">
            <button aria-label="添加附件" className="icon-button" type="button">
              <Icon name="attach" />
            </button>
            <textarea
              aria-label="输入故障描述"
              onChange={(event) => setDraft(event.target.value)}
              placeholder="描述故障现象，或粘贴检修记录..."
              rows={1}
              value={draft}
            />
          </div>
          <div className="composer-actions">
            <button className="tool-button" type="button">
              <Icon name="upload" />
              <span>上传手册</span>
            </button>
            <button className="tool-button" type="button">
              <Icon name="book" />
              <span>引用资料</span>
            </button>
            <button
              aria-label="发送"
              className="send-button"
              disabled={loading}
              type="submit"
            >
              <Icon name="send" />
            </button>
          </div>
        </form>
      </main>

      <aside className="artifact-shell" aria-label="维修工作区">
        <header className="artifact-header">
          <div>
            <p>Artifact</p>
            <h2>{response?.ai_coding ? "AI Coding 记录" : "维修 SOP"}</h2>
          </div>
          <button aria-label="展开工作区" className="icon-button" type="button">
            <Icon name="panel" />
          </button>
        </header>

        <div className="artifact-tabs" role="tablist" aria-label="工作区视图">
          {(["sop", "evidence", "log"] as ArtifactTab[]).map((tab) => (
            <button
              aria-selected={activeTab === tab}
              className={activeTab === tab ? "is-active" : ""}
              key={tab}
              onClick={() => setActiveTab(tab)}
              role="tab"
              type="button"
            >
              {tab === "sop" ? "SOP" : tab === "evidence" ? "证据" : "记录"}
            </button>
          ))}
        </div>

        {activeTab === "sop" ? (
          <section className="artifact-content">
            <div className="manual-visual" aria-hidden="true">
              <div className="manual-page">
                <span />
                <span />
                <span />
              </div>
              <div className="engine-diagram">
                <i className="engine-core" />
                <i className="engine-port left" />
                <i className="engine-port right" />
                <i className="engine-line first" />
                <i className="engine-line second" />
              </div>
            </div>

            <div className="risk-strip">
              <span>可信度</span>
              <strong>{confidence}%</strong>
              <div className="confidence-track">
                <i style={{ width: `${confidence}%` }} />
              </div>
            </div>

            {response?.evaluation?.issues?.length ? (
              <div className="risk-issues">
                <strong>风险提示</strong>
                {response.evaluation.issues.map((issue) => (
                  <span key={issue}>{issue}</span>
                ))}
              </div>
            ) : null}

            <ol className="sop-list">
              {displayedSop.map((step, index) => (
                <li key={`${step}-${index}`}>
                  <span>{index + 1}</span>
                  <p>{step}</p>
                </li>
              ))}
            </ol>
          </section>
        ) : null}

        {activeTab === "evidence" ? (
          <section className="artifact-content">
            <div className="evidence-stack">
              {response?.evidence?.length
                ? response.evidence.map((item, index) => (
                    <article className="evidence-row" key={`${item.source}-${index}`}>
                      <Icon name="file" />
                      <div>
                        <strong>{item.source}</strong>
                        <span>
                          {evidencePageLabel(item.page)} · 匹配度 {formatScore(item.score)}
                        </span>
                        <p className="artifact-copy">{item.snippet}</p>
                        {hasMetadata(item.metadata) ? (
                          <pre className="json-snippet">{formatJson(item.metadata)}</pre>
                        ) : null}
                      </div>
                    </article>
                  ))
                : evidenceItems.map((item) => (
                    <article className="evidence-row" key={item.page}>
                      <Icon name="file" />
                      <div>
                        <strong>{item.title}</strong>
                        <span>
                          {item.page} · 匹配度 {item.confidence}
                        </span>
                      </div>
                    </article>
                  ))}
            </div>
          </section>
        ) : null}

        {activeTab === "log" ? (
          <section className="artifact-content">
            <div className="log-timeline">
              {response?.trace_id ? (
                <article>
                  <Icon name="check" />
                  <div>
                    <strong>Trace ID</strong>
                    <span>{response.trace_id}</span>
                  </div>
                </article>
              ) : null}

              {response?.evaluation ? (
                <article>
                  <Icon name="check" />
                  <div>
                    <strong>评估结果 · 可信度 {confidence}%</strong>
                    <span>
                      安全：{response.evaluation.is_safe ? "通过" : "需复核"} · 合规：
                      {response.evaluation.is_compliant ? "通过" : "需复核"}
                    </span>
                    {response.evaluation.issues.length ? (
                      <p className="artifact-copy">
                        风险提示：{response.evaluation.issues.join("；")}
                      </p>
                    ) : null}
                  </div>
                </article>
              ) : null}

              {response?.tool_calls?.length ? (
                response.tool_calls.map((call, index) => (
                  <article key={`${call.tool_name}-${index}`}>
                    <Icon name="check" />
                    <div>
                      <strong>{call.tool_name}</strong>
                      <span>
                        {call.status}
                        {call.duration_ms !== null ? ` · ${call.duration_ms} ms` : ""}
                      </span>
                      <details>
                        <summary>输入 / 输出</summary>
                        <pre className="json-snippet">
                          {`input:\n${formatJson(call.input)}\n\noutput:\n${formatJson(call.output)}`}
                        </pre>
                      </details>
                    </div>
                  </article>
                ))
              ) : (
                <>
                  <article>
                    <Icon name="check" />
                    <div>
                      <strong>等待 Harness 调用</strong>
                      <span>提交问题后会显示工具调用记录</span>
                    </div>
                  </article>
                  <article>
                    <Icon name="check" />
                    <div>
                      <strong>等待生成 trace</strong>
                      <span>后端返回后会显示 trace_id</span>
                    </div>
                  </article>
                </>
              )}

              {response?.ai_coding ? (
                <article className="ai-coding-card">
                  <Icon name="file" />
                  <div>
                    <strong>AI Coding · {response.ai_coding.language || "unknown"}</strong>
                    {response.ai_coding.explanation ? (
                      <span>{response.ai_coding.explanation}</span>
                    ) : null}
                    {response.ai_coding.script ? (
                      <pre className="code-snippet">{response.ai_coding.script}</pre>
                    ) : null}
                    {response.ai_coding.sandbox_result ? (
                      <details open>
                        <summary>sandbox_result</summary>
                        <pre className="json-snippet">
                          {formatJson(response.ai_coding.sandbox_result)}
                        </pre>
                      </details>
                    ) : null}
                  </div>
                </article>
              ) : null}
            </div>
          </section>
        ) : null}
      </aside>
    </div>
  );
}

export default App;
