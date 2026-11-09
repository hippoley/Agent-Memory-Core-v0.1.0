# Development integration image: Apache AGE + pgvector in ONE PostgreSQL instance.
# Age's latest image currently targets PostgreSQL 18; this integration image
# intentionally verifies installed extensions at runtime rather than assuming.
FROM apache/age:latest
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-18-pgvector \
 && rm -rf /var/lib/apt/lists/*
