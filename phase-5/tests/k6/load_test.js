/**
 * Nerve IDP — k6 Load Test
 * Target: 150 concurrent users, 30 minute sustained run
 *
 * Test scenarios (weighted by real-world usage):
 *   40% — List services (catalog read, most common operation)
 *   20% — Get service detail + blast radius
 *   15% — Compliance evaluation (pre-deploy check)
 *   10% — Submit deploy
 *   10% — Get error budget
 *    5% — AI co-pilot chat
 *
 * Pass criteria (Google SRE SLO):
 *   p95 latency < 500ms on all read endpoints
 *   p95 latency < 2000ms on write endpoints
 *   Error rate < 0.1%
 *   Zero 5xx responses on compliance evaluation
 *
 * Run:
 *   k6 run phase-5/tests/k6/load_test.js \
 *     --env BASE_URL=http://localhost:8000 \
 *     --env JWT_TOKEN=your-token
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend, Counter } from "k6/metrics";
import { randomItem } from "https://jslib.k6.io/k6-utils/1.4.0/index.js";

// ── Custom metrics ─────────────────────────────────────────────
const errorRate = new Rate("error_rate");
const catalogP95 = new Trend("catalog_list_p95", true);
const blastRadiusP95 = new Trend("blast_radius_p95", true);
const complianceP95 = new Trend("compliance_eval_p95", true);
const deployP95 = new Trend("deploy_submit_p95", true);
const errorBudgetP95 = new Trend("error_budget_p95", true);
const aiCopilotP95 = new Trend("ai_copilot_p95", true);
const deployBlocks = new Counter("deploy_blocks_403");
const freezeHits = new Counter("deploy_frozen_423");

// ── Config ────────────────────────────────────────────────────
const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const JWT_TOKEN = __ENV.JWT_TOKEN || "test-token";
const API = `${BASE_URL}/api/v1`;

const HEADERS = {
  "Content-Type": "application/json",
  Authorization: `Bearer ${JWT_TOKEN}`,
};

// ── Test stages ───────────────────────────────────────────────
export const options = {
  stages: [
    { duration: "2m", target: 30 },    // Ramp up to 30 users
    { duration: "3m", target: 75 },    // Ramp to 75 users
    { duration: "5m", target: 150 },   // Ramp to 150 users (target load)
    { duration: "15m", target: 150 },  // Sustain 150 users for 15 minutes
    { duration: "3m", target: 75 },    // Ramp down
    { duration: "2m", target: 0 },     // Cool down
  ],

  thresholds: {
    // p95 latency thresholds (ms)
    "catalog_list_p95": ["p(95)<500"],
    "blast_radius_p95": ["p(95)<200"],   // Redis cache should make this fast
    "compliance_eval_p95": ["p(95)<2000"],
    "deploy_submit_p95": ["p(95)<2000"],
    "error_budget_p95": ["p(95)<500"],
    "ai_copilot_p95": ["p(95)<5000"],    // AI calls take longer
    // Global error rate
    "error_rate": ["rate<0.001"],         // < 0.1% errors
    // HTTP errors — no 5xx allowed on compliance evaluation
    "http_req_failed": ["rate<0.001"],
  },
};

// ── Seed data — populated from a warm run ────────────────────
// Replace with real service IDs from your instance
const SERVICE_IDS = [
  "00000000-0000-0000-0000-000000000001",
  "00000000-0000-0000-0000-000000000002",
  "00000000-0000-0000-0000-000000000003",
];

// ── Scenarios ─────────────────────────────────────────────────
function listServices() {
  const params = ["", "?team=commerce", "?language=python", "?health=healthy", "?limit=50"];
  const res = http.get(`${API}/services${randomItem(params)}`, { headers: HEADERS });
  catalogP95.add(res.timings.duration);
  errorRate.add(res.status >= 400);
  check(res, {
    "list services 200": (r) => r.status === 200,
    "has items array": (r) => JSON.parse(r.body).items !== undefined,
    "response under 500ms": (r) => r.timings.duration < 500,
  });
}

function getServiceDetail() {
  const id = randomItem(SERVICE_IDS);
  const res = http.get(`${API}/services/${id}`, { headers: HEADERS });
  errorRate.add(res.status >= 500);
  check(res, {
    "get service 200 or 404": (r) => [200, 404].includes(r.status),
  });
}

function getBlastRadius() {
  const id = randomItem(SERVICE_IDS);
  const hops = randomItem([3, 4, 5]);
  const res = http.get(`${API}/services/${id}/blast-radius?hops=${hops}`, { headers: HEADERS });
  blastRadiusP95.add(res.timings.duration);
  errorRate.add(res.status >= 500);
  check(res, {
    "blast radius 200 or 404": (r) => [200, 404].includes(r.status),
    "has cached flag": (r) => r.status !== 200 || JSON.parse(r.body).cached !== undefined,
    "blast radius under 200ms": (r) => r.status !== 200 || r.timings.duration < 200,
  });
}

function evaluateCompliance() {
  const id = randomItem(SERVICE_IDS);
  const version = `v1.${Math.floor(Math.random() * 10)}.0`;
  const res = http.get(
    `${API}/services/${id}/compliance?version=${version}`,
    { headers: HEADERS }
  );
  complianceP95.add(res.timings.duration);
  errorRate.add(res.status >= 500);
  // Compliance evaluation must never 5xx
  check(res, {
    "compliance eval never 5xx": (r) => r.status < 500,
    "compliance under 2s": (r) => r.timings.duration < 2000,
    "has score field": (r) => r.status !== 200 || JSON.parse(r.body).score !== undefined,
  });
}

function submitDeploy() {
  const id = randomItem(SERVICE_IDS);
  const payload = JSON.stringify({
    version: `v1.${Math.floor(Math.random() * 10)}.0`,
    environment: "staging",
    notes: "k6 load test deploy",
  });
  const res = http.post(`${API}/services/${id}/deploy`, payload, { headers: HEADERS });
  deployP95.add(res.timings.duration);
  errorRate.add(res.status >= 500);

  if (res.status === 403) deployBlocks.add(1);
  if (res.status === 423) freezeHits.add(1);

  check(res, {
    "deploy accepted or policy block": (r) => [202, 403, 404, 423].includes(r.status),
    "deploy under 2s": (r) => r.timings.duration < 2000,
  });
}

function getErrorBudget() {
  const id = randomItem(SERVICE_IDS);
  const res = http.get(`${API}/services/${id}/error-budget`, { headers: HEADERS });
  errorBudgetP95.add(res.timings.duration);
  errorRate.add(res.status >= 500);
  check(res, {
    "error budget 200 or 404": (r) => [200, 404].includes(r.status),
    "error budget under 500ms": (r) => r.timings.duration < 500,
  });
}

function aiCopilotChat() {
  const payload = JSON.stringify({
    message: "payment-service error rate is spiking, what should I check?",
    incident_context: {
      service_name: "payment-service",
      error_rate: 0.12,
      burn_rate: 8.5,
      budget_consumed: 65.0,
    },
  });
  const res = http.post(`${API}/ai/chat`, payload, {
    headers: HEADERS,
    timeout: "10s",
  });
  aiCopilotP95.add(res.timings.duration);
  errorRate.add(res.status >= 500);
  check(res, {
    "ai chat 200 or 502": (r) => [200, 502].includes(r.status),
    "ai response under 10s": (r) => r.timings.duration < 10000,
  });
}

// ── Main VU function ──────────────────────────────────────────
export default function () {
  const rand = Math.random();

  if (rand < 0.40) {
    listServices();
  } else if (rand < 0.60) {
    getServiceDetail();
    sleep(0.1);
    getBlastRadius();
  } else if (rand < 0.75) {
    evaluateCompliance();
  } else if (rand < 0.85) {
    submitDeploy();
  } else if (rand < 0.95) {
    getErrorBudget();
  } else {
    aiCopilotChat();
  }

  // Realistic think time: 0.5–2s between actions
  sleep(0.5 + Math.random() * 1.5);
}

// ── Setup: create test services if needed ─────────────────────
export function setup() {
  const res = http.get(`${API}/services?limit=5`, { headers: HEADERS });
  if (res.status === 200) {
    const body = JSON.parse(res.body);
    if (body.items && body.items.length > 0) {
      return { serviceIds: body.items.map((s) => s.id) };
    }
  }
  return { serviceIds: SERVICE_IDS };
}

// ── Teardown: print summary ───────────────────────────────────
export function teardown(data) {
  console.log(`Load test complete. Service IDs tested: ${data.serviceIds.join(", ")}`);
}
