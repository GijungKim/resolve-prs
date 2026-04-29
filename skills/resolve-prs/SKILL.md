---
name: resolve-prs
description: Resolve open dependency-update PRs on GitHub repos (Dependabot, Renovate, pyup, plus human-authored "chore(deps)" / "build(deps)" / "bump" PRs). Assesses each PR, merges safe ones, fixes and merges fixable ones, and closes broken ones with explanations. Use --all to process every git repo in the current directory in parallel. Use --dry-run to assess without taking action.
argument-hint: "[--all] [--dry-run] [owner/repo]"
disable-model-invocation: false
allowed-tools: Bash, Read, Edit, Write, Glob, Grep, Task, SendMessage, TeamCreate, TeamDelete, TaskCreate, TaskUpdate, TaskList
---

# Resolve Dependency-Update PRs

You are resolving open dependency-update PRs on GitHub repositories. The primary case is Dependabot, but the same workflow applies to Renovate, pyup, and human-authored dep bumps that follow conventional-commit naming. Follow this methodology precisely.

## Prerequisites

- `gh` CLI authenticated with access to the target repo(s)
- The project's package manager installed (bun, npm, yarn, or pnpm)
- Write access to push fixes and merge PRs

## Arguments

- No arguments: process the current git repo (uses `origin` remote to determine owner/repo)
- `--all`: find all git repos in the current directory and process each one in parallel using a Claude team (one agent per repo)
- `--dry-run`: assess and report on all PRs without merging, fixing, or closing anything
- `owner/repo`: process a specific GitHub repo

Flags can be combined, e.g. `--all --dry-run`.

Raw arguments: $ARGUMENTS

## Step 1: Determine Target Repos

If `--all` flag is present:
1. Run `find . -maxdepth 2 -name .git -type d` to discover repos
2. Create a Claude team with one agent per repo, **capped at 8 concurrent agents**. If more repos than the cap, queue the rest and process in waves as agents finish. The cap protects against I/O contention from parallel package installs and against accidentally hammering GitHub's API limits across many repos.
3. Each agent runs the PR resolution workflow below independently and reports a one-line status when it finishes (e.g. `repo X: 3 merged, 1 fixed & merged, 1 closed, 0 failed`)
4. Coordinate results from those one-liners and present a unified summary at the end

Otherwise, determine the single target repo:
- If a `owner/repo` argument is given, use that
- Otherwise, parse the current repo from `git remote get-url origin`

## Step 2: List Open PRs

```bash
gh pr list --repo OWNER/REPO --state open --json number,title,author,mergeable,mergeStateStatus,headRefName,labels,statusCheckRollup
```

Filter to dependency-update PRs. A PR qualifies if EITHER:
- Its author login matches a known dep-bump bot: `dependabot[bot]`, `dependabot-preview[bot]`, `renovate[bot]`, `renovate-bot`, `pyup-bot`, `pre-commit-ci[bot]`. Substring matches on `dependabot`, `renovate`, or `pyup` are also fine.
- OR its title starts with a conventional dep-bump prefix: `chore(deps):`, `chore(deps-dev):`, `build(deps):`, `build(deps-dev):`, `bump `, or `Bump ` (case-insensitive). This catches human-authored PRs that were opened before the bot was configured, or one-off manual upgrades.

If no PRs match, report that and stop.

### Apply `.resolve-prs-ignore` (if present)

If a `.resolve-prs-ignore` file exists at the repo root, exclude any PR whose updated dependency matches a pattern in the file. Use this for pinned-on-purpose deps (Expo SDK pins, packages on a fork, deps awaiting a coordinated bump, etc.) so the skill stops trying to merge them every run.

**File format:** one pattern per line; blank lines and `#` comments ignored. Patterns are matched against the dependency name from the PR's title/diff. Glob support: `*` matches anything within a name segment.

```
# .resolve-prs-ignore example
expo-*              # pinned by Expo SDK; do not bump
react-native        # pinned by Expo SDK
react-native-mmkv   # we're on a fork
@auth0/auth0-react  # waiting for v3
```

**Matching:** identify each PR's dependency name from the PR title (Dependabot/Renovate convention is `bump <package> from X to Y` / `chore(deps): update <package> to Y`) or, if ambiguous, from the changed `package.json` diff lines. A PR is ignored if the dependency name matches any pattern. Skipped PRs are reported with `Action = Skipped` and `Reason = "matches .resolve-prs-ignore: <pattern>"`.

The ignore file is per-repo. With `--all`, each repo reads its own.

## Step 3: Fetch Changelogs

For each PR, before assessing risk, try to fetch release notes or changelogs:

1. From the PR body itself (Dependabot and Renovate both inline a changelog summary; manual PRs may not)
2. From GitHub releases of the dependency:
   ```bash
   gh api repos/OWNER/DEPENDENCY/releases/latest --jq '.body' 2>/dev/null
   ```
3. Look for CHANGELOG.md or MIGRATION.md in the updated package:
   ```bash
   cat node_modules/PACKAGE/CHANGELOG.md 2>/dev/null | head -100
   ```

Use this information to anticipate breaking changes before testing. If the changelog explicitly mentions breaking changes or migration steps, factor that into the risk assessment and keep the info handy for Step 7 (fixing).

## Step 4: Assess Each PR

For each PR, get the diff:
```bash
gh pr diff NUMBER --repo OWNER/REPO
```

Categorize each PR by risk level:

### Low Risk (merge if signal is green)
- Patch version bumps (e.g., 1.2.3 -> 1.2.5)
- Minor version bumps of dev dependencies (e.g., prettier 3.1 -> 3.2)
- Dependencies with no peer dependency constraints

Even low-risk PRs require a green signal before merge. Never merge purely on the version-number heuristic. Semver is aspirational (libraries break in patch versions by accident), and supply-chain attacks ride patch/minor bumps (e.g. `eslint-config-prettier 8.10.1`, `xz-utils`). Acceptable signals, in order of preference:
1. `statusCheckRollup` from Step 2 is `SUCCESS` (CI passed on the PR)
2. Local smoke test passes: at minimum a typecheck (`tsc --noEmit` or the `compile` script) plus lint
If neither signal is available (no CI configured, no compile/lint script), treat the PR as Medium Risk and follow Step 6.

### Medium Risk (test first, then merge)
- Minor version bumps of runtime dependencies
- Major bumps of dev-only tools (linters, formatters) - test lint/compile
- Any bump of dependencies used in the project's core functionality

### High Risk (likely breaking)
- Major version bumps of core dependencies (react, react-native, etc.)
- Version bumps that create mismatches with pinned peer dependencies (e.g., react-dom bumped but react stays pinned)
- Bumps incompatible with framework SDK constraints (e.g., Expo SDK pins)

Always cross-reference with the "Common Breaking Change Patterns" section below and any changelog info from Step 3.

## Step 5: Detect Package Manager

Before testing, detect the project's package manager from the **repo root**:
- `bun.lock` or `bun.lockb` -> bun
- `yarn.lock` -> yarn
- `pnpm-lock.yaml` -> pnpm
- `package-lock.json` -> npm

Use the detected package manager for all install/run commands throughout. The package manager is determined once per repo, even in monorepos; the lockfile lives at the root.

## Step 5.5: Locate the Changed package.json (monorepos)

Many repos contain more than one `package.json`: workspaces (npm/yarn/pnpm/bun workspaces, Turborepo, Nx) and "two unrelated apps in one repo" cases (e.g. `app/` + `worker/`). The wrong directory means the wrong typecheck context, so the validation in Step 6 runs from the directory the PR actually touches.

1. From the PR diff, find which `package.json` files were modified:
   ```bash
   gh pr diff NUMBER --repo OWNER/REPO --name-only | grep '/package.json$\|^package.json$'
   ```
2. Determine each affected directory (the relative path of each modified `package.json`, dropping the filename). Call this list `pkg_dirs`. For most PRs `pkg_dirs` has exactly one entry; coordinated workspace bumps may have several.
3. Detect whether the repo is a workspace monorepo:
   - Root `package.json` contains a `"workspaces"` field, OR
   - Root contains `pnpm-workspace.yaml`, `turbo.json`, or `nx.json`
4. Set `install_dir` and `validate_dirs`:
   - **Workspace monorepo:** `install_dir = "."` (root); `validate_dirs = pkg_dirs`. The workspace tool resolves all packages from the root install.
   - **Non-workspace multi-package (e.g. `app/` + `worker/` with separate lockfiles or no root lockfile):** `install_dir = pkg_dir`; `validate_dirs = [pkg_dir]`. Run install AND validation inside each affected directory.
   - **Single package.json:** both equal `"."`.

Step 6 uses `install_dir` for the install and iterates `validate_dirs` for typecheck/lint/test.

## Step 6: Validate Locally

Use a git worktree to isolate the test environment. This guarantees the install resolves against the PR's actual lockfile (not the default branch's), never leaks state into the user's working tree if validation crashes, and avoids package-manager-specific `--no-save` flags.

For low-risk PRs without a green CI signal, run a smoke test (typecheck + lint). For medium/high risk PRs, run the full validation suite (typecheck + lint + tests).

1. Resolve the default branch name once at the start of the run (don't assume `main`):
   ```bash
   default_branch=$(gh repo view --json defaultBranchRef -q .defaultBranchRef.name)
   ```
   Use `$default_branch` anywhere the workflow refers to "main".

2. Fetch remote branches: `git fetch origin`

3. Create a temporary worktree on the PR branch and run validation inside it. Wrap setup, validation, and teardown in a single bash invocation so the `trap` cleans up reliably on failure. Use `install_dir` and `validate_dirs` from Step 5.5:
   ```bash
   worktree_dir=$(mktemp -d -t resolve-prs-XXXXXX)
   trap 'git worktree remove "$worktree_dir" --force 2>/dev/null' EXIT
   git worktree add "$worktree_dir" "origin/BRANCH"

   # Install once, in the right place
   cd "$worktree_dir/$install_dir"
   <pkg-manager> install            # uses PR's lockfile - real resolution

   # Validate in each affected package
   for d in "${validate_dirs[@]}"; do
     cd "$worktree_dir/$d"
     <pkg-manager> run compile      # or `tsc --noEmit` if no compile script
     <pkg-manager> run lint:check   # or whichever lint script exists
     <pkg-manager> test             # medium/high risk only
   done
   ```

   The worktree carries the PR's `package.json` AND lockfile, so the install matches what would actually land on `$default_branch`. No flags needed; the lockfile pins versions exactly.

4. The user's main checkout is never modified. There is no per-PR restore step. Each PR gets its own fresh worktree; the `trap` removes it whether validation passes, fails, or crashes.

## Step 7: Take Action

**If `--dry-run` is set, skip this step entirely. Just report the assessment from Step 9.**

### For safe/passing PRs: MERGE

A PR is mergeable only if it has a green signal: `statusCheckRollup` is `SUCCESS`, or local validation from Step 6 passed. Never merge purely on the Step 4 risk classification.

```bash
gh pr merge NUMBER --repo OWNER/REPO --merge
```

### For PRs that need fixes: FIX then MERGE

Use a worktree for the same reasons as Step 6: the user's main checkout stays clean.

1. Create a worktree with the PR branch checked out (not detached, so you can push):
   ```bash
   worktree_dir=$(mktemp -d -t resolve-prs-fix-XXXXXX)
   git worktree add -B BRANCH "$worktree_dir" "origin/BRANCH"
   cd "$worktree_dir/$install_dir"   # install_dir from Step 5.5 (root for workspaces, subpackage for multi-package repos)
   ```
2. Install dependencies: `<pkg-manager> install`
3. Make the necessary code changes (API migrations, config updates, mock updates, etc.)
4. Use changelog/migration guide info from Step 3 to inform the fix
5. Commit with a clear message explaining the fix
6. Push to the PR branch from the worktree: `git push origin BRANCH`
7. Remove the worktree: `cd - && git worktree remove "$worktree_dir" --force`
8. Merge the PR: `gh pr merge NUMBER --repo OWNER/REPO --merge`

### For broken/incompatible PRs: CLOSE with explanation
```bash
gh pr close NUMBER --repo OWNER/REPO --comment "Reason for closing..."
```

Always include a clear, specific reason:
- "Incompatible with X which requires Y"
- "Major version change requires migration of Z, not automated"
- "Version mismatch: A stays at X while B would be Y"

**Stop the bot from reopening it next week.** `gh pr close` does not stop Dependabot or Renovate from re-creating the same PR on the next scheduled run. When closing, append a config snippet to the close comment so the user can paste it into their bot config:

For Dependabot (append to `.github/dependabot.yml` under the matching `updates:` entry):
```yaml
ignore:
  - dependency-name: "PACKAGE"
    versions: ["X.Y.Z"]   # or use update-types: ["version-update:semver-major"]
```

For Renovate (append to `renovate.json`):
```json
"packageRules": [
  { "matchPackageNames": ["PACKAGE"], "matchUpdateTypes": ["major"], "enabled": false }
]
```

If the package is one the user already pins on purpose, also suggest they add it to `.resolve-prs-ignore` so this skill skips it on every future run too.

## Step 8: Auto-Learn (after fixing novel breaking changes)

When you successfully fix a breaking change that is NOT already listed in the "Common Breaking Change Patterns" section below, add it. Auto-learned patterns can over-fit to one project or grow stale across versions, so the format below tracks provenance and freshness. Verify a pattern before trusting it blindly.

### Rules for auto-learn

1. **Only record generalizable patterns.** The entry should help with ANY project that hits this upgrade, not just the current one. Example of good: "react-native-mmkv 3 -> 4: `new MMKV()` -> `createMMKV()`". Example of bad: "fixed import in src/utils/storage.ts".

2. **One line per pattern, with dates.** Format: `**package X -> Y** (learned YYYY-MM, last verified YYYY-MM): brief description of what changed and how to fix it.` Both dates equal the current month when first written. Hand-curated patterns (the ones already in this file without dates) are trusted as-is and don't need backfilling.

3. **Refresh the verified date on reuse.** When an existing pattern correctly applies to a new PR (its suggested fix worked), update its `last verified` date to the current month before merging. This keeps useful patterns fresh and lets unused ones age out visually.

4. **Treat patterns older than 6 months as hints, not rules.** Before applying any auto-learned pattern whose `last verified` date is >6 months old, re-verify by testing. Don't trust it blindly. If it still applies, refresh the date per rule 3. If it doesn't, fix or remove the pattern.

5. **Cap at 30 entries per subsection.** If a subsection hits 30, evict the entry with the oldest `last verified` date (least-recently-useful), not the oldest `learned` date. Patterns that keep proving useful stay; patterns nobody hits get culled.

6. **Don't duplicate.** If a similar pattern already exists, update it (refresh the dates and merge the description) rather than adding a new one.

7. **Where to write.** Edit the SKILL.md file directly in the skill's directory. The file location depends on where the skill is installed:
   - Personal: `~/.claude/skills/resolve-prs/SKILL.md`
   - Project: `.claude/skills/resolve-prs/SKILL.md`
   - Plugin: find via `ls ~/.claude/plugins/*/skills/resolve-prs/SKILL.md`

   Use the Edit tool to append to the appropriate subsection under "Common Breaking Change Patterns".

## Step 9: Cleanup

The worktree-based approach in Steps 6 and 7 means the user's working tree was never touched, so there's no stash to restore and no branch to switch back from.

1. Verify no leftover worktrees from crashed invocations:
   ```bash
   git worktree list | grep -E 'resolve-prs-(fix-)?[A-Za-z0-9]+' && \
     git worktree list | awk '/resolve-prs-/ {print $1}' | xargs -I {} git worktree remove --force {}
   ```
2. Pull merged changes on the user's current default branch checkout: `git pull origin "$default_branch"` (only if the user is on `$default_branch`; otherwise skip, and don't switch branches on their behalf).

## Step 10: Report

Present a summary table:

| PR | Title | Risk | Action | Reason |
|---|---|---|---|---|
| #N | ... | Low/Medium/High | Merged / Fixed & Merged / Closed / Skipped / Would merge (dry-run) / Would close (dry-run) | ... |

If `--dry-run`, use "Would merge", "Would close", "Would fix & merge" in the Action column. PRs filtered out by `.resolve-prs-ignore` use `Skipped` (the action would not have been taken anyway, so `--dry-run` doesn't change it).

If any new patterns were learned, mention them at the bottom:
> Learned N new breaking change pattern(s) - the skill will handle these automatically next time.

## Common Breaking Change Patterns

Reference these when assessing PRs. This is not exhaustive - always verify by testing. New patterns are added automatically via auto-learn (Step 8).

### JavaScript / TypeScript Ecosystem
- **ESLint 8 -> 9+**: Requires flat config migration (`.eslintrc.*` -> `eslint.config.js`). Check if `eslint-config-*` packages support flat config before merging.
- **ESLint 9 -> 10**: Removes `FlatESLint`/`LegacyESLint` exports, breaking `typescript-eslint` <8.56.0 and `@eslint/js` <10. Bump `@eslint/js` to ^10.0.0 and `typescript-eslint` to ^8.56.0+. Note: `eslint-plugin-react-hooks` may lack ESLint 10 peer dep but works anyway.
- **eslint-plugin-react-refresh 0.4 -> 0.5**: Ships ESM-only, changes default export to named `{ reactRefresh }`, and `configs.vite` becomes `configs.vite()` (function call). Also `customHOCs` renamed to `extraHOCs`.
- **Jest major bumps**: Often incompatible with framework-specific jest presets (`jest-expo`, `react-scripts`). Check the preset's peer dependencies.
- **TypeScript major bumps**: Check if all `@types/*` packages and build tools ship compatible definitions.
- **TypeScript 5.x -> 6.x**: `baseUrl` and `moduleResolution: "node"` are deprecated (error by default). Fix: remove `baseUrl` (default is `.`), change `moduleResolution` to `"bundler"`. Also, TS 6 no longer auto-includes all `@types/*` from `typeRoots`. Add an explicit `"types"` array listing needed type packages (e.g., `["react", "jest", "node"]`).
- **Prettier major bumps**: Usually safe but may reformat code - check if CI enforces formatting.

### React / React Native
- **react-dom without react**: Must always match the `react` version exactly.
- **react-native-mmkv 3 -> 4**: Constructor changed from `new MMKV()` to `createMMKV()`, `.delete()` renamed to `.remove()`, requires Nitro Modules jest mock.
- **Expo SDK pins**: Many dependencies are pinned by Expo SDK version. Verify with `npx expo install --fix`. Don't bump packages that Expo constrains.
- **React Navigation major bumps**: Often requires simultaneous updates of all `@react-navigation/*` packages.

### Python
- **Django major bumps**: Check middleware, URL config, and deprecated feature removals.
- **SQLAlchemy 1.x -> 2.x**: Query API completely changed.

### General
- **Peer dependency mismatches**: If package A requires `B@^2.0` but the PR bumps B to 3.0, close it.
- **Monorepo grouped updates**: If one package in the group is breaking, the whole PR fails. Consider asking Dependabot to split it.
