---
name: resolve-prs
description: Resolve open Dependabot PRs on GitHub repos. Assesses each PR, merges safe ones, fixes and merges fixable ones, and closes broken ones with explanations. Use --all to process every git repo in the current directory in parallel.
argument-hint: "[--all | owner/repo]"
disable-model-invocation: true
allowed-tools: Bash, Read, Edit, Write, Glob, Grep, Task, SendMessage, TeamCreate, TeamDelete, TaskCreate, TaskUpdate, TaskList
---

# Resolve Dependabot PRs

You are resolving open Dependabot dependency update PRs on GitHub repositories. Follow this methodology precisely.

## Prerequisites

- `gh` CLI authenticated with access to the target repo(s)
- The project's package manager installed (bun, npm, yarn, or pnpm)
- Write access to push fixes and merge PRs

## Arguments

- No arguments: process the current git repo (uses `origin` remote to determine owner/repo)
- `--all`: find all git repos in the current directory and process each one in parallel using a Claude team (one agent per repo)
- `owner/repo`: process a specific GitHub repo

Raw arguments: $ARGUMENTS

## Step 1: Determine Target Repos

If `--all` flag is present:
1. Run `find . -maxdepth 2 -name .git -type d` to discover repos
2. Create a Claude team with one agent per repo
3. Each agent runs the PR resolution workflow below independently
4. Coordinate results and present a unified summary

Otherwise, determine the single target repo:
- If a `owner/repo` argument is given, use that
- Otherwise, parse the current repo from `git remote get-url origin`

## Step 2: List Open PRs

```bash
gh pr list --repo OWNER/REPO --state open --json number,title,author,mergeable,mergeStateStatus,headRefName,labels,statusCheckRollup
```

Filter to Dependabot PRs (author login contains "dependabot"). If there are no open Dependabot PRs, report that and stop.

## Step 3: Assess Each PR

For each PR, get the diff:
```bash
gh pr diff NUMBER --repo OWNER/REPO
```

Categorize each PR by risk level:

### Low Risk (merge directly)
- Patch version bumps (e.g., 1.2.3 -> 1.2.5)
- Minor version bumps of dev dependencies (e.g., prettier 3.1 -> 3.2)
- Dependencies with no peer dependency constraints

### Medium Risk (test first, then merge)
- Minor version bumps of runtime dependencies
- Major bumps of dev-only tools (linters, formatters) — test lint/compile
- Any bump of dependencies used in the project's core functionality

### High Risk (likely breaking)
- Major version bumps of core dependencies (react, react-native, etc.)
- Version bumps that create mismatches with pinned peer dependencies (e.g., react-dom bumped but react stays pinned)
- Bumps incompatible with framework SDK constraints (e.g., Expo SDK pins)

## Step 4: Detect Package Manager

Before testing, detect the project's package manager:
- `bun.lock` or `bun.lockb` → bun
- `yarn.lock` → yarn
- `pnpm-lock.yaml` → pnpm
- `package-lock.json` → npm

Use the detected package manager for all install/run commands throughout.

## Step 5: Test Risky PRs Locally

For medium/high risk PRs:

1. Stash current changes: `git stash --include-untracked`
2. Fetch remote branches: `git fetch origin`
3. For each PR, checkout its package changes and test:
   ```bash
   git checkout origin/BRANCH -- package.json
   <pkg-manager> install --no-save  # or equivalent flag
   ```
4. Run the project's validation commands (check `package.json` scripts):
   - TypeScript compile check (e.g., `tsc --noEmit`, or a `compile` script)
   - Lint (e.g., `lint:check` or `lint` script)
   - Tests (e.g., `test` script)
5. Restore after each test: `git checkout main -- package.json && <pkg-manager> install`

## Step 6: Take Action

### For safe/passing PRs: MERGE
```bash
gh pr merge NUMBER --repo OWNER/REPO --merge
```

### For PRs that need fixes: FIX then MERGE
1. Checkout the PR branch: `git checkout BRANCH`
2. Install dependencies
3. Make the necessary code changes (API migrations, config updates, mock updates, etc.)
4. Commit with a clear message explaining the fix
5. Push to the PR branch
6. Merge the PR

### For broken/incompatible PRs: CLOSE with explanation
```bash
gh pr close NUMBER --repo OWNER/REPO --comment "Reason for closing..."
```

Always include a clear, specific reason:
- "Incompatible with X which requires Y"
- "Major version change requires migration of Z, not automated"
- "Version mismatch: A stays at X while B would be Y"

## Step 7: Cleanup

1. Return to the main/default branch
2. Pull merged changes
3. Restore any stashed work: `git stash pop`

## Step 8: Report

Present a summary table:

| PR | Title | Action | Reason |
|---|---|---|---|
| #N | ... | Merged / Fixed & Merged / Closed | ... |

## Common Breaking Change Patterns

Reference these when assessing PRs. This is not exhaustive — always verify by testing.

### JavaScript / TypeScript Ecosystem
- **ESLint 8 -> 9+**: Requires flat config migration (`.eslintrc.*` -> `eslint.config.js`). Check if `eslint-config-*` packages support flat config before merging.
- **Jest major bumps**: Often incompatible with framework-specific jest presets (`jest-expo`, `react-scripts`). Check the preset's peer dependencies.
- **TypeScript major bumps**: Check if all `@types/*` packages and build tools ship compatible definitions.
- **Prettier major bumps**: Usually safe but may reformat code — check if CI enforces formatting.

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
