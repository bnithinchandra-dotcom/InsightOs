\# InsightOS



> \*\*From Data to Decisions.\*\*



InsightOS is an open-source, local-first platform for \*\*agentic data intelligence, business intelligence, and predictive analytics\*\*.



Instead of requiring users to understand databases, SQL, data modeling, machine learning, and visualization separately, InsightOS is designed around a simple interaction:



> \*\*Describe the outcome you want. InsightOS handles the data, analytics, AI, visualization, and prediction workflow.\*\*



\---



\## Vision



A user should be able to say:



> "Analyze my sales data, show me what's happening, explain why revenue changed, identify unusual transactions, and forecast the next three months."



InsightOS should coordinate the technical workflow required to answer that request.



```text

User Request

&#x20;    ↓

Discover Data

&#x20;    ↓

Profile Data

&#x20;    ↓

Clean / Normalize

&#x20;    ↓

Discover Relationships

&#x20;    ↓

Design Analytical Model

&#x20;    ↓

Build Semantic Layer

&#x20;    ↓

Generate SQL

&#x20;    ↓

Analyze Data

&#x20;    ↓

Train / Select ML Models

&#x20;    ↓

Validate

&#x20;    ↓

Visualize

&#x20;    ↓

Dashboard

&#x20;    ↓

Explain Findings
The goal is not simply to build another dashboard tool.
The goal is to build an intelligent analytical system that turns raw data into validated, explainable decisions.
Core Capabilities
InsightOS is planned to support:
- CSV
- Excel
- JSON
- Parquet
- ZIP packages
- Multiple files and folders
- PostgreSQL
- MySQL
- SQL Server
- REST APIs
- Object storage
The platform will progressively support:
- Automated data profiling
- Data-quality analysis
- Relationship discovery
- Analytical schema design
- Semantic modeling
- Natural-language analytics
- SQL generation
- Dashboard generation
- Machine learning
- Forecasting
- Anomaly detection
- Root-cause analysis
- Live-data monitoring
- Alerts
Agentic Architecture
InsightOS uses a hierarchical, stateful multi-agent architecture.
The system combines:
- LLM reasoning
- Deterministic data processing
- SQL
- Machine learning
- Visualization
- Validation
- Tool execution
- Persistent workflow state
Planned agents include:
Planner / Orchestrator
        │
        ├── Data Intelligence Agent
        ├── SQL / Analytics Agent
        ├── ML Agent
        ├── Visualization Agent
        ├── Validator / Critic Agent
        └── Monitoring Agent

The orchestration layer is planned around LangGraph, with tool access through MCP.
InsightOS is not intended to be a custom-trained Large Action Model. It combines reasoning models with deterministic analytical systems and validation.
Local-First Design
InsightOS is designed to run locally whenever practical.
Browser
   │
   ▼
InsightOS Application
   │
   ├── FastAPI Backend
   ├── Worker
   ├── PostgreSQL
   ├── Redis
   └── MinIO

Heavy data processing should remain outside the browser.
The system is designed to adapt to:
- Low-end laptops
- Normal development machines
- Powerful workstations
- Future cloud deployments
Data & Analytics Engine
InsightOS is designed around memory-efficient analytical processing.
Planned technologies include:
- DuckDB
- Polars
- Apache Arrow
- Parquet
- Pandera
- PostgreSQL
Large analytical datasets should not be unnecessarily loaded into RAM or stored inside PostgreSQL.
The system should prefer:
- Streaming
- Chunk processing
- Disk-backed intermediates
- Columnar storage
- Parallel CPU processing
Machine Learning
InsightOS will progressively support:
- Classification
- Regression
- Forecasting
- Anomaly detection
- Clustering
Planned ML technologies include:
- scikit-learn
- XGBoost
- PyTorch
- Statsmodels
- SHAP
Predictions must be based on measured model results.
The LLM should explain predictions rather than invent them.
Visualization
InsightOS plans to use Apache ECharts for analytical visualization.
The system should select visualizations based on:
- The analytical question
- Data type
- Dataset structure
- Aggregation
- Time dimensions
- Relationships
- Statistical characteristics
Possible visualizations include:
- KPI cards
- Line charts
- Bar charts
- Scatter plots
- Heatmaps
- Maps
- Tables
- Trend visualizations
- Anomaly visualizations
Technology Stack
Frontend
- React
- TypeScript
- Vite
- Tailwind CSS
- shadcn/ui
- Apache ECharts
Backend
- Python
- FastAPI
- SQLAlchemy
- Alembic
- Pydantic
AI / Agents
- LangGraph
- MCP
- Ollama
- Qwen-family local LLMs
Data
- Polars
- Apache Arrow
- DuckDB
- Parquet
- Pandera
Storage
- PostgreSQL
- Redis
- MinIO
Machine Learning
- scikit-learn
- XGBoost
- PyTorch
- Statsmodels
- SHAP
Infrastructure
- Docker
- Docker Compose
- GitHub Actions
Security
- Keycloak
- Role-based authorization
- Audit logging
- Secure secret management
Project Architecture
InsightOS
│
├── frontend/
│
├── backend/
│
├── worker/
│
├── infrastructure/
│   ├── docker/
│   └── azure/
│
├── docs/
│   ├── architecture/
│   ├── api/
│   └── decisions/
│
├── scripts/
│   ├── dev/
│   └── db/
│
├── .github/
│   └── workflows/
│
├── .env.example
├── .gitignore
├── docker-compose.yml
├── README.md
└── LICENSE

Development Roadmap
Phase 0 — Project Definition
- Repository setup
- Documentation
- Architecture decisions
- Development conventions
- Git workflow
Phase 1 — Foundation
- React frontend
- FastAPI backend
- PostgreSQL
- Redis
- MinIO
- Worker
- Docker Compose
- Health monitoring
- CI
No AI in Phase 1.

Phase 2 — Data Ingestion
- CSV
- Excel
- JSON
- Parquet
- ZIP
- Multiple files
- Folder uploads
- Upload validation
- Dataset lifecycle
Phase 3 — Data Profiling & Quality
- Schema inspection
- Data types
- Missing values
- Duplicates
- Cardinality
- Invalid values
- Outliers
- Safe normalization
- Data Quality Reports
Phase 4 — AI Schema Architect
- Relationship discovery
- Schema recommendations
- Star schema
- Snowflake schema
- Normalized schema
- Hybrid schema
- Data Model Playground
Phase 5 — Semantic Layer
- Entities
- Dimensions
- Measures
- Metrics
- Lineage
Phase 6 — Agentic Intelligence
- Planner
- Data Intelligence Agent
- SQL Agent
- ML Agent
- Visualization Agent
- Validator Agent
- Monitoring Agent
- Stateful execution
Phase 7 — Natural-Language BI
- Natural-language questions
- Dashboard generation
- Root-cause analysis
- Trend analysis
- Comparisons
- What-if analysis
Phase 8 — ML & Predictive Analytics
- Classification
- Regression
- Forecasting
- Anomaly detection
- Clustering
- Model selection
- Explainability
Phase 9 — Live Data & Monitoring
- Scheduled refresh
- Source health
- Metric monitoring
- Anomaly detection
- Agent investigation
- Alerts
Phase 10 — Security & Data Lifecycle
- Authentication
- Authorization
- Keycloak
- Audit logging
- Data cleanup
- Secure file handling
Phase 11 — Optimization & Testing
- Large dataset testing
- Resource governance
- CPU/RAM/disk monitoring
- Failure recovery
- Checkpointing
- Performance optimization
Phase 12 — Deployment
- Local deployment
- Self-hosted deployment
- Cloud deployment options
Phase 13 — Open-Source Release
- Documentation
- CONTRIBUTING
- CODE_OF_CONDUCT
- SECURITY
- CHANGELOG
- Release process
- v1.0.0
Development Philosophy
InsightOS follows several principles:
1. Outcome over implementation
2. AI does not replace deterministic computation
3. Autonomous but controlled
4. Resource-aware execution
5. Never destroy user work
6. Privacy by design
7. Open-source first
8. Hardware adaptive
9. Explainable results
10. Build incrementally
Development Workflow
Every feature follows:
Plan
  ↓
Implement
  ↓
Test
  ↓
Fix
  ↓
Document
  ↓
Git Commit
  ↓
Next Feature

No major feature should be merged without testing.
Branch Strategy
main
 │
 ├── feature/*
 ├── fix/*
 └── experiment/*

main should remain stable.
Testing Strategy
InsightOS will use:
Backend
- Pytest
- API tests
- Service tests
- Database tests
Frontend
- TypeScript checks
- Component tests
- Playwright
Data
- Schema validation
- Transformation tests
- SQL validation
ML
- Training validation
- Test datasets
- Reproducibility
- Model evaluation
System
- Docker integration tests
- Large dataset tests
- Resource tests
- Failure recovery
- Checkpoint/resume tests
Security Principles
InsightOS follows:
- Least privilege
- Secure secret management
- Input validation
- File validation
- ZIP safety
- SQL injection prevention
- Parameterized queries
- Authentication
- Authorization
- Auditability
- Controlled external actions
Real secrets must never be committed to Git.
Current Status
Version: 0.1.0
Phase: Phase 0 — Project Definition
Status: 🟡 In Development
Current Objective
Establish a clean project foundation before implementing the application.
The immediate next milestone is:
Phase 0
   ↓
Phase 1 Foundation
   ↓
React + FastAPI
   ↓
PostgreSQL + Redis + MinIO
   ↓
Worker
   ↓
Docker Compose
   ↓
CI

Long-Term Goal
The final experience should feel like this:
"I have this data. Tell me what is happening, why it is happening, what happens next, and what I should consider."

InsightOS should transform that request into a transparent, validated, evidence-backed analytical workflow.
License
InsightOS is intended to be released as an open-source project.
See LICENSE for the applicable license.
InsightOS
From Data to Decisions.