// three.js viewer for the pipeline's GLBs: the LOD mesh at gate 2 and the animated rig at gate 3.

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { TransformControls } from "three/examples/jsm/controls/TransformControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { Button } from "../ui/kit";

/** A joint of the rig, in the mesh's space with Blender axes (Z up, -Y front), as the backend sends it. */
export type Vec3 = [number, number, number];
export interface Joint {
  head: Vec3;
  parent: string | null;
  /** The bone's direction and the way the joint faces (Blender axes), when the rig reports them. */
  axis?: Vec3;
  front?: Vec3;
}

/** The light copy of one of the app's GLBs (JPEG textures) for viewing; the exports keep the full ones. */
const preview = (url: string) => (url.includes("/api/assets/") ? `${url}${url.includes("?") ? "&" : "?"}preview=1` : url);

// Blender (Z up, -Y front) to glTF/three (Y up, +Z front) and back.
const toThree = ([x, y, z]: Vec3) => new THREE.Vector3(x, z, -y);
const toBlender = (v: THREE.Vector3): Vec3 => [round(v.x), round(-v.z), round(v.y)];
const round = (n: number) => Math.round(n * 10000) / 10000 + 0;
// Finger joints are left out of the editor: too small to drag, and rarely wrong.
const FINGER = /^(index|middle|ring|pinky|thumb)_/;
const FOV = 35;
const UP = new THREE.Vector3(0, 1, 0);
const COLOURS = {
  normal: 0x5ad1ff,
  moved: 0xff9a3c,
  picked: 0xffffff,
  facing: 0x5ee08a, // pointing forward
  turned: 0xffb347, // turned away from forward
  unknown: 0x9aa0b4, // a bone that points forward itself (a foot): no "forward" to compare with
};
const FACING_OK = THREE.MathUtils.degToRad(25);

/** Where a joint on a bone along ``axis`` should face: the character's forward (+Z), made square
 *  to the bone. Null when the bone itself points (nearly) forward. */
function facingTarget(axis: THREE.Vector3): THREE.Vector3 | null {
  const forward = new THREE.Vector3(0, 0, 1);
  const square = forward.sub(axis.clone().multiplyScalar(forward.dot(axis)));
  return square.length() < 0.3 ? null : square.normalize();
}

/** Degrees from ``from`` to ``to`` about ``axis`` (both square to it), signed by the right hand. */
function signedAngle(from: THREE.Vector3, to: THREE.Vector3, axis: THREE.Vector3): number {
  const cross = new THREE.Vector3().crossVectors(from, to);
  return THREE.MathUtils.radToDeg(Math.atan2(cross.dot(axis), from.dot(to)));
}
const FLAT_FOV = 2;  // narrow enough that the perspective is gone, wide enough to keep orbiting

// Camera directions. Characters face +Z, so their left side is at +X.
type View = "Front" | "Left" | "Back" | "Right" | "Top";
const VIEWS: Record<View, THREE.Vector3> = {
  Front: new THREE.Vector3(0, 0, 1),
  Left: new THREE.Vector3(1, 0, 0),
  Back: new THREE.Vector3(0, 0, -1),
  Right: new THREE.Vector3(-1, 0, 0),
  Top: new THREE.Vector3(0, 1, 0.0001).normalize(),
};
const VIEW_TITLES: Record<View, string> = {
  Front: "Look at the front",
  Left: "Look at the character's left side",
  Back: "Look at the back",
  Right: "Look at the character's right side",
  Top: "Look from above",
};

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
  /** Degrees each joint is turned about its bone, not yet re-rigged (its facing arrow turns). */
  twists?: Record<string, number>;
  /** What dragging the picked joint does: move it (arrows) or twist it about its bone (a ring). */
  jointMode?: "move" | "twist";
  onJointMove?: (name: string, head: Vec3) => void;
  /** Twist mode: degrees the picked joint is turned from the rig's result (dragging the ring). */
  onJointTwist?: (name: string, degrees: number) => void;
  /** Called with the joint clicked in the editor (for the placement hint). */
  onJointPick?: (name: string) => void;
  /** Select this joint in the editor (picked elsewhere, such as the guide's figure). */
  pickedJoint?: string;
  /** More GLBs shown with the model and playing the same clip: items fitted on it (an outfit). Their
   *  copy of the body (meshes named `mannequin`) is hidden; one without the clip is hidden too. */
  extras?: string[];
}

export function ModelViewer({
  url,
  lod,
  clip,
  onClips,
  wireframe,
  joints,
  moved,
  editJoints,
  twists,
  jointMode = "move",
  onJointMove,
  onJointTwist,
  onJointPick,
  pickedJoint,
  extras,
}: Props) {
  const host = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<{
    model: THREE.Object3D | null;
    mixer: THREE.AnimationMixer | null;
    clips: THREE.AnimationClip[];
    scene?: THREE.Scene;
    camera?: THREE.PerspectiveCamera;
    controls?: OrbitControls;
    canvas?: HTMLCanvasElement;
    radius?: number;
    /** Run every frame before rendering (the joint editor keeps its handles a fixed size on screen). */
    frameHook?: () => void;
    extras?: Map<string, { model: THREE.Object3D; mixer: THREE.AnimationMixer; clips: THREE.AnimationClip[] }>;
  }>({ model: null, mixer: null, clips: [] });
  const [extrasLoaded, setExtrasLoaded] = useState(0);
  const [flat, setFlat] = useState(false);
  const [pickedName, setPickedName] = useState<string | null>(null);
  const onJointMoveRef = useRef(onJointMove);
  useEffect(() => {
    onJointMoveRef.current = onJointMove;
  }, [onJointMove]);
  const onJointTwistRef = useRef(onJointTwist);
  useEffect(() => {
    onJointTwistRef.current = onJointTwist;
  }, [onJointTwist]);
  const onJointPickRef = useRef(onJointPick);
  useEffect(() => {
    onJointPickRef.current = onJointPick;
  }, [onJointPick]);
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
    // Neutral tone mapping keeps the texture's colours while taming bright highlights.
    renderer.toneMapping = THREE.NeutralToneMapping;
    renderer.toneMappingExposure = 1.1;
    el.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(FOV, 1, 0.01, 1000);
    camera.position.set(2.5, 1.6, 3.5);
    // A soft studio around the model: metal and PBR materials reflect their surroundings, and with
    // nothing to reflect they render nearly black. Plus a key light, a front fill and a rim light.
    const pmrem = new THREE.PMREMGenerator(renderer);
    const environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = environment;
    scene.add(new THREE.HemisphereLight(0xffffff, 0x3a3448, 0.6));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(3, 5, 4);
    scene.add(sun);
    const fill = new THREE.DirectionalLight(0xfff4e8, 0.8);
    fill.position.set(-3, 2, 5);
    scene.add(fill);
    const rim = new THREE.DirectionalLight(0xdfe8ff, 0.6);
    rim.position.set(0, 4, -5);
    scene.add(rim);
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
      sceneRef.current.frameHook?.();
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
      environment.dispose();
      pmrem.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      sceneRef.current = { model: null, mixer: null, clips: [] };
    };
  }, [url]);

  // Flat view: a very narrow lens from far away, so lines that are straight on the model look
  // straight (no perspective) while the orbit, zoom and joint arrows keep working as usual.
  useEffect(() => {
    const { camera, controls } = sceneRef.current;
    if (!camera || !controls) return;
    const fov = flat ? FLAT_FOV : FOV;
    if (camera.fov === fov) return;
    const offset = camera.position.clone().sub(controls.target);
    const scale = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) / Math.tan(THREE.MathUtils.degToRad(fov / 2));
    camera.fov = fov;
    camera.position.copy(controls.target).add(offset.multiplyScalar(scale));
    camera.near = (sceneRef.current.radius ?? 1) / 100;
    camera.far = Math.max(camera.position.distanceTo(controls.target) * 4, (sceneRef.current.radius ?? 1) * 100);
    camera.updateProjectionMatrix();
    controls.update();
  }, [flat, loaded]);

  /** Turns the camera to look exactly along an axis (keeping its distance). */
  const look = (view: View) => {
    const { camera, controls } = sceneRef.current;
    if (!camera || !controls) return;
    const distance = camera.position.distanceTo(controls.target);
    camera.position.copy(controls.target).add(VIEWS[view].clone().multiplyScalar(distance));
    camera.up.set(0, 1, 0);
    camera.lookAt(controls.target);
    controls.update();
  };

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

  // Joint editor: a handle per joint, a line per bone and an arrow for the way each joint faces,
  // drawn through the mesh at a fixed size on screen. Click a handle to pick it; drag the arrows to
  // move it, or (twist mode) the ring to turn it about its bone.
  const handlesRef = useRef<{
    handles: Map<string, THREE.Mesh>;
    facings: Map<string, THREE.Group>;
    ghost: THREE.Group;
    gizmo: TransformControls;
    picked: () => THREE.Mesh | null;
    select: (name: string) => void;
    update: () => void;
  } | null>(null);
  useEffect(() => {
    const { model, scene, camera, controls, canvas } = sceneRef.current;
    if (!editJoints || !joints || !model || !scene || !camera || !controls || !canvas) return;
    const overlay = new THREE.Group();
    overlay.renderOrder = 10;
    model.add(overlay);
    const names = Object.keys(joints).filter((n) => !FINGER.test(n));
    const sphere = new THREE.SphereGeometry(1, 16, 12);
    const handles = new Map<string, THREE.Mesh>();
    for (const name of names) {
      const handle = new THREE.Mesh(
        sphere,
        new THREE.MeshBasicMaterial({ color: COLOURS.normal, depthTest: false, transparent: true, opacity: 0.9 }),
      );
      handle.name = name;
      handle.renderOrder = 11;
      overlay.add(handle);
      handles.set(name, handle);
    }
    // Facing arrows: where each joint faces (a knee's points where the knee cap is). They all should
    // point forward, like the face: green when they do, amber when they are turned away.
    const shaft = new THREE.CylinderGeometry(0.035, 0.035, 0.72, 8).translate(0, 0.36, 0);
    const head = new THREE.ConeGeometry(0.12, 0.28, 12).translate(0, 0.86, 0);
    const arrow = (colour: number, opacity: number) => {
      const material = new THREE.MeshBasicMaterial({ color: colour, depthTest: false, transparent: true, opacity });
      const group = new THREE.Group();
      group.add(new THREE.Mesh(shaft, material), new THREE.Mesh(head, material));
      group.children.forEach((c) => (c.renderOrder = 12));
      group.userData.material = material;
      return group;
    };
    const facings = new Map<string, THREE.Group>();
    for (const name of names) {
      if (!joints[name].front || !joints[name].axis) continue;
      const group = arrow(COLOURS.facing, 0.95);
      overlay.add(group);
      facings.set(name, group);
    }
    // The picked joint's "forward": where its arrow should point.
    const ghost = arrow(0xffffff, 0.35);
    ghost.visible = false;
    overlay.add(ghost);

    const bones = names.filter((n) => joints[n].parent && handles.has(joints[n].parent!));
    const lineGeometry = new THREE.BufferGeometry();
    lineGeometry.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(bones.length * 6), 3));
    const lines = new THREE.LineSegments(
      lineGeometry,
      new THREE.LineBasicMaterial({ color: COLOURS.normal, depthTest: false, transparent: true, opacity: 0.8 }),
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

    const gizmo = new TransformControls(camera, canvas);
    gizmo.setSize(0.8);
    const helper = gizmo.getHelper();
    scene.add(helper);
    let picked: THREE.Mesh | null = null;
    const paint = () =>
      handles.forEach((h) => {
        const colour = h === picked ? COLOURS.picked : h.userData.moved ? COLOURS.moved : COLOURS.normal;
        (h.material as THREE.MeshBasicMaterial).color.setHex(colour);
      });
    handlesRef.current = {
      handles, facings, ghost, gizmo, picked: () => picked, update,
      select: (name) => {
        const handle = handles.get(name);
        if (handle && handle !== picked) pick(handle, false);
      },
    };

    // A fixed size on screen, whatever the zoom: handles 6 px, arrows 46 px long.
    const world = new THREE.Vector3();
    sceneRef.current.frameHook = () => {
      const height = canvas.clientHeight || 1;
      const perPixel = (distance: number) =>
        (2 * distance * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2))) / height;
      handles.forEach((h) => {
        h.getWorldPosition(world);
        h.scale.setScalar((h === picked ? 8 : 6) * perPixel(world.distanceTo(camera.position)));
      });
      for (const group of [...facings.values(), ghost]) {
        group.getWorldPosition(world);
        group.scale.setScalar((group === ghost ? 54 : 46) * perPixel(world.distanceTo(camera.position)));
      }
    };

    gizmo.addEventListener("dragging-changed", (event) => {
      controls.enabled = !event.value;
    });
    gizmo.addEventListener("objectChange", () => {
      if (!picked) return;
      if (gizmo.getMode() === "rotate") {
        const joint = joints[picked.name];
        if (!joint.axis || !joint.front) return;
        const axis = toThree(joint.axis).normalize();
        const now = new THREE.Vector3(0, 0, 1).applyQuaternion(picked.quaternion);
        onJointTwistRef.current?.(picked.name, signedAngle(toThree(joint.front).normalize(), now, axis));
        return;
      }
      picked.userData.moved = true;
      update();
      paint();
      onJointMoveRef.current?.(picked.name, toBlender(picked.position));
    });
    // Picks the handle nearest the click on screen (within 16 px).
    const onPointerDown = (event: PointerEvent) => {
      if (gizmo.dragging || gizmo.axis) return;
      const box = canvas.getBoundingClientRect();
      const at = new THREE.Vector3();
      let best: THREE.Mesh | null = null;
      let bestDistance = 16;
      handles.forEach((handle) => {
        handle.getWorldPosition(at).project(camera);
        const x = ((at.x + 1) / 2) * box.width + box.left;
        const y = ((1 - at.y) / 2) * box.height + box.top;
        const distance = Math.hypot(x - event.clientX, y - event.clientY);
        if (distance < bestDistance) {
          best = handle;
          bestDistance = distance;
        }
      });
      if (best) pick(best, true);
    };
    function pick(handle: THREE.Mesh, tell: boolean) {
      picked = handle;
      gizmo.attach(handle);
      canvas!.dataset.pickedJoint = handle.name; // for checks and tests
      paint();
      if (tell) onJointPickRef.current?.(handle.name);
      setPickedName(handle.name);
    }
    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.dataset.jointEditor = "on";
    return () => {
      canvas.removeEventListener("pointerdown", onPointerDown);
      delete canvas.dataset.jointEditor;
      delete canvas.dataset.pickedJoint;
      sceneRef.current.frameHook = undefined;
      gizmo.detach();
      scene.remove(helper);
      gizmo.dispose();
      model.remove(overlay);
      sphere.dispose();
      shaft.dispose();
      head.dispose();
      lineGeometry.dispose();
      handles.forEach((h) => (h.material as THREE.Material).dispose());
      [...facings.values(), ghost].forEach((g) => (g.userData.material as THREE.Material).dispose());
      (lines.material as THREE.Material).dispose();
      controls.enabled = true;
      handlesRef.current = null;
      setPickedName(null);
    };
  }, [editJoints, joints, loaded]);

  useEffect(() => {
    if (pickedJoint) handlesRef.current?.select(pickedJoint);
  }, [pickedJoint, editJoints, loaded]);

  // Move mode: arrows along the world axes. Twist mode: one ring about the picked joint's bone.
  useEffect(() => {
    const editor = handlesRef.current;
    if (!editor) return;
    const twist = jointMode === "twist";
    editor.gizmo.setMode(twist ? "rotate" : "translate");
    editor.gizmo.setSpace(twist ? "local" : "world");
    editor.gizmo.showX = !twist;
    editor.gizmo.showZ = !twist;
  }, [jointMode, editJoints, loaded]);

  // Handles follow the joints and the moves (a mirrored move, an undo); arrows the twists.
  useEffect(() => {
    const editor = handlesRef.current;
    if (!editor || !joints) return;
    editor.handles.forEach((handle, name) => {
      const at = moved?.[name] ?? joints[name].head;
      handle.position.copy(toThree(at));
      handle.userData.moved = !!moved?.[name];
      if (handle !== editor.picked()) {
        (handle.material as THREE.MeshBasicMaterial).color.setHex(moved?.[name] ? COLOURS.moved : COLOURS.normal);
      }
    });
    const dragging = editor.gizmo.dragging;
    editor.facings.forEach((group, name) => {
      const joint = joints[name];
      const handle = editor.handles.get(name)!;
      const axis = toThree(joint.axis!).normalize();
      const front = toThree(joint.front!).normalize().applyAxisAngle(axis, THREE.MathUtils.degToRad(twists?.[name] ?? 0));
      group.position.copy(handle.position);
      group.quaternion.setFromUnitVectors(UP, front);
      const forward = facingTarget(axis);
      const off = forward ? front.angleTo(forward) : 0;
      (group.userData.material as THREE.MeshBasicMaterial).color.setHex(
        !forward ? COLOURS.unknown : off < FACING_OK ? COLOURS.facing : COLOURS.turned,
      );
      // The handle's own frame (Y along the bone, Z the facing) is what the twist ring turns.
      if (!(dragging && handle === editor.picked())) {
        const side = new THREE.Vector3().crossVectors(axis, front).normalize();
        handle.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(side, axis, front));
      }
    });
    const picked = pickedName ? joints[pickedName] : undefined;
    const forward = picked?.axis ? facingTarget(toThree(picked.axis).normalize()) : null;
    editor.ghost.visible = !!forward;
    if (forward && pickedName) {
      editor.ghost.position.copy(editor.handles.get(pickedName)!.position);
      editor.ghost.quaternion.setFromUnitVectors(UP, forward);
    }
    editor.update();
  }, [joints, moved, twists, editJoints, loaded, pickedName]);

  return (
    <div className="stage" ref={host}>
      {state !== "ready" && (
        <div className="stage__overlay">{state === "loading" ? "Loading model…" : "The model could not be loaded."}</div>
      )}
      {state === "ready" && (
        <div className="stage__views" role="toolbar" aria-label="Camera">
          {(Object.keys(VIEWS) as View[]).map((view) => (
            <Button key={view} size="sm" variant="ghost" onClick={() => look(view)} title={VIEW_TITLES[view]}>
              {view}
            </Button>
          ))}
          <Button
            size="sm"
            variant={flat ? "primary" : "ghost"}
            aria-pressed={flat}
            onClick={() => setFlat(!flat)}
            title="No perspective: straight lines on the model stay straight, for lining up joints"
          >
            Flat
          </Button>
        </div>
      )}
    </div>
  );
}
