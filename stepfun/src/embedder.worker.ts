/// <reference lib="webworker" />

import { env, pipeline } from "@huggingface/transformers";

export const MODEL_ID = "Xenova/bge-small-zh-v1.5";
export const QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章：";

type Device = "webgpu" | "wasm";

type Incoming =
  | { type: "init"; device: Device; dtype: string }
  | { type: "embed"; id: number; texts: string[]; batchSize: number; kind: "doc" | "query" }
  | { type: "warmup"; id: number };

type Outgoing =
  | { type: "status"; stage: string; detail?: string }
  | { type: "download"; file: string; progress: number; loaded: number; total: number }
  | { type: "ready"; device: Device; dtype: string; loadMs: number; bytes: number; fallback: boolean }
  | { type: "progress"; id: number; done: number; total: number }
  | { type: "result"; id: number; vectors: number[][]; ms: number; batchMs: number[] }
  | { type: "error"; id?: number; message: string };

let extractor: any = null;
let activeKey = "";
const fileBytes = new Map<string, number>();

function post(msg: Outgoing) {
  (self as unknown as Worker).postMessage(msg);
}

async function hasLocalModel() {
  try {
    const res = await fetch(`/models/${MODEL_ID}/config.json`, { method: "HEAD" });
    const type = res.headers.get("content-type") || "";
    return res.ok && type.includes("json");
  } catch {
    return false;
  }
}

async function build(device: Device, dtype: string) {
  fileBytes.clear();
  const local = await hasLocalModel();
  env.allowLocalModels = local;
  env.localModelPath = "/models/";
  env.allowRemoteModels = !local;
  env.remoteHost = "https://hf-mirror.com";
  env.useBrowserCache = false;

  const started = performance.now();
  const ort = ((env as any).backends.onnx ||= {});
  ort.wasm = { ...(ort.wasm || {}), wasmPaths: "/ort/", numThreads: 1, proxy: false };
  extractor = await pipeline("feature-extraction", MODEL_ID, {
    device,
    dtype: dtype as any,
    progress_callback: (p: any) => {
      if (p?.status === "progress" && p.file) {
        if (p.total) fileBytes.set(p.file, Math.max(fileBytes.get(p.file) || 0, p.total));
        post({
          type: "download",
          file: p.file,
          progress: p.progress || 0,
          loaded: p.loaded || 0,
          total: p.total || 0,
        });
      } else if (p?.status === "ready") {
        post({ type: "status", stage: "ready", detail: p.file });
      }
    },
  });
  const loadMs = performance.now() - started;
  const bytes = [...fileBytes.values()].reduce((a, b) => a + b, 0);
  return { loadMs, bytes };
}

async function init(device: Device, dtype: string): Promise<void> {
  const key = `${device}:${dtype}`;
  if (extractor && activeKey === key) {
    post({ type: "ready", device, dtype, loadMs: 0, bytes: 0, fallback: false });
    return;
  }
  activeKey = key;
  extractor = null;
  post({ type: "status", stage: "loading", detail: `${device} / ${dtype}` });
  try {
    const { loadMs, bytes } = await build(device, dtype);
    post({ type: "ready", device, dtype, loadMs, bytes, fallback: false });
  } catch (err: any) {
    console.error("[embedder] init failed", device, dtype, err?.stack || err);
    if (device === "webgpu") {
      post({ type: "status", stage: "fallback", detail: String(err?.message || err) });
      activeKey = "wasm:int8";
      try {
        const { loadMs, bytes } = await build("wasm", "int8");
        post({ type: "ready", device: "wasm", dtype: "int8", loadMs, bytes, fallback: true });
        return;
      } catch (fallbackErr: any) {
        console.error("[embedder] wasm fallback failed", fallbackErr?.stack || fallbackErr);
        post({ type: "error", message: `WASM 回退失败：${fallbackErr?.message || fallbackErr}` });
        return;
      }
    }
    post({ type: "error", message: String(err?.message || err) });
  }
}

async function embed(id: number, texts: string[], batchSize: number, kind: "doc" | "query") {
  if (!extractor) {
    post({ type: "error", id, message: "模型尚未就绪" });
    return;
  }
  const started = performance.now();
  const vectors: number[][] = [];
  const batchMs: number[] = [];
  const size = Math.max(1, Math.min(batchSize || 8, 64));
  for (let i = 0; i < texts.length; i += size) {
    const slice = texts
      .slice(i, i + size)
      .map((t) => (kind === "query" ? QUERY_PREFIX + t : t));
    const t0 = performance.now();
    const out = await extractor(slice, { pooling: "cls", normalize: true });
    vectors.push(...(out.tolist() as number[][]));
    batchMs.push(Math.round((performance.now() - t0) * 10) / 10);
    post({ type: "progress", id, done: Math.min(i + size, texts.length), total: texts.length });
  }
  post({
    type: "result",
    id,
    vectors,
    ms: Math.round((performance.now() - started) * 10) / 10,
    batchMs,
  });
}

self.onmessage = async (event: MessageEvent<Incoming>) => {
  const msg = event.data;
  if (msg.type === "init") {
    await init(msg.device, msg.dtype);
  } else if (msg.type === "warmup") {
    try {
      await extractor(["预热"], { pooling: "cls", normalize: true });
      post({ type: "result", id: msg.id, vectors: [], ms: 0, batchMs: [] });
    } catch (err: any) {
      post({ type: "error", id: msg.id, message: String(err?.message || err) });
    }
  } else if (msg.type === "embed") {
    try {
      await embed(msg.id, msg.texts, msg.batchSize, msg.kind);
    } catch (err: any) {
      post({ type: "error", id: msg.id, message: String(err?.message || err) });
    }
  }
};
