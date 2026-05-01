# argocd-based-chart-upgrades

> **Example only — not officially supported by Astronomer.** This directory is
> provided as a reference implementation under the [MIT License](../LICENSE).
> It is offered "as is", with no warranty and no support commitment from
> Astronomer. Use at your own risk; review the code before running it against
> a production cluster, and adapt to fit your own conventions.

A small bash script that pulls a new official Astronomer Helm chart version
into a GitOps central-repo while preserving your in-tree customizations via
git's 3-way merge.

Single file. No dependencies beyond `git` and `curl` (optional `gh` for PR
creation). Self-documenting via `bin/upgrade-chart --help`.

---

## Purpose

In an ArgoCD + GitOps workflow where the upstream Astronomer Helm chart is
checked into your repository alongside in-tree customizations (Istio
VirtualServices, NetworkPolicies, custom RBAC, internal annotations,
subchart edits, environment-scoped values files, and so on), upgrading the
chart is error-prone:

- The "official" workflow is to download a new chart tarball, drop it into
  your repo, and reapply your customizations by hand or by memory.
- Every upgrade is a chance to silently drop a customization or miss a
  rename in a subchart.
- The customizations themselves are not versioned independently — they
  live as in-place edits across the chart tree and pure-add files alongside
  it.

This example formalizes the workflow so git does the customization-replay
automatically. The cost-of-upgrade drops from "an afternoon of careful diff
reading" to "five minutes of conflict resolution, if any conflicts come up
at all".

The mechanism: a dedicated `upstream-chart` branch tracks the vanilla
chart with no shared history. Each upgrade rebases your customizations on
top of the new vanilla state via a real 3-way merge — git auto-resolves
non-overlapping changes and surfaces only the conflicts that need a human.

---

## Prerequisites

This example assumes you are running an APC deployment that follows roughly
this pattern:

- A "central-repo" git layout, where the Astronomer Helm chart lives in a
  subdirectory of your repo (e.g. `astronomer/`) alongside your
  customizations and any per-environment values files.
- ArgoCD (or any GitOps tool) watches a customizations branch and syncs to
  the cluster on every commit.
- The chart version you currently run is published at
  `https://helm.astronomer.io/index.yaml` — the script downloads tarballs
  directly via `curl`, no Helm repo authentication required.

You will also need:

- `bash`, `git`, and `curl` on the machine that runs the upgrade.
- A working tree that is clean at the moment you run the script (the
  script refuses to operate on dirty trees).
- Optionally, the GitHub CLI (`gh`) installed and authenticated, to open a
  pull request automatically. Without it, the script prints the branch
  metadata so you can open the PR by hand.

The script is opinionated about three names but all are configurable via
flags:

| Default                      | Flag                | Meaning                                            |
|------------------------------|---------------------|----------------------------------------------------|
| `astronomer/`                | `--chart-dir`       | Subdirectory holding the chart inside your repo    |
| `dev`                        | `--target-branch`   | Branch holding your customizations                 |
| `upstream-chart`             | (not configurable)  | Dedicated chart-only branch the script maintains   |

---

## Instructions

### One-time bootstrap (per central-repo)

The first time you use the script in a given repo, you need to tell it what
official chart version your customizations branch currently sits on top of.
That value seeds the `upstream-chart` branch.

To find your current version:

```bash
awk '/^version:/ {print $2}' astronomer/Chart.yaml
```

Then run the bootstrap with that value:

```bash
cd <your-central-repo>
bin/upgrade-chart --bootstrap 1.1.0
```

This creates an orphan `upstream-chart` branch (no shared history with
`main`/`dev`) holding only the official chart contents at v1.1.0. The
bootstrap is one-time per repo — every future upgrade reuses this branch.

### Each future upgrade

```bash
bin/upgrade-chart 1.1.3
```

The script:

1. Fetches the new tarball from `https://helm.astronomer.io/`.
2. Updates `upstream-chart` to the new version on top of its previous head.
3. Creates `chart-upgrade/1.1.3` from your customizations branch.
4. Runs `git merge upstream-chart` — a real 3-way merge.
5. Pushes the upgrade branch and opens a PR (if `gh` is available).

The merge produces four classes of outcome:

- **Files you didn't touch** → applied upstream-clean (no work for you).
- **Files only you changed** → preserved (no conflict possible).
- **Files both touched, different regions** → auto-resolved.
- **Files both touched, overlapping regions** → CONFLICT, you resolve manually.

### Per-environment workflow

The script doesn't know about your environment topology — it operates on
whatever git repo it's invoked from. To upgrade across multiple
environments (e.g. dev / test / prod), run it from each central-repo
separately:

```bash
cd <your-dev-central-repo>
bin/upgrade-chart 1.1.3
# review PR, merge, watch ArgoCD sync, validate Houston

cd <your-test-central-repo>
bin/upgrade-chart 1.1.3
# same drill

cd <your-prod-central-repo>
bin/upgrade-chart 1.1.3
# same drill
```

Each repo gets its own bootstrap once, then is fully self-contained.
Upgrading dev first gives you a chance to surface and resolve conflicts
before touching test or prod.

### Resolving conflicts

Conflicts happen when both upstream and your customizations touch the same
region of the same file. They become more common on major upgrades. When
they happen:

1. Run `git status` — files in conflict show status code `UU`.
2. Open each file. Look for `<<<<<<<` / `=======` / `>>>>>>>` markers.
3. Edit the conflict region to capture the **intent** of both sides:
   - Upstream is usually doing something structural (refactor, new
     feature, deprecation).
   - Your customization is usually value-tuning (a different annotation,
     a bigger buffer, an extra label).
   - Most of the time both intents can coexist — keep upstream's
     structure and apply your tuning on top.
4. Sanity check before committing:
   ```bash
   diff <(git show upstream-chart:<file>) <file>
   ```
   The output should be **exactly** your intended customization. If you
   see extra lines, you've kept stale structure that should have been
   dropped.
5. `git add <file>` for each resolved file.
6. `git merge --continue`.
7. Push the upgrade branch and open the PR.

### Major version upgrades

Upgrading across major versions (e.g. 1.x → 2.0) may surface more
conflicts and some classes of breakage that are NOT just textual:

- **Files renamed** → your edits on the old name conflict with the
  deletion. Reapply your customization on the new file.
- **Values keys restructured** → chart installs but your values silently
  stop applying. Cross-reference the upstream changelog.
- **New required values with no defaults** → `helm template` fails
  post-merge.

The script can't detect any of these — they're semantic, not textual.
Always read the upstream release notes before a major upgrade.

---

## Troubleshooting

The full troubleshooting guide lives in the script itself — run
`bin/upgrade-chart --help`. Highlights:

| Message | Resolution |
|---------|------------|
| `Working tree is not clean` | Commit or stash your changes, then re-run. |
| `Branch 'upstream-chart' does not exist` | Run `--bootstrap <current-version>`. |
| `MERGE CONFLICTS encountered` | See "Resolving conflicts" above. |
| `Failed to download` | Check that the target version is published at https://helm.astronomer.io/index.yaml. Common typo: `1.13` vs `1.1.3`. |

---

## Validation

This example was developed and tested in a representative sandbox: a
vanilla v1.1.0 chart in a git repo, plus five customizations covering the
patterns most commonly seen in real central-repos:

- Pure-add files at the root of `templates/`
- Pure-add directories under `templates/` (Istio VirtualServices, custom
  glue manifests)
- In-place subchart edits (a tweak inside `charts/houston/templates/`)
- Sibling values files outside the chart directory (e.g. `env/dev.yaml`)
- A top-level values override file

A 1.1.0 → 1.1.2 upgrade run produced:

- 23 of 24 upstream-changed files merged silently with no human attention.
- 1 conflict on a file with overlapping edits — caught, reported with
  resolution instructions, exit code 3.
- All 5 customizations preserved on the upgrade branch.
- Chart version correctly bumped to 1.1.2 in `Chart.yaml`.

End-to-end test time from "decide to upgrade" through "ready to merge PR":
roughly five minutes, including manual conflict resolution.

---

## Help

This is an example, not a supported product. If something doesn't work
for your setup, reach out to your Astronomer account team.
