import type { RawDoc } from "./chunk";

type Entry = { path: string; course: string };

export const CORPUS_ENTRIES: Entry[] = [
  { path: "database/索引与查询优化.md", course: "数据库" },
  { path: "database/事务与隔离级别.md", course: "数据库" },
  { path: "database/日志与崩溃恢复.md", course: "数据库" },
  { path: "database/分布式事务与复制.md", course: "数据库" },
  { path: "os/虚拟内存与页面置换.md", course: "操作系统" },
  { path: "os/进程调度与并发.md", course: "操作系统" },
  { path: "net/TCP与拥塞控制.md", course: "计算机网络" },
  { path: "net/HTTP缓存与CDN.md", course: "计算机网络" },
];

export const CORPUS_BATCH_A = CORPUS_ENTRIES.slice(0, 4);
export const CORPUS_BATCH_B = CORPUS_ENTRIES.slice(4);

export const SAMPLE_QUERIES = [
  "B+ 树为什么适合做磁盘索引",
  "MVCC 快照读是怎么实现的",
  "redo log 和 undo log 的分工",
  "主从复制延迟会导致什么读问题",
  "优先级反转怎么解决",
  "TCP 为什么需要三次握手",
];

export async function loadEntries(entries: Entry[]): Promise<RawDoc[]> {
  const docs: RawDoc[] = [];
  for (const entry of entries) {
    const res = await fetch(`/corpus/${entry.path}`);
    if (!res.ok) throw new Error(`读取 ${entry.path} 失败`);
    docs.push({ path: entry.path, course: entry.course, text: await res.text() });
  }
  return docs;
}
