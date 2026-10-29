export type Device = "webgpu" | "wasm";

export type ReadyInfo = {
  device: Device;
  dtype: string;
  loadMs: number;
  bytes: number;
  fallback: boolean;
};

type Pending = {
  resolve: (value: any) => void;
  reject: (err: Error) => void;
  onProgress?: (done: number, total: number) => void;
};

export class Embedder {
  private worker: Worker;
  private seq = 0;
  private pending = new Map<number, Pending>();
  private initResolve: ((info: ReadyInfo) => void) | null = null;
  private initReject: ((err: Error) => void) | null = null;

  onStatus: (stage: string, detail?: string) => void = () => {};
  onDownload: (file: string, progress: number, loaded: number, total: number) => void = () => {};

  constructor() {
    this.worker = new Worker(new URL("./embedder.worker.ts", import.meta.url), {
      type: "module",
    });
    this.worker.onmessage = (event: MessageEvent) => {
      const msg = event.data;
      switch (msg.type) {
        case "status":
          this.onStatus(msg.stage, msg.detail);
          break;
        case "download":
          this.onDownload(msg.file, msg.progress, msg.loaded, msg.total);
          break;
        case "ready":
          this.initResolve?.({
            device: msg.device,
            dtype: msg.dtype,
            loadMs: msg.loadMs,
            bytes: msg.bytes,
            fallback: msg.fallback,
          });
          this.initResolve = null;
          this.initReject = null;
          break;
        case "progress":
          this.pending.get(msg.id)?.onProgress?.(msg.done, msg.total);
          break;
        case "result": {
          const entry = this.pending.get(msg.id);
          if (!entry) return;
          this.pending.delete(msg.id);
          entry.resolve({ vectors: msg.vectors, ms: msg.ms, batchMs: msg.batchMs });
          break;
        }
        case "error": {
          if (msg.id === undefined) {
            this.initReject?.(new Error(msg.message));
            this.initResolve = null;
            this.initReject = null;
            break;
          }
          const entry = this.pending.get(msg.id);
          if (!entry) return;
          this.pending.delete(msg.id);
          entry.reject(new Error(msg.message));
          break;
        }
      }
    };
  }

  init(device: Device, dtype: string): Promise<ReadyInfo> {
    return new Promise((resolve, reject) => {
      this.initResolve = resolve;
      this.initReject = reject;
      this.worker.postMessage({ type: "init", device, dtype });
    });
  }

  embed(
    texts: string[],
    batchSize: number,
    kind: "doc" | "query",
    onProgress?: (done: number, total: number) => void,
  ): Promise<{ vectors: number[][]; ms: number; batchMs: number[] }> {
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, onProgress });
      this.worker.postMessage({ type: "embed", id, texts, batchSize, kind });
    });
  }
}
