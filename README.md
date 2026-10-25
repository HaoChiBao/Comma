# comma, — Every Number Has a Story.

**A temporal knowledge graph and historical context engine for AI agents.**  
*Reconstruct the past, enforce temporal truth, and audit the context behind every number.*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Built for Cal Hacks](https://img.shields.io/badge/Built_for-Cal_Hacks_11.0-orange.svg)]()
[![Hosted Web](https://img.shields.io/badge/Hosted-Railway_App-brightgreen.svg)](https://web-production-def1f.up.railway.app)

---

## The Problem: AI Hindsight Bias

When evaluating historical financial decisions, standard AI agents and RAG pipelines suffer from **hindsight bias**. 

Imagine an accountant closing books on **September 30**: they book a **$150 accrual** based on a $145 purchase order and a $160 vendor email. Months later, in **December**, the vendor sends an invoice for **$158** printed with the date *"September 28"*. 

A traditional LLM accessing the entire corporate database will pull that December invoice and declare: *"The accountant made an $8 mistake!"*

Under GAAP, SOX, and audit standards, this is fundamentally wrong. Decisions must be evaluated **strictly on what was knowable at the moment**. 

**comma,** solves this by establishing a cryptographic temporal firewall: an invoice received in December stays completely out of a September snapshot—returning an explicit `FUTURE_INFORMATION` / 403 denial.

---

## Key Features

- **Point-in-Time Temporal Firewall**: Distinguishes between `document_date` (printed date) and `available_from` (actual received date). Enforces immutable SHA-256 snapshot manifests.
- **3D Obsidian Knowledge Graph**: Custom HTML5 Canvas visualizer mapping numbers to documents, calculations, and communications in a 3D rotating atom graph and 2D radial web.
- **Native Microsoft Excel Add-in (Office.js)**: Runs directly inside Excel. Inspects cell formula precedents (`=B3+C3`), auto-scans sheets to highlight anomalies with native hover notes (ExcelApi 1.18), and auto-drafts vendor discrepancy inquiries.
- **Autonomous Investigator & Agent Replay**: Simulates an autonomous audit agent probing evidence via tool calls. Logs blocked future reads into an immutable audit trail (`ToolEvent`).
- **SOX Separation of Duties**: The agent proposes actions rather than directly posting entries. Enforcing governance, only human users with the **Auditor** role can approve or reject recommendations.
- **Before / After Safety Comparison**: Pits an unrestricted LLM baseline (which leaks future data) against comma,'s time-fenced investigation.

---

## Architecture & Repo Structure

```text
├── apps
│   ├── web             # Next.js 15, React 19, TypeScript, Custom HTML5 Canvas 3D Graph
│   └── excel-addin     # Microsoft Excel Desktop Task Pane (Office.js, React, Webpack)
├── services
│   └── api             # FastAPI, Python 3.12, SQLAlchemy, PostgreSQL / SQLite, Graphiti
├── docs                # Architecture specifications & demo walkthrough scripts
└── fixtures            # Decagon synthetic company workbooks & seeded ground truth
```

---

## Quickstart

### 1. Run Everything with Docker (Recommended)

Starts Postgres, the FastAPI backend, and the Next.js web application:

```bash
docker compose up --build
```

- **Web Dashboard**: [http://localhost:3000](http://localhost:3000)
- **API Health Check**: [http://localhost:8000/health](http://localhost:8000/health)

*(Hosted deployments: [Web App](https://web-production-def1f.up.railway.app) · [API](https://api-production-69e9.up.railway.app))*

---

### 2. Manual Local Setup (Without Docker)

#### Backend (`services/api`)
```bash
cd services/api
pip install -r requirements.txt

# On macOS / Linux:
export PYTHONPATH=.
python -m uvicorn app.main:app --port 8000

# On Windows:
# set PYTHONPATH=.
# python -m uvicorn app.main:app --port 8000
```
*Uses a local SQLite file (`worldstate.db`) automatically.*

#### Web Frontend (`apps/web`)
```bash
cd apps
