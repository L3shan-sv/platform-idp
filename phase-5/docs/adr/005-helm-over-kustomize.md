# ADR-005: Helm Over Kustomize for Application Deployment

**Status:** Accepted  
**Date:** 2024-06-01

## Decision
Use Helm 3 for Kubernetes deployment of all application services. Use Kustomize only for infrastructure components (Prometheus, Grafana) where upstream chart customisation is the goal.

## Why Helm wins for application services

**Templating power.** The HPA, NetworkPolicy, and resource limits all vary by environment. Helm's `values.dev.yaml` / `values.prod.yaml` pattern handles this cleanly. Kustomize patches are verbose for this use case.

**Dependency management.** The umbrella chart in `phase-5/infra/helm/stack/` installs all 12 services with a single `helm install`. Kustomize has no equivalent.

**Rollback.** `helm rollback nerve-prod 3` atomically reverts to a previous release. Kustomize requires manual Git revert and re-apply.

**ArgoCD integration.** ArgoCD has first-class Helm support — it renders the chart, diffs the output, and shows the diff in the UI before sync.

## Trade-offs accepted

Helm templates are harder to read than raw YAML. Accepted — the `_helpers.tpl` file is well-documented and the templates follow the official Helm chart boilerplate.

Helm chart versioning adds overhead. Accepted — charts are versioned alongside the app code in the same monorepo.
