# cluster-overrides-via-api

> **Example only — not officially supported by Astronomer.** This directory is
> provided as a reference implementation under the [MIT License](../LICENSE).
> It is offered "as is", with no warranty and no support commitment from
> Astronomer. Use at your own risk; review the code before running it against
> a production cluster, and adapt to fit your own conventions.

A reference example for managing Astronomer (APC) cluster-level deployment
configuration overrides as code. A single JSON file in this repository is
the source of truth for what would otherwise be edited via the **Cluster
Deployment Configuration** page in the APC UI.

The example targets **APC 1.1.x** and is built around the Houston
`updateCluster` GraphQL mutation.

---

## Purpose

The Cluster Deployment Configuration page in the APC UI is a powerful but
inherently click-driven workflow. For organizations that prefer (or are
required to) manage all production-affecting configuration through git +
pull request review, that workflow can be improved: changes are made by
clicking, audit trails live in Houston's database, and recreating a
cluster's overrides on a new install means reapplying values by hand from
a known-good config.

This example improves that workflow by treating
`cluster.configOverride.deployments` as code. A single `overrides.json`
file is the source of truth; two GitHub Actions workflows (`plan` and
`apply`) use the Houston GraphQL API to diff and reconcile that file
against the live cluster.

What this gives you:

- **`overrides.json`** — one file representing the entire body of
  `cluster.configOverride.deployments`. Edit it like any other code asset.
- A **plan** workflow that runs on every pull request, computes a diff
  against the cluster's current state, and posts the diff as a PR comment.
- An **apply** workflow that runs on merge to `main`, reapplies only when
  the file actually differs from the cluster (no churn on no-op pushes),
  and writes a result summary to the GitHub Actions run.
- **Drift detection**: if someone has edited the cluster outside this repo
  (clickops via the UI), the next PR's plan will surface the drift as
  removals in the comment.

### What this does *not* do

- **It does not re-render existing deployments.** The `updateCluster`
  mutation writes only to Houston's database (`cluster.config` and
  `cluster.configOverride`). A cluster-level override change is picked up
  by **new** deployments at creation time. Existing deployments need an
  `upsertDeployment` mutation (or a UI update) to re-render with the new
  values. This is a property of the platform, not of this automation —
  it is the single most common surprise after a successful apply.
- **It does not validate config semantics.** Basic JSON parsing and the
  outer-wrapper check happen client-side; everything else (component
  resource math, valid component names, schema correctness) is left to
  Houston to validate at apply time. If the apply fails server-side, the
  workflow fails and the GraphQL error is surfaced.
- **It does not handle multiple clusters.** One repo == one cluster. To
  manage many clusters from one repo, fork this and parameterize
  `HOUSTON_CLUSTER_ID` per workflow.

---

## Prerequisites

- An APC 1.1.x install with the Houston API reachable from your GitHub
  Actions runners (either GitHub-hosted, if your Houston is publicly
  reachable, or self-hosted runners on your network).
- A Houston Service Account with the `SYSTEM_ADMIN` system-level role.
  Cluster-level config overrides require system admin; deployment-level
  roles are not enough.
- A GitHub repository where you can configure secrets, variables, and a
  protected environment named `production`.
- Python 3 on the runner (the script is stdlib-only — no `pip install`
  required). The default `ubuntu-latest` GitHub-hosted runner already has
  this.
- The cluster's UUID. You can read it from the Houston API (see step 4
  below) or from the APC UI's cluster detail page URL.

---

## Instructions

### 1. Create a Houston Service Account with `SYSTEM_ADMIN`

In the APC UI, go to **System Admin → Service Accounts → Create Service
Account**. Pick a label like `gha-cluster-overrides`, role `SYSTEM_ADMIN`.

The 32-character hex token is shown **once**, in a modal, for ten minutes
after creation. Copy it immediately. After the window expires, only the
first six characters are recoverable; you would need to delete the SA and
make a new one.

### 2. Configure GitHub repository variables and secrets

In your repository: **Settings → Secrets and variables → Actions**.

| Kind     | Name                  | Example value                                      |
|----------|-----------------------|----------------------------------------------------|
| Variable | `HOUSTON_BASE_URL`    | `https://houston.platform.example.com`             |
| Variable | `HOUSTON_CLUSTER_ID`  | `8a3f6c12-9d4b-4f7e-b8a0-1c2d3e4f5a6b`             |
| Secret   | `HOUSTON_SA_TOKEN`    | the 32-hex token from step 1                       |

The base URL and cluster ID are not credentials, so they are stored as
variables. The SA token is the only sensitive value. The script accepts
the base URL with or without a trailing `/v1` (either form works).

### 3. Configure the `production` environment (required, not optional)

**Settings → Environments → New environment** — name it `production`.
Under **Deployment protection rules**, enable **Required reviewers** and
list the people who must approve a cluster reconciliation.

The `apply.yml` workflow targets this environment, but the gate is only
as strong as the rule you configure. Without required reviewers, the
`workflow_dispatch` trigger lets any contributor with write access push
arbitrary cluster overrides to production with no human approval. Treat
the required-reviewers rule as part of the security model, not a
nice-to-have.

### 4. Find your cluster ID

If you don't already know it, query Houston:

```bash
curl -s -X POST "$HOUSTON_BASE_URL/v1" \
  -H "Authorization: Bearer $HOUSTON_SA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"query { clusters { id name } }"}'
```

### 5. Adapt `overrides.json` to match your cluster's current state

The committed `overrides.json` is a small starter payload showing one
Istio `VirtualService` route under `helm.extraObjects` and a few
`statsd.overrideMappings` for Airflow operator-level metrics. **Replace
its contents with whatever your cluster currently has** before opening
your first real PR — otherwise the first apply will overwrite the
cluster's existing config with the example payload.

The simplest way to seed the file:

```bash
curl -s -X POST "$HOUSTON_BASE_URL/v1" \
  -H "Authorization: Bearer $HOUSTON_SA_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"query { cluster(id: \\\"$HOUSTON_CLUSTER_ID\\\") { configOverride } }\"}" \
  | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["data"]["cluster"]["configOverride"].get("deployments",{}), indent=2))' \
  > overrides.json
```

Commit the result, then start managing changes via PR.

---

## Day-to-day workflow

1. Create a branch.
2. Edit `overrides.json`.
3. Open a pull request.
4. The `plan` workflow runs and comments on the PR with the diff.
5. Review the diff. Pay particular attention to the **Removals** section
   (see "Removing a value" below).
6. Merge. The `apply` workflow runs on `main`. The `production`
   environment gate pauses it until a required reviewer approves.
7. The apply step writes its result to the workflow run summary.

A manual reapply can be triggered any time from the **Actions** tab via
the `apply` workflow's **Run workflow** button. It is also gated by the
`production` environment.

---

## The shape of `overrides.json`

The file represents the body of `cluster.configOverride.deployments`.
Top-level keys are siblings under `deployments` — there is **no outer
`deployments:` wrapper**. The script rejects the file if a top-level
`deployments` key is present, because the API silently auto-wraps it
into `deployments.deployments.*` and the override becomes inert.

### Important: arrays REPLACE on merge

The Houston resolver merges your override into the cluster's effective
config with **array replacement**, not concatenation. If `overrides.json`
contains a partial `helm.airflow.statsd.overrideMappings` list, for example, the
cluster's previous mappings are dropped — not appended. Always treat
arrays in this file as the complete desired set.

---

## Removing a value

The Houston resolver merges your override into the cluster's effective
config but does **not** automatically drop keys you remove from the file.
This is true for the UI editor as well — it is a property of the API,
not the automation.

**To force-clear a previously-set override**, set the path to `null` in
`overrides.json`. The resolver's `cleanNullValues` step removes the path
from the effective config.

```json
{
  "helm": {
    "airflow": {
      "workers": {
        "replicas": null
      }
    }
  }
}
```

After the apply, you can leave the `null` in place forever (it is
idempotent), or delete the entry on a subsequent commit. The plan
workflow will tell you when a deletion in the file would leave a stale
value behind.

---

## Token rotation and compromise response

If the `HOUSTON_SA_TOKEN` is leaked or you simply want to rotate it on a
schedule:

1. **Delete the existing service account** in the APC UI (System Admin →
   Service Accounts → the SA → Delete). Do NOT skip this step. Creating
   a new SA does *not* invalidate the old one; the old token remains
   valid until the SA is deleted or set to inactive.
2. Create a new SA with `SYSTEM_ADMIN` role per step 1 of the install
   instructions.
3. Update the `HOUSTON_SA_TOKEN` GitHub secret with the new token.
4. If the token was leaked, audit Houston API logs (or `clusterAudit`
   records) for unexpected `updateCluster` calls in the window between
   compromise and SA deletion.

---

## Files

| File | Purpose |
|------|---------|
| `overrides.json` | The single source of truth for cluster overrides |
| `reconcile.py` | Stdlib-only Python: validates, queries Houston, diffs, applies |
| `.github/workflows/plan.yml` | Runs on PR, posts diff comment |
| `.github/workflows/apply.yml` | Runs on merge to main, reconciles cluster |

---

## Help

This is an example, not a supported product. If something doesn't work
for your setup, reach out to your Astronomer account team.
