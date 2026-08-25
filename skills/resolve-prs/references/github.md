# GitHub adapter

Read this reference when discovering PRs, collecting release evidence, rebasing conflicts, or taking GitHub actions. Use an equivalent structured GitHub integration when `gh` is unavailable.

## Resolve the target

For the current checkout, derive the repository from `git remote get-url origin`. For an explicit `owner/repo`, GitHub inspection does not require changing the user's checkout. Resolve the default branch rather than assuming `main`:

```bash
gh repo view OWNER/REPO --json defaultBranchRef -q .defaultBranchRef.name
```

## List and normalize PRs

```bash
gh pr list --repo OWNER/REPO --state open \
  --json number,title,body,author,mergeable,mergeStateStatus,headRefName,headRefOid,labels,statusCheckRollup \
  | python3 "$RESOLVE_PRS_SKILL_DIR/scripts/resolve_prs.py" filter-prs
```

The helper recognizes Dependabot, Renovate, pyup, pre-commit-ci, lock-file maintenance, and conventional `chore(deps)`, `build(deps)`, or `bump` titles. Ambiguous human PRs require diff inspection.

For each candidate:

```bash
gh pr diff NUMBER --repo OWNER/REPO
gh pr diff NUMBER --repo OWNER/REPO --name-only
```

Do not trust initial mergeability or CI fields at action time; they are a discovery snapshot.

## Release evidence

Use, in order:

1. PR body and bot-supplied release notes.
2. The dependency's official changelog, releases, or migration guide covering `(old, new]`.
3. Changelog or migration files from the updated package inside the validation worktree.

For npm packages, query the exact target version rather than `latest`:

```bash
npm view "PACKAGE@NEW_VERSION" repository.url time --json
```

Normalize GitHub repository URLs defensively and skip GitHub release lookup for non-GitHub sources. If the release list does not reach the old version, say coverage is incomplete.

## Conflict refresh

Process PRs sequentially. Before every action:

```bash
gh pr view NUMBER --repo OWNER/REPO \
  --json mergeable,mergeStateStatus,headRefOid,statusCheckRollup
```

Resolve the current default-branch commit SHA as well. If the PR head or base head changed, discard prior evidence. Reassess when the target version changed; always validate the PR and current base as a combined tree.

For a lockfile conflict:

- Dependabot: request `@dependabot rebase` in a comment.
- Renovate: use its supported rebase control for that repository.
- Poll only for a bounded period. If the head does not refresh, defer.
- Assess mode reports `Would defer (needs rebase)` and sends no request.

## Taking action

### Merge

Discover allowed methods:

```bash
gh repo view OWNER/REPO --json squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed
```

Select the first allowed method from policy. Validate the evidence record against the just-refreshed head:

```bash
python3 "$RESOLVE_PRS_SKILL_DIR/scripts/resolve_prs.py" gate \
  --evidence EVIDENCE.json \
  --repo OWNER/REPO \
  --pr NUMBER \
  --current-head HEAD_SHA \
  --current-base BASE_SHA \
  --policy .resolve-prs.json
```

Omit `--policy` when no override exists. This gate proves the recorded validation still matches the observed PR head and base; it does not make a subsequent GitHub merge atomic.

GitHub's ordinary merge API can compare-and-swap the PR head, but it cannot bind the operation to an expected base SHA. It therefore does not satisfy this skill's base-bound safety contract. Merge only through a merge queue or equivalent platform mechanism that creates and validates the actual merge group against the latest base. Required merge-group CI is the merge authorization; the head-and-base evidence remains supporting assessment evidence.

When a repository has no base-bound merge mechanism, report `Deferred (base-bound merge unavailable)` rather than calling `gh pr merge`. Do not weaken this rule because a race window appears small. The same rule applies after pushing a migration fix.

Queue or auto-merge only when policy permits it, the platform guarantees the validated PR head remains the target, and merge-group CI validates the final base combination. Otherwise report `Deferred`.

### Fix and push

Check out the PR branch in a dedicated fix worktree. Make only the migration required by that PR, install against its lockfile, run full validation, and commit. Gate evidence against the new local commit, refresh the remote PR head, and push only if the branch still has the head on which the fix was based. A normal fast-forward push provides this protection; never overwrite a changed PR head. The push changes the remote head SHA, so all pre-fix evidence becomes invalid for merging.

### Close

Close only with evidence of incompatibility or explicit project policy:

```bash
gh pr close NUMBER --repo OWNER/REPO --comment "CONCRETE_REASON"
```

Include an appropriate Dependabot/Renovate ignore suggestion so the same update is not recreated. Do not modify bot configuration unless the user authorized source changes.

### Report

`Auto-merge enabled` is not `Merged`. `Rebase requested` is not `Deferred`. Record the exact state observed when the run ends.
