(function () {
  const DEMO = window.DEMO;
  const form = document.getElementById('form');
  const input = document.getElementById('q');
  const courseSel = document.getElementById('course');
  const chips = document.getElementById('chips');
  const results = document.getElementById('results');
  const modeEl = document.getElementById('mode');
  const noteEl = document.getElementById('note');
  const evalBody = document.getElementById('eval-body');

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function terms(query) {
    const out = new Set();
    const latin = query.match(/[A-Za-z][A-Za-z0-9+#.]*|\d+/g) || [];
    latin.forEach((t) => { if (t.length >= 2) out.add(t.toLowerCase()); });
    const cjkRuns = query.match(/[\u4e00-\u9fa5]+/g) || [];
    cjkRuns.forEach((run) => {
      if (run.length > 1 && run.length <= 4) out.add(run);
      if (run.length === 1) return;
      for (let i = 0; i < run.length - 1; i++) out.add(run.slice(i, i + 2));
    });
    return [...out].filter((t) => t.length >= 2);
  }

  function highlight(text, ts, cap) {
    let html = esc(text);
    if (!ts.length) return html;
    const pattern = ts
      .slice()
      .sort((a, b) => b.length - a.length)
      .map((t) => esc(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
      .join('|');
    let left = cap || 6;
    return html.replace(new RegExp('(' + pattern + ')', 'gi'), (m) => {
      if (left <= 0) return m;
      left -= 1;
      return '<mark>' + m + '</mark>';
    });
  }

  function snippetOf(text, ts, width) {
    const plain = text.replace(/\s+/g, ' ').trim();
    const lower = plain.toLowerCase();
    let at = -1;
    ts.forEach((t) => {
      const i = lower.indexOf(t.toLowerCase());
      if (i >= 0 && (at < 0 || i < at)) at = i;
    });
    if (at <= 60) return plain.slice(0, width);
    return '…' + plain.slice(at - 60, at - 60 + width);
  }

  function prov(item) {
    const bits = [`<span class="rank">#${item.rank}</span>`];
    bits.push(`<span>${esc(item.course || '未分类')}${item.chapter ? ' / ' + esc(item.chapter) : ''}${item.page ? ' / 第 ' + item.page + ' 页' : ''}</span>`);
    if (item.sources) {
      item.sources.forEach((s) => {
        bits.push(`<span class="tag ${s === 'vector' ? 'vec' : 'txt'}">${s === 'vector' ? '向量召回' : '全文召回'}</span>`);
      });
    }
    if (item.scoreLabel) bits.push(`<span>${esc(item.scoreLabel)}</span>`);
    return bits.join('');
  }

  function render(items, ts) {
    results.innerHTML = items.map((item) => `
      <li class="hit">
        <h3>${esc(item.title)}</h3>
        <div class="prov">${prov(item)}</div>
        <p class="snip">${highlight(item.snippet || snippetOf(item.content, ts, 260), ts)}</p>
      </li>`).join('');
  }

  function findCase(query, course) {
    const q = query.trim().toLowerCase();
    return DEMO.cases.find((c) => c.q.toLowerCase() === q && (!course || c.course === course));
  }

  const GENERIC = new Set(['怎么', '什么', '如何', '为什', '可以', '是否', '哪些', '哪个', '有什', '么样', '怎样', '为什']);

  function localSearch(query, course) {
    const scope = DEMO.corpus.filter((c) => !course || c.course === course);
    const ts = terms(query).filter((t) => {
      if (GENERIC.has(t)) return false;
      const df = scope.filter((c) => (c.title + c.chapter + c.content).toLowerCase().includes(t.toLowerCase())).length;
      return df > 0 && df / scope.length <= 0.5;
    });
    const scored = [];
    scope.forEach((chunk) => {
      const body = (chunk.title + ' ' + chunk.chapter + ' ' + chunk.content).toLowerCase();
      const head = (chunk.title + chunk.chapter).toLowerCase();
      let score = 0;
      ts.forEach((t) => {
        const hits = body.split(t.toLowerCase()).length - 1;
        if (!hits) return;
        score += Math.min(hits, 4) * (1 + Math.min(t.length, 4) / 2);
        if (head.includes(t.toLowerCase())) score += 1.5;
      });
      if (!score) return;
      scored.push({
        title: chunk.title,
        course: chunk.course,
        chapter: chunk.chapter,
        page: chunk.page,
        content: chunk.content,
        score: Math.round(score * 100) / 100,
      });
    });
    scored.sort((a, b) => b.score - a.score);
    const floor = scored.length ? scored[0].score * 0.45 : 0;
    return scored.filter((r) => r.score >= floor).slice(0, 6);
  }

  function run(event) {
    if (event) event.preventDefault();
    const query = input.value.trim();
    if (!query) { input.focus(); return; }
    const course = courseSel.value;
    const matched = findCase(query, course);
    const ts = terms(query);

    [...chips.children].forEach((chip) => chip.setAttribute('aria-pressed', String(chip.dataset.q === query)));

    if (matched) {
      const items = matched.results
        .filter((r) => !course || r.course === course)
        .map((r, i) => ({ ...r, rank: i + 1, scoreLabel: 'RRF ' + r.score }));
      modeEl.textContent = '验证样例 · seekdb 真实混合检索';
      noteEl.textContent = '以上结果由本地 seekdb 实例返回：向量索引（HNSW + cosine）与中文全文索引（ik）双路召回后按 RRF 融合，得分即融合分。';
      render(items, ts);
      return;
    }

    const items = localSearch(query, course);
    modeEl.textContent = '在线演示 · 全文匹配（浏览器内计算）';
    noteEl.textContent = items.length
      ? '这个查询不在验证样例里，向量通道需要连接本地 seekdb 才能参与，所以这里只有全文匹配的结果。完整的混合检索请按「本地复现」跑起来。'
      : '内置语料里没有匹配的内容。换一个说法，或者点上面的验证样例。';
    render(items.map((r, i) => ({ ...r, rank: i + 1, scoreLabel: '匹配分 ' + r.score })), ts);
  }

  function buildChips() {
    DEMO.cases.slice(0, 6).forEach((c) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip';
      b.dataset.q = c.q;
      b.textContent = c.q;
      b.setAttribute('aria-pressed', 'false');
      b.addEventListener('click', () => {
        input.value = c.q;
        if (c.course) courseSel.value = c.course;
        run();
      });
      chips.appendChild(b);
    });
  }

  function buildCourses() {
    const seen = new Map();
    DEMO.corpus.forEach((c) => seen.set(c.course, (seen.get(c.course) || 0) + 1));
    [...seen.entries()]
      .sort((a, b) => a[0].localeCompare(b[0], 'zh'))
      .forEach(([name, n]) => {
        const o = document.createElement('option');
        o.value = name;
        o.textContent = `${name}（${n}）`;
        courseSel.appendChild(o);
      });
  }

  function buildEval() {
    evalBody.innerHTML = DEMO.cases.map((c) => {
      const idx = c.results.findIndex(
        (r) => r.title === c.expect.title && (r.chapter || '').includes(c.expect.chapter)
      );
      const rank = idx + 1;
      const strict = rank >= 1 && rank <= 3;
      const ok = strict || (idx >= 0 && rank <= c.topK);
      const channels = rank > 0 ? c.results[idx].sources : [];
      const label = channels.map((s) => (s === 'vector' ? '向量' : '全文')).join(' + ') || '—';
      const cell = ok
        ? `第 ${rank} 位 <span class="pass${strict ? '' : ' loose'}">${strict ? 'PASS' : 'PASS（放宽）'}</span>`
        : '未命中';
      return `<tr>
        <td>${esc(c.q)}</td>
        <td>${esc(c.expect.title.replace('.md', ''))} · ${esc(c.expect.chapter)}</td>
        <td>${cell}</td>
        <td>${esc(label)}</td>
      </tr>`;
    }).join('');
  }

  form.addEventListener('submit', run);
  buildCourses();
  buildChips();
  buildEval();

  const first = DEMO.cases[0];
  input.value = first.q;
  courseSel.value = first.course || '';
  run();
})();
