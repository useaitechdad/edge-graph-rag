/**
 * edge-graph-rag — M2.
 *
 * Two routes. `GET /health`, and `GET /search?q=...&k=10`, which is the vector
 * arm of the comparison: embed the question, take the nearest chunks, return
 * them with the page they came from. The graph arm lands in M4 as a second
 * `Retriever` beside this one, not as a rewrite of it.
 */

import { VectorRetriever, type Retriever } from './retrieval';

export interface Env {
	/** Chunks, nodes and edges. Local under `wrangler dev`. */
	DB: D1Database;
	/** Chunk embeddings, 768 dimensions, cosine. Remote binding. */
	VECTORS: Vectorize;
	/** Workers AI, for @cf/baai/bge-base-en-v1.5 embeddings. Remote binding. */
	AI: Ai;
}

/** Vectorize returns at most 50 matches when values or metadata come with them,
 * and D1 takes 100 bound parameters per query. 50 is under both. */
const MAX_K = 50;
const DEFAULT_K = 10;

export default {
	async fetch(request: Request, env: Env): Promise<Response> {
		const url = new URL(request.url);

		if (url.pathname === '/health') {
			if (request.method !== 'GET') {
				return json({ error: 'method not allowed' }, 405, { allow: 'GET' });
			}
			return json({ status: 'ok', service: 'edge-graph-rag', milestone: 'M2' });
		}

		if (url.pathname === '/search') {
			if (request.method !== 'GET') {
				return json({ error: 'method not allowed' }, 405, { allow: 'GET' });
			}
			return search(url, new VectorRetriever(env.AI, env.VECTORS, env.DB));
		}

		return json({ error: 'not found' }, 404);
	},
} satisfies ExportedHandler<Env>;

async function search(url: URL, retriever: Retriever): Promise<Response> {
	const query = (url.searchParams.get('q') ?? '').trim();
	if (!query) {
		return json({ error: "missing 'q'" }, 400);
	}

	const k = parseK(url.searchParams.get('k'));
	if (k === null) {
		return json({ error: `'k' must be a whole number between 1 and ${MAX_K}` }, 400);
	}

	try {
		const hits = await retriever.search(query, k);
		return json({ query, k, retriever: retriever.name, hits });
	} catch (error) {
		// The bindings that can fail here are remote, so this is usually a real
		// outage or a missing credential rather than a bug in the query.
		return json({ error: `retrieval failed: ${(error as Error).message}` }, 502);
	}
}

/** The requested k, or null if it was asked for and is not usable. */
function parseK(raw: string | null): number | null {
	if (raw === null || raw === '') {
		return DEFAULT_K;
	}
	// Number() rather than parseInt(): "10abc" is a mistake, not a 10.
	const value = Number(raw);
	if (!Number.isInteger(value) || value < 1 || value > MAX_K) {
		return null;
	}
	return value;
}

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json; charset=utf-8', ...headers },
	});
}
