# apc-examples

A small collection of example patterns for working with Astronomer Private
Cloud (APC) on Kubernetes. Each subdirectory is a self-contained reference
implementation — read the example's own `README.md` for purpose,
prerequisites, and step-by-step instructions.

## Not officially supported

The contents of this repository are **examples**, not products. They are
released under the [MIT License](./LICENSE) and provided **"as is"**, with
no warranty and no support commitment from Astronomer. If you choose to
use any of this code against a real cluster:

- Read it first. Adapt it to your conventions.
- Test it against a non-production environment before applying anywhere
  that matters.
- Treat it like any other third-party snippet you might pull off a wiki —
  it works in the contexts it was tested in, and may need adjustment for
  yours.

If you'd like to discuss whether one of these patterns is a good fit
for your install, reach out to your Astronomer account team.

## Examples

| Directory | What it solves |
|-----------|----------------|
| [`argocd-based-chart-upgrades/`](./argocd-based-chart-upgrades/) | Upgrade the Astronomer Helm chart in a GitOps central-repo while preserving in-tree customizations. A small bash script wraps git's 3-way merge so the customization-replay is automatic. |
| [`cluster-overrides-via-api/`](./cluster-overrides-via-api/) | Manage `cluster.configOverride.deployments` (the **Cluster Deployment Configuration** UI page) as code. A single `overrides.json` file plus two GitHub Actions workflows (`plan` and `apply`) replace the click-driven flow with a PR-reviewed flow. |
