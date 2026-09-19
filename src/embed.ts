/**
 * One embedding model, one pooling method, one place that says so.
 *
 * `scripts/ingest.py` embeds the chunks with `@cf/baai/bge-base-en-v1.5` and
 * `pooling: "cls"`. A query embedded any other way lands in a different vector
 * space and the scores become noise that still looks like scores — so the query
 * side has exactly one implementation, here, and both retrievers call it.
 */

export const EMBEDDING_MODEL = '@cf/baai/bge-base-en-v1.5';
export const POOLING = 'cls';
/** What the model emits, and what the Vectorize index was created with. */
export const DIMENSIONS = 768;

/** Embed one string. Throws if the model returns something unusable. */
export async function embedQuery(ai: Ai, text: string): Promise<number[]> {
	const output = await ai.run(EMBEDDING_MODEL, { text: [text], pooling: POOLING });

	const vector = 'data' in output ? output.data?.[0] : undefined;
	if (!vector) {
		throw new Error('the embedding model returned no vector');
	}
	if (vector.length !== DIMENSIONS) {
		throw new Error(`expected ${DIMENSIONS} dimensions, got ${vector.length}`);
	}
	return vector;
}
