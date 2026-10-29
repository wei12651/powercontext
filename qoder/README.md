# 课程笔记检索助手（seekdb 混合检索）

把课程笔记 / 讲义变成可检索的知识库：一个自然语言问题，直接定位到具体讲义的第几节、并给出原文片段。

检索同时走**向量召回**和**全文召回**两条通道，再用 RRF 融合排序——语义相近但用词不同的内容靠向量找得到，专有名词、代码符号、公式名靠全文精确命中。

## 用到的 seekdb 能力

| 能力 | 用法 |
| --- | --- |
| 向量类型与 HNSW 索引 | `content_vec VECTOR(1024)` + `VECTOR INDEX ... WITH (distance=cosine, type=hnsw)` |
| 向量相似度检索 | `ORDER BY cosine_distance(content_vec, %s) APPROXIMATE LIMIT n` |
| 中文全文索引 | `FULLTEXT KEY ft_content(content) WITH PARSER ik` |
| 全文相关度 | `MATCH(content) AGAINST(%s)`，返回值直接作为排序分 |
| 标量 + 向量混合过滤 | 同一条 SQL 里 `WHERE course=%s` 与向量排序并存 |

因为没有大模型参与生成，回答永远是原文片段本身，不会编造内容——这对复习场景比"帮你总结"更可靠。

## 目录结构

```
qoder/
├── src/
│   ├── schema.sql      # 建表语句：全文索引 + 向量索引同表
│   ├── config.py       # 读 .env
│   ├── db.py           # 连接、建表、向量字面量转换
│   ├── embedding.py    # 调 OpenAI 兼容的 /embeddings
│   ├── chunking.py     # 按标题分节 + 段落滑窗切块
│   ├── parsers.py      # md / txt / pdf / docx 解析
│   ├── ingest.py       # 导入入口
│   ├── search.py       # 双路召回 + RRF 融合
│   └── web.py          # FastAPI 界面
├── samples/            # 演示用课程笔记（数据库系统、操作系统）
├── eval/               # 验证样例、评测脚本、站点数据导出
├── site/               # 公开站点（静态页 + 已导出的真实检索结果）
└── requirements.txt
```

## 运行说明

### 1. 准备 seekdb

需要一个已启动的 seekdb 实例（默认走 MySQL 协议 2881 端口）。确认连通：

```bash
mysql -h127.0.0.1 -P2881 -uroot -e "SELECT 1"
```

### 2. 安装依赖

```bash
cd qoder
pip install -r requirements.txt
```

### 3. 配置

复制 `.env.example` 为 `.env`，填入 embedding 服务信息：

```
EMBED_BASE_URL=https://api.siliconflow.cn/v1
EMBED_API_KEY=你的 key
EMBED_MODEL=BAAI/bge-m3
EMBED_DIM=1024

SEEKDB_HOST=127.0.0.1
SEEKDB_PORT=2881
SEEKDB_USER=root
SEEKDB_PASSWORD=
SEEKDB_DATABASE=notes_rag
```

`.env` 已在 `.gitignore` 里，不会被提交。任何 OpenAI 兼容的 `/embeddings` 服务都能用，换模型时同步改 `EMBED_DIM`。

### 4. 导入笔记

建表在导入时自动完成（`CREATE DATABASE` + `CREATE TABLE` 都是幂等的）。

```bash
python -m src.ingest samples/database --course 数据库系统
python -m src.ingest samples/os --course 操作系统
```

同一个文件重复导入会按 `doc_id` 先删后插，不会产生重复块。

### 5. 检索

命令行：

```bash
python -m src.search "B+ 树为什么适合做索引"
python -m src.search "虚拟内存" --course 操作系统 --limit 5
```

Web 界面：

```bash
python -m uvicorn src.web:app --host 127.0.0.1 --port 8010
```

打开 <http://127.0.0.1:8010/>，输入问题即可；下拉框可按课程过滤。

## 验证样例

`eval/sample_queries.json` 里 8 条查询覆盖了两门课、四个主题，每条都标注了期望命中的源文件和章节号。跑评测：

```bash
python -m eval.run_eval
```

以本仓库 `samples/` 的两份笔记为语料，当前实测结果：

```
通过 8/8
```

8 条全部在前 3 位命中，其中「间隙锁为什么能解决幻读」排在第 2 位，其余排在第 1 位。每条结果都会标出来自「向量召回」「全文召回」还是两者都命中，方便判断是哪一路通道起了作用。

## 公开站点

站点地址：<https://course-notes-search-4stmu9183vl.qoder.zone/>

`site/` 是发布到 Qoder Sites 的静态展示页，内容包括项目说明、检索演示、seekdb 用法和验证结果表。页面上的检索框有两档：选中验证样例时展示本地 seekdb 返回的真实混合检索结果；自由输入时退化为浏览器内的全文字面匹配（向量通道需要连本地 seekdb 才能参与）。

演示数据由 `python -m eval.export_site_demo` 从当前数据库导出到 `site/demo-data.js`，重新导入语料后重跑一次即可刷新。

## 实现要点

**切块**：先按 Markdown 标题切节，章节路径压栈保存（`第 4 讲 索引与查询优化 / 4.2 B+ 树为什么适合做磁盘索引`），再在节内按段落聚合成约 700 字的块，相邻块保留 100 字重叠，避免答案被切断在边界上。

**双路召回**：向量通道取余弦距离最近的 40 条，全文通道取 `MATCH ... AGAINST` 得分最高的 40 条，各自按名次计 `1/(60+rank)` 后相加。RRF 只看名次不看原始分数，所以两路分数量纲不同也不影响融合。

**结果可解释**：每条结果都带源文件名、课程、章节路径、页码（PDF 才有）和命中通道，回到原文的成本很低。
