# Extractor providers

Text extraction is the only optional model touchpoint in this service. The
canonical store, bitemporal versioning, deduplication, relation
classification, consolidation, soft forgetting, event log and SQL/FTS/graph
retrieval are deterministic code and run with **no model of any kind**.

`POST /v1/memories/process` accepts either explicit `candidates` (no extractor
needed) or free `text` (needs a provider below). When no provider is
configured, the text path returns 503 while every other endpoint keeps working.

## Providers

| `MEMORY_EXTRACTOR_PROVIDER` | Model | Network | Typical use |
| --- | --- | --- | --- |
| *(unset)* | none | none | callers supply structured candidates |
| `rule` | none | none | offline/free text extraction from template rules |
| `openai-compat` | any LLM | outbound to your endpoint | hosted API key or self-hosted runtime |
| `ollama` | any local LLM | loopback only | fully local model, no egress |

All providers implement the same contract: `text -> list[{subject_id|subject_name,
predicate, value, confidence}]`, validated by `fact_contract.py` before any
write. Prompt-injection safety is shared: user text is always a JSON data
island, never instructions.

## rule provider (zero model)

Deterministic regex templates. Builtin rules cover common Chinese room and
preference utterances (noise, light, temperature, likes, dislikes, allergy).

```bash
MEMORY_EXTRACTOR_PROVIDER=rule
MEMORY_RULE_DEFAULT_SUBJECT=user
# optional custom rules:
MEMORY_RULE_PATTERNS=/etc/agent-memory/rules.json
```

Custom rule file:

```json
{"rules": [
  {"name": "charging_window",
   "regex": "(?P<subject>car)?[^。\\n]{0,6}充电[^\\d。\\n]{0,6}(?P<hour>\\d{1,2})点",
   "predicate": "charging_start",
   "value": {"hour": "{hour:int}"},
   "confidence": 0.9}
]}
```

Value entries are either literals (`"quiet"`, `26`) or templates
(`"{group}"`, `"{group:int}"`, `"{group:float}"`). Subjects are taken from a
named `subject` group when present, otherwise the room word of the sentence,
otherwise `MEMORY_RULE_DEFAULT_SUBJECT`. Invalid rules fail at construction,
never mid-request.

## openai-compat provider

Any endpoint that speaks `POST {base_url}/chat/completions`:

```bash
# hosted
MEMORY_EXTRACTOR_PROVIDER=openai-compat
MEMORY_EXTRACTOR_BASE_URL=https://api.deepseek.com/v1/chat/completions
MEMORY_EXTRACTOR_MODEL=deepseek-chat
MEMORY_EXTRACTOR_API_KEY=sk-...

# self-hosted vLLM / SGLang / LM Studio
MEMORY_EXTRACTOR_PROVIDER=openai-compat
MEMORY_EXTRACTOR_BASE_URL=http://127.0.0.1:8000/v1/chat/completions
MEMORY_EXTRACTOR_MODEL=Qwen2.5-72B-Instruct
```

Optional: `MEMORY_EXTRACTOR_TIMEOUT` (default 30s), `MEMORY_EXTRACTOR_JSON_MODE`
(default 1; set 0 for providers without `response_format`).

Data leaves the process only toward the endpoint you configure. Choosing a
hosted API trades the local-first data posture for model quality; keep
`rule` or `ollama` when utterance content must never leave the host.

## ollama provider

```bash
MEMORY_EXTRACTOR_PROVIDER=ollama
MEMORY_LOCAL_OLLAMA_MODEL=qwen2.5:7b
MEMORY_LOCAL_OLLAMA_ENDPOINT=http://127.0.0.1:11434/api/generate
```

Loopback addresses only, by design. Setting `MEMORY_LOCAL_OLLAMA_MODEL` without
`MEMORY_EXTRACTOR_PROVIDER` selects `ollama` for backward compatibility.

## Embeddings

Embeddings are caller-generated in this design: `PUT /v1/memories/{id}/embedding`
and `POST /v1/search` take `model_id` + vector. Any encoder works (hosted
`text-embedding-*`, DashScope, or a local BGE/TEI deployment); the database
tracks dimensions per `model_id`. Without embeddings the vector channel is
absent and retrieval degrades to SQL substring + full-text + graph, which is
reported in `degraded_channels`.

## Capability discovery

`GET /v1/capabilities` reports `memory.extraction` with the active provider
name, so clients can decide whether to send `text` or `candidates`.
