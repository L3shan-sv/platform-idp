# ADR-008: k6 for Load Testing

**Status:** Accepted  
**Date:** 2024-06-01

## Decision
Use k6 for load testing. Target: 150 concurrent users sustained for 30 minutes. Spike test to 500 users.

## Why k6 over Locust or JMeter

**k6:** JavaScript test scripts, Prometheus metrics output, first-class CLI, excellent thresholds DSL. Scripts live in the repo alongside the code they test.

**Locust:** Python, good for complex scenarios, but slower at high concurrency and harder to integrate with CI.

**JMeter:** XML config is unmaintainable. Ruled out.

## Test design decisions

**Weighted scenarios** match real usage patterns (40% list, 20% detail, 15% compliance, 10% deploy, 10% budget, 5% AI). A uniform distribution would be unrealistic and would overload AI endpoints.

**Think time** of 0.5–2 seconds simulates human interaction. Without think time, k6 would measure the server's maximum throughput, not realistic concurrent user load.

**Custom metrics** (Trend per endpoint) give per-endpoint p95 in the summary. The default `http_req_duration` aggregates all endpoints — hiding slow outliers.

## Pass/fail criteria

All thresholds are defined in the script's `options.thresholds` block. CI fails the job if any threshold is breached. This makes load tests a real gate, not just an informational run.
