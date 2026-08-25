# Validation adapter

Read this reference when CI is absent or insufficient, risk is medium/high, a knowledge entry requires a special check, or a migration fix is needed.

## Inspect the repository

Use the helper before inventing commands:

```bash
python3 "$RESOLVE_PRS_SKILL_DIR/scripts/resolve_prs.py" inspect-repo --root PATH \
  --changed path/to/package.json --changed path/to/lockfile
```

It reports package managers, workspace signals, affected package directories, and available scripts. If it reports ambiguity, inspect the manifests rather than choosing arbitrarily.

Run the project's own documented verification commands. Prefer CI-equivalent commands and package-manager-local executables. A check that silently rewrites files is not a passing check unless the resulting changes are intentionally included in a fix.

## Worktree isolation

Resolve the exact PR head and current default-branch head, fetch both, and create a disposable worktree outside the user's checkout. Track the path created by this run and remove that path in a `finally`/trap path.

Validation must exercise their combined tree. Either require the PR to rebase onto the current base first, or make a synthetic, uncommitted merge of `BASE_SHA` into the detached PR-head worktree. A merge conflict means the PR needs a rebase; do not validate the stale head by itself.

Conceptual sequence:

```bash
worktree_dir=$(mktemp -d -t resolve-prs-XXXXXX)
git worktree add --detach "$worktree_dir" HEAD_SHA
git -C "$worktree_dir" merge --no-commit --no-ff BASE_SHA
# install and validate inside $worktree_dir
git worktree remove "$worktree_dir" --force
```

Do not clean up worktrees merely because their names resemble this skill's prefix; another process may own them. Remove only paths created and recorded by the current run.

Install using the PR's manifest and lockfile:

- `bun.lock`/`bun.lockb`: bun
- `pnpm-lock.yaml`: pnpm
- `yarn.lock`: yarn
- `package-lock.json`: npm
- `Gemfile.lock`: Bundler

For workspace monorepos, install at the workspace root and validate each affected package using its actual scripts. For unrelated nested applications, install and validate in each affected root.

## Check selection

- Low risk without green CI: typecheck/compile plus non-mutating lint.
- Medium risk: install, typecheck/compile, lint, and tests.
- High risk: medium checks plus the production build/bundle and ecosystem diagnostics.
- Runtime/build-tool updates: always exercise the build artifact; typechecking may not catch removed runtime exports.
- Bundler updates: run `bundle install` plus a cheap `bundle exec` smoke command used by the repository.

Skip an absent check only after inspecting scripts/configuration. Record it as unavailable; do not call it passed.

## Evidence record

After validation, create an evidence record tied to the tested head and base:

```bash
python3 "$RESOLVE_PRS_SKILL_DIR/scripts/resolve_prs.py" evidence \
  --repo OWNER/REPO \
  --pr NUMBER \
  --head-sha HEAD_SHA \
  --base-sha BASE_SHA \
  --risk medium \
  --policy .resolve-prs.json \
  --check install=pass \
  --check typecheck=pass \
  --check lint=pass \
  --check test=pass > EVIDENCE.json
```

Omit `--policy` when the repository has no override. Use `fail`, `unavailable`, or `skipped` honestly. Evidence requirements come from the effective policy. The gate rejects failed checks or a mismatched repository, PR, head, or base.

## Ecosystem branches

### Expo

When an affected manifest contains `expo`, run inside the installed worktree:

```bash
CI=1 npx expo install --check
```

For an Expo SDK transition, also run `npx expo-doctor` and align the SDK's React Native/React package set. Treat an SDK major as a coordinated migration, not an ordinary package bump. Do not reverse the bot diff merely to make the check green.

### JavaScript bundlers and Workers

Run the repository's production bundle or documented dry-run deployment for runtime dependencies. Typechecking alone does not prove bundled imports exist.

### Ruby/Bundler

When `Gemfile` or `Gemfile.lock` changes, validate with Bundler even in repositories that also contain JavaScript. Use a repository-provided `bundle exec` smoke command such as listing Rake tasks or Fastlane lanes when available.

## Migration fixes

Use a branch-attached fix worktree, not the user's checkout. After the fix:

1. Run the full relevant validation set.
2. Verify the worktree contains only scoped migration changes and expected lockfile updates.
3. Commit and push only with authorization.
4. Resolve the new remote head SHA.
5. Generate new evidence for that SHA and follow the guarded merge flow.
