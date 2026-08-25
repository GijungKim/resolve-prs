# Risk and decision policy

Read this reference for every candidate PR. Policy values come from `.resolve-prs.json`, falling back to `default-policy.json`.

## Qualification

A PR qualifies when its author is a recognized dependency bot or its title uses a conventional dependency-update prefix. Use `python3 "$RESOLVE_PRS_SKILL_DIR/scripts/resolve_prs.py" filter-prs`; do not reproduce title parsing in the prompt.

Renovate lock-file maintenance qualifies even without a package name. Skip package-specific knowledge matching and validate the install plus the affected project's checks.

Apply `.resolve-prs.json` `ignore` patterns first, then legacy `.resolve-prs-ignore` patterns. Report ignored PRs as `Skipped`.

## Baseline risk

- **Low**: patch updates; minor dev-only updates; lock-file maintenance; no known peer/framework constraint.
- **Medium**: minor runtime updates; major dev-tool updates; updates used directly on a critical path; low-risk updates without green CI or a viable smoke test.
- **High**: core runtime majors; framework/SDK transitions; peer-version mismatch; grouped update containing a breaking member; explicit migration or breaking notice.

Adjust upward for release evidence, affected surface, dependency centrality, or stale/conflicting knowledge. Never lower risk merely because a knowledge entry supplies a fix.

## Release age

For registries that expose publish time, defer patch/minor releases younger than `minimumReleaseAgeHours`. A missing timestamp is not proof of age; note the unavailable signal and continue based on other evidence.

## Evidence hierarchy

Use the strongest available evidence:

1. Validation run on the combined current PR head and base in an isolated worktree.
2. Green required CI attached to the current PR head.
3. Peer and framework constraint checks.
4. Release notes and migration guides covering the complete old-to-new range.
5. Matching knowledge entries.
6. Semver classification.

Items 5 and 6 never independently justify a merge.

## Outcomes

Before selecting an outcome, confirm its mutation is present in the effective policy's `allowedActions`. Missing permission means report or defer.

- **Merge** only with green evidence, no unresolved incompatibility, and a base-bound merge mechanism whose CI validates the actual merge group.
- **Fix then merge** when the migration is understood, scoped, validated after the fix, and permitted by policy.
- **Close** only for a concrete incompatibility or an update intentionally excluded by project policy. Explain how to keep the bot from reopening it.
- **Defer** for young releases, changing heads, unresolved conflicts, missing authorization, incomplete evidence, or uncertainty.
- **Skip** for ignore-policy matches.
- **Report-only** in assess mode.

Prefer defer over close when evidence is incomplete.
