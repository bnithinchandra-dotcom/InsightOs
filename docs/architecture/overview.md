# Architecture overview

This page is a short entry point, not an independent source of implementation
claims. The [canonical system design](system-design.md) records current
architecture, contracts, deployment boundaries, and verified capability
status.

## Current runtime

```text
Browser shell
    | health / system information
    v
FastAPI API ---- PostgreSQL metadata
    |             Redis connectivity (no job consumer)
    +----------- MinIO raw and normalized artifacts
    |
    +-- synchronous upload, normalization, quality-analysis, and EDA requests

Python libraries:
    Arrow preprocessing (in-memory)
```

The Compose development ports bind to localhost. The API has no
authentication/ownership enforcement, so remote and multi-user exposure is not
supported.

## Design direction

InsightOS is being built incrementally around deterministic data operations,
validated provenance, bounded resource use, and explicit failure states.
Agentic, semantic-layer, ML, and monitoring capabilities are roadmap items;
their presence in the product vision does not mean they are implemented.

See the canonical design for data flow and the distinctions between
implemented, partial, unverified, and planned behavior.
