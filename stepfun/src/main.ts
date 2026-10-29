import "./styles.css";
import { api, type CollectionInfo, type SearchRow } from "./api";
import { Embedder, type Device, type ReadyInfo } from "./embedder";
import { chunkDocument, type Chunk, type RawDoc } from "./chunk";
import {
  CORPUS_BATCH_A,
  CORPUS_BATCH_B,
  SAMPLE_QUERIES,
  loadEntries,
} from "./corpus";
import { EVAL_CASES } from "./eval-cases";

const app = document.querySelector<HTMLDivElement>("#app")!;

app.innerHTML = `
  <div class="shell">
    <header class="masthead">
      <div class="masthead__text">
        <p class="kicker">阶跃星辰赛道 · 端侧知识库</p>
        <h1>课程笔记混合检索</h1>
        <p class="lede">向量化只发生在浏览器；全文与向量两路召回、RRF 融合全部由 seekdb 执行。</p>
      </div>
      <div class="badges" id="badges">
        <span class="badge" id="badge-db">seekdb 连接中</span>
        <span class="badge" id="badge-backend">后端待加载</span>
        <span class="badge" id="badge-size">模型 --</span>
      </div>
    </header>

    <div class="layout">
      <aside class="panel panel--side">
        <section class="block">
          <h2>1 · 端侧模型</h2>
          <div class="field-row">
            <label class="field">
              <span>推理后端</span>
              <select id="device">
                <option value="auto">自动（WebGPU 优先）</option>
                <option value="webgpu">WebGPU</option>
                <option value="wasm">WASM（CPU）</option>
              </select>
            </label>
            <label class="field">
              <span>量化精度</span>
              <select id="dtype">
                <option value="fp16">FP16 · 47.5 MB</option>
                <option value="int8">INT8 · 23.9 MB</option>
                <option value="fp32">FP32 · 94.9 MB</option>
              </select>
            </label>
          </div>
          <button class="btn btn--ghost" id="load-model">加载 / 切换模型</button>
          <p class="hint" id="model-status">模型首次加载需要下载权重，之后走浏览器缓存。</p>
        </section>

        <section class="block">
          <h2>2 · 集合</h2>
          <div class="field-row">
            <label class="field field--grow">
              <span>当前集合</span>
              <select id="collection"></select>
            </label>
            <button class="btn btn--ghost btn--square" id="delete-collection" title="删除集合">删</button>
          </div>
          <div class="field-row">
            <label class="field field--grow">
              <span>新建集合</span>
              <input id="new-collection" placeholder="例如 notes_b" autocomplete="off" />
            </label>
            <button class="btn btn--ghost btn--square" id="create-collection" title="创建集合">建</button>
          </div>
          <p class="hint" id="collection-meta">--</p>
        </section>

        <section class="block">
          <h2>3 · 导入语料</h2>
          <div class="btn-stack">
            <button class="btn" id="import-a">导入示例语料 A（数据库 4 篇）</button>
            <button class="btn btn--ghost" id="import-b">追加批次 B（OS / 网络 4 篇）</button>
          </div>
          <label class="file">
            <input type="file" id="files" multiple accept=".md,.txt" />
            <span>选择本地 .md / .txt 文件</span>
          </label>
          <label class="field">
            <span>粘贴文本（作为一篇笔记）</span>
            <textarea id="pasted" rows="3" placeholder="把课程笔记粘贴到这里…"></textarea>
          </label>
          <div class="field-row">
            <label class="field field--grow">
              <span>向量化批大小</span>
              <select id="batch-size">
                <option value="1">1</option>
                <option value="4">4</option>
                <option value="8" selected>8</option>
                <option value="16">16</option>
              </select>
            </label>
            <button class="btn btn--ghost" id="import-paste">导入文本</button>
          </div>
          <div class="progress" aria-hidden="true"><div class="progress__bar" id="progress"></div></div>
          <p class="hint" id="import-status">导入时会先在浏览器完成向量化，再写入 seekdb 并刷新索引。</p>
        </section>
      </aside>

      <main class="panel panel--main">
        <section class="search">
          <form id="search-form" class="search__form">
            <label class="field field--grow">
              <span>检索问题</span>
              <input id="query" placeholder="例如：redo log 和 undo log 分工有什么不同" autocomplete="off" />
            </label>
            <label class="field field--narrow">
              <span>Top K</span>
              <select id="topk">
                <option>3</option>
                <option selected>5</option>
                <option>8</option>
                <option>12</option>
              </select>
            </label>
            <button class="btn btn--primary" id="search-btn" type="submit">混合检索</button>
          </form>
          <div class="chips" id="chips"></div>
          <p class="hint" id="search-status">查询向量在浏览器内计算，检索请求只携带向量与关键词。</p>
        </section>

        <section class="results" id="results" aria-live="polite">
          <div class="empty">
            <p class="empty__title">还没有检索结果</p>
            <p>先导入示例语料，再输入一个问题。结果由 seekdb 的 RRF 融合排序给出。</p>
          </div>
        </section>

        <details class="sql" id="sql-box">
          <summary>查看 seekdb 生成的融合 SQL</summary>
          <pre id="sql-text">检索后显示。</pre>
        </details>
      </main>
    </div>

    <section class="metrics">
      <div class="metrics__head">
        <h2>性能与质量</h2>
        <div class="metrics__actions">
          <button class="btn btn--ghost" id="run-eval">运行评测集（10 条）</button>
          <button class="btn btn--ghost" id="run-bench">批量基准（40 片段）</button>
          <button class="btn btn--ghost" id="export">导出指标 JSON</button>
        </div>
      </div>
      <div class="metric-grid" id="metric-grid"></div>
      <div class="eval" id="eval-box"></div>
      <div class="bench" id="bench-box"></div>
    </section>

    <footer class="footer">
      <p>这里没有云端推理：模型权重从 HF 镜像下载后被浏览器缓存，推理在 WebGPU / WASM 中完成。</p>
      <p>代码基于 <code>oceanbase/powercontext</code> 的 <code>2026-competition-ecosystem</code> 分支 · 向量与全文索引均为 seekdb 原生索引。</p>
    </footer>
  </div>
  <div class="toast" id="toast" role="status"></div>
`;

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;

type Metric = { label: string; value: string; note?: string };

type State = {
  ready: ReadyInfo | null;
  device: Device;
  dtype: string;
  collections: CollectionInfo[];
  collection: string;
  chunks: number;
  metrics: Metric[];
  importStats: Record<string, unknown>;
  searchStats: Record<string, number>;
  evalStats: unknown;
  benchStats: unknown;
  lastSql: string;
};

const state: State = {
  ready: null,
  device: "wasm",
  dtype: "int8",
  collections: [],
  collection: "",
  chunks: 0,
  metrics: [],
  importStats: {},
  searchStats: {},
  evalStats: null,
  benchStats: null,
  lastSql: "",
};

const embedder = new Embedder();

function toast(message: string, kind: "ok" | "err" = "ok") {
  const node = $("toast");
  node.textContent = message;
  node.className = `toast toast--show toast--${kind}`;
  setTimeout(() => (node.className = "toast"), 3200);
}

function setMetric(label: string, value: string, note?: string) {
  const next = state.metrics.filter((m) => m.label !== label);
  next.push({ label, value, note });
  state.metrics = next;
  renderMetrics();
}

function renderMetrics() {
  $("metric-grid").innerHTML = state.metrics
    .map(
      (m) => `
      <div class="metric">
        <span class="metric__label">${escapeHtml(m.label)}</span>
        <strong class="metric__value">${escapeHtml(m.value)}</strong>
        ${m.note ? `<span class="metric__note">${escapeHtml(m.note)}</span>` : ""}
      </div>`,
    )
    .join("");
}

function escapeHtml(text: string) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatMs(ms: number) {
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  return `${ms.toFixed(0)} ms`;
}

function formatBytes(bytes: number) {
  if (!bytes) return "--";
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function setBusy(buttonId: string, busy: boolean, text?: string) {
  const btn = $(buttonId) as HTMLButtonElement;
  btn.disabled = busy;
  if (text) btn.textContent = text;
}

function autoDevice(): { device: Device; dtype: string } {
  const choice = ($("device") as HTMLSelectElement).value;
  const dtype = ($("dtype") as HTMLSelectElement).value;
  if (choice === "webgpu" || choice === "wasm") return { device: choice, dtype };
  return "gpu" in navigator ? { device: "webgpu", dtype } : { device: "wasm", dtype: "int8" };
}

async function loadModel(initial = false) {
  const { device, dtype } = autoDevice();
  setBusy("load-model", true, "加载中…");
  $("model-status").textContent = "正在准备推理后端…";
  embedder.onStatus = (stage, detail) => {
    if (stage === "loading") $("model-status").textContent = `加载 ${detail || ""}…`;
    if (stage === "fallback") $("model-status").textContent = `WebGPU 不可用，回退到 WASM：${detail || ""}`;
  };
  embedder.onDownload = (file, progress) => {
    $("model-status").textContent = `下载 ${file} · ${Math.round(progress)}%`;
  };
  try {
    const info = await embedder.init(device, dtype);
    state.ready = info;
    state.device = info.device;
    state.dtype = info.dtype;
    const backend = info.device === "webgpu" ? "WebGPU" : "WASM";
    $("badge-backend").textContent = `推理 · ${backend} / ${info.dtype.toUpperCase()}`;
    $("badge-backend").className = `badge ${info.device === "webgpu" ? "badge--good" : "badge--warn"}`;
    $("badge-size").textContent = `模型 ${formatBytes(info.bytes)}`;
    $("model-status").textContent = info.fallback
      ? `WebGPU 初始化失败，已回退到 WASM / INT8（${formatMs(info.loadMs)}）。`
      : `就绪：${backend} / ${info.dtype.toUpperCase()}，加载 ${formatMs(info.loadMs)}。`;
    setMetric("模型加载", formatMs(info.loadMs), `${backend} / ${info.dtype.toUpperCase()}`);
    setMetric("模型体积", formatBytes(info.bytes), "浏览器实际下载量");
    if (!initial) toast(`模型已切换到 ${backend} / ${info.dtype.toUpperCase()}`);
  } catch (err: any) {
    $("model-status").textContent = `模型加载失败：${err.message}`;
    $("badge-backend").textContent = "推理 · 不可用";
    $("badge-backend").className = "badge badge--bad";
    toast(err.message, "err");
    throw err;
  } finally {
    setBusy("load-model", false, "加载 / 切换模型");
  }
}

function fillCollections(list: CollectionInfo[]) {
  state.collections = list;
  const select = $("collection") as HTMLSelectElement;
  const previous = state.collection;
  select.innerHTML = list
    .map((c) => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)} · ${c.count} 片段</option>`)
    .join("");
  const next = list.find((c) => c.name === previous) || list[0];
  state.collection = next?.name || "";
  if (state.collection) select.value = state.collection;
  renderCollectionMeta();
}

function renderCollectionMeta() {
  const info = state.collections.find((c) => c.name === state.collection);
  $("collection-meta").textContent = info
    ? `当前集合 ${info.name}：${info.count} 个片段，检索与写入都作用于它。`
    : "还没有集合。创建或使用默认集合后即可导入。";
}

async function refreshCollections() {
  const { collections } = await api.collections();
  fillCollections(collections);
}

async function ensureCollection() {
  const { collections } = await api.collections();
  if (collections.length === 0) {
    await api.createCollection("notes");
    await refreshCollections();
    return;
  }
  fillCollections(collections);
}

async function ensureModel() {
  if (!state.ready) await loadModel(true);
}

function currentBatch() {
  return Number(($("batch-size") as HTMLSelectElement).value) || 8;
}

function setProgress(value: number) {
  $("progress").style.width = `${Math.max(0, Math.min(100, value))}%`;
}

async function importRaw(docs: RawDoc[], label: string) {
  if (!state.collection) throw new Error("请先创建或选择一个集合");
  await ensureModel();
  const chunks: Chunk[] = docs.flatMap((doc) => chunkDocument(doc));
  if (chunks.length === 0) throw new Error("没有解析出可导入的片段");
  const batch = currentBatch();
  $("import-status").textContent = `浏览器内向量化：${chunks.length} 个片段，批大小 ${batch}…`;
  const embedStart = performance.now();
  const embedded = await embedder.embed(
    chunks.map((c) => c.document),
    batch,
    "doc",
    (done, total) => {
      setProgress((done / total) * 100);
      $("import-status").textContent = `向量化 ${done}/${total}`;
    },
  );
  const embedMs = performance.now() - embedStart;
  const payload = chunks.map((chunk, i) => ({
    id: chunk.id,
    document: chunk.document,
    metadata: chunk.metadata,
    embedding: embedded.vectors[i],
  }));
  $("import-status").textContent = "写入 seekdb 并刷新索引…";
  const result = await api.importDocs(state.collection, payload);
  setProgress(100);
  $("import-status").textContent = `${label}：${result.written} 个片段，写入 ${result.writeMs} ms，刷新索引 ${result.refreshMs} ms。`;
  state.chunks = result.total;
  state.importStats = {
    label,
    docs: docs.length,
    chunks: chunks.length,
    batchSize: batch,
    embedMs: Math.round(embedMs),
    embedPerChunkMs: Math.round((embedMs / chunks.length) * 10) / 10,
    writeMs: result.writeMs,
    refreshMs: result.refreshMs,
    totalMs: Math.round(embedMs + result.writeMs + result.refreshMs),
    total: result.total,
  };
  setMetric("最近一次导入", `${chunks.length} 片段 / ${formatMs(embedMs + result.writeMs + result.refreshMs)}`, `${label} · 批 ${batch}`);
  setMetric("库内片段数", String(result.total), `集合 ${state.collection}`);
  await refreshCollections();
  toast(`已导入 ${result.written} 个片段并刷新索引`);
}

async function importSamples(which: "A" | "B") {
  const button = which === "A" ? "import-a" : "import-b";
  setBusy(button, true, "导入中…");
  try {
    const entries = which === "A" ? CORPUS_BATCH_A : CORPUS_BATCH_B;
    const docs = await loadEntries(entries);
    await importRaw(docs, which === "A" ? "示例语料 A" : "追加批次 B");
  } catch (err: any) {
    $("import-status").textContent = `导入失败：${err.message}`;
    toast(err.message, "err");
  } finally {
    setBusy(button, false, which === "A" ? "导入示例语料 A（数据库 4 篇）" : "追加批次 B（OS / 网络 4 篇）");
  }
}

async function importFiles(files: FileList) {
  const docs: RawDoc[] = [];
  for (const file of Array.from(files)) {
    docs.push({ path: file.name, course: "自定义", text: await file.text() });
  }
  await importRaw(docs, `本地文件 ×${docs.length}`);
}

async function importPasted() {
  const text = ($("pasted") as HTMLTextAreaElement).value.trim();
  if (!text) {
    toast("先粘贴一段笔记", "err");
    return;
  }
  await importRaw([{ path: "粘贴笔记.md", course: "自定义", text }], "粘贴文本");
  ($("pasted") as HTMLTextAreaElement).value = "";
}

function terms(text: string) {
  const out = new Set<string>();
  const cleaned = text.toLowerCase().replace(/[+\-&|!(){}[\]^"~*?:\\/]/g, " ");
  for (const token of cleaned.match(/[a-z0-9]{2,}/g) || []) out.add(token);
  for (const run of cleaned.match(/[\u4e00-\u9fa5]+/g) || []) {
    if (run.length >= 2) out.add(run);
    for (let i = 0; i + 2 <= run.length && i < 12; i += 1) out.add(run.slice(i, i + 2));
  }
  return [...out].filter((t) => t.length >= 2).sort((a, b) => b.length - a.length).slice(0, 8);
}

function highlight(text: string, query: string) {
  const plain = text.replace(/\s+/g, " ");
  const snippet = plain.slice(0, 420);
  const safe = escapeHtml(snippet);
  const list = terms(query).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (list.length === 0) return safe + (plain.length > 420 ? "…" : "");
  let count = 0;
  const body = safe.replace(new RegExp(list.join("|"), "gi"), (match) => {
    if (count++ >= 5) return match;
    return `<mark>${match}</mark>`;
  });
  return body + (plain.length > 420 ? "…" : "");
}

function renderResults(rows: SearchRow[], query: string) {
  if (rows.length === 0) {
    $("results").innerHTML = `<div class="empty"><p class="empty__title">没有命中</p><p>换一种问法，或者先导入更多语料。</p></div>`;
    return;
  }
  $("results").innerHTML = rows
    .map((row) => {
      const meta = row.metadata || {};
      return `
      <article class="hit">
        <header class="hit__head">
          <span class="hit__rank">#${row.rank}</span>
          <h3>${escapeHtml(meta.chapter || meta.title || row.id)}</h3>
          <span class="hit__score" title="RRF 融合分值">RRF ${row.score.toFixed(4)}</span>
        </header>
        <p class="hit__body">${highlight(row.document, query)}</p>
        <footer class="hit__meta">
          <span>${escapeHtml(meta.course || "未分类")}</span>
          <span>${escapeHtml(meta.source || "")}</span>
          <span>片段 ${Number(meta.chunk ?? 0) + 1}</span>
        </footer>
      </article>`;
    })
    .join("");
}

function trimSql(sql: string) {
  if (!sql) return "（未获取到 SQL）";
  return sql
    .replace(/'\[[^\]]{200,}\]'/g, "'[向量已省略]'")
    .replace(/FROM `[^`]+`/g, "FROM `<集合表>`")
    .trim();
}

async function search(query: string) {
  if (!query.trim()) return;
  if (!state.collection) {
    toast("先创建集合并导入语料", "err");
    return;
  }
  try {
    await ensureModel();
    setBusy("search-btn", true, "检索中");
    const embedStart = performance.now();
    const { vectors, ms: embedMs } = await embedder.embed([query], currentBatch(), "query");
    const started = performance.now();
    const topK = Number(($("topk") as HTMLSelectElement).value) || 5;
    const res = await api.search({
      collection: state.collection,
      queryText: query,
      queryEmbedding: vectors[0],
      topK,
    });
    const clientMs = performance.now() - started;
    renderResults(res.rows, query);
    state.lastSql = res.sql;
    $("sql-text").textContent = trimSql(res.sql);
    state.searchStats = {
      embedMs: Math.round(embedMs * 10) / 10,
      dbMs: res.searchMs,
      roundTripMs: Math.round(clientMs * 10) / 10,
      window: res.window,
      topK,
    };
    setMetric("上次检索", `${formatMs(clientMs)}`, `查询向量化 ${Math.round(embedMs)} ms · seekdb ${res.searchMs} ms`);
    $("search-status").textContent = `命中 ${res.rows.length} 条 · 查询向量化 ${Math.round(embedMs)} ms · seekdb 融合检索 ${res.searchMs} ms。`;
  } catch (err: any) {
    toast(err.message, "err");
    $("search-status").textContent = `检索失败：${err.message}`;
  } finally {
    setBusy("search-btn", false, "混合检索");
  }
}

async function runEval() {
  if (!state.collection) {
    toast("先导入语料再评测", "err");
    return;
  }
  try {
    await ensureModel();
    setBusy("run-eval", true, "评测中…");
    $("eval-box").innerHTML = `<p class="hint">正在逐条检索…</p>`;
    const results = [];
    for (const item of EVAL_CASES) {
      const { vectors } = await embedder.embed([item.query], currentBatch(), "query");
      const res = await api.search({
        collection: state.collection,
        queryText: item.query,
        queryEmbedding: vectors[0],
        topK: 3,
      });
      const hit = res.rows.some((row) => row.metadata?.source === item.expectSource);
      const top = res.rows[0]?.metadata?.source || "无结果";
      results.push({ ...item, hit, top, scores: res.rows.map((r) => Number(r.score.toFixed(4))) });
    }
    const passed = results.filter((r) => r.hit).length;
    state.evalStats = { passed, total: results.length, results, device: state.device, dtype: state.dtype };
    $("eval-box").innerHTML = `
      <h3>评测集 Top-3 命中：${passed}/${results.length}</h3>
      <table class="table">
        <thead><tr><th>问题</th><th>期望来源</th><th>首位命中</th><th>结果</th></tr></thead>
        <tbody>
          ${results
            .map(
              (r) => `<tr class="${r.hit ? "ok" : "miss"}">
                <td>${escapeHtml(r.query)}</td>
                <td>${escapeHtml(r.expectSource)}</td>
                <td>${escapeHtml(r.top)}</td>
                <td>${r.hit ? "命中" : "未命中"}</td>
              </tr>`,
            )
            .join("")}
        </tbody>
      </table>`;
    setMetric("评测 Top-3", `${passed}/${results.length}`, `${state.device} / ${state.dtype}`);
    toast(`评测完成：${passed}/${results.length}`);
  } catch (err: any) {
    toast(err.message, "err");
  } finally {
    setBusy("run-eval", false, "运行评测集（10 条）");
  }
}

async function runBench() {
  if (!state.collection) {
    toast("先导入语料再基准测试", "err");
    return;
  }
  try {
    await ensureModel();
    setBusy("run-bench", true, "基准中…");
    const docs = await loadEntries([...CORPUS_BATCH_A, ...CORPUS_BATCH_B]);
    const texts = docs.flatMap((doc) => chunkDocument(doc)).map((c) => c.document);
    const rows: { batch: number; ms: number; per: number }[] = [];
    for (const batch of [1, 4, 8, 16]) {
      await embedder.embed(texts.slice(0, 4), batch, "doc");
      const { ms } = await embedder.embed(texts, batch, "doc");
      rows.push({ batch, ms: Math.round(ms), per: Math.round((ms / texts.length) * 10) / 10 });
    }
    const queryTexts = SAMPLE_QUERIES.slice(0, 5);
    const { vectors } = await embedder.embed(queryTexts, currentBatch(), "query");
    const db: number[] = [];
    for (let i = 0; i < queryTexts.length; i += 1) {
      const res = await api.search({
        collection: state.collection,
        queryText: queryTexts[i],
        queryEmbedding: vectors[i],
        topK: 5,
      });
      db.push(res.searchMs);
    }
    db.sort((a, b) => a - b);
    const p50 = db[Math.floor(db.length / 2)];
    state.benchStats = { texts: texts.length, batches: rows, dbMs: db, dbP50: p50, device: state.device, dtype: state.dtype };
    $("bench-box").innerHTML = `
      <h3>批量向量化（${texts.length} 片段，${state.device} / ${state.dtype}）</h3>
      <table class="table">
        <thead><tr><th>批大小</th><th>总耗时</th><th>每片段</th></tr></thead>
        <tbody>${rows
          .map((r) => `<tr><td>${r.batch}</td><td>${r.ms} ms</td><td>${r.per} ms</td></tr>`)
          .join("")}</tbody>
      </table>
      <p class="hint">库内混合检索（Top-5）耗时：${db.map((x) => `${x.toFixed(1)} ms`).join(" / ")}，P50 ${p50.toFixed(1)} ms。</p>`;
    setMetric("批量最优", `${Math.min(...rows.map((r) => r.ms))} ms`, `${texts.length} 片段 · 批 ${rows.reduce((a, b) => (a.ms <= b.ms ? a : b)).batch}`);
    setMetric("检索 P50", `${p50.toFixed(1)} ms`, "seekdb 内 RRF 融合");
  } catch (err: any) {
    toast(err.message, "err");
  } finally {
    setBusy("run-bench", false, "批量基准（40 片段）");
  }
}

function exportMetrics() {
  const payload = {
    exportedAt: new Date().toISOString(),
    userAgent: navigator.userAgent,
    model: "Xenova/bge-small-zh-v1.5",
    dimensions: 512,
    backend: state.ready,
    collection: state.collection,
    import: state.importStats,
    search: state.searchStats,
    eval: state.evalStats,
    benchmark: state.benchStats,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `stepfun-metrics-${Date.now()}.json`;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function boot() {
  renderMetrics();
  $("chips").innerHTML = SAMPLE_QUERIES.map(
    (q) => `<button class="chip" data-query="${escapeHtml(q)}">${escapeHtml(q)}</button>`,
  ).join("");
  $("chips").addEventListener("click", (event) => {
    const chip = (event.target as HTMLElement).closest<HTMLButtonElement>(".chip");
    if (!chip) return;
    ($("query") as HTMLInputElement).value = chip.dataset.query || "";
    search(chip.dataset.query || "");
  });
  $("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    search(($("query") as HTMLInputElement).value);
  });
  $("load-model").addEventListener("click", () => loadModel().catch(() => {}));
  $("import-a").addEventListener("click", () => importSamples("A"));
  $("import-b").addEventListener("click", () => importSamples("B"));
  $("import-paste").addEventListener("click", () => importPasted().catch((e) => toast(e.message, "err")));
  $("files").addEventListener("change", async (event) => {
    const input = event.target as HTMLInputElement;
    if (!input.files?.length) return;
    try {
      await importFiles(input.files);
    } catch (err: any) {
      toast(err.message, "err");
    } finally {
      input.value = "";
    }
  });
  ($("collection") as HTMLSelectElement).addEventListener("change", (event) => {
    state.collection = (event.target as HTMLSelectElement).value;
    renderCollectionMeta();
    $("results").innerHTML = `<div class="empty"><p class="empty__title">已切换集合</p><p>当前集合 ${escapeHtml(state.collection)} 的检索结果会出现在这里。</p></div>`;
    $("sql-text").textContent = "检索后显示。";
  });
  $("create-collection").addEventListener("click", async () => {
    const name = ($("new-collection") as HTMLInputElement).value.trim();
    if (!name) return toast("输入集合名", "err");
    try {
      await api.createCollection(name);
      ($("new-collection") as HTMLInputElement).value = "";
      state.collection = name;
      await refreshCollections();
      toast(`已创建集合 ${name}`);
    } catch (err: any) {
      toast(err.message, "err");
    }
  });
  $("delete-collection").addEventListener("click", async () => {
    if (!state.collection) return;
    if (!confirm(`删除集合 ${state.collection} 及其全部片段？`)) return;
    try {
      await api.deleteCollection(state.collection);
      state.collection = "";
      await ensureCollection();
      toast("集合已删除");
    } catch (err: any) {
      toast(err.message, "err");
    }
  });
  $("run-eval").addEventListener("click", runEval);
  $("run-bench").addEventListener("click", runBench);
  $("export").addEventListener("click", exportMetrics);

  try {
    const health = await api.health();
    $("badge-db").textContent = `seekdb ${health.version.replace("OceanBase ", "")}`;
    $("badge-db").className = "badge badge--good";
    await ensureCollection();
  } catch (err: any) {
    $("badge-db").textContent = "seekdb 未连接";
    $("badge-db").className = "badge badge--bad";
    toast(`服务未就绪：${err.message}`, "err");
    return;
  }
  try {
    await loadModel(true);
  } catch {
    /* 具体错误已显示在模型状态与徽标中 */
  }
}

boot();
