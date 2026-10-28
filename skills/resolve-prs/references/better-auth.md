# Better Auth upgrade validation

Read for changes to a deployed Better Auth server or adapter, including patch
updates. For a client-only update, validate client APIs/storage and compatibility
with the deployed server; do not invent a database migration.

## Inspect the exact version and adapter

- Read release notes across the complete old-to-new range and inspect the
  installed adapter's runtime validation. Patch releases can change startup and
  schema contracts. Keep related Better Auth packages coordinated.
- Compare the actual configured models, plugins, field mappings, ORM schema,
  migration files, and target database schema. Respect application-specific table
  prefixes and shared databases. Never diff-push the whole shared schema.
- **Nullable values do not make a column optional.** Better Auth 1.7.3 introduced
  initialization-time validation; the 1.7.5 Drizzle adapter rejects a missing
  `account.password` declaration even with password authentication disabled.
  A check that filters `getAuthTables()` to fields with `required: true` misses
  this failure. Inspect every field expected by the configured adapter.
- Drizzle's initialization check reads the configured ORM schema, not the live
  database. Check both independently: add a missing declaration when the column
  already exists; apply a reviewed additive migration when the declaration
  exists but the column does not; repair both when both are absent.
  `account.password` is nullable text in that configuration; confirm the exact
  target adapter requires it before changing either schema. Do not enable
  password sign-in,
  reset credentials, or disable validation to make a schema error disappear.

## Exercise authentication before merging

1. Run the existing checks and production bundle, then instantiate the real
   application auth factory with its actual adapter, schema, and plugins.
2. Await an unauthenticated request to the configured `get-session` handler.
   Assert the expected success status and unauthenticated body, not merely that
   construction did not throw. Use isolated test configuration; never reuse
   production credentials or silently access production bindings.
3. Exercise affected sign-in paths against a disposable database/test setup,
   including account lookup/creation when the upgrade changes account identity.
   Cover every enabled provider when shared authentication behavior changes;
   reuse relevant integration evidence for the tested head/base rather than
   requiring new external-provider exchanges for every patch.
   The empty-session smoke catches initialization errors but does not prove a
   real Apple/Google token exchange, session write, or returning-user login.
4. For schema changes, require evidence that the schema will be ready before
   the new runtime serves traffic. Verify the target schema and migration ledger
   when already applied, or a tested deployment sequence that applies and
   verifies migrations before activating the new runtime and stops on failure.
   An unapplied migration alone does not block a safe deployment sequence.
   Apply migrations only with explicit authorization; defer when readiness or
   ordering cannot be established before a merge that automatically deploys.
   Record unavailable authentication checks and defer when affected flows remain
   unvalidated, rather than substituting unrelated unit tests or green CI.

The agent must explicitly add `--required auth-runtime` to the helper evidence
alongside the policy checks; the helper does not detect Better Auth updates or
add ecosystem requirements automatically. Record `--check auth-runtime=pass`
only after the configured-handler smoke passes; otherwise record the actual
failed, unavailable, or skipped outcome. Add `--required auth-flows` and
`--required schema-ready` when changed flows or migrations require those checks;
record their actual outcomes. Missing required evidence fails the gate.

## Verify the deployed result

When deployment is authorized (including an automatic deploy on merge), check
the real public auth `get-session` endpoint and production errors immediately
afterward. A generic `/health` route may bypass auth entirely. A successful
deployment or dry-run bundle is not evidence that authentication initialized.
Distinguish an HTTP 200 empty-session smoke from confirmed provider sign-in.
Pause further upgrades on a regression and obtain authorization for recovery.

## Version-specific account identity

- **1.7.0–1.7.2:** issuer-based account identity required issuer migration and
  reviewed backfill. This is not guidance for all 1.7 releases.
- **1.7.3:** restored `(providerId, accountId)` identity. Projects that applied
  the issuer schema must remove its unique index and relax `issuer NOT NULL`
  before deploying the restored behavior. Check duplicate provider/account
  keys. Do not reapply the older issuer backfill/index advice to newer targets.

Sources: [1.7.3 release](https://github.com/better-auth/better-auth/releases/tag/v1.7.3),
[upgrade guide](https://better-auth.com/docs/guides/1-7-upgrade-guide),
[Drizzle validation change](https://github.com/better-auth/better-auth/pull/11179).

### Tagged source evidence

For `better-auth` and `@better-auth/drizzle-adapter` 1.7.5 with default model/field
names, schema validation enabled, and password sign-in disabled:

- [Core account fields](https://github.com/better-auth/better-auth/blob/v1.7.5/packages/core/src/db/get-tables.ts#L319-L324)
  still include nullable `password`.
- [Drizzle schema inspection](https://github.com/better-auth/better-auth/blob/v1.7.5/packages/drizzle-adapter/src/schema-check.ts#L38-L46)
  passes all configured fields to the
  [schema comparison](https://github.com/better-auth/better-auth/blob/v1.7.5/packages/core/src/db/schema-diff.ts#L103-L111),
  which reports missing fields without filtering on `required`.
- The [upstream schema-check test](https://github.com/better-auth/better-auth/blob/v1.7.5/packages/drizzle-adapter/src/schema-check.test.ts#L54-L62)
  covers a missing nullable `accessToken` column. That test supports the general
  nullable-field rule; it is not a password-specific sign-in test.

These source links explain the failure mechanism. They do not replace runtime
evidence from the application and exact dependency versions being upgraded.
For the isolated handler check and its observed results, read the
[1.7.5 reproduction](better-auth-1.7.5-repro.md). It proves the schema-mismatch
behavior without claiming database, migration, or provider coverage.
