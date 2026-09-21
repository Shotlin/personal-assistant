import { useEffect, useRef } from "react";

interface WaveformProps {
  levels: number[];
  active: boolean;
  color: string;
}

/**
 * Real microphone RMS levels, mirrored around the center like a quiet
 * voice visualizer. No fake animation: zero input is a flat line.
 */
export default function Waveform({ levels, active, color }: WaveformProps) {
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

      const data = levelsRef.current;
      const barCount = 44;
      const gap = 2.5;
      const barW = (w - gap * (barCount - 1)) / barCount;
      const midY = h / 2;

      ctx.fillStyle = color;
      for (let i = 0; i < barCount; i++) {
        // sample the rolling level buffer, newest at the right edge
        const src = data.length > 0 ? data[Math.min(i, data.length - 1)] : 0;
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
  }, [color]);

  return <canvas ref={canvasRef} style={{ width: "100%", height: "100%", display: "block" }} />;
}
