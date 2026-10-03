/**
 * When Sani needs the user to choose, it ends its message with:
 *
 *     [Options]
 *     - Indigo, calm and trustworthy
 *     - Charcoal and amber
 *
 * The chat folds that block into tappable choices. Tapping one sends it as the
 * user's next message, so it is just a faster way to type the answer.
 */
export const OPTIONS_MARKER = "[Options]";

export function splitOptions(text: string): { text: string; options: string[] } {
  const index = text.lastIndexOf(OPTIONS_MARKER);
  if (index < 0) return { text, options: [] };
  const lines = text
    .slice(index + OPTIONS_MARKER.length)
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const options = lines.filter((line) => line.startsWith("- ")).map((line) => line.slice(2).trim());
  // Anything that is not a plain list after the marker means it was not a choice block.
  if (options.length < 2 || options.length !== lines.length) return { text, options: [] };
  return { text: text.slice(0, index).trimEnd(), options: options.slice(0, 5) };
}

/** While streaming, hide a half-arrived options block rather than show raw markup. */
export function hideOptionsWhileStreaming(text: string): string {
  const index = text.lastIndexOf(OPTIONS_MARKER);
  return index < 0 ? text : text.slice(0, index).trimEnd();
}
