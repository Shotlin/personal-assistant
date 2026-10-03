/**
 * Attachments travel as a short block at the end of the message text:
 *
 *     Fix the layout shown here.
 *
 *     [Attached files]
 *     - /Users/you/Library/.../attachments/ab12-screenshot.png
 *
 * so the paths reach the agent unchanged, nothing about the history schema had
 * to change, and the chat can fold the block back into chips when it draws it.
 */
export const ATTACH_MARKER = "[Attached files]";

export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;
export const ACCEPT = ".png,.jpg,.jpeg,.gif,.webp,.pdf,.txt,.md,.json";

const IMAGE = /\.(png|jpe?g|gif|webp)$/i;

export const isImage = (name: string) => IMAGE.test(name);

export interface AttachedFile {
  path: string;
  name: string;
}

export function withAttachments(text: string, paths: string[]): string {
  if (paths.length === 0) return text;
  const list = paths.map((path) => `- ${path}`).join("\n");
  return `${text.trim()}\n\n${ATTACH_MARKER}\n${list}`;
}

/** Fold a trailing attachment block back into the text plus file chips. */
export function splitAttachments(text: string): { text: string; files: AttachedFile[] } {
  const index = text.lastIndexOf(`\n${ATTACH_MARKER}\n`);
  if (index < 0) return { text, files: [] };
  const lines = text
    .slice(index + ATTACH_MARKER.length + 2)
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.every((line) => line.startsWith("- /"))) return { text, files: [] };
  const files = lines.map((line) => {
    const path = line.slice(2);
    // The stored name is "<id>-<name>"; show only the part the user chose.
    const base = path.split("/").pop() ?? path;
    return { path, name: base.replace(/^[0-9a-f]{32}-/, "") };
  });
  return { text: text.slice(0, index).trimEnd(), files };
}

export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`Couldn't read ${file.name}.`));
    reader.onload = () => {
      const result = String(reader.result ?? "");
      resolve(result.slice(result.indexOf(",") + 1));
    };
    reader.readAsDataURL(file);
  });
}
