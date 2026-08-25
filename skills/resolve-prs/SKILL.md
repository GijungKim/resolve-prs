---
name: resolve-prs
description: Assess and resolve GitHub dependency-update pull requests from Dependabot, Renovate, pyup, and conventional dependency-bump PRs. Use for dependency PR triage, isolated upgrade validation, safe merging, migration fixes, or explaining incompatible updates. Supports read-only assessment and multi-repository runs.
---

# Resolve Dependency PRs

Resolve dependency-update PRs without treating version numbers as proof of safety. Keep deterministic mechanics in the bundled helper, use the references only when their branch applies, and reserve model judgment for release research, risk assessment, and migration work.

## Inputs

Infer these from the request and current repository:

- **Target**: current repository, an explicit `owner/repo`, or every repository below the current directory when the user requests `--all`.
- **Mode**: `assess` for `--dry-run` or read-only requests; otherwise `resolve`.
- **Policy**: load `.resolve-prs.json` when present, validate it against [references/policy.schema.json](references/policy.schema.json), and merge it over [references/default-policy.json](references/default-policy.json). Continue honoring the legacy `.resolve-prs-ignore` file.

Do not depend on host-specific argument variables, tool names, agent APIs, or skill installation paths.

Resolve the absolute directory containing this loaded `SKILL.md` and call it `RESOLVE_PRS_SKILL_DIR`. Every bundled helper path below is relative to that directory, not the target repository. If the host does not expose the loaded path, locate the installed `resolve-prs/SKILL.md` first.

Load the effective policy deterministically with `python3 "$RESOLVE_PRS_SKILL_DIR/scripts/resolve_prs.py" load-policy`, adding `--path .resolve-prs.json` when the repository supplies an override.

## Required capabilities

- Authenticated GitHub access through `gh` or an equivalent GitHub integration.
- Git with worktree support.
- The package managers and project toolchains needed by affected repositories.
- Write access only for resolve mode.

If a capability is absent, continue with the portions that remain reliable and report the limitation. Never present an untested update as safe.

## Safety contract

These invariants override convenience and ecosystem guidance:

1. Assess mode performs no remote mutations and no source edits.
2. Validate medium/high-risk PRs in a disposable worktree created from the PR head.
3. Tie validation evidence to the exact PR head SHA and base SHA whose combined tree was tested. A changed head or base invalidates the evidence.
4. Process merges sequentially, lowest risk first. Earlier merges may invalidate later lockfiles or peer assumptions.
5. A merge requires green CI or passing local validation. Semver alone is never sufficient.
6. Re-read PR state immediately before every merge, close, rebase request, comment, or push.
7. Merge only through a merge queue or equivalent mechanism that validates the actual PR-head/base combination. A head-only compare-and-swap is insufficient; defer when base-bound merging is unavailable.
8. Never enable auto-merge unless policy permits it and merge-group validation covers the final base combination. Otherwise defer.
9. Keep the user's checkout unchanged; clean up only worktrees created by this run.
10. Get authorization at the point required by the host before an external mutation. A skill invocation does not bypass host approval policy.
11. Never perform an action omitted from the effective policy's `allowedActions`.

Use `python3 "$RESOLVE_PRS_SKILL_DIR/scripts/resolve_prs.py" evidence` and `gate` to create and verify the head-and-base evidence record rather than relying on conversational memory.

## Workflow

1. **Discover and normalize.** Read [references/github.md](references/github.md). Use the helper's `discover-repos`, `filter-prs`, and `inspect-repo` commands for deterministic classification and repository inspection.
2. **Assess.** Read [references/risk-policy.md](references/risk-policy.md). Inspect each diff, release notes, dependency constraints, and matching entries from `knowledge/patterns.json`. Treat knowledge entries as leads, not verdicts.
3. **Validate.** For medium/high risk, missing green CI, or ecosystem-specific concerns, read [references/validation.md](references/validation.md). Record checks against the tested head and base SHAs.
4. **Decide.** Choose one outcome: merge, fix then merge, close, defer, skip, or report-only. A close must include a concrete incompatibility; uncertainty means defer, not close.
5. **Act sequentially.** Before each mutation, read [references/github.md](references/github.md#taking-action) and refresh PR state. Require the green evidence gate for merge and migration-push actions; close, comment, and rebase requests instead require fresh state plus the specific evidence or policy that justifies them. In assess mode, describe the action without taking it.
6. **Report.** Include PR, update, risk, tested head/base, evidence, action, and reason. Distinguish `Merged` from `Auto-merge enabled` and both from `Deferred`.

## Multi-repository runs

Discover repositories with:

```bash
python3 "$RESOLVE_PRS_SKILL_DIR/scripts/resolve_prs.py" discover-repos --root . --max-depth 2
```

Process at most the configured number concurrently, subject to the host's available orchestration. Do not prescribe a specific subagent or team API. Within each repository, actions remain sequential.

## Knowledge maintenance

Never edit this installed skill during a PR-resolution run.

When a novel, generalizable migration succeeds, emit a candidate with `python3 "$RESOLVE_PRS_SKILL_DIR/scripts/resolve_prs.py" candidate`. Include the candidate in the report or contribute it to this repository for review. Read [references/knowledge.md](references/knowledge.md) before using, refreshing, or contributing entries.

## Report shape

| PR | Update | Risk | Tested head/base | Action | Evidence / reason |
|---|---|---|---|---|---|
| #N | package X → Y | Low/Medium/High | SHA or `not tested` | Merged/Fixed/Closed/Skipped/Deferred/Would… | concise evidence |

List any pattern candidates separately. Never claim the skill has learned a pattern until a reviewed knowledge change has landed.
