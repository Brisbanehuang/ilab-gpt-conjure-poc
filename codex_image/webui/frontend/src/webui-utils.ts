export function escapeHtml(value: unknown): string {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

export function cssEscape(value: unknown): string {
  const text = String(value || "");
  if (window.CSS?.escape) return window.CSS.escape(text);
  return text.replace(/["\\]/g, "\\$&");
}

export function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message || fallback : fallback;
}

export function prefersReducedMotion(): boolean {
  return Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
}

// Download-name extension for a task output: the record's own format, else the task's requested
// output_format, else png.
export function outputDownloadExtension(task: any, url: unknown): string {
  const record = Array.isArray(task?.outputs)
    ? task.outputs.find((item: any) => item?.url && String(item.url) === String(url || ""))
    : null;
  const format = String(record?.format || task?.params?.output_format || "png").toLowerCase();
  if (format === "jpeg" || format === "jpg") return "jpg";
  return ["png", "webp", "gif"].includes(format) ? format : "png";
}
