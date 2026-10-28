# Better Auth 1.7.5 nullable-field reproduction

Reproduced on 2026-10-28 with Node 24.22.1. This isolates Drizzle's local schema
validation with default model/field names, validation enabled, and password
sign-in disabled. It does not connect to a database or test provider sign-in,
SQL types, migrations, or live database readiness. The synthetic tables use
text columns because this check compares field presence, not SQL types.

In an empty temporary directory, install the coordinated versions without
package lifecycle scripts:

```sh
npm install --ignore-scripts --no-audit --no-fund --save-exact \
  better-auth@1.7.5 @better-auth/core@1.7.5 \
  @better-auth/drizzle-adapter@1.7.5 @better-auth/utils@0.4.2 \
  drizzle-orm@0.45.2
```

Run this with `node --input-type=module` on stdin:

```js
import assert from 'node:assert/strict';
import { betterAuth } from 'better-auth';
import { getAuthTables } from 'better-auth/db';
import { drizzleAdapter } from '@better-auth/drizzle-adapter';
import { pgTable, text } from 'drizzle-orm/pg-core';

const options = { emailAndPassword: { enabled: false } };
const tables = getAuthTables(options);
assert.equal(tables.account.fields.password.required, false);
for (const omitPassword of [false, true]) {
  const schema = Object.fromEntries(Object.entries(tables).map(([model, table]) => {
    const columns = { id: text('id').primaryKey() };
    for (const [field, definition] of Object.entries(table.fields)) {
      if (omitPassword && model === 'account' && field === 'password') continue;
      const column = text(field);
      columns[field] = definition.required ? column.notNull() : column;
    }
    return [model, pgTable(model, columns)];
  }));
  const auth = betterAuth({
    ...options,
    secret: 'isolated-public-reproduction-secret-with-no-real-credentials',
    baseURL: 'http://localhost:3000',
    logger: { disabled: true },
    database: drizzleAdapter({ _: { fullSchema: schema } }, { provider: 'pg', schema }),
  });
  const request = new Request('http://localhost:3000/api/auth/get-session');
  if (omitPassword) {
    await assert.rejects(auth.handler(request), (error) =>
      error.code === 'SCHEMA_MISMATCH' && error.findings.some((finding) =>
        finding.kind === 'missing-column' && finding.table === 'account' && finding.column === 'password'));
    console.log('missing password: configured handler rejects with account.password schema mismatch');
  } else {
    const response = await auth.handler(request);
    assert.equal(response.status, 200);
    assert.equal(await response.text(), 'null');
    console.log('complete schema: configured handler returns HTTP 200 null');
  }
}
```

Observed output:

```text
complete schema: configured handler returns HTTP 200 null
missing password: configured handler rejects with account.password schema mismatch
```

The direct handler rejects rather than returning an HTTP response for the
schema mismatch. How a deployed server translates that rejection is specific
to its integration; this reproduction does not assert a particular HTTP error
status or prove the behavior of other releases/adapters.
