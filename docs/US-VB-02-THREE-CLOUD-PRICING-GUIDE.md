# AMC US-VB-02 · Three-cloud retrieval and deployment pricing guide

Status: engineering/billing guide, **not a current purchase quote**. Updated 2026-11-27.
Canonical PostgreSQL, fact history, permissions and durable outbox remain authoritative.
AWS / Alibaba / Azure indices are optional, rebuildable projections.

## 1. What is equivalent and what is not

| Provider | Retrieval product | Original evidence objects | API / billable model |
| --- | --- | --- | --- |
| Local | pgvector / FTS | local content-addressed objects | DB host CPU/RAM/disk, operator time |
| AWS | S3 Vectors | ordinary S3 | vector GB-month + PUT logical GB + query requests + query processed data + returned bytes |
| Alibaba | OSS Vectors Standard | ordinary OSS | vector GB-month + PutVectorData GB + QueryVectorData TB index scanned + OSS API calls + outbound traffic |
| Azure | Azure AI Search (Foundry IQ) | Azure Blob | Serverless preview compute CU-hour + indexed GB-month OR Dedicated search units/hour, plus feature add-ons |
| Azure alternative | Cosmos DB NoSQL + DiskANN | Blob | provisioned/autoscale RU/s or Serverless RU, storage, requests, optional multi-region |
| Azure alternative | Azure PostgreSQL Flexible Server + pgvector | Blob | vCore compute/hour + provisioned data storage + backups/HA/network |

**Azure Blob Storage is NOT a native S3 Vectors-compatible bucket.** AMC `azure_search.py` uses an Azure SearchClient adapter with a preprovisioned index. Do not point the S3 Vectors client at Blob.

## 2. Cost ledger (always itemize separately)

C01 source objects (S3 / OSS / Blob, storage tiers, redundancy, minimum duration)
C02 vector/index storage (logical vector bytes + keys + metadata + native index overhead where applicable)
C03 vector writes/reindex/rebuild and provider-specific minimum PUT size
C04 vector queries (per request, processed index volume, RU or CU/SU capacity, returned GB)
C05 upstream embeddings and query rewriting/answer tokens
C06 ingestion and ETL / indexers / reprocessing
C07 networking: egress, inter-region, cross-cloud and private link, CDN
C08 retention, versioning, backup, replication, KMS, snapshot restoration
C09 auditing: data access logs, logs retention, metrics and monitoring
C10 reliability: multi-AZ replicas, overprovisioning, failover and reconciliation
C11 operator hours and provider switching costs

Never compare only $/GB. In particular AWS S3 Vectors request pricing can have 128KB minimum per PUT; batch writes. Alibaba QueryVectorData bills **whole index size for each QueryVectors call**, not just returned vectors. Azure Dedicated scales **replicas × partitions**; capacity is not the same as data GB used.

## 3. Budget equations

Let N = indexed vector count, D = dimensions, M = average metadata+key bytes per vector, Q = monthly queries, U = logical GB uploaded per month, E = external source object GB, R = average bytes returned per query, I_j = size of queried index j, and q_j = queries to index j.

Approximate raw embedding GB: `N * D * 4 / 1e9`; logical stored GB: `N * (D*4 + M) / 1e9`. This is **not physical disk allocation**; actual index overhead varies.

AWS reference formula:
```
C_aws = storage_GB * regional_vector_storage_rate
      + max(put_GB, per_put_minimums) * regional_put_GB_rate
      + Q * regional_query_request_rate
      + sum_j(q_j * processed_GB_in_index_j * per_TB_query_tier_rate_j / 1000)
      + billable_return_GB * regional_return_rate
      + source_S3 + network + embedding + monitoring + other
```
AWS tiered processed price depends on the number of vectors per index; index layout is a **billing variable**. Do not assume one global index or ignore query request and return fees.

Alibaba Standard reference formula:
```
C_aliyun = storage_GB * VectorStorage_rate
         + billable_put_GB * PutVectorData_rate
         + sum_j(q_j * I_j_in_TB * QueryVectorData_rate)
         + OSS_GET_PUT_request_fees + NetworkOut
         + source_OSS + embedding + monitoring + other
```
20GB free monthly vector input quota is per UID and region under published terms; confirm actual deductions. Fusion Mode preview pricing must not be used as a long-term zero-cost assumption.

Azure AI Search Dedicated:
```
SU = replicas * partitions
C_azure_dedicated = SU * hourly_tier_rate * billable_hours
                  + add_on_features + source_Blob + network
                  + embeddings + indexer/AI enrichment + monitoring
```
Azure AI Search Serverless (preview):
```
C_azure_serverless = consumed_compute_CU_hours * regional_CU_hour_rate
                   + indexed_storage_GB_month * regional_storage_rate
                   + add_on_features + source_Blob + network
                   + embeddings + monitoring
```
Azure Cosmos DB:
```
C_cosmos = provisioned_RU_per_second_hours * regional_rate
         + storage_GB_month * storage_rate
         + multi_region_replication + backup + network + Blob
```
Serverless Cosmos substitutes consumed RU billing and applicable service constraints.

Azure PostgreSQL:
```
C_azure_pg = vCore_server_hours * SKU_rate + provisioned_storage
           + backups + HA + read_replica + network + Blob
```

Do not insert unverified Azure SKU dollar amounts into a quote. Obtain exact **region, currency, purchase model, service tier and effective enterprise discount** first.

## 4. Deployment and topology decision matrix

| Pattern | AMC setup | Billing shape | Strength | Weakness / gate |
| --- | --- | --- | --- | --- |
| P0 Local-only | PG + pgvector + local evidence | sunk hardware + electricity/backup | offline, lowest dependency | operational/DR burden |
| P1 Cloud archive | canonical PG + local pgvector + one S3/OSS vector index | local baseline + remote storage/PUT/queries | low-frequency durable recall | stale projection & data residency |
| P2 Azure-native | Azure PG + Azure Search + Blob | PG compute + Search Serverless/Dedicated + Blob | strong Azure integration | two paid always-on services in Dedicated |
| P3 Cloud split | canonical PG in one region + another cloud's vector search | each cloud product + egress both ways | flexible provider choice | latency, cross-border transfer and independent permissions |
| P4 Dual mirror | PG + AWS + Alibaba (possibly Azure) | each full cloud index and write + queries per policy | measured failover / compare | 2x/3x replicated storage and reconciliation |
| P5 Tiered | PG hot + one cloud warm/cold index + Blob/OSS/S3 evidence | hot compute + low query-index storage | avoid serving hot reads on cold index | query router, reindex, delete consistency |
| P6 Query-all quorum | PG + 2-3 cloud indices queried on every request | sum of query charges, cross-cloud network, latency | research/benchmark | not automatically safer and expensive |

### Recommended starting shape for AMC

Keep PostgreSQL as canonical, retain local pgvector as default. Enable **one** optional cloud vector backend at a time for the first test. Enable AWS+Alibaba+Azure only as independent shadow indexes with no impact on user-visible answers. Gate live retrieval with explicit flags, telemetry and cost limits. Dual or triple provider must not become a synchronous write dependency for canonical commit.

## 5. Workload classes: do NOT confuse price and SLO

W1 (Lab): 100k vectors, <10k queries/month, no HA promise. Compare AWS, Alibaba, Azure Search Serverless preview and pgvector incremental cost.
W2 (Knowledge archive): 10m vectors, 100k queries/month, monthly <=5% updates. Query and billing per tenant **index** not only global total.
W3 (Online product): 10m vectors, sustained concurrency, p95 target, freshness SLO, high uptime. Compare Azure Dedicated / Cosmos / Milvus / pgvector and bucket designs on same judged data.
W4 (Regulated): tenant-specific keys, deletion, provenance, data residency, multi-region failover. Price KMS, audit, private endpoints, backup and operational controls.
W5 (Hybrid/edge): AMD local agent + PG local + cloud history search. Measure WAN p95, timeouts and fail-open policy. Cloud failure must not corrupt canonical memory.

## 6. Required benchmark and billing receipt

For each identical corpus/model and region: vector count, avg logical vector/metadata/key bytes, number of indexes/tenants, source object GB; monthly inserted/updated/deleted vectors; queries by index; topK and filters; embedding tokens; p50/p95/p99; Recall@5/10, MRR@10, index freshness and stale hit rates. Collect provider billing export by SKU/line item, region and currency, service request IDs, month, build SHA, auth mode and evidence manifests. Distinguish projected vs invoice values and rounding. Enforce no secrets in reports.

## 7. Azure index and authentication prerequisites

Pre-provision Azure AI Search index with fields `id` (key),
`tenant_id` and `model_id` (filterable), `memory_id` (retrievable),
and `embedding` (searchable vector float32, correct dimensions and
vector search profile). Use least-privilege Azure RBAC with
`DefaultAzureCredential` where possible; for tests a key with limited
data-plane permissions may be used. Never send raw memory text or a whole
provenance receipt as vector metadata by default.

Python optional dependency:
```sh
pip install 'azure-search-documents>=11.6,<13' 'azure-identity>=1.19'
```

```python
from azure.identity import DefaultAzureCredential
from agent_memory_core.azure_search import AzureSearchConfig, AzureSearchAdapter
from agent_memory_core.hybrid import HybridMemorySearch

azure = AzureSearchAdapter.from_azure_sdk(
    AzureSearchConfig("https://your-service.search.windows.net", "memory-index", 384),
    credential=DefaultAzureCredential(),
)
searcher = HybridMemorySearch(postgres_store,
    vector_buckets={"aws": aws_adapter, "aliyun": aliyun_adapter, "azure": azure})
```
This is explicit dependency injection; the AMC server does not yet automatically enable Azure. Ingestion, correction, outbox, and real cloud acceptance are **not completed**.

## 8. Public price and capability reference pages

- AWS: https://aws.amazon.com/s3/pricing/
- Alibaba: https://www.alibabacloud.com/help/en/oss/vector-billing-item
- Azure Search: https://azure.microsoft.com/en-us/pricing/details/search/
- Azure Search models: https://learn.microsoft.com/en-us/azure/search/search-sku-tier
- Azure Cosmos: https://azure.microsoft.com/en-us/pricing/details/cosmos-db/standard-provisioned/
- Azure PostgreSQL: https://azure.microsoft.com/en-us/pricing/details/postgresql/flexible-server/
- Azure Blob: https://azure.microsoft.com/en-us/pricing/details/storage/blobs/
