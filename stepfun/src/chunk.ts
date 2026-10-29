export type RawDoc = { path: string; course: string; text: string };

export type Chunk = {
  id: string;
  document: string;
  metadata: {
    source: string;
    title: string;
    chapter: string;
    course: string;
    chunk: number;
    chars: number;
  };
};

const SIZE = 420;
const OVERLAP = 80;

function sentences(text: string) {
  return text
    .split(/(?<=[。！？；!?;])/)
    .map((s) => s.replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

function pack(section: string, body: string, size: number, overlap: number) {
  const pieces: string[] = [];
  const units = sentences(body);
  let buffer = "";
  for (const unit of units) {
    if (buffer.length + unit.length > size && buffer.length > 0) {
      pieces.push(buffer);
      buffer = buffer.slice(Math.max(0, buffer.length - overlap));
    }
    buffer += unit;
  }
  if (buffer.trim()) pieces.push(buffer);
  return pieces.map((p) => p.trim()).filter((p) => p.length > 0);
}

export function chunkDocument(doc: RawDoc): Chunk[] {
  const lines = doc.text.replace(/\r\n/g, "\n").split("\n");
  const source = doc.path.split("/").pop() || doc.path;
  let title = source.replace(/\.md$/i, "");
  let chapter = "";
  const sections: { chapter: string; body: string }[] = [];
  let buffer: string[] = [];

  const flush = () => {
    const body = buffer.join("\n").trim();
    if (body) sections.push({ chapter, body });
    buffer = [];
  };

  for (const line of lines) {
    const h1 = line.match(/^#\s+(.+)/);
    const h2 = line.match(/^##\s+(.+)/);
    if (h1) {
      title = h1[1].trim();
      continue;
    }
    if (h2) {
      flush();
      chapter = h2[1].trim();
      continue;
    }
    buffer.push(line);
  }
  flush();

  const chunks: Chunk[] = [];
  const seen = new Set<string>();
  for (const section of sections) {
    for (const piece of pack(section.chapter, section.body, SIZE, OVERLAP)) {
      const index = chunks.length;
      const key = `${source}#${index}`;
      if (seen.has(key)) continue;
      seen.add(key);
      chunks.push({
        id: key,
        document: section.chapter ? `${section.chapter}\n${piece}` : piece,
        metadata: {
          source,
          title,
          chapter: section.chapter,
          course: doc.course,
          chunk: index,
          chars: piece.length,
        },
      });
    }
  }
  return chunks;
}
