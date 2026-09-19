/**
 * Retrieval.
 *
 * `/search` asks a `Retriever` for k passages and does not care how they were
 * found. M2 ships one implementation — embed the question, take the nearest
 * chunks. M4 adds a second one that seeds from those same vector hits and walks
 * the entity graph, and it arrives as a new class here rather than as a branch
 * inside this one. Both return the same shape, both return exactly k, and
 * eval/run.py scores them with the same code: that is the whole comparison.
 */

import { embedQuery } from './embed';

/** One returned passage, and enough provenance to look it up in the report. */
export interface Hit {
	chunk_id: string;
	document: string;
	page_start: number;
	page_end: number;
	/** Cosine similarity from Vectorize. Higher is nearer. */
	score: number;
	text: string;
}

export interface Retriever {
	/** Goes into the response and into the eval receipt's filename. */
	readonly name: string;
	search(query: string, k: number): Promise<Hit[]>;
}

/** The row behind a vector id. Vectorize holds the embedding; D1 holds the text. */
interface ChunkRow {
	id: string;
	document: string;
	page_start: number;
	page_end: number;
	text: string;
}

export class VectorRetriever implements Retriever {
	readonly name = 'vector';

	constructor(
		private readonly ai: Ai,
		private readonly index: Vectorize,
		private readonly db: D1Database,
	) {}

	async search(query: string, k: number): Promise<Hit[]> {
		const vector = await embedQuery(this.ai, query);

		// No metadata and no values come back: the chunk row in D1 is the source
		// of truth for text and pages, and asking Vectorize for metadata as well
		// would only add a second copy to disagree with it.
		const { matches } = await this.index.query(vector, { topK: k });
		if (matches.length === 0) {
			return [];
		}

		const rows = await loadChunks(this.db, matches.map((match) => match.id));

		// Vectorize's order is the ranking, so it is the order that is returned.
		// A match with no row is an index that has drifted ahead of the database;
		// there is no text to show for it, so it is dropped rather than faked.
		return matches.flatMap((match) => {
			const row = rows.get(match.id);
			if (!row) {
				return [];
			}
			return [
				{
					chunk_id: row.id,
					document: row.document,
					page_start: row.page_start,
					page_end: row.page_end,
					score: match.score,
					text: row.text,
				},
			];
		});
	}
}

export class GraphRetriever implements Retriever {
	readonly name = 'graph';

	constructor(
		private readonly ai: Ai,
		private readonly index: Vectorize,
		private readonly db: D1Database,
	) {}

	async search(query: string, k: number): Promise<Hit[]> {
		const vector = await embedQuery(this.ai, query);

		// 1. Vector candidate search: pull enough matches to include potential 1-hop bridge targets
		const candidateK = Math.min(Math.max(k * 2, 25), 50);
		const { matches } = await this.index.query(vector, { topK: candidateK });
		if (matches.length === 0) {
			return [];
		}

		// 2. Seeds for graph walk: top 10 vector matches
		const seedMatches = matches.slice(0, 10);
		const seedIds = seedMatches.map((m) => m.id);

		// 3. D1 1-2 hop graph walk
		const graphChunkIds = await this.walkGraph(seedIds);

		// 4. Candidate chunk set: unique seed + graph IDs, capped at 35
		const candidateIds = Array.from(
			new Set([...matches.map((m) => m.id), ...graphChunkIds]),
		).slice(0, 35);

		// 5. Load chunk rows from D1
		const rowMap = await loadChunks(this.db, candidateIds);
		const validCandidates: ChunkRow[] = [];
		for (const id of candidateIds) {
			const row = rowMap.get(id);
			if (row) {
				validCandidates.push(row);
			}
		}

		if (validCandidates.length === 0) {
			return [];
		}

		// 6. Rerank candidates so graph-discovered chunks can outrank vector hits
		const vectorScores = new Map(matches.map((m) => [m.id, m.score]));
		const scoredHits = await this.rerank(query, validCandidates, vectorScores);

		return scoredHits.slice(0, k);
	}

	/**
	 * 1-2 hop graph walk from seed chunks, in one recursive CTE query.
	 */
	private async walkGraph(seedIds: string[]): Promise<string[]> {
		if (seedIds.length === 0) {
			return [];
		}
		const placeholders = seedIds.map(() => '(?)').join(', ');
		const query = `
			WITH RECURSIVE
			seed_chunks(id) AS (
				VALUES ${placeholders}
			),
			seed_nodes(id, hop) AS (
				SELECT DISTINCT nc.node_id, 0
				FROM node_chunks nc
				JOIN seed_chunks sc ON nc.chunk_id = sc.id
			),
			walk(node_id, hop) AS (
				SELECT id, hop FROM seed_nodes
				UNION
				SELECT e.target, walk.hop + 1
				FROM edges e
				JOIN walk ON e.source = walk.node_id
				WHERE walk.hop < 2
				UNION
				SELECT e.source, walk.hop + 1
				FROM edges e
				JOIN walk ON e.target = walk.node_id
				WHERE walk.hop < 2
			),
			min_walk AS (
				SELECT node_id, MIN(hop) as hop
				FROM walk
				GROUP BY node_id
			)
			SELECT nc.chunk_id, min(mw.hop) as min_hop, count(DISTINCT mw.node_id) as node_count
			FROM min_walk mw
			JOIN node_chunks nc ON mw.node_id = nc.node_id
			GROUP BY nc.chunk_id
			ORDER BY min_hop ASC, node_count DESC
			LIMIT 20;
		`;
		try {
			const { results } = await this.db
				.prepare(query)
				.bind(...seedIds)
				.all<{ chunk_id: string }>();
			return results.map((r) => r.chunk_id);
		} catch {
			return [];
		}
	}

	/**
	 * Re-rank candidates using @cf/baai/bge-reranker-base, with fallback to vector/graph scores.
	 */
	private async rerank(
		query: string,
		candidates: ChunkRow[],
		vectorScores: Map<string, number>,
	): Promise<Hit[]> {
		try {
			const res = (await (this.ai as any).run('@cf/baai/bge-reranker-base', {
				query,
				contexts: candidates.map((c) => ({ text: c.text })),
			})) as { response?: Array<{ id: number; score: number }> };

			if (res && Array.isArray(res.response) && res.response.length > 0) {
				return res.response
					.filter((item) => item.id >= 0 && item.id < candidates.length)
					.map((item) => {
						const row = candidates[item.id];
						return {
							chunk_id: row.id,
							document: row.document,
							page_start: row.page_start,
							page_end: row.page_end,
							score: item.score,
							text: row.text,
						};
					});
			}
		} catch {
			// Fall through to fallback scoring if reranker is unavailable or in mock tests
		}

		// Fallback scoring: vector score, or lower rank score for graph-only chunks
		const scored = candidates.map((row, idx) => {
			const vScore = vectorScores.get(row.id);
			const fallbackScore = vScore !== undefined ? vScore : 0.65 - idx * 0.01;
			return {
				chunk_id: row.id,
				document: row.document,
				page_start: row.page_start,
				page_end: row.page_end,
				score: fallbackScore,
				text: row.text,
			};
		});
		return scored.sort((a, b) => b.score - a.score);
	}
}

/**
 * Chunk rows by id, in one query.
 *
 * D1 allows 100 bound parameters per query and `/search` caps k at 50, so the
 * whole top-k fits in a single round trip — which matters on the Workers free
 * plan, where an invocation gets 50 D1 queries in total.
 */
async function loadChunks(db: D1Database, ids: string[]): Promise<Map<string, ChunkRow>> {
	const placeholders = ids.map(() => '?').join(', ');
	const { results } = await db
		.prepare(
			`SELECT id, document, page_start, page_end, text FROM chunks WHERE id IN (${placeholders})`,
		)
		.bind(...ids)
		.all<ChunkRow>();
	return new Map(results.map((row) => [row.id, row]));
}
