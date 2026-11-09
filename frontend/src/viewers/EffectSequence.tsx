// Canvas preview of a skill effect as the game plays it: a caster on the left, a target on the
// right; the cast, the projectile flying with its trail of particles, the impact. Bursts play at
// the target and auras loop on the caster. Glowing parts ("add") are drawn additively.

import { useEffect, useRef, useState } from "react";

export interface SequencePart {
  part: "cast" | "projectile" | "impact" | "aura";
  frames: string[];
  loop: boolean;
  blend: "add" | "mix";
  scale: number;
}

interface Props {
  kind: "projectile" | "burst" | "aura";
  parts: SequencePart[];
  fps: number;
  palette: string[];
  /** Pixels per second in the game; the preview's distance is 600 game pixels. */
  travelSpeed: number;
  particles: { enabled: boolean; amount: number; lifetime: number; speed: number; spread: number; gravity: number; size: number };
}

const WIDTH = 900;
const HEIGHT = 420;
const GROUND = 330;
const CASTER = { x: 150, y: GROUND - 90 };
const TARGET = { x: 750, y: GROUND - 90 };
const BASE = 160; // a scale-1 part's size in the preview, pixels
const PAUSE = 0.6; // seconds between runs

type Loaded = Record<string, HTMLImageElement[]>;

function figure(ctx: CanvasRenderingContext2D, x: number) {
  ctx.fillStyle = "#3a3646";
  ctx.beginPath();
  ctx.arc(x, GROUND - 150, 18, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillRect(x - 16, GROUND - 128, 32, 70);
  ctx.fillRect(x - 14, GROUND - 58, 11, 58);
  ctx.fillRect(x + 3, GROUND - 58, 11, 58);
}

export function EffectSequence({ kind, parts, fps, palette, travelSpeed, particles }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [loaded, setLoaded] = useState<{ key: string; images: Loaded } | null>(null);
  const key = parts.map((p) => p.frames.join("|")).join("#");

  useEffect(() => {
    let cancelled = false;
    const load = (src: string) =>
      new Promise<HTMLImageElement>((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = reject;
        img.src = src;
      });
    Promise.all(parts.map(async (p) => [p.part, await Promise.all(p.frames.map(load))] as const))
      .then((entries) => !cancelled && setLoaded({ key, images: Object.fromEntries(entries) }))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // parts is described by key
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const images = loaded?.key === key ? loaded.images : null;

  useEffect(() => {
    const el = canvas.current;
    const ctx = el?.getContext("2d");
    if (!el || !ctx || !images) return;
    el.width = WIDTH;
    el.height = HEIGHT;
    const byName = Object.fromEntries(parts.map((p) => [p.part, p]));
    const seconds = (name: string) => (images[name]?.length ?? 0) / fps;
    const flight = 600 / Math.max(100, travelSpeed);
    const cast = byName.cast ? seconds("cast") : 0;
    const impact = byName.impact ? seconds("impact") : 0;
    const total = kind === "aura" ? Math.max(seconds("aura"), 1) : cast + (kind === "projectile" ? flight : 0) + impact + PAUSE;
    const dots: { x: number; y: number; vx: number; vy: number; born: number }[] = [];
    let lastSpawn = 0;
    const started = performance.now();

    const draw = (name: string, t: number, x: number, y: number, angle = 0) => {
      const part = byName[name];
      const list = images[name];
      if (!part || !list?.length) return;
      const n = Math.floor(t * fps);
      const index = part.loop ? n % list.length : Math.min(n, list.length - 1);
      const size = BASE * part.scale;
      ctx.save();
      ctx.globalCompositeOperation = part.blend === "add" ? "lighter" : "source-over";
      ctx.translate(x, y);
      ctx.rotate(angle);
      ctx.drawImage(list[index], -size / 2, -size / 2, size, size);
      ctx.restore();
    };

    let raf = 0;
    const frame = (now: number) => {
      const time = (now - started) / 1000;
      const t = time % total;
      ctx.globalCompositeOperation = "source-over";
      ctx.fillStyle = "#15131c";
      ctx.fillRect(0, 0, WIDTH, HEIGHT);
      ctx.fillStyle = "#211e2b";
      ctx.fillRect(0, GROUND, WIDTH, HEIGHT - GROUND);
      figure(ctx, CASTER.x - 40);
      if (kind !== "aura") figure(ctx, TARGET.x + 40);

      if (kind === "aura") {
        draw("aura", t, CASTER.x - 40, GROUND - 90);
      } else {
        if (t < cast) draw("cast", t, CASTER.x, CASTER.y);
        const flying = kind === "projectile" && t >= cast && t < cast + flight;
        if (flying) {
          const k = (t - cast) / flight;
          const x = CASTER.x + (TARGET.x - CASTER.x) * k;
          if (particles.enabled && time - lastSpawn > particles.lifetime / Math.max(4, particles.amount)) {
            lastSpawn = time;
            const spread = ((Math.random() - 0.5) * particles.spread * Math.PI) / 180;
            const speed = particles.speed * (0.5 + Math.random() * 0.5);
            dots.push({ x, y: CASTER.y, vx: -Math.cos(spread) * speed, vy: Math.sin(spread) * speed, born: time });
          }
          draw("projectile", t - cast, x, CASTER.y);
        }
        const hitAt = cast + (kind === "projectile" ? flight : 0);
        if (t >= hitAt && t < hitAt + impact) draw("impact", t - hitAt, TARGET.x, TARGET.y);
      }

      // The trail: soft dots in the palette's first colour, fading out over their lifetime.
      ctx.globalCompositeOperation = "lighter";
      for (let i = dots.length - 1; i >= 0; i--) {
        const d = dots[i];
        const age = time - d.born;
        if (age > particles.lifetime) {
          dots.splice(i, 1);
          continue;
        }
        const life = 1 - age / particles.lifetime;
        const px = d.x + d.vx * age;
        const py = d.y + d.vy * age + 0.5 * particles.gravity * age * age;
        const radius = particles.size * (0.6 + life);
        const glow = ctx.createRadialGradient(px, py, 0, px, py, radius);
        glow.addColorStop(0, palette[0] ?? "#ffffff");
        glow.addColorStop(1, "transparent");
        ctx.globalAlpha = life;
        ctx.fillStyle = glow;
        ctx.fillRect(px - radius, py - radius, radius * 2, radius * 2);
      }
      ctx.globalAlpha = 1;
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [images, parts, fps, kind, palette, travelSpeed, particles]);

  return (
    <div className="stage stage--sprite stage--sequence">
      <canvas ref={canvas} />
      {!images && <div className="stage__overlay">Loading the effect…</div>}
    </div>
  );
}
