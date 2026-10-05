```markdown
# InsightOS

> **From Data to Decisions.**

InsightOS is an open-source, local-first platform for **agentic data intelligence, business intelligence, and predictive analytics**.

Instead of requiring users to understand databases, SQL, data modeling, machine learning, and visualization separately, InsightOS is designed around a simple interaction:

> **Describe the outcome you want. InsightOS handles the data, analytics, AI, visualization, and prediction workflow.**

---

## 🎯 Vision

A user should be able to say:

> *"Analyze my sales data, show me what's happening, explain why revenue changed, identify unusual transactions, and forecast the next three months."*

InsightOS coordinates the technical workflow required to fulfill that request end-to-end:

```text
User Request
     ↓
Discover Data
     ↓
Profile Data
     ↓
Clean / Normalize
     ↓
Discover Relationships
     ↓
Design Analytical Model
     ↓
Build Semantic Layer
     ↓
Generate SQL
     ↓
Analyze Data
     ↓
Train / Select ML Models
     ↓
Validate
     ↓
Visualize
     ↓
Dashboard
     ↓
Explain Findings

```

The goal is not simply to build another dashboard tool. The goal is to build an **intelligent analytical system** that turns raw data into validated, explainable decisions.

---

## ✨ Core Capabilities

### Data Sources

* **Files:** CSV, Excel, JSON, Parquet, ZIP packages, folder uploads.
* **Databases:** PostgreSQL, MySQL, SQL Server.
* **Integrations:** REST APIs, Object Storage.

### Progressive Capabilities

* **Data Intelligence:** Automated profiling, data-quality analysis, relationship discovery, and schema design.
* **Semantic Layer:** Metrics definition, business logic encapsulation, and data lineage.
* **Natural Language Analytics:** Text-to-SQL generation, natural-language query engine, and auto-dashboard generation.
* **Predictive & Advanced Analytics:** Machine learning, statistical forecasting, anomaly detection, and root-cause analysis.
* **Monitoring & Alerting:** Live-data monitoring, automated metric tracking, and anomaly alerts.

---

## 🏗️ Architecture

### Agentic Architecture

InsightOS uses a **hierarchical, stateful multi-agent system** combining LLM reasoning with deterministic analytical engines.

```text
Planner / Orchestrator (LangGraph)
        │
        ├── Data Intelligence Agent
        ├── SQL / Analytics Agent
        ├── ML Agent
        ├── Visualization Agent
        ├── Validator / Critic Agent
        └── Monitoring Agent

```

The orchestration layer is powered by **LangGraph**, with tool execution routed via Model Context Protocol (**MCP**). InsightOS combines reasoning models with deterministic computational tools and strict validation steps to prevent hallucinated insights.

### Local-First Architecture

Designed to keep sensitive data local and execute compute-heavy workloads outside the browser engine.

```text
Browser Client
   │
   ▼
InsightOS Application Core
   │
   ├── FastAPI Backend
   ├── Async Worker
   ├── PostgreSQL
   ├── Redis
   └── MinIO

```

### Data & Analytics Engine

Built around memory-efficient analytical processing capable of scaling from low-end laptops to powerful workstations.

* **Processing:** DuckDB, Polars, Apache Arrow, Pandera.
* **Strategy:** Streaming, chunk processing, disk-backed intermediates, and columnar storage.

---

## 🛠️ Technology Stack

| Domain | Technology |
| --- | --- |
| **Frontend** | React, TypeScript, Vite, Tailwind CSS, shadcn/ui, Apache ECharts |
| **Backend** | Python, FastAPI, SQLAlchemy, Alembic, Pydantic |
| **AI / Agents** | LangGraph, MCP, Ollama, Qwen-family local LLMs |
| **Data Engine** | DuckDB, Polars, Apache Arrow, Parquet, Pandera |
| **Storage & Caching** | PostgreSQL, Redis, MinIO |
| **Machine Learning** | scikit-learn, XGBoost, PyTorch, Statsmodels, SHAP |
| **Infrastructure** | Docker, Docker Compose, GitHub Actions |
| **Security** | Keycloak, Role-Based Access Control (RBAC), Audit Logging |

---

## 📁 Repository Structure

```text
InsightOS/
├── frontend/             # React application
├── backend/              # FastAPI application
├── worker/               # Asynchronous task workers
├── infrastructure/       # Deployment configs
│   ├── docker/           # Dockerfiles & compose configs
│   └── azure/            # Cloud infrastructure templates
├── docs/                 # System documentation
│   ├── architecture/     # Architectural decision records
│   ├── api/              # API specifications
│   └── decisions/        # Design proposals
├── scripts/              # Developer tools & utilities
│   ├── dev/              # Development environment scripts
│   └── db/               # Database management scripts
├── .github/              # CI/CD workflows
├── .env.example          # Template environment variables
├── docker-compose.yml    # Main orchestration configuration
├── README.md             # Project overview
└── LICENSE               # License file

```

---

## 🗺️ Development Roadmap

```text
Phase 0 ──► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 ──► Phase 5 ──► Phase 6 ──► Phase 7 ──► Phase 8+
Project     Foundation  Ingestion   Profiling   Schema      Semantic    Agentic     NL-BI       ML &
Definition  (No AI)     & Storage   & Quality   Architect   Layer       Engine      Engine      Deployment

```

* [x] **Phase 0 — Project Definition:** Repository setup, architecture specification, and conventions.
* [ ] **Phase 1 — Foundation:** Core infrastructure (React, FastAPI, PostgreSQL, Redis, MinIO, Worker, Docker Compose).
* [ ] **Phase 2 — Data Ingestion:** Upload handlers (CSV, Excel, JSON, Parquet, ZIP) and dataset lifecycle management.
* [ ] **Phase 3 — Data Profiling & Quality:** Schema inspection, missing value analysis, duplicate detection, and quality reports.
* [ ] **Phase 4 — AI Schema Architect:** Relationship discovery, model recommendations (Star, Snowflake), and Data Model Playground.
* [ ] **Phase 5 — Semantic Layer:** Business metrics definitions, dimensions, measures, and data lineage mapping.
* [ ] **Phase 6 — Agentic Intelligence:** LangGraph multi-agent deployment (Planner, SQL, ML, Visualization, Validator).
* [ ] **Phase 7 — Natural-Language BI:** Conversational query interface, automated dashboard generation, and root-cause analysis.
* [ ] **Phase 8 — ML & Predictive Analytics:** Automated model selection, forecasting, anomaly detection, and SHAP explainability.
* [ ] **Phase 9 — Live Data & Monitoring:** Scheduled syncs, metric tracking, and anomaly alerts.
* [ ] **Phase 10 — Security & Governance:** Keycloak integration, RBAC, and audit logging.
* [ ] **Phase 11 — Optimization & Testing:** High-volume dataset profiling, failure recovery, and resource governance.
* [ ] **Phase 12 — Deployment:** Self-hosted and cloud production tooling.
* [ ] **Phase 13 — Open-Source Release:** v1.0.0 public launch.

---

## 🧠 Development Philosophy

1. **Outcome Over Implementation:** Users describe *what* they want; the system manages *how* to achieve it.
2. **Deterministic Execution First:** LLMs reason and plan; validated, deterministic engines execute computations.
3. **Autonomous but Controlled:** High agency with built-in validation gates and human oversight.
4. **Hardware Adaptive:** Runs comfortably on local developer laptops before scaling up to enterprise servers.
5. **Never Destroy User Work:** Non-destructive operations with full lineage tracking and checkpoints.
6. **Privacy by Design:** Local processing first to keep sensitive business data on-premise.

---

## 🔒 Security Principles

* **Least Privilege:** Strict scoping for agent and tool access.
* **SQL Injection Prevention:** Parameterized query construction across all database connectors.
* **Container & File Safety:** Comprehensive validation on archive uploads (ZIP bomb mitigation) and inputs.
* **Auditability:** Complete logging of system actions, generated queries, and agent executions.

---

## 📌 Current Status

* **Version:** `0.1.0`
* **Phase:** `Phase 0 — Project Definition`
* **Status:** 🟡 In Development

**Immediate Goal:** Finalize the project foundation, container setup, and base backend/frontend integration (Phase 1).

---

## 📄 License

InsightOS is released as open-source software under the terms specified in the [LICENSE](https://www.google.com/search?q=LICENSE) file.

```

```