# ADR-007: RBAC Role Hierarchy

**Status:** Accepted  
**Date:** 2024-06-01

## Decision
Four roles in a strict hierarchy. Higher roles inherit all permissions of lower roles.

```
developer < sre < platform_engineer < engineering_manager
```

## Role permissions

| Action | developer | sre | platform_engineer | engineering_manager |
|---|---|---|---|---|
| Read catalog | ✅ | ✅ | ✅ | ✅ |
| Submit deploy | ✅ | ✅ | ✅ | ❌ |
| Scaffold service | ✅ | ✅ | ✅ | ❌ |
| Submit IaC request | ✅ | ✅ | ✅ | ❌ |
| Execute runbook | ❌ | ✅ | ✅ | ❌ |
| Unfreeze service | ❌ | ✅ | ✅ | ❌ |
| Approve IaC request | ❌ | ❌ | ✅ | ❌ |
| Create chaos experiment | ❌ | ❌ | ✅ | ❌ |
| Fleet bulk operations | ❌ | ❌ | ✅ | ❌ |
| View team costs | ❌ | ❌ | ✅ | ✅ |
| View audit log | ❌ | ✅ | ✅ | ✅ |

## Implementation

RBAC is enforced at the gateway via the `require_role(minimum_role)` dependency. Each router specifies the minimum role. Internal service-to-service calls use `NERVE_INTERNAL_TOKEN` (bearer token, not JWT) and bypass RBAC — this is safe because internal services are only reachable within the Kubernetes cluster.

## Why not ABAC or OPA for RBAC?

ABAC is over-engineered for 4 roles. OPA handles policy compliance (the 6 golden path checks) — mixing OPA with RBAC creates two policy systems that are hard to reason about independently. JWT role field + hierarchy check in Python is simple, auditable, and fast.
