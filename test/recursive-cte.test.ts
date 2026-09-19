import { env } from 'cloudflare:test';
import { describe, expect, it } from 'vitest';

/**
 * Capability probe, not retrieval code.
 *
 * The graph walk in M4 has to reach nodes two hops out. On the Workers Free
 * plan an invocation gets 50 D1 queries, so "one query per node" is a dead end
 * and the walk wants to be set-based. `WITH RECURSIVE` would be the neatest way
 * to write it — but Cloudflare's D1 documentation does not say whether it is
 * supported, so this asserts what the local D1 simulator actually does rather
 * than what anyone remembers.
 *
 * Result, run here against the local simulator (D1 local is SQLite inside
 * workerd): WITH RECURSIVE works, and a bounded two-hop walk comes back in a
 * single query. Two caveats stay open until the M2 milestone puts a real
 * database in reach: this is the local simulator, not D1 in production, and
 * a recursive CTE still counts as one query but not as one unit of work — the
 * 30 s per-query and 10 ms CPU limits are the ones to watch when the graph is
 * real rather than five rows.
 */
describe('WITH RECURSIVE on local D1', () => {
	it('runs, and walks a chain two hops out in one query', async () => {
		// a -> b -> c -> d, plus an unrelated pair, all hung off one chunk.
		await env.DB.batch([
			env.DB.prepare(
				'INSERT INTO documents (slug, title, source_url, sha256, page_count) VALUES (?, ?, ?, ?, ?)',
			).bind('demo', 'Demo report', 'https://example.com/demo.pdf', 'deadbeef', 1),
			env.DB.prepare(
				'INSERT INTO chunks (id, document, page_start, page_end, ordinal, text) VALUES (?, ?, ?, ?, ?, ?)',
			).bind('c1', 'demo', 1, 1, 0, 'chain'),
			env.DB.prepare("INSERT INTO nodes (id, name, type) VALUES ('a', 'A', 'thing')"),
			env.DB.prepare("INSERT INTO nodes (id, name, type) VALUES ('b', 'B', 'thing')"),
			env.DB.prepare("INSERT INTO nodes (id, name, type) VALUES ('c', 'C', 'thing')"),
			env.DB.prepare("INSERT INTO nodes (id, name, type) VALUES ('d', 'D', 'thing')"),
			env.DB.prepare("INSERT INTO nodes (id, name, type) VALUES ('z', 'Z', 'thing')"),
			env.DB.prepare(
				"INSERT INTO edges (id, source, target, relation, chunk_id) VALUES ('e1', 'a', 'b', 'links', 'c1')",
			),
			env.DB.prepare(
				"INSERT INTO edges (id, source, target, relation, chunk_id) VALUES ('e2', 'b', 'c', 'links', 'c1')",
			),
			env.DB.prepare(
				"INSERT INTO edges (id, source, target, relation, chunk_id) VALUES ('e3', 'c', 'd', 'links', 'c1')",
			),
			env.DB.prepare(
				"INSERT INTO edges (id, source, target, relation, chunk_id) VALUES ('e4', 'z', 'z', 'links', 'c1')",
			),
		]);

		const { results } = await env.DB.prepare(
			`WITH RECURSIVE walk(node_id, hop) AS (
			   SELECT ?1, 0
			   UNION
			   SELECT CASE WHEN e.source = walk.node_id THEN e.target ELSE e.source END, walk.hop + 1
			   FROM edges e
			   JOIN walk ON e.source = walk.node_id OR e.target = walk.node_id
			   WHERE walk.hop < ?2
			 )
			 SELECT node_id, MIN(hop) AS hop FROM walk GROUP BY node_id ORDER BY hop, node_id`,
		)
			.bind('a', 2)
			.all<{ node_id: string; hop: number }>();

		expect(results).toEqual([
			{ node_id: 'a', hop: 0 },
			{ node_id: 'b', hop: 1 },
			{ node_id: 'c', hop: 2 },
		]);
	});
});
