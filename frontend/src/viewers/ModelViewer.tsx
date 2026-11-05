// three.js viewer for the pipeline's GLBs: the LOD mesh at gate 2 and the animated rig at gate 3.

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

interface Props {
  url: string;
  /** Show only the node named `LOD<n>` (mesh GLBs hold LOD0, LOD1 and LOD2). */
  lod?: number;
  /** Play this animation of the GLB, looping. */
  clip?: string;
  /** Called with the GLB's animation names once it has loaded. */
  onClips?: (names: string[]) => void;
  wireframe?: boolean;
}

export function ModelViewer({ url, lod, clip, onClips, wireframe }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<{
    model: THREE.Object3D | null;
    mixer: THREE.AnimationMixer | null;
    clips: THREE.AnimationClip[];
  }>({ model: null, mixer: null, clips: [] });
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
      sceneRef.current.mixer?.update(timer.getDelta());
      controls.update();
      renderer.render(scene, camera);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);

    let cancelled = false;
    new GLTFLoader().load(
      url,
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

  // Clip playback.
  useEffect(() => {
    const { mixer, clips } = sceneRef.current;
    if (!mixer) return;
    mixer.stopAllAction();
    const found = clips.find((c) => c.name === clip) ?? clips[0];
    if (found) mixer.clipAction(found).reset().setLoop(THREE.LoopRepeat, Infinity).play();
  }, [clip, loaded]);

  return (
    <div className="stage" ref={host}>
      {state !== "ready" && (
        <div className="stage__overlay">{state === "loading" ? "Loading model…" : "The model could not be loaded."}</div>
      )}
    </div>
  );
}
