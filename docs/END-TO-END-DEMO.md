# End-to-end real data-flow demo

Run a single combined PostgreSQL + pgvector + AGE instance:

```bash
docker compose up -d --build --wait
python -m pip install -e '.[dev,postgres]'
PYTHONPATH=src python examples/end_to_end.py
```

The script writes actual sample rows for a home, bedroom, window, CO2 sensor,
agent, conversation source, sourced claim, graph edges, time-series observation,
durable outbox event and content-addressed raw object. It projects the edges
into Apache AGE and queries them back.

The Temporal adapter is deliberately not started by this demo. Temporal is a
separate optional service and must be started only for durable long-running
workflows. See ADR-0004. This demo does not pretend that an outbox row has
already been consumed by an embedding worker.
