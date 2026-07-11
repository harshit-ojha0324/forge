// k6 load profile for the failover demo.
//
//   docker run --rm -i --network host grafana/k6 run - < k6-failover.js
//
// Thresholds encode the demo's claim: under sustained load with a
// mid-run primary kill, client-visible failures stay under 1%.
// 429s are expected behaviour (load shedding), not failures.
import http from 'k6/http';
import { check } from 'k6';

export const options = {
  scenarios: {
    steady: {
      executor: 'constant-arrival-rate',
      rate: 20,
      timeUnit: '1s',
      duration: '60s',
      preAllocatedVUs: 60,
    },
  },
  thresholds: {
    checks: ['rate>0.99'],
  },
};

const URL = __ENV.GATEWAY_URL || 'http://localhost:8080';
const KEY = __ENV.FORGE_API_KEY || 'forge-loadtest-localdev-key';

export default function () {
  const res = http.post(
    `${URL}/v1/chat/completions`,
    JSON.stringify({
      model: 'forge-default',
      messages: [{ role: 'user', content: `k6 probe ${__VU}-${__ITER}` }],
      max_tokens: 64,
    }),
    {
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${KEY}`,
      },
      timeout: '30s',
    },
  );
  check(res, {
    'no 5xx (failover held)': (r) => r.status < 500,
    'served or shed': (r) => r.status === 200 || r.status === 429,
  });
}
