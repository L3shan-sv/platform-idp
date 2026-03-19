# ADR-006: ArgoCD for GitOps Promotion

**Status:** Accepted  
**Date:** 2024-06-01

## Decision
Use ArgoCD ApplicationSet to manage three environments (dev, staging, prod) via GitOps. Dev auto-syncs on every push to main. Staging and prod require manual approval in the ArgoCD UI.

## Environment promotion model

```
push to main → ArgoCD auto-syncs dev
              → ArgoCD notifies: "staging out of sync"
              → SRE reviews diff in ArgoCD UI
              → SRE clicks "Sync" for staging
              → After staging validation, SRE syncs prod
```

No automated deployments to production. This is intentional. The golden path enforcer gates individual service deploys, but the platform infrastructure itself requires a human in the loop.

## Why not Flux?

ArgoCD UI is superior for a platform engineering team. The diff view, sync history, rollback UI, and application health tree are all better in ArgoCD. Flux is a better fit for teams that want pure CLI/GitOps workflows with no UI.

## Self-healing

ArgoCD `selfHeal: true` on dev only. If someone applies a one-off `kubectl apply` to the dev cluster, ArgoCD will revert it within 3 minutes. Staging and prod do not self-heal — manual `kubectl` is sometimes needed for incident response.
