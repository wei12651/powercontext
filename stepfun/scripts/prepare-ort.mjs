import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = path.join(root, "node_modules", "onnxruntime-web", "dist");
const out = path.join(root, "public", "ort");
const wanted = [
  "ort-wasm-simd-threaded.wasm",
  "ort-wasm-simd-threaded.asyncify.wasm",
  "ort-wasm-simd-threaded.jsep.wasm",
  "ort-wasm-simd-threaded.jspi.wasm",
];

if (!fs.existsSync(src)) {
  console.warn("[ort] 未找到 onnxruntime-web，跳过");
  process.exit(0);
}
fs.mkdirSync(out, { recursive: true });
let copied = 0;
for (const file of wanted) {
  const from = path.join(src, file);
  if (!fs.existsSync(from)) continue;
  fs.copyFileSync(from, path.join(out, file));
  copied += 1;
}
console.log(`[ort] 已准备 ${copied} 个 WASM 后端文件到 public/ort`);
