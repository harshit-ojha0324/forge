#!/usr/bin/env bash
# The interview demo: hammer the gateway, kill the primary model server
# mid-load, watch traffic fail over to the fallback with zero 5xx, then
# watch the circuit breaker recover when the primary comes back.
#
# Watch alongside: Grafana http://localhost:3000/d/forge-overview
set -euo pipefail
cd "$(dirname "$0")/../deploy/local"

DURATION=${DURATION:-60}
KILL_AT=${KILL_AT:-20}
REVIVE_AT=${REVIVE_AT:-40}

echo "==> starting the Forge stack"
docker compose up -d --build

echo "==> waiting for the gateway"
until curl -sf http://localhost:8080/healthz > /dev/null; do sleep 1; done

echo "==> load for ${DURATION}s; killing mock-vllm at t=${KILL_AT}s, reviving at t=${REVIVE_AT}s"
python3 ../../loadtest/loadgen.py --duration "$DURATION" --concurrency 12 &
LOADGEN_PID=$!

sleep "$KILL_AT"
echo "==> KILLING PRIMARY (mock-vllm)"
docker compose stop -t 0 mock-vllm

sleep $((REVIVE_AT - KILL_AT))
echo "==> REVIVING PRIMARY"
docker compose start mock-vllm

wait "$LOADGEN_PID"

echo
echo "Grafana:    http://localhost:3000/d/forge-overview"
echo "Prometheus: http://localhost:9090/alerts"
echo "Jaeger:     http://localhost:16686"
