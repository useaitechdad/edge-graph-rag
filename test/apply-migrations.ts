import { applyD1Migrations, env } from 'cloudflare:test';

// Runs once per test file, before the per-test storage isolation starts, so
// every test sees the migrated schema and an empty database.
await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);
