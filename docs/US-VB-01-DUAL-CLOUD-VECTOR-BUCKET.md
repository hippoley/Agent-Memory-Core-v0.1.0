# US-VB-01 · AWS S3 Vectors and Alibaba OSS Vectors integration slice

Status: **adapter-only implementation; offline tests pending CI; cloud credentials and production end-to-end evidence NOT available**.

## Why this exists

AMC has a single authority: PostgreSQL canonical claims, bitemporal history and outbox. pgvector, FTS and AGE are derived indexes. AWS S3 Vectors and Alibaba OSS Vectors MUST remain derived candidate indexes. Provider results never override a canonical claim, tenant authorization, source evidence, `valid_at`, or `known_at`.

This first slice adds `agent_memory_core.vector_bucket` with a shared Boto3 `s3vectors` protocol for:
- `put`: deterministic, tenant/model-qualified keys with metadata.
- `query`: enforced tenant/model metadata filter; validates returned key, tenant, model and memory ID.
- `delete`: deterministic key deletion.
- Injected clients for offline contract tests, with no cloud credentials required.

It does **not** yet connect remote writes to the Postgres outbox or remote reads into `HybridMemorySearch`. No real AWS/Alibaba call is claimed, and the adapter is not a drop-in replacement for Postgres.

## Install and configure

Use a Boto3 version which provides `boto3.client("s3vectors")`; the historical minimum in AMC's `[s3]` extra is not sufficient to promise that API. Upgrade Boto3 explicitly:

```sh
python -m pip install --upgrade boto3
python -m pytest -q tests/test_vector_bucket.py
```

Cloud bucket and index are pre-provisioned with the same embedding dimensions and model profile as the caller. Set the index metadata policy so `tenant_id` and `model_id` are **filterable**.

AWS (use IAM role / Boto3 default credential chain; no hard-coded key):

```python
from agent_memory_core.vector_bucket import VectorBucketConfig, VectorBucketAdapter
config = VectorBucketConfig(provider="aws", bucket="amc-vector-bucket",
                            index="amc384", region="us-east-1", dimensions=384)
adapter = VectorBucketAdapter.from_boto3(config)
```

Alibaba (endpoint is the **vector bucket** endpoint, not ordinary OSS):

```python
import os
from agent_memory_core.vector_bucket import VectorBucketConfig, VectorBucketAdapter
config = VectorBucketConfig(provider="aliyun", bucket="your-vector-bucket",
    index="amc384", region="cn-hangzhou", dimensions=384,
    endpoint_url="https://your-vector-bucket-123456789012.cn-hangzhou.oss-vectors.aliyuncs.com")
adapter = VectorBucketAdapter.from_boto3(config,
    aws_access_key_id=os.environ["ALIBABA_CLOUD_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["ALIBABA_CLOUD_ACCESS_KEY_SECRET"])
```

Both use `put(tenant_id=..., memory_id=..., model_id=..., embedding=...)`, `query(tenant_id=..., model_id=..., embedding=..., top_k=...)` and `delete(...)`.

**Never** send API secrets to a PR, a log, or fixtures. For production use dedicated roles, TLS, least privilege, secret rotation and appropriate data residency.

## Required follow-up before calling this integrated

1. **Transactional outbox publication**: Add separately tracked `aws_vector` and `aliyun_vector` destinations. The current `ProjectionWorker.consume()` executes handlers inside a PostgreSQL transaction, so DO NOT make remote PUT or DELETE from that handler and call it atomic. Instead claim/lease events, perform idempotent remote mutation outside the SQL transaction, then persist independent success/receipt/retry/dead state. Handle crash-after-remote-write-before-ack and correction/supersession deletes.
2. **Canonical lookup on search**: extend `HybridMemorySearch` with optional provider candidate source(s). The cloud is allowed to return IDs only; the existing final canonical SQL tenant + two-times + supersession recheck must still apply. Never return provider metadata as an authoritative fact.
3. **Bounded retries and visibility**: distinguish remote timeout, throttling, API permission errors, index not found, stale projection. Alibaba documents eventual query visibility after PutVectors; do not treat PUT success as immediate query visibility.
4. **Index/version lifecycle**: one embedding model/dimension per index, strict config validation, rotation and reindex/cutover, deletion/retention reconciliation. Encrypt sensitive metadata and avoid storing raw user content in vector metadata.
5. **Real credentials acceptance**: provision AWS and Alibaba vector buckets, write known embeddings, query exact tenant, test tenant rejection, delete, restart/replay, and rate-limit/error cases. Save redacted request IDs, counts, run SHA, time and billed usage.
6. **Benchmark**: for the same Chinese AMC judged corpus, compare pgvector vs AWS vs Alibaba with Recall@5/MRR@10, P50/P95/P99, stale-hit rate, indexing visibility and total cost. Do not claim parity before measured receipts.

## Explicit engineering boundaries

- Current PostgreSQL local vector projection accepts 384 or 512 dimensions; this adapter can validate other cloud-supported dimensions but does not change the canonical embedder or SQL schema.
- An index metadata filter is **not** independent authorization. Prefer dedicated tenant indexes when stronger separation is required; enforce the same permission policy in PostgreSQL.
- Vector buckets are not general-purpose OSS object stores. Existing AMC evidence blobs remain separately stored and provenance-addressable.
- No automatic dual-write success or cross-cloud transactional guarantee; degraded modes must be exposed.
- Azure's query-rewrite pattern should be implemented as a separately testable, typed query planner, not buried inside storage clients.

## Sources

- https://docs.aws.amazon.com/boto3/latest/reference/services/s3vectors/client/put_vectors.html
- https://docs.aws.amazon.com/boto3/latest/reference/services/s3vectors/client/query_vectors.html
- https://www.alibabacloud.com/help/en/oss/developer-reference/compatibility-with-amazon-s3

## Verified cross-provider compatibility profile (2026-11-27)

| Property | AWS S3 Vectors | Alibaba OSS Vectors | AMC portable behavior |
|---|---|---|---|
| Boto3 service | `s3vectors` | `s3vectors` | shared calls |
| Addressing | AWS regional endpoint + bucket name | `https://{bucket}-{account}.{region}.oss-vectors.aliyuncs.com` | bucket name and index name only, never AWS ARN |
| Index naming | permits hyphen/dot | letters and digits only | choose `amc384` for both |
| Put / Query / Delete | supported | supported via S3 compatibility | portable core |
| Metadata filters | supported | supported in Standard mode | only tenant/model equality AND |
| Enhanced/index mode | Enhanced available | not supported via S3 API | never send `queryMode` by default |
| Fusion Mode | not Alibaba Fusion | Alibaba native API only | separate extension, not silently treated as portable |
| SSE configuration | supported | AWS-compatible parameter ignored | no portable SSE guarantee |
| Tags | supported | unsupported | no portable tags API |

The adapter explicitly validates Alibaba index name and verifies bucket/account/region endpoint shape to avoid silently misdirecting sensitive data. The AWS index may use its broader naming rules, but portable deployments should use the common naming subset.

Sources:
- https://help.aliyun.com/zh/oss/developer-reference/compatibility-with-amazon-s3
- https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors-query.html

No IAM/RAM provisioning is performed automatically. Operator must verify the SDK/region's actual protocol behavior, including request-signing and paginated query responses. Cloud production acceptance remains pending.
