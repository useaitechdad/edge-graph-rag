import { SELF } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';

describe('GET /health', () => {
	it('answers 200 with JSON', async () => {
		const response = await SELF.fetch('https://example.com/health');
		expect(response.status).toBe(200);
		expect(response.headers.get('content-type')).toContain('application/json');
		expect(await response.json()).toEqual({
			status: 'ok',
			service: 'edge-graph-rag',
			milestone: 'M0',
		});
	});

	it('rejects other methods', async () => {
		const response = await SELF.fetch('https://example.com/health', { method: 'POST' });
		expect(response.status).toBe(405);
		expect(response.headers.get('allow')).toBe('GET');
	});

	it('404s everything else — there is nothing else yet', async () => {
		const response = await SELF.fetch('https://example.com/search');
		expect(response.status).toBe(404);
	});
});
