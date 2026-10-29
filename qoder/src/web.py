"""Web 界面：一个搜索框 + 课程筛选 + 带出处的检索结果。"""
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

from . import db, search

app = FastAPI(title="课程笔记检索助手")


@app.get("/api/courses")
def list_courses():
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT course, COUNT(*) FROM doc_chunks GROUP BY course ORDER BY course")
        return [{"course": row[0], "chunks": row[1]} for row in cur.fetchall()]


@app.get("/api/search")
def api_search(
    q: str = Query(..., min_length=1),
    course: str = "",
    limit: int = Query(8, ge=1, le=30),
):
    try:
        return {"query": q, "results": search.search(q, course, limit)}
    except Exception as exc:
        return {"query": q, "results": [], "error": str(exc)}


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>课程笔记检索助手</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 48px 20px; font: 16px/1.6 -apple-system, "Segoe UI", "PingFang SC", sans-serif; }
  main { max-width: 780px; margin: 0 auto; }
  h1 { font-size: 26px; margin: 0 0 6px; }
  .sub { margin: 0 0 28px; opacity: .62; font-size: 14px; }
  form { display: flex; gap: 10px; flex-wrap: wrap; }
  input[type=search] { flex: 1 1 320px; padding: 12px 14px; font-size: 16px; border: 1px solid #8a8a8a55; border-radius: 10px; background: transparent; color: inherit; }
  button { padding: 12px 20px; font-size: 15px; border: 0; border-radius: 10px; background: #2f6feb; color: #fff; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
  #meta { margin: 18px 0 0; font-size: 13px; opacity: .6; min-height: 20px; }
  .hit { padding: 16px 0; border-top: 1px solid #8a8a8a33; }
  .hit h2 { font-size: 16px; margin: 0 0 6px; font-weight: 600; }
  .prov { font-size: 12px; opacity: .72; margin-bottom: 8px; font-variant-numeric: tabular-nums; }
  .prov span { display: inline-block; margin-right: 10px; }
  .tag { padding: 1px 7px; border-radius: 999px; border: 1px solid #8a8a8a55; }
  .snip { margin: 0; font-size: 14px; }
  mark { background: #f2d64b; color: #1a1a1a; padding: 0 2px; border-radius: 3px; }
  footer { margin-top: 40px; font-size: 12px; opacity: .5; }
</style>
</head>
<body>
<main>
  <h1>课程笔记检索助手</h1>
  <p class="sub">向量召回 + 全文召回，RRF 融合排序 · 数据存于 seekdb</p>
  <form id="f">
    <input id="q" type="search" placeholder="例如：B+ 树为什么适合做索引" autofocus>
    <select id="course"></select>
    <button id="go" type="submit">检索</button>
  </form>
  <p id="meta"></p>
  <section id="out"></section>
  <footer>结果里的「向量召回 / 全文召回」表示该片段被哪一路通道命中。</footer>
</main>
<script>
const out = document.getElementById('out');
const meta = document.getElementById('meta');
const go = document.getElementById('go');
const q = document.getElementById('q');
const courseSel = document.getElementById('course');

function esc(s) { return s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

function mark(text, query) {
  const safe = esc(text);
  const tokens = query.split(/[\\s，。、；：？！,.;:?!]+/).filter(t => t.length >= 2);
  if (!tokens.length) return safe;
  const re = new RegExp('(' + tokens.map(t => esc(t).replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&')).join('|') + ')', 'gi');
  return safe.replace(re, '<mark>$1</mark>');
}

async function loadCourses() {
  try {
    const rows = await (await fetch('/api/courses')).json();
    courseSel.innerHTML = '<option value="">全部课程</option>' +
      rows.map(r => `<option value="${esc(r.course)}">${esc(r.course)}（${r.chunks}）</option>`).join('');
  } catch (e) { courseSel.innerHTML = '<option value="">全部课程</option>'; }
}

async function run(event) {
  if (event) event.preventDefault();
  const query = q.value.trim();
  if (!query) return;
  go.disabled = true;
  meta.textContent = '检索中…';
  out.innerHTML = '';
  try {
    const url = `/api/search?q=${encodeURIComponent(query)}&course=${encodeURIComponent(courseSel.value)}`;
    const data = await (await fetch(url)).json();
    if (data.error) { meta.textContent = '出错：' + data.error; return; }
    meta.textContent = `“${query}” 命中 ${data.results.length} 条`;
    out.innerHTML = data.results.map((r, i) => {
      const prov = [
        `<span>#${i + 1}</span>`,
        `<span>${esc(r.course || '未分类')}${r.chapter ? ' / ' + esc(r.chapter) : ''}${r.page ? ' / 第' + r.page + '页' : ''}</span>`,
        r.sources.map(s => `<span class="tag">${s === 'vector' ? '向量召回' : '全文召回'}</span>`).join(''),
        `<span>RRF ${r.score}</span>`
      ].join('');
      return `<article class="hit"><h2>${esc(r.title)}</h2><div class="prov">${prov}</div><p class="snip">${mark(r.snippet, query)}</p></article>`;
    }).join('') || '<p class="sub">没有命中，试试换个说法。</p>';
  } catch (e) {
    meta.textContent = '请求失败：' + e;
  } finally {
    go.disabled = false;
  }
}

document.getElementById('f').addEventListener('submit', run);
loadCourses();
</script>
</body>
</html>
"""
