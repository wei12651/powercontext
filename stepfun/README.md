# 端侧知识库 · stepfun × seekdb

课程笔记检索助手：语料在**浏览器本地**完成向量化，向量、原文与元数据写入 **seekdb**，检索时由 **seekdb 在库内完成全文 + 向量混合检索（RRF 融合）**。

- 端侧推理：Transformers.js + ONNX Runtime Web，WebGPU 优先，自动回退 WASM，不调用任何云端推理 API
- 数据库：seekdb 1.3+，通过 seekdb-js 访问；每个集合一张表，ik 全文索引 + HNSW 余弦向量索引
- 混合检索：调用 `DBMS_HYBRID_SEARCH`，融合排序全部在数据库内完成，界面可直接查看实际执行的 SQL

## 架构

```
┌────────────────────────── 浏览器 ──────────────────────────┐
│  src/main.ts（界面 / 状态 / 指标）                          │
│        │ postMessage                       │ fetch /api/*  │
│        ▼                                   ▼               │
│  src/embedder.worker.ts                server/index.mjs     │
│  @huggingface/transformers             Express 5 + seekdb-js│
│  onnxruntime-web (WebGPU / WASM)           │               │
└────────────────────────────────────────────┼───────────────┘
                                             ▼
                                    seekdb 127.0.0.1:2881
                                    c$v2$<collection_id>
                                    ├─ FULLTEXT (ik, smart)
                                    ├─ VECTOR(512) HNSW COSINE
                                    └─ document / metadata
```

向量只在“浏览器 → 写入接口”这一个方向出现；服务端不生成向量、不缓存查询结果，也不做结果拼合。检索请求只携带查询向量与查询文本，排序由 `DBMS_HYBRID_SEARCH` 生成并执行一条 SQL 完成。

## 目录

```
stepfun/
├── server/index.mjs          # API：集合、导入、检索、库内融合 SQL 回显
├── src/
│   ├── main.ts               # 界面、导入流程、检索、评测与基准、指标导出
│   ├── embedder.worker.ts    # 端侧向量化 Worker（模型加载、量化、回退）
│   ├── embedder.ts           # Worker 消息封装
│   ├── api.ts                # 后端接口封装
│   ├── chunk.ts              # 分块（420 字 / 80 重叠，保留章节与来源）
│   ├── corpus.ts             # 示例语料与示例查询
│   ├── eval-cases.ts         # 10 条评测用例
│   └── styles.css
├── public/corpus/            # 8 篇课程笔记（数据库 / 操作系统 / 计算机网络）
├── scripts/prepare-ort.mjs   # 复制 onnxruntime-web 的 wasm 资源
├── scripts/fetch-model.mjs   # 下载 bge-small-zh-v1.5（fp32 / fp16 / int8）
└── TECHNICAL_NOTE.md         # 一页技术说明（含量化与性能数据）
```

## 环境要求

| 依赖 | 版本 / 说明 |
| --- | --- |
| Node.js | ≥ 20 |
| seekdb | ≥ 1.3.0（需要 `DBMS_HYBRID_SEARCH` 与索引刷新接口）；本项目验证于 `5.7.25-OceanBase seekdb-v1.3.0.0` |
| 浏览器 | Chrome / Edge ≥ 121（WebGPU）；不支持 WebGPU 时自动回退 WASM |
| 磁盘 | 约 200 MB 模型权重 + 约 70 MB ORT wasm 资源（均不进仓库） |

seekdb 按官方文档安装并启动，默认监听 `127.0.0.1:2881`，用户 `root`、空密码。数据库 `stepfun_kb` 由服务端首次启动时自动创建。连接信息不同时用环境变量覆盖：

| 变量 | 默认值 |
| --- | --- |
| `SEEKDB_HOST` | `127.0.0.1` |
| `SEEKDB_PORT` | `2881` |
| `SEEKDB_USER` | `root` |
| `SEEKDB_PASSWORD` | 空 |
| `SEEKDB_DATABASE` | `stepfun_kb` |
| `PORT` | `8787` |
| `MODEL_MIRROR` | `https://hf-mirror.com` |

## 快速开始

```bash
cd stepfun
npm install            # .npmrc 已设置 legacy-peer-deps（seekdb-js 的可选 peer 依赖）
npm run fetch:model    # 下载 Xenova/bge-small-zh-v1.5 三种量化权重（跳过已存在文件）
npm run dev            # Vite 5173 + API 8787
```

打开 <http://localhost:5173>。

- 生产模式：`npm run build && npm start`，访问 <http://127.0.0.1:8787>
- 只跑接口：`npm start`
- 模型镜像切换：`MODEL_MIRROR=https://huggingface.co npm run fetch:model`
- 若 `npm run dev` 输出的模型目录已有文件，脚本只做存在性检查，不会重复下载

## 使用流程

1. 选择推理后端（自动 / WebGPU / WASM）与量化精度（FP16 / INT8 / FP32），点“加载 / 切换模型”
2. 新建或选择集合；集合之间完全隔离
3. “导入示例语料 A”（数据库 4 篇 → 20 个片段），再“追加批次 B”（操作系统 / 网络 4 篇 → 20 个片段）；也可选择本地 `.md`/`.txt` 或直接粘贴文本
4. 输入问题检索；结果卡片显示 RRF 得分与来源，展开“查看 seekdb 生成的融合 SQL”可核对实际执行的融合语句
5. “运行评测集”给出 10 条标注查询的 Top-3 命中率；“批量基准”对比批大小 1/4/8/16 的向量化耗时与库内检索延迟；指标可导出 JSON

## 验证样例

以下步骤在一台 Windows 11 + Chromium 150（WebGPU 可用）+ seekdb 1.3.0.0 的环境实测通过，按顺序执行即可复现。

| # | 操作 | 预期结果 |
| --- | --- | --- |
| 1 | 启动后访问 `/api/health` | `{"ok":true,"version":"5.7.25-OceanBase seekdb-v1.3.0.0","dimensions":512,...}` |
| 2 | 导入示例语料 A | 提示“已导入 20 个片段并刷新索引”，集合信息显示 20 个片段 |
| 3 | 追加批次 B | 集合变为 40 个片段 |
| 4 | 检索 `redo log 和 undo log 分工有什么不同` | 首位命中《6.2 redo log 与 undo log 的分工》，来源 `日志与崩溃恢复.md`，RRF 0.0328 |
| 5 | 检索 `MVCC 快照读是怎么实现的` | 首位命中《5.3 MVCC 是怎么实现的》，来源 `事务与隔离级别.md` |
| 6 | 检索 `TCP 为什么需要三次握手` | 首位命中《5.1 三次握手为什么不是两次》 |
| 7 | 检索 `CDN 缓存穿透和雪崩的区别` | 首位命中《6.5 缓存穿透、击穿与雪崩》 |
| 8 | 追加 B 后立刻检索 `优先级反转怎么解决` | 首位命中 `进程调度与并发.md`（新语料无需重启即可检索） |
| 9 | 新建空集合并检索 | 返回“没有命中”，不泄漏其它集合数据；切回原集合结果不变 |
| 10 | 运行评测集（10 条） | Top-3 命中 10/10 |
| 11 | 批量基准（40 片段） | 输出批大小 1/4/8/16 的耗时表与库内检索 P50 |
| 12 | 精度切到 FP32 / INT8 各跑一次评测 | 均为 10/10；模型体积分别约 90.9 MB / 23.2 MB |

`/api/health` 与 `/api/collections` 也可直接用 curl 验证：

```bash
curl http://127.0.0.1:8787/api/health
curl http://127.0.0.1:8787/api/collections
```

## 赛题要求对应

| 要求 | 实现位置 |
| --- | --- |
| 浏览器本地向量化，禁用云端推理 | `src/embedder.worker.ts`：本地权重 + onnxruntime-web，`allowRemoteModels` 仅在不使用本地权重时才开启，且仅指向 HF 镜像获取**模型文件**，不发送文本 |
| 量化与体积 / 质量对比 | 精度选择器支持 FP32 / FP16 / INT8；数据见 `TECHNICAL_NOTE.md` |
| WebGPU 优先、自动回退 WASM 并提示 | `embedder.worker.ts` 回退分支 + `main.ts` 徽标与状态文案（warn 样式） |
| seekdb-js 访问，存向量 / 原文 / 元数据 | `server/index.mjs` `collectionSchema()` 与 `upsert` |
| 混合检索由 seekdb 执行 | `POST /api/search` → `collection.hybridSearch(..., rank:{rrf:{}})`；界面回显 `DBMS_HYBRID_SEARCH.GET_SQL` 生成的 SQL |
| 增量写入与索引刷新 | `POST /api/import` 内 `upsert` 后调用 `refresh_index`，返回写库 / 刷新耗时 |
| 连续导入与切换集合的状态一致性 | 每个集合独立表；`listCollections({withEmbeddingFunction:false})`；界面切换后即刻生效 |
| 性能优化与前后对比 | 分块批量向量化、模型本地缓存、索引刷新、库内融合；基准表见技术说明 |
| 最新 Chrome / Edge 运行 | 已在 Chromium 150（WebGPU）与 WASM 路径实测 |

## 常见问题

- **模型加载失败 / 下载超时**：默认从 `hf-mirror.com` 下载；先执行 `npm run fetch:model`，确保 `public/models/Xenova/bge-small-zh-v1.5/onnx/` 下有三个权重文件
- **提示“WebGPU 初始化失败，已回退到 WASM / INT8”**：浏览器或驱动不支持 WebGPU，功能不受影响，仅速度较慢
- **接口 500 且提示连接失败**：确认 seekdb 已启动并监听 2881；`curl http://127.0.0.1:8787/api/health` 会给出 seekdb 版本
- **端口占用**：`PORT=9000 npm start`；开发模式下同时修改 `vite.config.ts` 的代理目标

## 隐私

语料、查询文本、向量与检索结果全部留在本机浏览器与本地 seekdb 中；应用不含任何云端推理或遥测请求。
