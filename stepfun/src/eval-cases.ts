export type EvalCase = {
  query: string;
  expectSource: string;
  note: string;
};

export const EVAL_CASES: EvalCase[] = [
  { query: "B+ 树为什么适合做磁盘索引", expectSource: "索引与查询优化.md", note: "关键词 + 语义" },
  { query: "覆盖索引如何减少回表", expectSource: "索引与查询优化.md", note: "关键词" },
  { query: "MVCC 快照读是怎么实现的", expectSource: "事务与隔离级别.md", note: "语义改写" },
  { query: "什么是间隙锁", expectSource: "事务与隔离级别.md", note: "关键词" },
  { query: "checkpoint 之后日志为什么可以截断", expectSource: "日志与崩溃恢复.md", note: "关键词 + 语义" },
  { query: "主从复制延迟会导致什么读问题", expectSource: "分布式事务与复制.md", note: "语义改写" },
  { query: "优先级反转怎么解决", expectSource: "进程调度与并发.md", note: "语义改写" },
  { query: "LRU 页面置换的缺点", expectSource: "虚拟内存与页面置换.md", note: "关键词 + 语义" },
  { query: "TCP 为什么需要三次握手", expectSource: "TCP与拥塞控制.md", note: "关键词 + 语义" },
  { query: "CDN 缓存穿透和雪崩的区别", expectSource: "HTTP缓存与CDN.md", note: "语义改写" },
];
