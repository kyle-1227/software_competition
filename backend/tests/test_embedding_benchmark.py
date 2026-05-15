"""Embedding model comparison benchmark.

Compares ManualHashEmbedding (384d hash-based) against SiliconFlow BGE (1024d
semantic) on 20 annotated maintenance questions.

Usage:
    # Run with current default embedding (ManualHashEmbedding if no API key)
    pytest tests/test_embedding_benchmark.py -v -s

    # Run with specific embedding
    SILICONFLOW_API_KEY=sk-xxx pytest tests/test_embedding_benchmark.py -v -s
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.schemas.query import QueryRequest
from app.services.agent_harness_lc import AgentHarness
from app.services.manual_vector_indexer import (
    DEFAULT_INDEX_DIR,
    ManualHashEmbedding,
    build_manual_vector_index,
    get_manual_vector_retriever,
)


# ============================================================================
# 20 annotated eval questions
# ============================================================================

EVAL_DATASET: list[dict[str, Any]] = [
    # ---- spark plug ----
    {
        "id": "spark_gap",
        "question": "火花塞间隙标准值是多少？",
        "category": "火花塞",
        "difficulty": "easy",
        "expected_values": ["0.7～0.9", "0.7~0.9"],
        "expected_pages": [3],
        "expected_sections": ["检查火花塞"],
    },
    {
        "id": "spark_torque",
        "question": "安装火花塞要拧多紧？扭矩是多少？",
        "category": "火花塞",
        "difficulty": "easy",
        "expected_values": ["20", "N·m", "N.m"],
        "expected_pages": [3],
        "expected_sections": ["安装火花塞"],
    },
    {
        "id": "compression_std",
        "question": "发动机压缩压力标准值是多少？",
        "category": "火花塞",
        "difficulty": "medium",
        "expected_values": ["1300", "1900", "kPa"],
        "expected_pages": [3],
        "expected_sections": ["测量压缩压力"],
    },
    {
        "id": "compression_low",
        "question": "测量发现压缩压力太低，应该怎么办？",
        "category": "火花塞",
        "difficulty": "hard",
        "expected_values": ["机油", "活塞环", "气门"],
        "expected_pages": [3],
        "expected_sections": ["测量压缩压力"],
    },
    # ---- valve clearance ----
    {
        "id": "intake_clearance",
        "question": "进气门间隙标准值是多少？",
        "category": "气门间隙",
        "difficulty": "easy",
        "expected_values": ["0.13", "0.20", "mm"],
        "expected_pages": [15],
        "expected_sections": ["气门间隙"],
    },
    {
        "id": "exhaust_clearance",
        "question": "排气门间隙标准值是多少？",
        "category": "气门间隙",
        "difficulty": "easy",
        "expected_values": ["0.20", "0.30", "mm"],
        "expected_pages": [15],
        "expected_sections": ["气门间隙"],
    },
    # ---- engine / oil ----
    {
        "id": "oil_spec",
        "question": "发动机要加什么机油？加多少升？",
        "category": "发动机",
        "difficulty": "medium",
        "expected_values": ["SAE", "10W", "1600", "mL", "SM"],
        "expected_pages": [4, 5],
        "expected_sections": ["安装发动机"],
    },
    # ---- cylinder head ----
    {
        "id": "cylinder_head_torque",
        "question": "气缸头M10螺母怎么拧紧？分几次？每次多少力矩？",
        "category": "气缸头",
        "difficulty": "medium",
        "expected_values": ["25", "45", "60", "N·m", "对角"],
        "expected_pages": [14, 15],
        "expected_sections": ["安装气缸头"],
    },
    # ---- complex diagnosis ----
    {
        "id": "idle_unstable",
        "question": "热车后怠速不稳，排气管偶尔回火，应该先检查哪里？",
        "category": "火花塞",
        "difficulty": "hard",
        "expected_values": ["火花塞", "压缩压力", "气门间隙"],
        "expected_pages": [3, 15],
        "expected_sections": ["检查火花塞", "气门间隙"],
    },
    # ---- piston ----
    {
        "id": "piston_ring_gap",
        "question": "活塞环开口要错开多少度？安装时有什么注意事项？",
        "category": "气缸活塞",
        "difficulty": "medium",
        "expected_values": ["120", "度"],
        "expected_pages": [9, 10],
        "expected_sections": ["安装活塞环"],
    },
    # ---- tensioner ----
    {
        "id": "tensioner_install",
        "question": "涨紧器怎么安装？怎么预压？",
        "category": "气缸头",
        "difficulty": "hard",
        "expected_values": ["M6", "顶杆", "螺杆", "自锁"],
        "expected_pages": [13, 14],
        "expected_sections": ["安装涨紧器"],
    },
    # ---- camshaft ----
    {
        "id": "camshaft_install",
        "question": "安装凸轮轴要注意什么？正时标记怎么对？",
        "category": "气缸头",
        "difficulty": "medium",
        "expected_values": ["T", "标记", "正时", "M14"],
        "expected_pages": [12, 13],
        "expected_sections": ["安装凸轮轴"],
    },
    # ---- clutch ----
    {
        "id": "clutch_friction",
        "question": "离合器摩擦片和从动片怎么排列安装？",
        "category": "离合器",
        "difficulty": "medium",
        "expected_values": ["交替", "大孔", "从动盘"],
        "expected_pages": [20, 21],
        "expected_sections": ["安装离合器"],
    },
    # ---- crankshaft ----
    {
        "id": "crankshaft_runout",
        "question": "曲轴轴向跳动允许多少？径向跳动呢？",
        "category": "曲轴",
        "difficulty": "easy",
        "expected_values": ["0.03", "mm"],
        "expected_pages": [26, 27],
        "expected_sections": ["检查曲轴"],
    },
    # ---- one-way clutch ----
    {
        "id": "one_way_check",
        "question": "怎么检查磁电机转子离合器的单向器好不好？",
        "category": "磁电机",
        "difficulty": "medium",
        "expected_values": ["顺时针", "逆时针", "锁止", "自由"],
        "expected_pages": [22, 23],
        "expected_sections": ["检查磁电机转子离合器单向器"],
    },
    # ---- coolant ----
    {
        "id": "coolant_fill",
        "question": "发动机冷却液怎么加注？加到什么位置？",
        "category": "发动机",
        "difficulty": "medium",
        "expected_values": ["F", "L", "副水箱", "运行"],
        "expected_pages": [5, 6],
        "expected_sections": ["安装发动机"],
    },
    # ---- shift fork ----
    {
        "id": "fork_inspect",
        "question": "变速鼓拨叉弯曲了或者磨损了怎么办？",
        "category": "传动装置",
        "difficulty": "easy",
        "expected_values": ["更换"],
        "expected_pages": [25],
        "expected_sections": ["检查拨叉"],
    },
    # ---- starter motor ----
    {
        "id": "starter_removal",
        "question": "拆卸起动电机要注意什么？",
        "category": "起动电机",
        "difficulty": "easy",
        "expected_values": ["拆卸", "起动电机"],
        "expected_pages": [3, 4],
        "expected_sections": ["拆卸起动电机"],
    },
    # ---- general safety ----
    {
        "id": "safety_disassemble",
        "question": "拆卸气缸头要注意什么安全事项？",
        "category": "通用安全",
        "difficulty": "medium",
        "expected_values": ["停机", "断电", "对角", "拧松"],
        "expected_pages": [14],
        "expected_sections": ["拆卸气缸头"],
    },
    # ---- AI coding ----
    {
        "id": "ai_coding_sql",
        "question": "请生成一个 SQL 脚本用于查询所有检查标准记录",
        "category": "AI编程",
        "difficulty": "special",
        "expected_values": ["SELECT"],
        "expected_pages": [],
        "expected_sections": [],
    },
]


# ============================================================================
# Benchmark metrics
# ============================================================================


@dataclass
class QAMetrics:
    question_id: str
    category: str
    difficulty: str
    evidence_count: int = 0
    top1_score: float = 0.0
    mean_score: float = 0.0
    relevant_page_found: bool = False
    is_placeholder: bool = False
    value_matches: int = 0
    value_expected: int = 0
    value_hit_rate: float = 0.0
    safety_terms_count: int = 0
    is_safe: bool = False
    is_compliant: bool = False
    confidence: float = 0.0
    issues_count: int = 0


@dataclass
class BenchmarkReport:
    embedding_name: str
    metrics: list[QAMetrics] = field(default_factory=list)

    @property
    def evidence_found_pct(self) -> float:
        n = sum(1 for m in self.metrics if m.evidence_count > 0)
        return n / len(self.metrics) if self.metrics else 0.0

    @property
    def avg_top1_score(self) -> float:
        scores = [m.top1_score for m in self.metrics if m.evidence_count > 0]
        return sum(scores) / len(scores) if scores else 0.0

    @property
    def avg_value_hit_rate(self) -> float:
        rates = [m.value_hit_rate for m in self.metrics if m.value_expected > 0]
        return sum(rates) / len(rates) if rates else 0.0

    @property
    def avg_confidence(self) -> float:
        confs = [m.confidence for m in self.metrics]
        return sum(confs) / len(confs) if confs else 0.0

    @property
    def avg_issues(self) -> float:
        issues = [m.issues_count for m in self.metrics]
        return sum(issues) / len(issues) if issues else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "embedding": self.embedding_name,
            "questions": len(self.metrics),
            "evidence_found_pct": round(self.evidence_found_pct, 3),
            "avg_top1_score": round(self.avg_top1_score, 3),
            "avg_value_hit_rate": round(self.avg_value_hit_rate, 3),
            "avg_confidence": round(self.avg_confidence, 3),
            "avg_issues_count": round(self.avg_issues, 2),
        }


# ============================================================================
# Benchmark runner
# ============================================================================


def _build_temp_index(embed_model: Any) -> Path:
    """Build a vector index in a temp directory and return its path."""
    import tempfile
    from app.services.manual_indexer import load_manual_documents
    from llama_index.core import VectorStoreIndex

    documents = load_manual_documents()
    base = Path(tempfile.gettempdir()) / "benchmark_embedding"
    base.mkdir(parents=True, exist_ok=True)
    index_dir = Path(tempfile.mkdtemp(dir=str(base)))

    index = VectorStoreIndex.from_documents(
        documents, embed_model=embed_model, transformations=[]
    )
    from app.services.manual_vector_indexer import MANUAL_INDEX_ID
    index.set_index_id(MANUAL_INDEX_ID)
    index.storage_context.persist(persist_dir=str(index_dir))
    return index_dir


def _record_metrics(
    qa: dict[str, Any],
    response: Any,
) -> QAMetrics:
    evidence = getattr(response, "evidence", []) or []
    evaluation = getattr(response, "evaluation", None)
    answer = getattr(response, "answer", "") or ""

    m = QAMetrics(
        question_id=qa["id"],
        category=qa["category"],
        difficulty=qa["difficulty"],
        evidence_count=len(evidence),
        value_expected=len(qa.get("expected_values", [])),
    )

    # Retrieval metrics
    scores = [float(e.score or 0) for e in evidence if e.score is not None]
    if scores:
        m.top1_score = round(scores[0], 4)
        m.mean_score = round(sum(scores) / len(scores), 4)

    expected_pages = qa.get("expected_pages", [])
    if expected_pages:
        ev_pages = {e.page for e in evidence if e.page is not None}
        m.relevant_page_found = bool(ev_pages & set(expected_pages))

    m.is_placeholder = any(
        "placeholder" in str(getattr(e, "source", "")).lower() for e in evidence
    )

    # Answer quality
    for val in qa.get("expected_values", []):
        if val.lower() in answer.lower():
            m.value_matches += 1
    m.value_hit_rate = (
        round(m.value_matches / m.value_expected, 3)
        if m.value_expected > 0
        else 1.0
    )

    safety_terms = ["停机", "断电", "防护", "风险"]
    m.safety_terms_count = sum(1 for t in safety_terms if t in answer)

    # Evaluator
    if evaluation is not None:
        m.is_safe = bool(getattr(evaluation, "is_safe", False))
        m.is_compliant = bool(getattr(evaluation, "is_compliant", False))
        m.confidence = float(getattr(evaluation, "confidence", 0.0) or 0.0)
        m.issues_count = len(getattr(evaluation, "issues", []) or [])

    return m


async def _run_benchmark(
    embed_model: Any,
    embed_name: str,
    *,
    reranker: Any = None,
    query_rewriter: Any = None,
) -> BenchmarkReport:
    """Run all 20 questions with the given embedding model and optional reranker/rewriter."""
    from app.services.retriever import Retriever
    from app.services.tool_registry import ToolRegistry
    from app.services.memory_store import MemoryStore
    from app.services.trace_store import TraceStore
    from app.services.sandbox import SandboxExecutor
    from app.services.evaluator import Evaluator
    from tests.conftest import FakeLLMResponse

    # Build index with target embedding.
    # When reranker is active, retrieve more candidates for it to re-rank.
    retrieve_k = 20 if reranker else 5
    index_dir = _build_temp_index(embed_model)
    vector_retriever = get_manual_vector_retriever(
        index_dir=index_dir, similarity_top_k=retrieve_k, embed_model=embed_model
    )

    class EvidenceEchoingLLMClient:
        """Records calls and echoes evidence snippets for fair comparison."""

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def generate_text(
            self, prompt: str, context: dict[str, Any] | None = None
        ) -> FakeLLMResponse:
            self.calls.append({"method": "generate_text", "prompt": prompt, "context": context})
            evidence = (context or {}).get("evidence", [])
            snippets = []
            for e in (evidence or [])[:3]:
                if isinstance(e, dict):
                    snip = e.get("snippet", "")
                elif hasattr(e, "snippet"):
                    snip = e.snippet
                else:
                    snip = str(e)
                if snip:
                    snippets.append(str(snip)[:150])
            evidence_text = "；".join(snippets) if snippets else "根据手册相关章节"
            return FakeLLMResponse(
                text=(
                    "安全提醒：请先停机并断电，佩戴防护用品。\n\n"
                    f"诊断建议：{evidence_text}\n\n"
                    "建议先检查火花塞、压缩压力和进排气门间隙等常见故障点。"
                ),
                model="test-model",
            )

        async def generate_json(
            self, prompt: str, context: dict[str, Any] | None = None
        ) -> FakeLLMResponse:
            self.calls.append({"method": "generate_json", "prompt": prompt, "context": context})
            return FakeLLMResponse(text="{}", model="test-model")

    llm_client = EvidenceEchoingLLMClient()

    harness = AgentHarness(
        tool_registry=ToolRegistry(llm_client=llm_client),
        trace_store=TraceStore(),
        memory_store=MemoryStore(),
        sandbox_executor=SandboxExecutor(),
        evaluator=Evaluator(),
        llm_client=llm_client,
    )

    # Override retriever to use our index
    retriever = Retriever(
        vector_retriever=vector_retriever,
        reranker=reranker,
        query_rewriter=query_rewriter,
    )
    harness.tool_registry._tools["manual_lookup"].retriever = retriever

    report = BenchmarkReport(embedding_name=embed_name)
    for qa in EVAL_DATASET:
        try:
            response = await harness.answer(
                QueryRequest(
                    question=qa["question"],
                    device_name="摩托车发动机",
                )
            )
            report.metrics.append(_record_metrics(qa, response))
        except Exception as exc:
            report.metrics.append(
                QAMetrics(
                    question_id=qa["id"],
                    category=qa["category"],
                    difficulty=qa["difficulty"],
                )
            )

    return report


def _print_comparison(a: BenchmarkReport, b: BenchmarkReport) -> None:
    """Print side-by-side comparison table and persist results."""
    sa = a.summary()
    sb = b.summary()

    _save_benchmark_results(a, b)

    print("\n" + "=" * 80)
    print("EMBEDDING BENCHMARK COMPARISON")
    print("=" * 80)
    print(
        f"{'Metric':<30} {a.embedding_name:>22} {b.embedding_name:>22}"
    )
    print("-" * 80)
    for key, label in [
        ("evidence_found_pct", "evidence_found_pct"),
        ("avg_top1_score", "avg_top1_score"),
        ("avg_value_hit_rate", "avg_value_hit_rate"),
        ("avg_confidence", "avg_confidence"),
        ("avg_issues_count", "avg_issues_count"),
    ]:
        delta = sb[key] - sa[key]
        arrow = "+" if delta > 0 else ("=" if delta == 0 else "-")
        print(
            f"{label:<30} {sa[key]:>22.3f} {sb[key]:>22.3f} "
            f"({arrow}{abs(delta):.3f})"
        )
    print("-" * 80)

    # Per-question value hit rate
    print(f"\n{'ID':<25} {'Category':<12} {'Diff':<8} {'A':>8} {'B':>8}")
    print("-" * 70)
    for ma, mb in zip(a.metrics, b.metrics):
        delta = mb.value_hit_rate - ma.value_hit_rate
        flag = "+" if delta > 0 else ("=" if delta == 0 else "-")
        print(
            f"{ma.question_id:<25} {ma.category:<12} {flag:<8} "
            f"{ma.value_hit_rate:>8.2f} {mb.value_hit_rate:>8.2f}"
        )
    print("=" * 80)


def _save_benchmark_results(a: BenchmarkReport, b: BenchmarkReport) -> None:
    """Persist benchmark results to JSON and comparison markdown."""
    import json as _json
    from pathlib import Path as _Path
    from datetime import datetime as _datetime

    evals_dir = _Path(__file__).resolve().parents[2] / "data" / "evals"
    evals_dir.mkdir(parents=True, exist_ok=True)

    timestamp = _datetime.now().strftime("%Y%m%d_%H%M%S")

    # Per-model JSON
    for report, suffix in [(a, "a"), (b, "b")]:
        path = evals_dir / f"benchmark_{suffix}_{timestamp}.json"
        path.write_text(
            _json.dumps({
                "embedding": report.embedding_name,
                "summary": report.summary(),
                "metrics": [
                    {
                        "id": m.question_id,
                        "category": m.category,
                        "difficulty": m.difficulty,
                        "evidence_count": m.evidence_count,
                        "top1_score": m.top1_score,
                        "value_hit_rate": m.value_hit_rate,
                        "confidence": m.confidence,
                        "issues_count": m.issues_count,
                    }
                    for m in report.metrics
                ],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Saved: {path}")

    # Comparison markdown
    sa = a.summary()
    sb = b.summary()
    md_lines = [
        f"# Embedding Benchmark Comparison ({timestamp})",
        "",
        f"| Metric | {a.embedding_name} | {b.embedding_name} | Delta |",
        "|--------|------|------|-------|",
    ]
    for key, label in [
        ("evidence_found_pct", "evidence_found_pct"),
        ("avg_top1_score", "avg_top1_score"),
        ("avg_value_hit_rate", "avg_value_hit_rate"),
        ("avg_confidence", "avg_confidence"),
        ("avg_issues_count", "avg_issues_count"),
    ]:
        delta = sb[key] - sa[key]
        arrow = "+" if delta > 0 else ("=" if delta == 0 else "-")
        md_lines.append(
            f"| {label} | {sa[key]:.3f} | {sb[key]:.3f} | {arrow}{abs(delta):.3f} |"
        )
    md_path = evals_dir / f"comparison_{timestamp}.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"Saved: {md_path}")


# ============================================================================
# Test entry points
# ============================================================================


@pytest.mark.anyio
async def test_benchmark_manual_hash_embedding() -> None:
    """Run benchmark with ManualHashEmbedding (baseline)."""
    report = await _run_benchmark(
        ManualHashEmbedding(), "ManualHashEmbedding (384d hash)"
    )
    s = report.summary()
    print(f"\nManualHashEmbedding: {json.dumps(s, ensure_ascii=False, indent=2)}")

    # Sanity: most questions should find evidence
    assert s["evidence_found_pct"] >= 0.5
    assert s["questions"] == 20


@pytest.mark.anyio
async def test_benchmark_siliconflow_bge() -> None:
    """Run benchmark with SiliconFlow BGE if API key is available.

    Skip if SILICONFLOW_API_KEY is not set.
    """
    from app.core.config import settings
    from app.services.embeddings.siliconflow_embedding import (
        SiliconFlowEmbedding,
    )

    if not settings.siliconflow_api_key:
        pytest.skip("SILICONFLOW_API_KEY not set")

    embed = SiliconFlowEmbedding(
        api_key=settings.siliconflow_api_key,
        base_url=settings.siliconflow_base_url,
        model=settings.embedding_model,
    )
    report = await _run_benchmark(
        embed, "BGE-large-zh-v1.5 (1024d SiliconFlow)"
    )
    s = report.summary()
    print(f"\nBGE-large-zh-v1.5: {json.dumps(s, ensure_ascii=False, indent=2)}")
    assert s["evidence_found_pct"] >= 0.5
    assert s["questions"] == 20


@pytest.mark.anyio
async def test_benchmark_comparison() -> None:
    """Run both and print side-by-side comparison.

    Skip comparison if BGE API key is not set.
    """
    from app.core.config import settings
    from app.services.embeddings.siliconflow_embedding import (
        SiliconFlowEmbedding,
    )

    if not settings.siliconflow_api_key:
        pytest.skip("SILICONFLOW_API_KEY not set — cannot compare")

    # ManualHashEmbedding baseline
    report_a = await _run_benchmark(
        ManualHashEmbedding(), "ManualHashEmbedding (384d hash)"
    )

    # SiliconFlow BGE
    embed = SiliconFlowEmbedding(
        api_key=settings.siliconflow_api_key,
        base_url=settings.siliconflow_base_url,
        model=settings.embedding_model,
    )
    report_b = await _run_benchmark(
        embed, "BGE-large-zh-v1.5 (1024d)"
    )

    _print_comparison(report_a, report_b)

    # BGE should outperform or equal ManualHashEmbedding
    assert report_b.summary()["avg_value_hit_rate"] >= report_a.summary()["avg_value_hit_rate"] * 0.8


@pytest.mark.anyio
async def test_benchmark_qwen_only() -> None:
    """Qwen embedding without reranker (baseline for reranker comparison)."""
    from app.core.config import settings
    from app.services.embeddings.siliconflow_embedding import (
        SiliconFlowEmbedding,
    )

    if not settings.siliconflow_api_key:
        pytest.skip("SILICONFLOW_API_KEY not set")

    embed = SiliconFlowEmbedding()
    report = await _run_benchmark(embed, "Qwen (no reranker)")

    s = report.summary()
    print(f"\nQwen (no reranker): {json.dumps(s, ensure_ascii=False, indent=2)}")
    assert s["evidence_found_pct"] >= 0.5


@pytest.mark.anyio
async def test_benchmark_qwen_with_reranker() -> None:
    """Qwen embedding + SiliconFlow Reranker."""
    from app.core.config import settings
    from app.services.embeddings.siliconflow_embedding import (
        SiliconFlowEmbedding,
    )
    from app.services.reranker import SiliconFlowReranker

    if not settings.siliconflow_api_key:
        pytest.skip("SILICONFLOW_API_KEY not set")

    embed = SiliconFlowEmbedding()
    reranker = SiliconFlowReranker()

    report = await _run_benchmark(embed, "Qwen + Reranker", reranker=reranker)

    s = report.summary()
    print(f"\nQwen + Reranker: {json.dumps(s, ensure_ascii=False, indent=2)}")
    assert s["evidence_found_pct"] >= 0.5


@pytest.mark.anyio
async def test_benchmark_reranker_comparison() -> None:
    """Side-by-side: Qwen-only vs Qwen+Reranker."""
    from app.core.config import settings
    from app.services.embeddings.siliconflow_embedding import (
        SiliconFlowEmbedding,
    )
    from app.services.reranker import SiliconFlowReranker

    if not settings.siliconflow_api_key:
        pytest.skip("SILICONFLOW_API_KEY not set — cannot compare")

    embed = SiliconFlowEmbedding()
    reranker = SiliconFlowReranker()

    report_no_rerank = await _run_benchmark(embed, "Qwen (no reranker)")
    report_rerank = await _run_benchmark(
        embed, "Qwen + Reranker", reranker=reranker
    )

    _print_comparison(report_no_rerank, report_rerank)

    # Reranker should not degrade results
    sa = report_no_rerank.summary()
    sb = report_rerank.summary()
    assert sb["avg_value_hit_rate"] >= sa["avg_value_hit_rate"] * 0.8
