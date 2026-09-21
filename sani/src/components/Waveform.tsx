import { useEffect, useRef } from "react";

interface WaveformProps {
  levels: number[];
  active: boolean;
  color: string;
  /**
   * Split mode: the reference layout puts a bar group on each side of the mic
   * button. "left" mirrors the buffer so the newest sample sits next to the
   * button; "right" reads it forwards. Omit for a single full-width strip.
   */
  side?: "left" | "right";
  bars?: number;
}

/**
 * Real microphone RMS levels. No fake animation: zero input is a flat line.
 */
export default function Waveform({ levels, active, color, side, bars = 24 }: WaveformProps) {
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

      // Newest samples last; each half of a split strip reads the recent window.
      const data = levelsRef.current;
      const window = side ? Math.min(data.length, bars) : data.length;
      const recent = data.slice(Math.max(0, data.length - window));
      if (side === "left") recent.reverse();

      const gap = 3;
      const barW = Math.max(1.5, (w - gap * (bars - 1)) / bars);
      const midY = h / 2;

      ctx.fillStyle = color;
      for (let i = 0; i < bars; i++) {
        const src = recent.length > 0 ? recent[Math.min(i, recent.length - 1)] : 0;
        const amplitude = Math.max(0.02, Math.min(1, src * 6.5));
        const barH = activeRef.current ? amplitude * (h - 6) : 2;
        const x = i * (barW + gap);
        const y = midY - barH / 2;
        const r = Math.min(barW / 2, 2);
        ctx.beginPath();
        ctx.roundRect(x, y, barW, barH, r);
        ctx.fill();
      }
      animRef.current = requestAnimationFrame(draw);
    };
    animRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(animRef.current);
  }, [color, side, bars]);

  return <canvas ref={canvasRef} style={{ width: "100%", height: "100%", display: "block" }} />;
}
