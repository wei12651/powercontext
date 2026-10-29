# Qoder 开发记录

- Qoder 账号 UID：`01a182d2-d663-7f33-9533-385287e80975`
- 参赛者：wei12651
- 开发时间：2026-10-29
- 代码仓库：https://github.com/wei12651/powercontext （分支 `2026-competition-ecosystem`，代码位于 `qoder/` 目录）
- 公开站点：https://course-notes-search-4stmu9183vl.qoder.zone/

## 1. 需求确认

赛题要求「用 Qoder 开发一个学习 / 科研 / 工作提效项目，必须接入 seekdb 或 PowerContext」。经与 Qoder 讨论后确定：

- 场景：**课程笔记 / 讲义检索**。真实痛点是期末复习时"记得讲过，但翻不到在哪一页"。
- 范围：**只做检索，不做大模型生成**。理由是自己复习时更需要原文出处，而不是二手总结；同时避免引入幻觉，评测也可以量化成"命中位置是否正确"。
- 数据：Markdown / TXT / PDF / DOCX 讲义，按课程分组。
- 存储：seekdb 同时承担全文索引和向量索引，避免再引入一个向量库。

## 2. 技术选型中的决策

| 决策点 | 结论 | 为什么 |
| --- | --- | --- |
| 检索方式 | 向量 + 全文双路，RRF 融合 | 纯向量对专有名词、代码符号容易漂移；纯全文对"换个说法的同一概念"无解 |
| 相似度 | cosine + HNSW | seekdb 的 `VECTOR INDEX` 要求显式给出 `distance` 和 `type`，cosine 对归一化的文本向量更直观 |
| 中文分词 | `WITH PARSER ik` | 默认分词器切不开中文词组，`MATCH ... AGAINST` 基本无效 |
| 切块策略 | 按标题分节 + 段落滑窗（700 字 / 100 字重叠） | 章节标题本身就是最好的上下文，写进 `chapter` 字段后可展示为出处 |
| Embedding | OpenAI 兼容的云端 `/embeddings`（`BAAI/bge-m3`, 1024 维） | 本机 Python 3.15 装不上 torch 系依赖；把 embedding 抽象成 HTTP 调用，换服务只改 `.env` |
| 前端 | FastAPI + 单页内联 HTML | 检索是唯一交互，没必要上构建工具链 |
| 依赖 | 只用 PyMySQL / pypdf / FastAPI / uvicorn | `.env` 解析、DOCX 解析都用标准库实现，少一层依赖少一处失败点 |

## 3. 实现过程

1. **先验证 seekdb 的语法，再写代码**。逐条探测建表选项、`cosine_distance()`、`APPROXIMATE` 关键字、`MATCH ... AGAINST`，确认"全文过滤 + 向量排序"能写在同一条 SQL 里。这一步避免了一个大坑：`VECTOR KEY v(v)` 这种写法在 seekdb 1.3 会直接报 *vector index params not set*，必须写成 `VECTOR INDEX v(v) WITH (distance=cosine, type=hnsw)`。
2. 按"解析 → 切块 → 向量化 → 入库"拆成独立模块，`ingest.py` 只负责编排；切块和解析互不依赖，改切块策略不影响解析。
3. 检索层把两路召回写成独立的 `_vector_recall` / `_fulltext_recall`，融合逻辑与 SQL 分离。
4. 跑通 CLI 后再加 Web 层，Web 只调用 `search.search()`。
5. 用 `eval/sample_queries.json` 固化 8 条回归样例，改切块或融合参数后重跑 `python -m eval.run_eval` 看通过率。

## 4. 开发中实际修掉的问题

- **建表语句被注释吃掉**：SQL 文件开头两行 `--` 注释和 `CREATE DATABASE` 被 `;\n` 切在同一段里，最初用"整段以 `--` 开头就跳过"的方式过滤，导致建库语句被静默丢弃，报 `(1046, 'No database selected')`。改为逐行剥离注释后再切分。
- **全文通道被虚词撑满**：查询 `LRU 和 Clock 有什么区别` 时，IK 会切出单字词，导致无关片段也进候选。增加查询词过滤，丢弃长度小于 2 的 token 后再送 `MATCH`。
- **切块边界切断答案**：块间加 100 字重叠后，"为什么 B+ 树适合做索引"的答案不再被截断在块尾。

## 5. 验证证据

- 语料：`samples/database/` 两份笔记 + `samples/os/` 一份笔记，共 15 个块。
- 回归：`python -m eval.run_eval` → **通过 8/8**（全部前 3 位命中，1 条第 2 位、7 条第 1 位）。
- 界面：本地 `uvicorn` 起服务后，在浏览器实际输入查询，「向量召回 / 全文召回」标签、出处路径、关键词高亮均正常；课程筛选切换后结果只来自所选课程。
- 公开站点：用 Qoder Sites 发布为公开访问，站点内容与本地一致；线上检索框里选中验证样例时展示的是本地跑出的真实结果（导出为 `site/demo-data.js`），自由输入时走浏览器内全文匹配——这一点在页面上有明确标注，没有把离线结果伪装成线上后端返回。
- 检索结果里每条都带文件名、章节路径、页码（PDF）和命中通道，可作为"命中位置正确"的判据。
