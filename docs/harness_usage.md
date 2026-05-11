# Harness Usage

The backend query flow is:

```text
QueryRequest -> AgentHarness -> ToolRegistry -> Tools/Retriever
             -> Draft Answer -> Evaluator -> TraceStore -> QueryResponse
```

## API

`POST /api/query`

```json
{
  "question": "发动机无法启动怎么办",
  "device_name": "摩托车发动机",
  "device_model": "optional"
}
```

The response keeps the existing `answer`, `plan`, and `evidence` fields and adds:

- `tool_calls`: executed tool records
- `evaluation`: safety, compliance, and confidence result
- `trace_id`: in-memory trace identifier
- `sop`: minimal work checklist

## Tools

Default tools are registered in `ToolRegistry`:

- `manual_lookup`: returns structured manual evidence
- `ai_coding`: generates reviewable script text without execution
- `compliance_check`: checks basic maintenance safety terms

## Extending

Add a tool by subclassing `BaseTool`, returning `ToolResult`, and registering it in `ToolRegistry`. Replace the Retriever placeholder with a real LlamaIndex index built from `data/processed/` and persisted under `data/indexes/`.
