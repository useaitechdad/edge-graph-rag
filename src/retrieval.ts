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
