// Pure GIF output helpers (no DOM access) so they can be unit-tested in isolation.
//
// The upstream renders every GIF frame into one sprite sheet. `size` is the single-frame size and
// the sheet grid (columns x rows) depends on the frame count. A request is valid when at least one
// candidate grid keeps the sheet within GIF_SHEET_MAX_SIDE per side and GIF_SHEET_MAX_PIXELS in total.

export const GIF_OUTPUT_FORMAT = "gif";
export const GIF_DEFAULT_FRAME_COUNT = 9;
export const GIF_MIN_FRAME_COUNT = 2;
export const GIF_MAX_FRAME_COUNT = 16;
export const GIF_DEFAULT_FRAME_DURATION_MS = 200;
export const GIF_MIN_FRAME_DURATION_MS = 20;
export const GIF_MAX_FRAME_DURATION_MS = 2000;
export const GIF_FRAME_DURATION_STEP_MS = 10;
export const GIF_SHEET_MAX_SIDE = 3840;
export const GIF_SHEET_MAX_PIXELS = 8294400;
export const GIF_FRAME_MAX_SIDE = 1024;
export const GIF_FRAME_STEP = 16;
export const GIF_ALLOWED_QUALITIES = ["low", "medium"];
export const GIF_DEFAULT_QUALITY = "medium";

export type GifSheetGrid = [columns: number, rows: number];

export function gifModelSupported(model: unknown): boolean {
  return /^gpt-image-2\.6-/.test(String(model || "").trim());
}

export function gifSheetGrids(frameCount: number): GifSheetGrid[] {
  if (!Number.isInteger(frameCount) || frameCount < GIF_MIN_FRAME_COUNT || frameCount > GIF_MAX_FRAME_COUNT) return [];
  if (frameCount <= 8) return [[4, 2], [2, 4]];
  if (frameCount === 9) return [[3, 3]];
  if (frameCount <= 12) return [[4, 3], [3, 4], [4, 4]];
  return [[4, 4]];
}

export function gifSheetGridFits(width: number, height: number, [columns, rows]: GifSheetGrid): boolean {
  return columns * width <= GIF_SHEET_MAX_SIDE
    && rows * height <= GIF_SHEET_MAX_SIDE
    && columns * width * rows * height <= GIF_SHEET_MAX_PIXELS;
}

export function gifSheetFits(width: number, height: number, frameCount: number): boolean {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0) return false;
  return gifSheetGrids(frameCount).some((grid) => gifSheetGridFits(width, height, grid));
}

function floorToStep(value: number): number {
  return Math.floor(value / GIF_FRAME_STEP + 1e-9) * GIF_FRAME_STEP;
}

/**
 * Largest single-frame size for a ratio and frame count that satisfies the sheet rule, keeps the
 * longest side within GIF_FRAME_MAX_SIDE (the gateway's 1K billing tier) and rounds both sides down
 * to multiples of 16. Invalid ratios fall back to 1:1; frame counts are clamped to 2-16.
 */
export function gifFrameSizeFor(ratioW: number, ratioH: number, frameCount: number): [number, number] {
  const validRatio = Number.isFinite(ratioW) && Number.isFinite(ratioH) && ratioW > 0 && ratioH > 0;
  const rw = validRatio ? ratioW : 1;
  const rh = validRatio ? ratioH : 1;
  const frames = normalizeGifFrameCount(frameCount);
  const target = rw / rh;
  let best: { width: number; height: number; area: number; error: number } | null = null;
  for (const [columns, rows] of gifSheetGrids(frames)) {
    const scale = Math.min(
      GIF_FRAME_MAX_SIDE / Math.max(rw, rh),
      GIF_SHEET_MAX_SIDE / (columns * rw),
      GIF_SHEET_MAX_SIDE / (rows * rh),
      Math.sqrt(GIF_SHEET_MAX_PIXELS / (columns * rows * rw * rh)),
    );
    const width = Math.max(GIF_FRAME_STEP, floorToStep(scale * rw));
    const height = Math.max(GIF_FRAME_STEP, floorToStep(scale * rh));
    if (!gifSheetGridFits(width, height, [columns, rows])) continue;
    const area = width * height;
    const error = Math.abs(Math.log(width / height / target));
    if (!best || area > best.area || (area === best.area && error < best.error)) {
      best = { width, height, area, error };
    }
  }
  return best ? [best.width, best.height] : [GIF_FRAME_STEP, GIF_FRAME_STEP];
}

export function parseGifRatio(ratio: unknown): [number, number] {
  const match = String(ratio || "").trim().match(/^(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)$/);
  if (!match) return [1, 1];
  const width = Number(match[1]);
  const height = Number(match[2]);
  return width > 0 && height > 0 ? [width, height] : [1, 1];
}

export function normalizeGifFrameCount(value: unknown): number {
  const parsed = Math.round(Number(value));
  if (String(value ?? "").trim() === "" || !Number.isFinite(parsed)) return GIF_DEFAULT_FRAME_COUNT;
  return Math.min(GIF_MAX_FRAME_COUNT, Math.max(GIF_MIN_FRAME_COUNT, parsed));
}

export function normalizeGifFrameDuration(value: unknown): number {
  const parsed = Number(value);
  if (String(value ?? "").trim() === "" || !Number.isFinite(parsed)) return GIF_DEFAULT_FRAME_DURATION_MS;
  const stepped = Math.round(parsed / GIF_FRAME_DURATION_STEP_MS) * GIF_FRAME_DURATION_STEP_MS;
  return Math.min(GIF_MAX_FRAME_DURATION_MS, Math.max(GIF_MIN_FRAME_DURATION_MS, stepped));
}
