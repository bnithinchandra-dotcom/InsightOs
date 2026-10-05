\# InsightOS Architecture Overview



\## Purpose



This document describes the high-level architecture of InsightOS.



InsightOS is designed as a local-first, modular platform for agentic data intelligence, business intelligence, and predictive analytics.



\## High-Level Architecture



```text

┌─────────────────────────────────────────────┐

│                 Frontend                    │

│          React + TypeScript + Vite          │

└──────────────────────┬──────────────────────┘

&#x20;                      │

&#x20;                      ▼

┌─────────────────────────────────────────────┐

│              FastAPI Backend                │

│        API + Application Coordination       │

└───────┬──────────────┬──────────────┬───────┘

&#x20;       │              │              │

&#x20;       ▼              ▼              ▼

&#x20;PostgreSQL          Redis          MinIO

&#x20;Metadata           Cache /        Object Storage

&#x20;                   Messaging

&#x20;       │

&#x20;       ▼

┌─────────────────────────────────────────────┐

│                   Worker                    │

│       Background / Analytical Processing    │

└─────────────────────────────────────────────┘

Core Design Principles

\- Local-first execution

\- Deterministic computation for analytical operations

\- AI-assisted reasoning and orchestration

\- Stateful workflows

\- Resource-aware execution

\- Validation before final results

\- Privacy by design

\- Modular services

\- Incremental development

Technology Layers

Presentation

\- React

\- TypeScript

\- Vite

\- Tailwind CSS

\- shadcn/ui

\- Apache ECharts

Application

\- FastAPI

\- Python

\- SQLAlchemy

\- Pydantic

\- Alembic

Processing

\- Worker

\- Polars

\- Apache Arrow

\- DuckDB

\- Parquet

Storage

\- PostgreSQL

\- Redis

\- MinIO

AI

\- Ollama

\- Qwen-family local LLMs

\- LangGraph

\- MCP

Machine Learning

\- scikit-learn

\- XGBoost

\- PyTorch

\- Statsmodels

\- SHAP

Architecture Evolution

The architecture will evolve through the project phases.

Phase 1 establishes the infrastructure foundation.

AI and agentic capabilities are intentionally introduced only in later phases after the deterministic data platform is stable.



Save and close.



\### 3. Create our first Architecture Decision Record



Run:



```powershell

notepad docs\\decisions\\0001-local-first-architecture.md



\# ADR 0001: Local-First Architecture



\## Status



Accepted



\## Context



InsightOS is intended to process user-provided analytical data while maintaining privacy, predictable resource usage, and the ability to operate without mandatory cloud infrastructure.



\## Decision



InsightOS will follow a local-first architecture.



The browser will primarily act as the user interface and control surface.



Heavy computation and data processing will be performed by the local InsightOS runtime.



The architecture will use:



\- FastAPI for backend APIs
- PostgreSQL for metadata

\- Redis for caching and communication

\- MinIO for object storage

\- A dedicated worker for background processing

\- DuckDB, Polars, Arrow, and Parquet for analytical workloads



\## Consequences



\### Positive



\- Better data privacy

\- Reduced cloud dependency

\- Local development is straightforward

\- Large analytical workloads can remain outside the browser

\- The architecture can later support self-hosted and cloud deployments



\### Trade-offs



\- Local hardware resources become important

\- Users may need Docker and supporting services

\- Resource governance will be required for large datasets



\## Related Principles



\- Privacy by design

\- Hardware adaptive

\- Resource-aware execution

\- Open-source first

