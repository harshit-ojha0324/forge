"""Smart-city agent — Forge tenant #1.

A LangGraph ReAct agent whose LLM calls go through the Forge gateway
instead of a hosted API: the gateway decides whether self-hosted vLLM or
the Gemini fallback answers, and the agent never knows the difference.
That indirection is the whole point — swap models, survive GPU loss, and
meter spend without touching agent code.

Notes:
  * Tool calling requires a backend that supports it (vLLM with
    --enable-auto-tool-choice, or real Gemini). Against the local mock
    backends the agent still runs end-to-end but the model answers
    directly without invoking tools.
  * --loop makes it emit continuous traffic, useful for dashboards.

Usage:
    python agent.py "Is traffic bad downtown right now?"
    python agent.py --loop
"""
import argparse
import itertools
import os
import random
import sys
import time

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

try:  # langgraph >= 1.0
    from langchain.agents import create_agent as create_react_agent
except ImportError:  # older langgraph
    from langgraph.prebuilt import create_react_agent

GATEWAY_URL = os.environ.get("FORGE_GATEWAY_URL", "http://localhost:8080/v1")
API_KEY = os.environ.get("FORGE_API_KEY", "forge-sc-localdev-key")
MODEL = os.environ.get("FORGE_MODEL", "forge-default")


@tool
def get_traffic(area: str) -> str:
    """Current traffic congestion level for a city area."""
    level = random.choice(["light", "moderate", "heavy", "gridlocked"])
    return f"Traffic in {area}: {level} (updated 2 min ago, sensor cluster ok)"


@tool
def get_bus_delays(route: str) -> str:
    """Delay report for a bus route."""
    delay = random.choice([0, 3, 7, 15])
    return f"Route {route}: running {delay} min behind schedule"


@tool
def get_air_quality(district: str) -> str:
    """Air quality index for a district."""
    aqi = random.randint(20, 160)
    return f"AQI in {district}: {aqi} ({'good' if aqi < 50 else 'moderate' if aqi < 100 else 'unhealthy'})"


PROMPTS = [
    "Is traffic bad downtown right now?",
    "How late is bus route 42 running?",
    "Should the city issue an air quality advisory for the river district?",
    "Compare congestion downtown versus the industrial zone.",
    "A citizen asks the best time to commute — what do the sensors say?",
]


def build_agent():
    llm = ChatOpenAI(
        model=MODEL,
        base_url=GATEWAY_URL,
        api_key=API_KEY,
        temperature=0.2,
        timeout=60,
    )
    return create_react_agent(llm, [get_traffic, get_bus_delays, get_air_quality])


def run_once(agent, prompt: str) -> str:
    result = agent.invoke({"messages": [("user", prompt)]})
    return result["messages"][-1].content


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="?", default=None)
    parser.add_argument("--loop", action="store_true", help="emit continuous traffic")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()

    agent = build_agent()
    if args.loop:
        for i in itertools.count():
            prompt = PROMPTS[i % len(PROMPTS)]
            try:
                answer = run_once(agent, prompt)
                print(f"[{i}] {prompt}\n    -> {answer[:120]}\n", flush=True)
            except Exception as exc:  # keep the traffic flowing through failovers
                print(f"[{i}] ERROR: {exc}", file=sys.stderr, flush=True)
            time.sleep(args.interval)
    else:
        prompt = args.prompt or PROMPTS[0]
        print(run_once(agent, prompt))


if __name__ == "__main__":
    main()
