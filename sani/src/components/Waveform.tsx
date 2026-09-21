import { useEffect, useRef } from "react";

interface WaveformProps {
  levels: number[];
  active: boolean;
  color: string;
  /**
   * Split mode: the reference layout puts a bar group on each side of the mic
   * button. Both groups keep the newest sample on the mic side.
   */
  side: "left" | "right";
}

const PITCH = 5;
const BAR_WIDTH = 3;
const BASELINE = 3;
/** Floor for a live bar. Below this a quiet room collapses to 3px squares,
 *  which read as a dashed rule rather than a bar group. */
const BAR_FLOOR = 9;
const MAX_SAMPLES = 48;

/**
 * Real microphone RMS levels. No fake animation: zero input is a flat line.
 */
export default function Waveform({ levels, active, color, side }: WaveformProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const levelsRef = useRef(levels);
  levelsRef.current = levels;
  const activeRef = useRef(active);
  activeRef.current = active;
  const animRef = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const draw = () => {
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
        canvas.width = w * dpr;
        canvas.height = h * dpr;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const data = levelsRef.current.slice(-MAX_SAMPLES);
      const count = Math.max(4, Math.floor(w / PITCH));
      const midY = h / 2;
      const startX = (w - (count * PITCH - (PITCH - BAR_WIDTH))) / 2;

      ctx.fillStyle = color;
      if (!activeRef.current || data.length === 0) {
        // No live signal: one continuous baseline reads as "silent". A row of
        // zero-height bars just looks like a broken dashed rule.
        const x0 = startX;
        const x1 = w - startX;
        ctx.beginPath();
        ctx.roundRect(x0, midY - BASELINE / 2, x1 - x0, BASELINE, BASELINE / 2);
        ctx.fill();
      } else {
        const last = data.length - 1;
        for (let i = 0; i < count; i++) {
          // Nearest-index resample so the group always spans the visible width,
          // with the newest sample always on the mic side of the strip.
          const t = i / Math.max(1, count - 1);
          const src = data[Math.round((side === "left" ? t : 1 - t) * last)];
          const amplitude = Math.max(0.02, Math.min(1, src * 6.5));
          const barH = Math.max(BAR_FLOOR, amplitude * (h - 6));
          const x = startX + i * PITCH;
          ctx.beginPath();
          ctx.roundRect(x, midY - barH / 2, BAR_WIDTH, barH, Math.min(BAR_WIDTH, barH) / 2);
          ctx.fill();
        }
      }
      animRef.current = requestAnimationFrame(draw);
    };
    animRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(animRef.current);
  }, [color, side]);

  return <canvas ref={canvasRef} style={{ width: "100%", height: "100%", display: "block" }} />;
}
