export type CollectionInfo = { name: string; count: number };

export type SearchRow = {
  id: string;
  document: string;
  metadata: Record<string, any>;
  score: number;
  rank: number;
};

export type SearchResponse = {
  rows: SearchRow[];
  searchMs: number;
  window: number;
  sql: string;
};

export type ImportResponse = {
  written: number;
  total: number;
  writeMs: number;
  refreshMs: number;
  totalMs: number;
};

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body?.error || `HTTP ${res.status}`);
  return body as T;
}

export const api = {
  health: () =>
    request<{ ok: boolean; version: string; database: string; dimensions: number }>(
      "/api/health",
    ),
  collections: () => request<{ collections: CollectionInfo[] }>("/api/collections"),
  createCollection: (name: string) =>
    request<{ ok: boolean }>("/api/collections", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  deleteCollection: (name: string) =>
    request<{ ok: boolean }>(`/api/collections/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  importDocs: (collection: string, docs: unknown[]) =>
    request<ImportResponse>("/api/import", {
      method: "POST",
      body: JSON.stringify({ collection, docs }),
    }),
  search: (payload: {
    collection: string;
    queryText: string;
    queryEmbedding: number[];
    topK: number;
    where?: Record<string, unknown>;
  }) =>
    request<SearchResponse>("/api/search", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
