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
| `deepseek` `dashscope` `zhipu` `openai` | any LLM | outbound | hosted API key, two lines of config |
| `vllm` `ollama-openai` | any local LLM | your GPU box | self-hosted OpenAI-compatible runtime |
| `custom` | any LLM | outbound to your endpoint | any other `/chat/completions` URL |
| `ollama` | any local LLM | loopback only | fully local native Ollama, no egress |

Presets supply `base_url` and a default `model`, so a hosted provider needs
only two variables:

```bash
MEMORY_EXTRACTOR_PROVIDER=deepseek
MEMORY_EXTRACTOR_API_KEY=sk-...
```

Anything can be overridden explicitly (`MEMORY_EXTRACTOR_BASE_URL`,
`MEMORY_EXTRACTOR_MODEL`, `MEMORY_EXTRACTOR_TIMEOUT=30`,
`MEMORY_EXTRACTOR_JSON_MODE=1`). Unknown provider names fail at startup with
the list of valid names instead of silently degrading.

A JSON file can hold the whole provider surface; environment variables win:

```json
{"extractor": {"provider": "dashscope", "model": "qwen-plus", "api_key": "sk-..."},
 "embedding": {"provider": "dashscope", "model": "text-embedding-v3", "dimensions": 512}}
```

```bash
MEMORY_PROVIDER_CONFIG=/etc/agent-memory/providers.json
```

All providers implement the same contract: `text -> list[{subject_id|subject_name,
predicate, value, confidence}]`, validated by `fact_contract.py` before any
write. Prompt-injection safety is shared: user text is always a JSON data
island, never instructions.

## Embedding provider (semantic channel)

Embeddings are the second model touchpoint and are equally optional. Without a
provider the vector channel only works when callers generate vectors
themselves. With one configured, the server vectorises claims on write and
queries on search:

```bash
MEMORY_EMBEDDING_PROVIDER=dashscope      # or openai | zhipu | vllm | ollama
MEMORY_EMBEDDING_API_KEY=sk-...
MEMORY_EMBEDDING_MODEL=text-embedding-v3
MEMORY_EMBEDDING_DIMENSIONS=512          # store has vector(384)/vector(512) only
MEMORY_AUTO_EMBED=1                      # 0 disables server-side vectorisation
```

Behaviour guarantees:

- `POST /v1/memories` gains `"embedded": true|false` only when a provider is
  configured; a provider outage never fails the canonical write.
- `POST /v1/memories/process` reports failed claim ids in `embedding_failures`.
- `POST /v1/search` without a caller `embedding` embeds the query itself;
  provider failure degrades to SQL/FTS/graph channels instead of erroring.
- Vectors that are non-finite, ragged, of the wrong width, or outside
  `{384, 512}` are rejected before anything is stored.
- `/v1/capabilities` reports `memory.embedding` with the active model so a
  client can decide whether to send vectors or let the server do it.

`MEMORY_AUTO_EMBED=0` keeps the provider configured (useful for evals or
backfills) while turning server-side vectorisation off.

## Choosing

| Situation | Recommendation |
| --- | --- |
| No network, no GPU | `rule` |
| Chinese quality matters, have a key | `deepseek` / `dashscope` extractor + `dashscope` embeddings at 512 dims |
| Data must never leave the rack | `ollama` or `vllm` on the GPU box |
| Cost-sensitive batch ingestion | `rule` + self-hosted `bge-m3` embeddings |

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

Two modes, chosen by whether `MEMORY_EMBEDDING_PROVIDER` is set:

- **Provider mode** — the server vectorises claims on `POST /v1/memories` and
  `POST /v1/memories/process`, and vectorises the query text on
  `POST /v1/search`. Write responses report `embedded: true|false`; a provider
  failure marks `embedded: false` and never fails the canonical write. Search
  falls back to the lexical channel and lists the gap in `degraded_channels`.
- **Caller mode** — no provider: callers send `model_id` + vector to
  `PUT /v1/memories/{id}/embedding` and `POST /v1/search`. The database tracks
  dimensions per `model_id` and rejects ragged or out-of-profile vectors.

Without embeddings of either kind the vector channel is absent and retrieval
degrades to SQL substring + full-text + graph, reported in `degraded_channels`.

## Capability discovery

`GET /v1/capabilities` reports `memory.extraction` with the active provider
name, so clients can decide whether to send `text` or `candidates`.
