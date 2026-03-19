# ADR-001: Temporal.io for IaC and Scaffolding Workflows

**Status:** Accepted  
**Date:** 2024-01-01

---

## Context

IaC apply and service scaffolding require multi-step workflows that interact with external systems: GitHub API, Terraform Cloud, Kubernetes, and Vault. These workflows are long-running (2–30 minutes), require human approval gates, and must be resumable if the worker crashes mid-execution.

---

## Decision

Use **Temporal.io** for IaC, scaffold, runbook, and chaos workflows.  
Keep **Celery** for lightweight stateless tasks: DORA computation, cost polling, maturity scoring.

---

## Why Temporal wins here

**Durable execution.** Temporal persists workflow state after every activity. If the worker crashes after creating the GitHub repo but before pushing the initial commit, Temporal resumes at the push step. Celery loses this state entirely.

**Human approval gates.** Temporal signals let a workflow pause indefinitely waiting for an external HTTP call. No polling, no database flags.

**Idempotency.** Each activity has an ID. Retries are deduplicated. `create_github_repo` checks if the repo exists before creating — safe to retry at any point.

**GitHub rate limit handling.** The `create_github_repo` activity distinguishes between a 403 auth failure (non-retryable) and a 403 rate limit (retryable after `X-RateLimit-Reset`). Temporal schedules the retry correctly.

---

## Workflow definitions

**ScaffoldWorkflow** (`nerve-scaffold` queue):  
validate → render template → create GitHub repo → push commit → branch protection → k8s namespace → Vault secrets → register catalog → sync Neo4j

**IaCApplyWorkflow** (`nerve-iac` queue):  
generate plan → store output → [await approval signal] → validate approver RBAC → apply → create k8s resources → provision Vault → update catalog → audit log

**RemediationWorkflow** (`nerve-runbooks` queue):  
validate RBAC → [await approval if required] → execute actions → write immutable audit log with runbook snapshot

---

## What Celery keeps

Maturity scoring, DORA computation, cost polling, doc indexing. All short-lived, stateless, tolerate retry without consequence.
