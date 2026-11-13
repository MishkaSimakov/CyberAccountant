"""Report and require a running, healthy container for every Compose service."""

import json
import os
import subprocess


def compose(*args):
    return subprocess.check_output(["docker", "compose", *args], text=True)


print(f"## Compose health: {os.environ['COMPOSE_FILE']}\n", flush=True)
print("| Component | State | Health |", flush=True)
print("| --- | --- | --- |", flush=True)
healthy = True
for service in compose("config", "--services").splitlines():
    ids = compose("ps", "--all", "--quiet", service).splitlines()
    if not ids:
        print(f"| {service} | missing | missing |", flush=True)
        healthy = False
    for container_id in ids:
        state = json.loads(subprocess.check_output(
            ["docker", "inspect", "--format", "{{json .State}}", container_id], text=True,
        ))
        health = state.get("Health", {}).get("Status", "missing")
        print(f"| {service} | {state['Status']} | {health} |", flush=True)
        healthy &= state["Status"] == "running" and health == "healthy"
raise SystemExit(0 if healthy else 1)
