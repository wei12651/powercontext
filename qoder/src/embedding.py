"""调用 OpenAI 兼容的 /embeddings 接口把文本转成向量。"""
import json
import time
import urllib.error
import urllib.request

from . import config


def embed_texts(texts, batch_size: int = 16):
    vectors = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(_embed_batch(texts[start:start + batch_size]))
    return vectors


def _embed_batch(batch):
    payload = json.dumps(
        {"model": config.EMBED_MODEL, "input": batch, "encoding_format": "float"}
    ).encode("utf-8")
    request = urllib.request.Request(
        config.EMBED_BASE_URL + "/embeddings",
        data=payload,
        headers={
            "Authorization": "Bearer " + config.EMBED_API_KEY,
            "Content-Type": "application/json",
        },
    )
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=90) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            items = sorted(body["data"], key=lambda item: item.get("index", 0))
            return [item["embedding"] for item in items]
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.read().decode('utf-8', 'ignore')[:300]}"
        except Exception as exc:  # 网络抖动重试
            last_error = repr(exc)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"embedding 调用失败: {last_error}")


def embed_query(text: str):
    return embed_texts([text])[0]
