# Benchmarks and evaluation data

- `chinese_memory_smoke.jsonl` is a 12-case smoke fixture covering positive
  preferences, negation, corrections, coreference and no-fact examples. It is
  useful for prompt and parser regression checks only.
- It is **not** a human-adjudicated release corpus and cannot satisfy the
  200-case Chinese extraction acceptance threshold.
- Release acceptance requires a separate 200+ case adjudicated JSONL file
  covering all seven categories required by `scripts/evaluate_local_extractor.py`.
- `scripts/benchmark_memory_concurrency.py` runs 100 logical requests by
  default against a disposable PostgreSQL tenant and reports p50/p95 latency,
  throughput and provenance integrity. Its rows persist; never use production.
