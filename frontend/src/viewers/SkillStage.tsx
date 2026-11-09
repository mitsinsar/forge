// A hero playing a skill animation with the skill's effect, in 3D: the cast glows at the right
// hand, the projectile leaves the hand at the release time and flies forward to a target, where the
// impact plays. Bursts play at the target, auras loop on the hero. Effect parts are flipbook sprites.

import { useCallback, useEffect, useRef } from "react";
import * as THREE from "three";
import type { SequencePart } from "./EffectSequence";
import { ModelViewer, type StageFrame } from "./ModelViewer";

export interface StageEffect {
  kind: "projectile" | "burst" | "aura";
  parts: SequencePart[];
  fps: number;
  palette: string[];
  /** Game pixels per second; the flight covers 600 game pixels like the 2D preview. */
  travelSpeed: number;
}

interface Props {
  url: string;
  clip?: string;
  effect?: StageEffect | null;
  /** Where in the clip (0-1) the effect leaves the hand. */
  release: number;
}

const DISTANCE = 4; // metres from the hero to the target
const SIZE = 0.9; // a scale-1 part's size, metres
const HAND = ["hand_r", "weapon_r", "lowerarm_r"];

interface Shot {
  age: number;
  from: THREE.Vector3;
  to: THREE.Vector3;
  flight: number;
}

function sprite(blend: "add" | "mix") {
  const material = new THREE.SpriteMaterial({
    transparent: true,
    depthWrite: false,
    blending: blend === "add" ? THREE.AdditiveBlending : THREE.NormalBlending,
  });
  const s = new THREE.Sprite(material);
  s.visible = false;
  s.renderOrder = 10;
  return s;
}

function dotTexture(color: string) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext("2d")!;
  const glow = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
  glow.addColorStop(0, color);
  glow.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, 64, 64);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

export function SkillStage({ url, clip, effect, release }: Props) {
  // Everything the frame hook needs, rebuilt when the effect changes.
  const state = useRef<{
    key: string;
    group: THREE.Group | null;
    textures: Record<string, THREE.Texture[]>;
    sprites: Record<string, THREE.Sprite>;
    shots: Shot[];
    dots: { sprite: THREE.Sprite; age: number; velocity: THREE.Vector3 }[];
    dotTexture: THREE.Texture | null;
    last: number;
    hand: THREE.Object3D | null | undefined;
    framed: boolean;
  }>({ key: "", group: null, textures: {}, sprites: {}, shots: [], dots: [], dotTexture: null, last: -1, hand: undefined,
       framed: false });

  const key = effect ? effect.parts.map((p) => p.frames[0]).join("|") : "";

  // Frame textures for the effect's parts.
  useEffect(() => {
    const current = state.current;
    const loader = new THREE.TextureLoader();
    const textures: Record<string, THREE.Texture[]> = {};
    for (const part of effect?.parts ?? []) {
      textures[part.part] = part.frames.map((f) => {
        const t = loader.load(f);
        t.colorSpace = THREE.SRGBColorSpace;
        return t;
      });
    }
    current.textures = textures;
    current.dotTexture = effect ? dotTexture(effect.palette[0] ?? "#ffffff") : null;
    current.key = ""; // the sprites are made again on the next frame
    return () => {
      Object.values(textures).flat().forEach((t) => t.dispose());
      current.dotTexture?.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  // A new model: find its hand again.
  useEffect(() => {
    state.current.hand = undefined;
    state.current.framed = false;
  }, [url]);

  const onFrame = useCallback(
    (frame: StageFrame) => {
      const s = state.current;
      const { scene, model, delta, clipTime, clipDuration } = frame;
      if (s.key !== key) {
        // (Re)build the sprites for this effect.
        if (s.group) scene.remove(s.group);
        s.group = new THREE.Group();
        s.sprites = {};
        s.shots = [];
        s.dots.forEach((d) => d.sprite.material.dispose());
        s.dots = [];
        for (const part of effect?.parts ?? []) {
          const one = sprite(part.blend);
          one.scale.setScalar(SIZE * part.scale);
          s.sprites[part.part] = one;
          s.group.add(one);
        }
        scene.add(s.group);
        s.key = key;
      }
      if (!effect || !s.group) return;
      if (s.hand === undefined) {
        s.hand = null;
        for (const name of HAND) {
          const found = model.getObjectByName(name);
          if (found) {
            s.hand = found;
            break;
          }
        }
      }
      const box = new THREE.Box3().setFromObject(model);
      const chest = box.min.y + (box.max.y - box.min.y) * 0.6;
      const hand = new THREE.Vector3();
      if (s.hand) s.hand.getWorldPosition(hand);
      else hand.set(0, chest, 0.3);
      const target = new THREE.Vector3(0, chest, DISTANCE);
      if (!s.framed && effect.kind !== "aura") {
        // From the hero's right side, a little in front: the hero, the flight and the target all show.
        s.framed = true;
        const middle = new THREE.Vector3(0, chest * 0.7, DISTANCE * 0.45);
        frame.controls.target.copy(middle);
        frame.camera.position.copy(middle).add(new THREE.Vector3(-DISTANCE * 1.6, 0.8, -0.3));
        frame.camera.far = Math.max(frame.camera.far, 100);
        frame.camera.updateProjectionMatrix();
      }
      const seconds = (name: string) => (s.textures[name]?.length ?? 0) / effect.fps;
      const show = (name: string, t: number, at: THREE.Vector3, angle = 0) => {
        const one = s.sprites[name];
        const list = s.textures[name];
        const part = effect.parts.find((p) => p.part === name);
        if (!one || !list?.length || !part) return;
        const n = Math.floor(t * effect.fps);
        if (!part.loop && n >= list.length) return;
        one.material.map = list[part.loop ? n % list.length : n];
        one.material.rotation = angle;
        one.material.needsUpdate = true;
        one.position.copy(at);
        one.visible = true;
      };
      Object.values(s.sprites).forEach((one) => (one.visible = false));

      if (effect.kind === "aura") {
        s.last += delta;
        show("aura", s.last, new THREE.Vector3(0, (box.min.y + box.max.y) / 2, 0));
        return;
      }
      const releaseAt = release * (clipDuration || 1);
      // The cast glows at the hand up to the release.
      const cast = seconds("cast");
      if (cast && clipTime <= releaseAt && clipTime >= releaseAt - cast) show("cast", clipTime - (releaseAt - cast), hand);
      // Released: a new shot (the clip may loop before the last one lands).
      const prev = s.last;
      const wrapped = clipTime < prev;
      const passed = prev >= 0 && (wrapped ? releaseAt > prev || releaseAt <= clipTime : prev < releaseAt && clipTime >= releaseAt);
      if (passed) {
        const from = effect.kind === "projectile" ? hand.clone() : target.clone();
        s.shots.push({ age: 0, from, to: target.clone(), flight: effect.kind === "projectile" ? 600 / Math.max(100, effect.travelSpeed) : 0 });
      }
      s.last = clipTime;

      const impact = seconds("impact");
      s.shots = s.shots.filter((shot) => shot.age < shot.flight + impact + 0.1);
      for (const shot of s.shots) {
        shot.age += delta;
        if (shot.age < shot.flight) {
          const k = shot.age / shot.flight;
          const at = shot.from.clone().lerp(shot.to, k);
          // The picture points right: turn it to the flight's direction on screen.
          const a = shot.from.clone().project(frame.camera);
          const b = shot.to.clone().project(frame.camera);
          const angle = Math.atan2((b.y - a.y) / frame.camera.aspect, b.x - a.x);
          show("projectile", shot.age, at, angle);
          if (s.dotTexture && Math.random() < 0.6) {
            const dot = new THREE.Sprite(new THREE.SpriteMaterial({ map: s.dotTexture, transparent: true, depthWrite: false,
                                                                     blending: THREE.AdditiveBlending }));
            dot.position.copy(at);
            dot.scale.setScalar(0.12);
            s.group.add(dot);
            const spread = () => (Math.random() - 0.5) * 0.4;
            s.dots.push({ sprite: dot, age: 0, velocity: new THREE.Vector3(spread(), spread(), -0.3) });
          }
        } else if (impact) {
          show("impact", shot.age - shot.flight, shot.to);
        }
      }
      for (const dot of s.dots) {
        dot.age += delta;
        dot.sprite.position.addScaledVector(dot.velocity, delta);
        dot.sprite.material.opacity = Math.max(0, 1 - dot.age / 0.8);
      }
      s.dots = s.dots.filter((dot) => {
        if (dot.age < 0.8) return true;
        s.group!.remove(dot.sprite);
        dot.sprite.material.dispose();
        return false;
      });
    },
    [effect, key, release],
  );

  return <ModelViewer url={url} clip={clip} onFrame={onFrame} />;
}
