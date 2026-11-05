// Canvas player for rendered sprite frames, at the profile's fps.

import { useEffect, useRef, useState } from "react";

interface Props {
  /** Frame image URLs in order. */
  frames: string[];
  fps: number;
  /** Draw mirrored, as Godot does with flip_h for mirrored directions. */
  flip?: boolean;
  /** Nearest-neighbour scaling for the pixel look. */
  pixelated?: boolean;
}

export function SpritePlayer({ frames, fps, flip, pixelated }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [images, setImages] = useState<{ key: string; list: HTMLImageElement[] } | null>(null);
  const [failedKey, setFailedKey] = useState<string | null>(null);
  const key = frames.join("|");

  useEffect(() => {
    let cancelled = false;
    Promise.all(
      frames.map(
        (src) =>
          new Promise<HTMLImageElement>((resolve, reject) => {
            const img = new Image();
            img.onload = () => resolve(img);
            img.onerror = reject;
            img.src = src;
          }),
      ),
    )
      .then((list) => !cancelled && setImages({ key, list }))
      .catch(() => !cancelled && setFailedKey(key));
    return () => {
      cancelled = true;
    };
    // frames is described by key
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const ready = images?.key === key ? images.list : null;

  useEffect(() => {
    const el = canvas.current;
    if (!el || !ready?.length) return;
    const ctx = el.getContext("2d");
    if (!ctx) return;
    el.width = ready[0].naturalWidth;
    el.height = ready[0].naturalHeight;
    ctx.imageSmoothingEnabled = !pixelated;
    const started = performance.now();
    let raf = 0;
    let last = -1;
    const draw = (now: number) => {
      const index = Math.floor(((now - started) / 1000) * fps) % ready.length;
      if (index !== last) {
        last = index;
        ctx.save();
        ctx.clearRect(0, 0, el.width, el.height);
        if (flip) {
          ctx.translate(el.width, 0);
          ctx.scale(-1, 1);
        }
        ctx.drawImage(ready[index], 0, 0);
        ctx.restore();
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [ready, fps, flip, pixelated]);

  return (
    <div className="stage stage--sprite">
      <canvas ref={canvas} aria-label="Sprite animation" />
      {!ready && (
        <div className="stage__overlay">{failedKey === key ? "The frames could not be loaded." : "Loading frames…"}</div>
      )}
    </div>
  );
}
