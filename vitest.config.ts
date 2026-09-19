import { defineConfig } from 'vitest/config';
import { cloudflareTest, readD1Migrations } from '@cloudflare/vitest-pool-workers';

// Read at config time, in Node, and hand the SQL to the test worker as a
// binding — inside the Workers runtime there is no filesystem to read it from.
const migrations = await readD1Migrations('./migrations');

export default defineConfig({
	plugins: [
		cloudflareTest({
			main: './src/index.ts',
			miniflare: {
				// Matches wrangler.template.jsonc.
				compatibilityDate: '2026-08-22',
				// D1 is the only binding with a local simulator, so it is the only
				// binding the tests use. Nothing here touches the network.
				d1Databases: ['DB'],
				bindings: { TEST_MIGRATIONS: migrations },
			},
		}),
	],
	test: {
		setupFiles: ['./test/apply-migrations.ts'],
	},
});
