import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const MODEL = "Xenova/bge-small-zh-v1.5";
const BASE = process.env.MODEL_MIRROR || "https://hf-mirror.com";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outDir = path.join(root, "public", "models", MODEL);
const files = [
  "config.json",
  "tokenizer.json",
  "tokenizer_config.json",
  "special_tokens_map.json",
  "vocab.txt",
  "onnx/model_fp32.onnx",
  "onnx/model_fp16.onnx",
  "onnx/model_int8.onnx",
];

fs.mkdirSync(path.join(outDir, "onnx"), { recursive: true });

for (const file of files) {
  // 同一份权重在 HF 上可能命名为 model.onnx，逐个尝试。
  const candidates = file === "onnx/model_fp32.onnx" ? ["onnx/model.onnx", file] : [file];
  let saved = false;
  for (const candidate of candidates) {
    const target = path.join(outDir, candidate);
    if (fs.existsSync(target) && fs.statSync(target).size > 0) {
      console.log(`[model] 已存在 ${candidate}`);
      saved = true;
      break;
    }
    const url = `${BASE}/${MODEL}/resolve/main/${candidate}`;
    try {
      const res = await fetch(url, { redirect: "follow" });
      if (!res.ok) {
        console.warn(`[model] ${candidate} -> HTTP ${res.status}`);
        continue;
      }
      const buffer = Buffer.from(await res.arrayBuffer());
      if (buffer.length < 100) {
        console.warn(`[model] ${candidate} 内容异常，跳过`);
        continue;
      }
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, buffer);
      console.log(`[model] ${candidate} ${(buffer.length / 1024 / 1024).toFixed(1)} MB`);
      saved = true;
      break;
    } catch (err) {
      console.warn(`[model] ${candidate} 下载失败：${err.message}`);
    }
  }
  if (!saved) {
    console.error(`[model] 缺少 ${file}，请检查网络或设置 MODEL_MIRROR`);
    process.exitCode = 1;
  }
}

// 让 Transformers.js 按标准文件名找到 fp32 权重
const fp32Alt = path.join(outDir, "onnx", "model.onnx");
const fp32 = path.join(outDir, "onnx", "model_fp32.onnx");
if (!fs.existsSync(fp32Alt) && fs.existsSync(fp32)) {
  fs.copyFileSync(fp32, fp32Alt);
}
console.log(`[model] 模型目录：${outDir}`);
