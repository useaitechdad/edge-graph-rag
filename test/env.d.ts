/// <reference types="@cloudflare/vitest-pool-workers/types" />

import type { D1Migration } from '@cloudflare/vitest-pool-workers';
import type { Env as AppEnv } from '../src/index';

declare global {
	namespace Cloudflare {
		interface Env extends AppEnv {
			/** migrations/, read in Node by vitest.config.ts and passed in as a binding. */
			TEST_MIGRATIONS: D1Migration[];
		}
	}
}
