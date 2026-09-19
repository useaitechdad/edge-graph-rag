import { env } from 'cloudflare:test';
import { beforeEach, describe, expect, it } from 'vitest';
import worker, { type Env } from '../src/index';
import { EMBEDDING_MODEL, POOLING, DIMENSIONS } from '../src/embed';

/**
 * `/search` against the real local D1 and fakes for the two bindings that have
 * no local simulator. The fakes are deliberately dumb: they record what they
 * were asked for and return what they were told to. What is under test is the
 * route — the contract, the validation, and that the ranking Vectorize returns
 * is the ranking the caller gets, with the text and pages read from D1.
 */

const VECTOR = Array.from({ length: DIMENSIONS }, () => 0.5);

interface AiCall {
	model: string;
	input: unknown;
}

function fakeAi(calls: AiCall[], fail?: Error) {
	return {
		async run(model: string, input: any) {
			calls.push({ model, input });
			if (fail) {
				throw fail;
			}
			if (model === '@cf/baai/bge-reranker-base') {
				const contexts = (input as { contexts?: Array<{ text: string }> }).contexts ?? [];
				return {
					response: contexts.map((_, idx) => ({ id: idx, score: 0.95 - idx * 0.01 })),
				};
			}
			return { shape: [1, DIMENSIONS], data: [VECTOR], pooling: input.pooling };
		},
	} as unknown as Ai;
}

function fakeIndex(ranked: Array<{ id: string; score: number }>, options: { topK: number[] }) {
	return {
		async query(_vector: number[], queryOptions: { topK?: number }) {
			const topK = queryOptions.topK ?? 5;
			options.topK.push(topK);
			const matches = ranked.slice(0, topK);
			return { matches, count: matches.length };
		},
	} as unknown as Vectorize;
}

function envWith(ai: Ai, index: Vectorize): Env {
	return { DB: env.DB, AI: ai, VECTORS: index };
}

async function get(url: string, ai: Ai, index: Vectorize): Promise<Response> {
	return worker.fetch(new Request(url), envWith(ai, index));
}

/** Twelve chunks of one document, so a default k of 10 has something to leave out. */
const CHUNK_IDS = Array.from({ length: 12 }, (_, i) => `demo:${String(i).padStart(4, '0')}`);

// OR REPLACE, so the fixture is the same whether or not the pool rolls storage
// back between tests — the rows are identical every time either way.
beforeEach(async () => {
	await env.DB.prepare(
		'INSERT OR REPLACE INTO documents (slug, title, source_url, sha256, page_count) VALUES (?, ?, ?, ?, ?)',
	)
		.bind('demo', 'Demo report', 'https://example.com/demo.pdf', 'deadbeef', 12)
		.run();
	await env.DB.batch(
		CHUNK_IDS.map((id, ordinal) =>
			env.DB.prepare(
				'INSERT OR REPLACE INTO chunks (id, document, page_start, page_end, ordinal, text) VALUES (?, ?, ?, ?, ?, ?)',
			).bind(id, 'demo', ordinal + 1, ordinal + 2, ordinal, `passage number ${ordinal}`),
		),
	);
});

describe('GET /search', () => {
	it('returns k hits in the order Vectorize ranked them', async () => {
		const calls: AiCall[] = [];
		const seen = { topK: [] as number[] };
		const ranked = CHUNK_IDS.map((id, i) => ({ id, score: 1 - i / 100 }));

		const response = await get(
			'https://example.com/search?q=who%20chaired%20the%20board&k=3',
			fakeAi(calls),
			fakeIndex(ranked, seen),
		);

		expect(response.status).toBe(200);
		expect(seen.topK).toEqual([3]);
		expect(await response.json()).toEqual({
			query: 'who chaired the board',
			k: 3,
			retriever: 'vector',
			hits: [
				{
					chunk_id: 'demo:0000',
					document: 'demo',
					page_start: 1,
					page_end: 2,
					score: 1,
					text: 'passage number 0',
				},
				{
					chunk_id: 'demo:0001',
					document: 'demo',
					page_start: 2,
					page_end: 3,
					score: 0.99,
					text: 'passage number 1',
				},
				{
					chunk_id: 'demo:0002',
					document: 'demo',
					page_start: 3,
					page_end: 4,
					score: 0.98,
					text: 'passage number 2',
				},
			],
		});
	});

	it('embeds the query the way the chunks were embedded', async () => {
		const calls: AiCall[] = [];
		const ranked = CHUNK_IDS.map((id, i) => ({ id, score: 1 - i / 100 }));
		await get('https://example.com/search?q=units', fakeAi(calls), fakeIndex(ranked, { topK: [] }));

		// Same model and the same pooling as scripts/ingest.py. Different pooling
		// would put the question in a different space from the corpus.
		expect(calls).toEqual([
			{ model: EMBEDDING_MODEL, input: { text: ['units'], pooling: POOLING } },
		]);
	});

	it('defaults k to 10', async () => {
		const seen = { topK: [] as number[] };
		const ranked = CHUNK_IDS.map((id, i) => ({ id, score: 1 - i / 100 }));
		const response = await get(
			'https://example.com/search?q=units',
			fakeAi([]),
			fakeIndex(ranked, seen),
		);
		const body = (await response.json()) as { k: number; hits: unknown[] };
		expect(seen.topK).toEqual([10]);
		expect(body.k).toBe(10);
		expect(body.hits).toHaveLength(10);
	});

	it('returns fewer than k only when the index holds fewer', async () => {
		const ranked = CHUNK_IDS.slice(0, 4).map((id, i) => ({ id, score: 1 - i / 100 }));
		const response = await get(
			'https://example.com/search?q=units&k=10',
			fakeAi([]),
			fakeIndex(ranked, { topK: [] }),
		);
		const body = (await response.json()) as { k: number; hits: unknown[] };
		expect(body.k).toBe(10);
		expect(body.hits).toHaveLength(4);
	});

	it('drops a match with no row rather than inventing text for it', async () => {
		const ranked = [
			{ id: 'demo:0000', score: 0.9 },
			{ id: 'demo:9999', score: 0.8 }, // indexed, never written to D1
			{ id: 'demo:0001', score: 0.7 },
		];
		const response = await get(
			'https://example.com/search?q=units&k=3',
			fakeAi([]),
			fakeIndex(ranked, { topK: [] }),
		);
		const body = (await response.json()) as { hits: Array<{ chunk_id: string }> };
		expect(body.hits.map((hit) => hit.chunk_id)).toEqual(['demo:0000', 'demo:0001']);
	});

	it('answers 200 with an empty list when the index is empty', async () => {
		const response = await get(
			'https://example.com/search?q=units',
			fakeAi([]),
			fakeIndex([], { topK: [] }),
		);
		expect(response.status).toBe(200);
		expect((await response.json()) as { hits: unknown[] }).toMatchObject({ hits: [] });
	});

	it('supports graph retriever and returns k hits with retriever: graph', async () => {
		const calls: AiCall[] = [];
		const ranked = CHUNK_IDS.map((id, i) => ({ id, score: 1 - i / 100 }));

		const response = await get(
			'https://example.com/search?q=who%20chaired%20the%20board&k=3&retriever=graph',
			fakeAi(calls),
			fakeIndex(ranked, { topK: [] }),
		);

		expect(response.status).toBe(200);
		const body = (await response.json()) as {
			query: string;
			k: number;
			retriever: string;
			hits: Array<{ chunk_id: string; score: number }>;
		};
		expect(body.retriever).toBe('graph');
		expect(body.k).toBe(3);
		expect(body.hits).toHaveLength(3);
		expect(body.hits[0].chunk_id).toBe('demo:0000');
	});
});

describe('GET /search — what it refuses', () => {
	const ranked = CHUNK_IDS.map((id, i) => ({ id, score: 1 - i / 100 }));

	it('needs a question', async () => {
		for (const url of ['https://example.com/search', 'https://example.com/search?q=%20%20']) {
			const response = await get(url, fakeAi([]), fakeIndex(ranked, { topK: [] }));
			expect(response.status).toBe(400);
		}
	});

	it.each(['0', '-1', '51', '1.5', 'ten', '10abc'])('rejects k=%s', async (k) => {
		const response = await get(
			`https://example.com/search?q=units&k=${k}`,
			fakeAi([]),
			fakeIndex(ranked, { topK: [] }),
		);
		expect(response.status).toBe(400);
	});

	it.each(['1', '50'])('accepts k=%s', async (k) => {
		const response = await get(
			`https://example.com/search?q=units&k=${k}`,
			fakeAi([]),
			fakeIndex(ranked, { topK: [] }),
		);
		expect(response.status).toBe(200);
	});

	it('rejects other methods', async () => {
		const response = await worker.fetch(
			new Request('https://example.com/search?q=units', { method: 'POST' }),
			envWith(fakeAi([]), fakeIndex(ranked, { topK: [] })),
		);
		expect(response.status).toBe(405);
		expect(response.headers.get('allow')).toBe('GET');
	});

	it('reports a failing remote binding as a 502, not a 200 with no hits', async () => {
		const response = await get(
			'https://example.com/search?q=units',
			fakeAi([], new Error('AI binding unavailable')),
			fakeIndex(ranked, { topK: [] }),
		);
		expect(response.status).toBe(502);
		expect((await response.json()) as { error: string }).toMatchObject({
			error: expect.stringContaining('AI binding unavailable'),
		});
	});

	it('rejects an embedding of the wrong width', async () => {
		const narrow = {
			async run() {
				return { shape: [1, 4], data: [[0.1, 0.2, 0.3, 0.4]] };
			},
		} as unknown as Ai;
		const response = await get(
			'https://example.com/search?q=units',
			narrow,
			fakeIndex(ranked, { topK: [] }),
		);
		expect(response.status).toBe(502);
		expect((await response.json()) as { error: string }).toMatchObject({
			error: expect.stringContaining('768'),
		});
	});
});
