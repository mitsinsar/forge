// three.js viewer for the pipeline's GLBs: the LOD mesh at gate 2 and the animated rig at gate 3.

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { TransformControls } from "three/examples/jsm/controls/TransformControls.js";

/** A joint of the rig, in the mesh's space with Blender axes (Z up, -Y front), as the backend sends it. */
export type Vec3 = [number, number, number];
export interface Joint {
  head: Vec3;
  parent: string | null;
}

/** The light copy of one of the app's GLBs (JPEG textures) for viewing; the exports keep the full ones. */
const preview = (url: string) => (url.includes("/api/assets/") ? `${url}${url.includes("?") ? "&" : "?"}preview=1` : url);

// Blender (Z up, -Y front) to glTF/three (Y up, +Z front) and back.
const toThree = ([x, y, z]: Vec3) => new THREE.Vector3(x, z, -y);
const toBlender = (v: THREE.Vector3): Vec3 => [round(v.x), round(-v.z), round(v.y)];
const round = (n: number) => Math.round(n * 10000) / 10000 + 0;
// Finger joints are left out of the editor: too small to drag, and rarely wrong.
const FINGER = /^(index|middle|ring|pinky|thumb)_/;

interface Props {
  url: string;
  /** Show only the node named `LOD<n>` (mesh GLBs hold LOD0, LOD1 and LOD2). */
  lod?: number;
  /** Play this animation of the GLB, looping. */
  clip?: string;
  /** Called with the GLB's animation names once it has loaded. */
  onClips?: (names: string[]) => void;
  wireframe?: boolean;
  /** The rig's joints. With `editJoints` the model holds its rest pose and they can be dragged. */
  joints?: Record<string, Joint>;
  /** Joints moved so far (they are shown where they were moved to). */
  moved?: Record<string, Vec3>;
  editJoints?: boolean;
  onJointMove?: (name: string, head: Vec3) => void;
  /** More GLBs shown with the model and playing the same clip: items fitted on it (an outfit). Their
   *  copy of the body (meshes named `mannequin`) is hidden; one without the clip is hidden too. */
  extras?: string[];
}

export function ModelViewer({ url, lod, clip, onClips, wireframe, joints, moved, editJoints, onJointMove, extras }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<{
    model: THREE.Object3D | null;
    mixer: THREE.AnimationMixer | null;
    clips: THREE.AnimationClip[];
    scene?: THREE.Scene;
    camera?: THREE.Camera;
    controls?: OrbitControls;
    canvas?: HTMLCanvasElement;
    radius?: number;
    extras?: Map<string, { model: THREE.Object3D; mixer: THREE.AnimationMixer; clips: THREE.AnimationClip[] }>;
  }>({ model: null, mixer: null, clips: [] });
  const [extrasLoaded, setExtrasLoaded] = useState(0);
  const onJointMoveRef = useRef(onJointMove);
  useEffect(() => {
    onJointMoveRef.current = onJointMove;
  }, [onJointMove]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [loaded, setLoaded] = useState(0);
  const onClipsRef = useRef(onClips);
  useEffect(() => {
    onClipsRef.current = onClips;
  }, [onClips]);

  // Scene, renderer and loading: once per URL.
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    setState("loading");

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    el.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
    camera.position.set(2.5, 1.6, 3.5);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x2a2536, 1.0));
    const sun = new THREE.DirectionalLight(0xffffff, 1.5);
    sun.position.set(3, 5, 4);
    scene.add(sun);
    const grid = new THREE.GridHelper(4, 16, 0x463f57, 0x2f2a3b);
    scene.add(grid);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;

    const resize = () => {
      const { clientWidth: w, clientHeight: h } = el;
      if (!w || !h) return;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    resize();

    const timer = new THREE.Timer();
    let frame = 0;
    const tick = (time: number) => {
      timer.update(time);
      const delta = timer.getDelta();
      sceneRef.current.mixer?.update(delta);
      sceneRef.current.extras?.forEach((extra) => extra.mixer.update(delta));
      controls.update();
      renderer.render(scene, camera);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);

    let cancelled = false;
    new GLTFLoader().load(
      preview(url),
      (gltf) => {
        if (cancelled) return;
        const model = gltf.scene;
        // LODs are shown one at a time: put them all where LOD0 is, and frame LOD0.
        const lods = model.children.filter((c) => /^LOD\d+$/.test(c.name));
        const lod0 = lods.find((c) => c.name === "LOD0");
        if (lod0) lods.forEach((c) => c.position.copy(lod0.position));
        lods.forEach((c) => (c.visible = c === lod0));
        scene.add(model);
        // Frame the model: stand it on the grid and fit the camera to it.
        const box = new THREE.Box3().setFromObject(model);
        const size = box.getSize(new THREE.Vector3());
        const center = box.getCenter(new THREE.Vector3());
        const radius = Math.max(size.x, size.y, size.z) || 1;
        model.position.sub(new THREE.Vector3(center.x, box.min.y, center.z));
        grid.scale.setScalar(radius / 2);
        controls.target.set(0, size.y / 2, 0);
        // Far enough back that the whole model fits the field of view, with a margin.
        const distance = (radius / 2 / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2))) * 1.5;
        const direction = new THREE.Vector3(0.45, 0.25, 1).normalize();
        camera.position.copy(direction.multiplyScalar(distance)).add(controls.target);
        camera.near = radius / 100;
        camera.far = radius * 100;
        camera.updateProjectionMatrix();

        sceneRef.current = {
          model,
          mixer: gltf.animations.length ? new THREE.AnimationMixer(model) : null,
          clips: gltf.animations,
          scene,
          camera,
          controls,
          canvas: renderer.domElement,
          radius,
        };
        onClipsRef.current?.(gltf.animations.map((a) => a.name));
        setState("ready");
        setLoaded((n) => n + 1);
      },
      undefined,
      () => !cancelled && setState("error"),
    );

    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      sceneRef.current.mixer?.stopAllAction();
      scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
          obj.geometry.dispose();
          const materials = Array.isArray(obj.material) ? obj.material : [obj.material];
          materials.forEach((m: THREE.Material & { map?: THREE.Texture | null }) => {
            m.map?.dispose();
            m.dispose();
          });
        }
      });
      renderer.dispose();
      renderer.domElement.remove();
      sceneRef.current = { model: null, mixer: null, clips: [] };
    };
  }, [url]);

  // LOD visibility and wireframe.
  useEffect(() => {
    const { model } = sceneRef.current;
    if (!model) return;
    if (lod !== undefined) {
      model.children.forEach((child) => {
        child.visible = !/^LOD\d+$/.test(child.name) || child.name === `LOD${lod}`;
      });
    }
    model.traverse((obj) => {
      if (obj instanceof THREE.Mesh) {
        const materials = Array.isArray(obj.material) ? obj.material : [obj.material];
        materials.forEach((m: THREE.MeshStandardMaterial) => (m.wireframe = !!wireframe));
      }
    });
  }, [lod, wireframe, loaded]);

  // Extra GLBs (an outfit's items): loaded into the same scene at the model's place.
  const extrasKey = (extras ?? []).join("|");
  useEffect(() => {
    const { scene, model } = sceneRef.current;
    if (!scene || !model) return;
    const current = sceneRef.current.extras ?? new Map();
    sceneRef.current.extras = current;
    const wanted = new Set(extras ?? []);
    current.forEach((extra, key) => {
      if (!wanted.has(key)) {
        extra.mixer.stopAllAction();
        scene.remove(extra.model);
        current.delete(key);
      }
    });
    let cancelled = false;
    for (const extraUrl of wanted) {
      if (current.has(extraUrl)) continue;
      new GLTFLoader().load(preview(extraUrl), (gltf) => {
        if (cancelled || !sceneRef.current.extras) return;
        const extra = gltf.scene;
        extra.traverse((obj) => {
          if (/^mannequin/i.test(obj.name)) obj.visible = false;
        });
        extra.position.copy(model.position);
        scene.add(extra);
        sceneRef.current.extras.set(extraUrl, { model: extra, mixer: new THREE.AnimationMixer(extra), clips: gltf.animations });
        setExtrasLoaded((n) => n + 1);
      });
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [extrasKey, loaded]);

  // Clip playback (held at the rest pose while joints are edited).
  useEffect(() => {
    const { mixer, clips, model } = sceneRef.current;
    if (!mixer || !model) return;
    mixer.stopAllAction();
    const name = (clips.find((c) => c.name === clip) ?? clips[0])?.name;
    sceneRef.current.extras?.forEach((extra) => {
      extra.mixer.stopAllAction();
      const own = extra.clips.find((c) => c.name === name);
      extra.model.visible = !!own || !name;
      if (own) extra.mixer.clipAction(own).reset().setLoop(THREE.LoopRepeat, Infinity).play();
    });
    if (editJoints) {
      model.traverse((obj) => {
        if (obj instanceof THREE.SkinnedMesh) obj.skeleton.pose();
      });
      return;
    }
    const found = clips.find((c) => c.name === clip) ?? clips[0];
    if (found) mixer.clipAction(found).reset().setLoop(THREE.LoopRepeat, Infinity).play();
  }, [clip, loaded, editJoints, extrasLoaded]);

  // Joint editor: a handle per joint and a line per bone, drawn through the mesh; click a handle to
  // move it with the arrows.
  const handlesRef = useRef<{ handles: Map<string, THREE.Mesh>; update: () => void } | null>(null);
  useEffect(() => {
    const { model, scene, camera, controls, canvas, radius } = sceneRef.current;
    if (!editJoints || !joints || !model || !scene || !camera || !controls || !canvas) return;
    const overlay = new THREE.Group();
    overlay.renderOrder = 10;
    model.add(overlay);
    const names = Object.keys(joints).filter((n) => !FINGER.test(n));
    const size = (radius ?? 1.8) * 0.012;
    const sphere = new THREE.SphereGeometry(size, 12, 8);
    const colours = { normal: 0x5ad1ff, moved: 0xff9a3c, picked: 0xffffff };
    const handles = new Map<string, THREE.Mesh>();
    for (const name of names) {
      const handle = new THREE.Mesh(
        sphere,
        new THREE.MeshBasicMaterial({ color: colours.normal, depthTest: false, transparent: true }),
      );
      handle.name = name;
      handle.renderOrder = 11;
      overlay.add(handle);
      handles.set(name, handle);
    }
    const bones = names.filter((n) => joints[n].parent && handles.has(joints[n].parent!));
    const lineGeometry = new THREE.BufferGeometry();
    lineGeometry.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(bones.length * 6), 3));
    const lines = new THREE.LineSegments(
      lineGeometry,
      new THREE.LineBasicMaterial({ color: 0x5ad1ff, depthTest: false, transparent: true, opacity: 0.8 }),
    );
    lines.renderOrder = 10;
    overlay.add(lines);
    const update = () => {
      const position = lineGeometry.getAttribute("position") as THREE.BufferAttribute;
      bones.forEach((name, i) => {
        const a = handles.get(joints[name].parent!)!.position;
        const b = handles.get(name)!.position;
        position.setXYZ(i * 2, a.x, a.y, a.z);
        position.setXYZ(i * 2 + 1, b.x, b.y, b.z);
      });
      position.needsUpdate = true;
    };
    handlesRef.current = { handles, update };

    const gizmo = new TransformControls(camera, canvas);
    gizmo.setSize(0.6);
    const helper = gizmo.getHelper();
    scene.add(helper);
    let picked: THREE.Mesh | null = null;
    const paint = () =>
      handles.forEach((h, name) => {
        const moved = (h.userData.moved as boolean) ?? false;
        (h.material as THREE.MeshBasicMaterial).color.setHex(h === picked ? colours.picked : moved ? colours.moved : colours.normal);
        void name;
      });
    gizmo.addEventListener("dragging-changed", (event) => {
      controls.enabled = !event.value;
    });
    gizmo.addEventListener("objectChange", () => {
      if (!picked) return;
      picked.userData.moved = true;
      update();
      paint();
      onJointMoveRef.current?.(picked.name, toBlender(picked.position));
    });
    // Picks the handle nearest the click on screen (within 14 px): the handles are small.
    const onPointerDown = (event: PointerEvent) => {
      if (gizmo.dragging || gizmo.axis) return;
      const box = canvas.getBoundingClientRect();
      const world = new THREE.Vector3();
      let best: THREE.Mesh | null = null;
      let bestDistance = 14;
      handles.forEach((handle) => {
        handle.getWorldPosition(world).project(camera);
        const x = ((world.x + 1) / 2) * box.width + box.left;
        const y = ((1 - world.y) / 2) * box.height + box.top;
        const distance = Math.hypot(x - event.clientX, y - event.clientY);
        if (distance < bestDistance) {
          best = handle;
          bestDistance = distance;
        }
      });
      if (best) {
        picked = best;
        gizmo.attach(picked);
        canvas.dataset.pickedJoint = (best as THREE.Mesh).name;  // for checks and tests
        paint();
      }
    };
    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.dataset.jointEditor = "on";
    return () => {
      canvas.removeEventListener("pointerdown", onPointerDown);
      delete canvas.dataset.jointEditor;
      delete canvas.dataset.pickedJoint;
      gizmo.detach();
      scene.remove(helper);
      gizmo.dispose();
      model.remove(overlay);
      sphere.dispose();
      lineGeometry.dispose();
      handles.forEach((h) => (h.material as THREE.Material).dispose());
      (lines.material as THREE.Material).dispose();
      controls.enabled = true;
      handlesRef.current = null;
    };
  }, [editJoints, joints, loaded]);

  // Handles follow the joints and the moves (a mirrored move, an undo).
  useEffect(() => {
    const editor = handlesRef.current;
    if (!editor || !joints) return;
    editor.handles.forEach((handle, name) => {
      const at = moved?.[name] ?? joints[name].head;
      handle.position.copy(toThree(at));
      handle.userData.moved = !!moved?.[name];
      (handle.material as THREE.MeshBasicMaterial).color.setHex(moved?.[name] ? 0xff9a3c : 0x5ad1ff);
    });
    editor.update();
  }, [joints, moved, editJoints, loaded]);

  return (
    <div className="stage" ref={host}>
      {state !== "ready" && (
        <div className="stage__overlay">{state === "loading" ? "Loading model…" : "The model could not be loaded."}</div>
      )}
    </div>
  );
}
