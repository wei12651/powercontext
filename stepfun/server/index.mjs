import express from "express";
import mysql from "mysql2/promise";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";
import {
  SeekdbClient,
  Schema,
  FulltextIndexConfig,
  VectorIndexConfig,
} from "seekdb";

const DIM = 512;
const PORT = Number(process.env.PORT || 8787);
const SEEKDB = {
  host: process.env.SEEKDB_HOST || "127.0.0.1",
  port: Number(process.env.SEEKDB_PORT || 2881),
  user: process.env.SEEKDB_USER || "root",
  password: process.env.SEEKDB_PASSWORD || "",
  database: process.env.SEEKDB_DATABASE || "stepfun_kb",
};
const RRF_WINDOW = 40;

if (!/^[A-Za-z0-9_]+$/.test(SEEKDB.database)) {
  throw new Error("SEEKDB_DATABASE 只允许字母、数字和下划线");
}

const app = express();
app.use(express.json({ limit: "32mb" }));

let client;
let pool;
let serverVersion = "";

function collectionName(raw) {
  const name = String(raw || "").trim();
  if (!/^[a-z0-9][a-z0-9_-]{1,40}$/.test(name)) {
    throw new Error("集合名只允许小写字母、数字、下划线和短横线（2-41 位）");
  }
  return name;
}

async function withCollection(name) {
  const clean = collectionName(name);
  if (!(await client.hasCollection(clean))) {
    throw new Error(`集合 ${clean} 不存在`);
  }
  return client.getCollection({ name: clean, embeddingFunction: null });
}

function sanitizeQuery(text) {
  return String(text || "")
    .replace(/[+\-&|!(){}[\]^"~*?:\\/]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 200);
}

function checkEmbedding(vec) {
  if (!Array.isArray(vec) || vec.length !== DIM) {
    throw new Error(`向量维度必须是 ${DIM}，收到 ${Array.isArray(vec) ? vec.length : typeof vec}`);
  }
  return vec.map((x) => {
    const n = Number(x);
    if (!Number.isFinite(n)) throw new Error("向量包含非法数值");
    return n;
  });
}

function collectionSchema() {
  return new Schema({
    fulltextIndex: new FulltextIndexConfig("ik", { ik_mode: "smart" }),
    vectorIndex: new VectorIndexConfig({
      hnsw: { dimension: DIM, distance: "cosine" },
      embeddingFunction: null,
    }),
  });
}

async function tableNameOf(name) {
  const [rows] = await pool.query(
    "SELECT collection_id FROM sdk_collections WHERE collection_name = ?",
    [name],
  );
  if (!rows.length) throw new Error(`集合 ${name} 尚未在 SDK 中注册`);
  return `c$v2$${rows[0].collection_id}`;
}

app.get("/api/health", async (_req, res) => {
  try {
    const collections = await client.listCollections({ withEmbeddingFunction: false });
    res.json({
      ok: true,
      version: serverVersion,
      database: SEEKDB.database,
      dimensions: DIM,
      collections: collections.map((c) => c.name),
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/api/collections", async (_req, res) => {
  try {
    const list = await client.listCollections({ withEmbeddingFunction: false });
    const items = [];
    for (const meta of list) {
      const col = await client.getCollection({ name: meta.name, embeddingFunction: null });
      items.push({
        name: meta.name,
        count: await col.count(),
        createdAt: meta.createdAt ?? null,
      });
    }
    items.sort((a, b) => a.name.localeCompare(b.name));
    res.json({ collections: items });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post("/api/collections", async (req, res) => {
  try {
    const name = collectionName(req.body?.name);
    if (await client.hasCollection(name)) {
      return res.status(409).json({ error: `集合 ${name} 已存在` });
    }
    await client.createCollection({ name, schema: collectionSchema() });
    res.json({ ok: true, name });
  } catch (err) {
    res.status(400).json({ error: err.message });
  }
});

app.delete("/api/collections/:name", async (req, res) => {
  try {
    const name = collectionName(req.params.name);
    await client.deleteCollection(name);
    res.json({ ok: true });
  } catch (err) {
    res.status(400).json({ error: err.message });
  }
});

app.post("/api/import", async (req, res) => {
  const started = performance.now();
  try {
    const { collection, docs } = req.body || {};
    if (!Array.isArray(docs) || docs.length === 0) {
      throw new Error("docs 不能为空");
    }
    if (docs.length > 512) throw new Error("单次最多写入 512 个片段");
    const ids = [];
    const documents = [];
    const metadatas = [];
    const embeddings = [];
    for (const doc of docs) {
      if (!doc?.id || typeof doc.document !== "string" || !doc.document.trim()) {
        throw new Error("每个片段都需要 id 与 document");
      }
      ids.push(String(doc.id));
      documents.push(doc.document);
      metadatas.push(doc.metadata && typeof doc.metadata === "object" ? doc.metadata : {});
      embeddings.push(checkEmbedding(doc.embedding));
    }

    const col = await withCollection(collection);
    const writeStart = performance.now();
    await col.upsert({ ids, documents, metadatas, embeddings });
    const writeMs = performance.now() - writeStart;

    const refreshStart = performance.now();
    await col.refresh_index();
    const refreshMs = performance.now() - refreshStart;

    res.json({
      ok: true,
      written: ids.length,
      total: await col.count(),
      writeMs: Math.round(writeMs * 10) / 10,
      refreshMs: Math.round(refreshMs * 10) / 10,
      totalMs: Math.round((performance.now() - started) * 10) / 10,
    });
  } catch (err) {
    res.status(400).json({ error: err.message });
  }
});

app.post("/api/search", async (req, res) => {
  try {
    const { collection, queryText, queryEmbedding, topK = 8, where } = req.body || {};
    const q = sanitizeQuery(queryText);
    if (!q) throw new Error("检索词不能为空");
    const embedding = checkEmbedding(queryEmbedding);
    const k = Math.max(1, Math.min(Number(topK) || 8, 30));
    const filters = where && typeof where === "object" ? where : undefined;
    const col = await withCollection(collection);

    const started = performance.now();
    // 全文与向量两路召回、RRF 融合都由 seekdb 执行：
    // SDK 调用 DBMS_HYBRID_SEARCH.GET_SQL 生成带 RANK() + FULL JOIN 的单条 SQL。
    const result = await col.hybridSearch({
      query: {
        whereDocument: { $contains: q },
        ...(filters ? { where: filters } : {}),
        nResults: RRF_WINDOW,
      },
      knn: {
        queryEmbeddings: [embedding],
        ...(filters ? { where: filters } : {}),
        nResults: RRF_WINDOW,
      },
      rank: { rrf: {} },
      nResults: k,
    });
    const searchMs = performance.now() - started;

    const ids = result.ids?.[0] || [];
    const docs = result.documents?.[0] || [];
    const metas = result.metadatas?.[0] || [];
    const scores = result.distances?.[0] || [];
    const rows = ids.map((id, i) => ({
      id,
      document: docs[i] || "",
      metadata: metas[i] || {},
      score: Number(scores[i] || 0),
      rank: i + 1,
    }));

    let sql = "";
    try {
      const table = await tableNameOf(collectionName(collection));
      const parm = {
        query: { query_string: { fields: ["document"], query: q } },
        knn: { field: "embedding", k: RRF_WINDOW, query_vector: embedding },
        rank: { rrf: {} },
        size: k,
      };
      await pool.query("SET @search_parm = ?", [JSON.stringify(parm)]);
      const [sqlRows] = await pool.query(
        "SELECT DBMS_HYBRID_SEARCH.GET_SQL(?, @search_parm) AS q",
        [table],
      );
      sql = String(sqlRows[0]?.q || "");
    } catch {
      sql = "";
    }

    res.json({
      ok: true,
      rows,
      searchMs: Math.round(searchMs * 10) / 10,
      window: RRF_WINDOW,
      sql,
    });
  } catch (err) {
    res.status(400).json({ error: err.message });
  }
});

const here = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(here, "../dist");
if (fs.existsSync(distDir)) {
  app.use(express.static(distDir));
  app.get(/^\/(?!api\/).*/, (_req, res) => {
    res.sendFile(path.join(distDir, "index.html"));
  });
}

async function main() {
  const bootstrap = await mysql.createConnection({
    host: SEEKDB.host,
    port: SEEKDB.port,
    user: SEEKDB.user,
    password: SEEKDB.password,
    charset: "utf8mb4",
  });
  await bootstrap.query(
    `CREATE DATABASE IF NOT EXISTS \`${SEEKDB.database}\` DEFAULT CHARSET utf8mb4`,
  );
  const [verRows] = await bootstrap.query("SELECT VERSION() AS v");
  serverVersion = verRows[0].v;
  await bootstrap.end();

  pool = mysql.createPool({
    host: SEEKDB.host,
    port: SEEKDB.port,
    user: SEEKDB.user,
    password: SEEKDB.password,
    database: SEEKDB.database,
    charset: "utf8mb4",
    connectionLimit: 4,
  });

  client = new SeekdbClient({
    host: SEEKDB.host,
    port: SEEKDB.port,
    user: SEEKDB.user,
    password: SEEKDB.password,
    database: SEEKDB.database,
  });

  app.listen(PORT, "127.0.0.1", () => {
    console.log(`[api] http://127.0.0.1:${PORT}  seekdb=${serverVersion} db=${SEEKDB.database}`);
  });
}

main().catch((err) => {
  console.error("[api] 启动失败:", err);
  process.exit(1);
});
