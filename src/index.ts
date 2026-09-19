/**
 * edge-graph-rag — M0.
 *
 * One route, `GET /health`. Retrieval lands in later milestones; the eval
 * questions are frozen in git before any of it exists.
 */

export interface Env {
	/** Chunks, nodes and edges. Local under `wrangler dev`. */
	DB: D1Database;
	/** Chunk embeddings, 768 dimensions, cosine. Remote binding. */
	VECTORS: Vectorize;
	/** Workers AI, for @cf/baai/bge-base-en-v1.5 embeddings. Remote binding. */
	AI: Ai;
}

export default {
	async fetch(request: Request): Promise<Response> {
		const { pathname } = new URL(request.url);

		if (pathname === '/health') {
			if (request.method !== 'GET') {
				return json({ error: 'method not allowed' }, 405, { allow: 'GET' });
			}
			return json({ status: 'ok', service: 'edge-graph-rag', milestone: 'M0' });
		}

		return json({ error: 'not found' }, 404);
	},
} satisfies ExportedHandler<Env>;

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json; charset=utf-8', ...headers },
	});
}
