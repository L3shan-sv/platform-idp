/**
 * Nerve IDP — k6 Spike Test
 * Simulates a sudden traffic spike to 500 concurrent users.
 * Tests PgBouncer and Redis resilience under burst load.
 *
 * Run:
 *   k6 run phase-5/tests/k6/spike_test.js \
 *     --env BASE_URL=http://localhost:8000 \
 *     --env JWT_TOKEN=your-token
 */
import http from "k6/http";
import { check, sleep } from "k6";
import { Rate } from "k6/metrics";

const errorRate = new Rate("error_rate");
const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const JWT_TOKEN = __ENV.JWT_TOKEN || "test-token";
const API = `${BASE_URL}/api/v1`;
const HEADERS = { "Content-Type": "application/json", Authorization: `Bearer ${JWT_TOKEN}` };

export const options = {
  stages: [
    { duration: "30s", target: 10 },   // Baseline
    { duration: "1m", target: 500 },   // Sudden spike to 500
    { duration: "3m", target: 500 },   // Hold spike
    { duration: "1m", target: 10 },    // Quick recovery
    { duration: "30s", target: 0 },
  ],
  thresholds: {
    // Under spike, allow slightly more latency but no 5xx
    "http_req_duration": ["p(99)<5000"],
    "error_rate": ["rate<0.01"],        // Allow up to 1% errors during spike
    "http_req_failed": ["rate<0.01"],
  },
};

export default function () {
  // Spike test focuses on the most common read path
  const res = http.get(`${API}/services?limit=20`, { headers: HEADERS });
  errorRate.add(res.status >= 500);
  check(res, {
    "list services not 5xx": (r) => r.status < 500,
  });
  sleep(0.1 + Math.random() * 0.4);
}
