#!/usr/bin/env python3
"""Reconcile cluster deployment configuration overrides from a JSON file.

Calls the Houston `updateCluster` GraphQL mutation with overrides.json as the
`deploymentsConfigOverride` argument. The file represents the body of
`cluster.configOverride.deployments` -- no outer "deployments" wrapper.

Subcommands:
    plan    Compare overrides.json with the cluster's current state and print
            a diff. Exits 0 on success or validation failure (the plan output
            is always meant for a PR comment).
    apply   Reconcile the cluster to match overrides.json. Skips the API call
            entirely when the file already matches the cluster's stored
            override (shift-left no-op).

Required environment:
    HOUSTON_BASE_URL    e.g. https://houston.example.astronomer.io
    HOUSTON_CLUSTER_ID  the target cluster's UUID
    HOUSTON_SA_TOKEN    a 32-char hex SA token with SYSTEM_ADMIN role

Stdlib only -- no pip install required.
"""

import argparse
import copy
import difflib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any


class UserError(Exception):
    """An error worth surfacing in a PR comment, not a Python traceback."""


_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")


def _redact(s: str) -> str:
    """Strip bearer tokens from any string (e.g. an upstream proxy that
    echoes our Authorization header in its 502 page) before it reaches
    a PR comment or step summary."""
    return _BEARER_RE.sub(r"\1[REDACTED]", s)


def _normalize_base_url(base_url: str) -> str:
    """Tolerate users who paste the base URL with or without a trailing
    `/v1`. Both `https://houston.example.com` and
    `https://houston.example.com/v1` should resolve to the GraphQL endpoint."""
    u = base_url.rstrip("/")
    if u.endswith("/v1"):
        u = u[: -len("/v1")]
    return u


# --- Houston client ---------------------------------------------------

GET_CLUSTER = """
query getCluster($id: Uuid!) {
  cluster(id: $id) {
    id
    name
    configOverride
  }
}
"""

UPDATE_CLUSTER = """
mutation updateCluster($id: Uuid!, $deploymentsConfigOverride: JSON) {
  updateCluster(id: $id, deploymentsConfigOverride: $deploymentsConfigOverride) {
    id
    name
    configOverride
  }
}
"""


def gql(query: str, variables: dict, base_url: str, token: str) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        url=f"{_normalize_base_url(base_url)}/v1",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise UserError(
            f"Houston API HTTP {e.code}: "
            + _redact(e.read().decode("utf-8", "replace"))
        )
    except urllib.error.URLError as e:
        raise UserError(f"Houston API unreachable at {base_url}: {e.reason}")

    if payload.get("errors"):
        raise UserError(
            "Houston API returned errors:\n"
            + _redact(json.dumps(payload["errors"], indent=2))
        )
    return payload["data"]


def get_current_overrides(cluster_id: str, base_url: str, token: str) -> dict:
    data = gql(GET_CLUSTER, {"id": cluster_id}, base_url, token)
    cluster = data.get("cluster")
    if cluster is None:
        raise UserError(f"Cluster {cluster_id} not found")
    return ((cluster.get("configOverride") or {}).get("deployments")) or {}


def apply_overrides(cluster_id: str, overrides: dict, base_url: str, token: str) -> dict:
    return gql(
        UPDATE_CLUSTER,
        {"id": cluster_id, "deploymentsConfigOverride": overrides},
        base_url,
        token,
    )["updateCluster"]


# --- Validation -------------------------------------------------------

def load_and_validate(path: str) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        raise UserError(f"{path}: not found")
    except json.JSONDecodeError as e:
        raise UserError(f"{path}: invalid JSON: {e}")

    if not isinstance(data, dict):
        raise UserError(
            f"{path}: top-level must be a JSON object, got {type(data).__name__}"
        )

    if "deployments" in data:
        raise UserError(
            f"{path}: outer 'deployments' key is not allowed.\n"
            "The Houston API expects the body of cluster.configOverride.deployments,\n"
            "not a wrapped object. Move everything under your 'deployments' key up\n"
            "one level. The Cluster Deployment Configuration UI has the same rule."
        )

    return data


# --- Diff helper -----------------------------------------------------

def compute_override(base: dict, desired: dict) -> dict:
    """Minimal override that, when merged into base, produces desired.

    Used to generate the path-bucketed diff display only -- we never submit
    the result to the API, because the Houston resolver replaces
    configOverride.deployments wholesale (see
    houston-api/src/lib/clusters/index.js). Mirrors computeOverride()
    in apc-ui/src/utils/configDiff.ts.
    """
    override: dict = {}
    for k, dv in desired.items():
        if k not in base:
            override[k] = copy.deepcopy(dv)
        elif isinstance(dv, dict) and isinstance(base[k], dict):
            nested = compute_override(base[k], dv)
            if nested:
                override[k] = nested
        elif dv != base[k]:
            override[k] = copy.deepcopy(dv)
    for k in base:
        if k not in desired:
            override[k] = None
    return override


# --- Diff display -----------------------------------------------------

def _canonical(d: dict) -> str:
    return json.dumps(d, indent=2, sort_keys=True)


def _flatten_paths(d: Any, prefix: str = "") -> list:
    if isinstance(d, dict) and d:
        out = []
        for k, v in d.items():
            out.extend(_flatten_paths(v, f"{prefix}.{k}" if prefix else k))
        return out
    return [(prefix, d)]


def render_diff(current: dict, desired: dict) -> str:
    if _canonical(current) == _canonical(desired):
        return (
            "## Cluster overrides plan\n\n"
            "No changes. `overrides.json` already matches the cluster's stored "
            "`configOverride.deployments`. Apply will be a no-op."
        )

    delta = compute_override(current, desired)
    removed = sorted(p for p, v in _flatten_paths(delta) if v is None)
    added_or_changed = sorted(p for p, v in _flatten_paths(delta) if v is not None)

    parts = ["## Cluster overrides plan", ""]

    if removed:
        parts += [
            "### Removals (paths in the cluster but missing from `overrides.json`)",
            "",
            "If a removal below was not intentional, the cluster may have been edited",
            "outside this repository (clickops drift). Investigate before merging.",
            "",
            "Note: removing a path here removes it from `configOverride.deployments`,",
            "but the previously-merged value may still remain in the cluster's",
            "*effective* config. To force-clear an inherited or previously-merged",
            "value, set the path to `null` in `overrides.json` instead of deleting it.",
            "",
        ]
        for p in removed:
            parts.append(f"- `{p}`")
        parts.append("")

    if added_or_changed:
        parts += [
            "### Additions and changes",
            "",
        ]
        for p in added_or_changed:
            parts.append(f"- `{p}`")
        parts.append("")

    cur_lines = _canonical(current).splitlines(keepends=True)
    des_lines = _canonical(desired).splitlines(keepends=True)
    udiff = "".join(
        difflib.unified_diff(
            cur_lines, des_lines,
            fromfile="cluster (configOverride.deployments)",
            tofile="overrides.json",
            n=3,
        )
    )
    parts += ["### Unified diff", "", "```diff", udiff.rstrip("\n"), "```"]
    return "\n".join(parts)


# --- Subcommands ------------------------------------------------------

def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise UserError(f"Environment variable {name} is required")
    return v


def cmd_plan(args) -> int:
    try:
        desired = load_and_validate(args.file)
        current = get_current_overrides(
            _env("HOUSTON_CLUSTER_ID"),
            _env("HOUSTON_BASE_URL"),
            _env("HOUSTON_SA_TOKEN"),
        )
        print(render_diff(current, desired))
        return 0
    except UserError as e:
        print(f"## Cluster overrides plan FAILED\n\n```\n{e}\n```")
        return 1


def cmd_apply(args) -> int:
    try:
        desired = load_and_validate(args.file)
        cluster_id = _env("HOUSTON_CLUSTER_ID")
        base_url = _env("HOUSTON_BASE_URL")
        token = _env("HOUSTON_SA_TOKEN")

        current = get_current_overrides(cluster_id, base_url, token)
        if _canonical(current) == _canonical(desired):
            print(
                "No changes -- cluster's configOverride.deployments already matches "
                "overrides.json. Skipping API call."
            )
            return 0

        print(render_diff(current, desired))
        print("\nApplying...")
        result = apply_overrides(cluster_id, desired, base_url, token)
        print(f"Cluster {result['name']} ({result['id']}) updated successfully.")
        return 0
    except UserError as e:
        print(f"Apply failed: {e}", file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--file",
        default="overrides.json",
        help="Path to the overrides JSON file (default: overrides.json)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan", help="Print a diff between overrides.json and the cluster")
    sub.add_parser("apply", help="Apply overrides.json to the cluster")
    args = parser.parse_args()
    return {"plan": cmd_plan, "apply": cmd_apply}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
