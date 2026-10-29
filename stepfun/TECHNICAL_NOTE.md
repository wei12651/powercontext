# 技术说明（一页）

**端侧知识库 · stepfun × seekdb** — 浏览器本地向量化 + seekdb 库内混合检索的迷你知识库。

## 1. 目标与约束

课程笔记量小、更新频繁，但查询需要同时命中术语（如 “redo log”）与语义近似表达（如 “快照读怎么实现”）。因此设计为：**向量化完全在端侧完成，检索融合完全在数据库内完成**，服务端只做转发与参数校验，不生成向量、不预计算、不拼合两路结果。

## 2. 架构与数据流

```
浏览器 Worker（Transformers.js + onnxruntime-web）
  文本 → 分块 → 向量（512 维，L2 归一化）
       → POST /api/import  {ids, documents, metadatas, embeddings}
Express + seekdb-js → seekdb：upsert 后 refresh_index
查询：浏览器编码问题 → POST /api/search {queryText, queryEmbedding}
Express → collection.hybridSearch({whereDocument, knn, rank:{rrf:{}}})
       → seekdb 生成并执行一条 DBMS_HYBRID_SEARCH SQL → 返回 topK
```

表结构（由 seekdb-js 按 Schema 创建）：`_id varbinary(512) 主键`、`document`、`embedding VECTOR(512)`、`metadata json`、`FULLTEXT KEY ... WITH PARSER ik (ik_mode=smart)`、`VECTOR KEY ... (DISTANCE=COSINE, TYPE=HNSW, LIB=VSAG, SYNC_MODE=ASYNC)`。每个集合一张独立表（`c$v2$<collection_id>`），集合间无共享行。

## 3. 端侧模型与量化

选型 `Xenova/bge-small-zh-v1.5`：中文检索效果稳定、512 维、提供 fp32/fp16/int8 三档 ONNX 权重（24–95 MB），适合浏览器首次加载。池化用 CLS + L2 归一化；查询侧加前缀「为这个句子生成表示以用于检索相关文章：」，文档侧不加。

| 精度 | ONNX 权重 | 浏览器下载量 | 相对 FP32 | 评测 Top-3 |
| --- | --- | --- | --- | --- |
| FP32 | 94.9 MB | 90.9 MB | 1.00× | 10/10 |
| FP16 | 47.5 MB | 45.7 MB | **2.00× 压缩** | 10/10 |
| INT8 | 23.9 MB | 23.2 MB | **3.97× 压缩** | 10/10 |

评测集为 10 条人工标注查询（覆盖 8 篇笔记），判定首位命中来源文件。三档量化均为 10/10、逐条首位来源一致——在本语料与模型上，INT8 未造成可观测的 Top-3 质量损失，且体积降到 FP32 的 25%。样本量小，结论仅代表该量级语料。

## 4. 库内混合检索（关键实现）

`POST /api/search` 调用 seekdb-js 的 `hybridSearch`，指定全文条件、KNN 查询向量与 `rank: { rrf: {} }`（窗口 40）。SDK 把参数写入 `@search_parm`，由数据库生成 SQL：

```sql
SELECT ..., (ifnull(1 / (`_fts`.`_keyword_rank` + 60), 0)
           + ifnull(1 / (`_vs`.`_semantic_rank` + 60), 0)) AS `_score`
FROM (
  SELECT *, RANK() OVER (ORDER BY `_keyword_score` DESC) AS `_keyword_rank`
  FROM (SELECT ..., MATCH(`document`) AGAINST('redo log 和 undo log 分工' IN NATURAL LANGUAGE MODE) AS `_keyword_score`
        FROM `stepfun_kb`.`c$v2$...` WHERE MATCH(...) ... LIMIT 60)
) `_fts`
FULL JOIN (
  SELECT *, RANK() OVER (ORDER BY `_semantic_score` DESC) AS `_semantic_rank`
  FROM (SELECT ..., 1 - cosine_distance(`embedding`, '[...]') ... ORDER BY `_distance` APPROXIMATE LIMIT 40)
) `_vs` ON `_fts`.`__pk_increment` = `_vs`.`__pk_increment`
ORDER BY `_score` DESC LIMIT 5;
```

两路召回、排名、RRF 加权（k=60）与最终排序都在 seekdb 内部完成，客户端只接收最终 topK；检索响应同时返回该 SQL，界面“库内执行 SQL”面板可核对，**不存在客户端各取一路再自行拼合**。

## 5. 增量写入与索引刷新

- 片段 ID 为 `文件名#序号`，同一文件重复导入走 upsert，天然幂等
- 每次导入后调用 `refresh_index()`（`CALL dbms_index_manager.refresh()`），返回 `writeMs / refreshMs`
- 实测（FP16/WebGPU，40 片段集合）：首批 20 片段写库 598 ms + 刷新 391 ms；追加 20 片段写库 248 ms + 刷新 417 ms
- 追加后立即检索「优先级反转怎么解决」，首位即新导入的《进程调度与并发》，无需重启或重建集合
- 向量索引为 `SYNC_MODE=ASYNC`，刷新后首次查询可能触发后台构建（实测一次 254 ms），其余为 11–17 ms

集合与会话一致性：切换集合会重新读取该集合片段数并清空旧结果；在空集合（`notes_b`）检索返回 0 行，切回 `notes` 后 40 片段与原有结果不受影响。量化对比使用独立集合（`notes` / `q_int8` / `q_fp32`）分别导入，互不影响。

## 6. 性能

批量向量化，40 个片段（每片段约 420 字），单位 ms/片段：

| 后端 / 精度 | 批 1 | 批 4 | 批 8 | 批 16 | 最优总耗时 |
| --- | --- | --- | --- | --- | --- |
| WebGPU / FP32 | 22.8 | 9.9 | 8.2 | 10.1 | 329 ms |
| WebGPU / FP16 | 20.5 | 7.8 | **5.5** | 6.0 | **221 ms** |
| WASM / INT8 | 509.2 | 607.2 | 691.7 | 802.9 | 20370 ms |

- WebGPU 上批量收益明显：FP16 从 20.5 降到 5.5 ms/片段（约 3.7×），默认批大小取 8
- WASM/INT8 上批量反而变慢：单线程 int8 内核对长文本批量没有吞吐收益，且批内按最长序列填充放大计算量；该后端最优批大小为 1
- 端到端导入 20 片段：FP32/WebGPU 1.31 s，INT8/WASM 12.79 s（向量化占主要部分）
- 模型加载：FP16/WebGPU 首次（含 45.7 MB 权重下载）12.6 s，同浏览器再次加载 4.4 s；FP32 1.6 s；INT8/WASM 0.5 s（HTTP 缓存命中）
- 库内混合检索（Top-5，40 行）P50：11.4 ms（FP16 集合）/ 16.9 ms（INT8 集合）/ 17.3 ms（FP32 集合）——差异主要来自缓存与异步索引状态，检索开销与精度无关

## 7. 回退与错误处理

`device=webgpu` 初始化抛错时，Worker 自动以 `wasm/int8` 重建并回传 `fallback=true`；界面把后端徽标置为 warn 色并显示「WebGPU 初始化失败，已回退到 WASM / INT8（耗时）」，顶部另有切换提示。该分支通过注入一次失败实测（随后移除注入），回退后检索与导入均正常。

模型权重优先使用本地文件（`public/models`）；本地不存在时才允许从镜像拉取文件，且 `env.useBrowserCache` 关闭，避免把 HTML 错误页当作模型缓存。

## 8. 限制

- 语料规模为 40 片段，未做万级向量召回压测；HNSW 参数与 RRF k=60 为固定值
- WASM 为单线程（未配置 COOP/COEP），CPU 路径只作为回退
- 索引刷新为整表刷新，尚未按集合增量调度
- 评测集 10 条，用于回归而非评测模型能力
- 应用为本地单用户工具，无鉴权；服务默认只监听 127.0.0.1

**环境**：Windows 11、Chromium 150（WebGPU 可用）、seekdb `5.7.25-OceanBase seekdb-v1.3.0.0`、Node 24。
