-- 课程笔记检索：一个 chunk 一行，同时建全文索引和向量索引
-- {db_name} / {dim} 由 src/db.py 在运行时填充
CREATE DATABASE IF NOT EXISTS {db_name} DEFAULT CHARACTER SET utf8mb4;

CREATE TABLE IF NOT EXISTS doc_chunks (
  id BIGINT NOT NULL AUTO_INCREMENT,
  doc_id VARCHAR(64) NOT NULL COMMENT '源文件标识',
  title VARCHAR(255) COMMENT '源文件名',
  course VARCHAR(64) COMMENT '课程/科目',
  chapter VARCHAR(255) COMMENT '章节路径',
  page_no INT COMMENT '页码，Markdown 为 0',
  chunk_no INT COMMENT '块序号',
  content TEXT COMMENT '原文片段',
  content_vec VECTOR({dim}),
  PRIMARY KEY (id),
  KEY idx_course (course),
  KEY idx_doc (doc_id),
  FULLTEXT KEY ft_content (content) WITH PARSER ik,
  VECTOR INDEX vidx_vec (content_vec) WITH (distance=cosine, type=hnsw)
);
