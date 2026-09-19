import { env } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';

async function names(type: 'table' | 'index'): Promise<string[]> {
	const { results } = await env.DB.prepare(
		"SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%' ORDER BY name",
	)
		.bind(type)
		.all<{ name: string }>();
	return results.map((row) => row.name);
}

describe('migrations/0001_init.sql applies to local D1', () => {
	it('creates every table', async () => {
		expect(await names('table')).toEqual(
			expect.arrayContaining(['chunks', 'documents', 'edges', 'node_chunks', 'nodes']),
		);
	});

	it('indexes edges in both directions, so a hop is cheap either way', async () => {
		const indexes = await names('index');
		expect(indexes).toContain('idx_edges_source');
		expect(indexes).toContain('idx_edges_target');
	});

	it('keeps a chunk of provenance on every edge', async () => {
		await env.DB.batch([
			env.DB.prepare(
				'INSERT INTO documents (slug, title, source_url, sha256, page_count) VALUES (?, ?, ?, ?, ?)',
			).bind('demo', 'Demo report', 'https://example.com/demo.pdf', 'deadbeef', 2),
			env.DB.prepare(
				'INSERT INTO chunks (id, document, page_start, page_end, ordinal, text) VALUES (?, ?, ?, ?, ?, ?)',
			).bind('c1', 'demo', 1, 1, 0, 'The navigation team used pound-seconds.'),
			env.DB.prepare('INSERT INTO nodes (id, name, type, description) VALUES (?, ?, ?, ?)').bind(
				'n1',
				'Navigation team',
				'organisation',
				null,
			),
			env.DB.prepare('INSERT INTO nodes (id, name, type, description) VALUES (?, ?, ?, ?)').bind(
				'n2',
				'Pound-seconds',
				'unit',
				null,
			),
			env.DB.prepare(
				'INSERT INTO edges (id, source, target, relation, chunk_id) VALUES (?, ?, ?, ?, ?)',
			).bind('e1', 'n1', 'n2', 'used', 'c1'),
			env.DB.prepare('INSERT INTO node_chunks (node_id, chunk_id) VALUES (?, ?)').bind('n1', 'c1'),
		]);

		const row = await env.DB.prepare(
			'SELECT c.text AS text FROM edges e JOIN chunks c ON c.id = e.chunk_id WHERE e.id = ?',
		)
			.bind('e1')
			.first<{ text: string }>();

		expect(row?.text).toBe('The navigation team used pound-seconds.');
	});
});
