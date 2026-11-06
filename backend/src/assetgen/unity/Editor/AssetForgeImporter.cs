// assetgen-importer-version: 1
// Written by assetgen (Forge Of Figures). Replaced when a newer version ships; do not edit.
//
// Turns every export folder Assets/Generated/<kind>/<Name>/ (the FBX and its
// asset.json) into a ready-to-use prefab <Name>.prefab in the same folder:
//
//   * model import: Humanoid avatar for "Humanoid" rigs, Generic otherwise,
//     no avatar or clips for weapons, material import off; one clip per entry
//     of asset.json's "clips" with its name, loop flag and duration;
//   * Materials/<Name>.mat: URP Lit with the albedo texture;
//   * <Name>.controller: one state per clip, idle as the default state;
//   * the prefab: a LODGroup (LOD0/1/2 from the <Name>_LOD<n> meshes, switch
//     heights from the LOD ratios), the model as child "Model" with the
//     Animator; weapons hold the model at the grip offset, so the prefab root
//     is the grip and goes straight under the weapon_r / weapon_l socket; gear
//     carries an AssetForgeGear that binds its skinned meshes to the wearer's
//     skeleton by bone name.
//
// A re-export replaces the files beside the prefab; the prefab, controller and
// material are updated in place (same asset, same GUID), so scenes and other
// prefabs that use them keep their references.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

namespace AssetForge.Editor
{
    [Serializable]
    public class SidecarMaterial
    {
        public string file;
        public string shader;
        public string albedo;
    }

    [Serializable]
    public class SidecarLod
    {
        public int level;
        public float ratio;
        public int triangles;
    }

    [Serializable]
    public class SidecarClip
    {
        public string name;
        public bool loop;
        public float duration;
    }

    [Serializable]
    public class SidecarGear
    {
        public string slot;
        public string skeleton;
        public bool skinned;
        public string[] hide_body;
        public string[] bones;
    }

    [Serializable]
    public class SidecarGrip
    {
        public string socket;
        public float[] position;
        public float[] rotation;
    }

    [Serializable]
    public class SidecarWeapon
    {
        public string socket;
        public SidecarGrip grip;
        public string prefab;
    }

    /// <summary>asset.json as written by assetgen's Unity export.</summary>
    [Serializable]
    public class Sidecar
    {
        public string generator;
        public string id;
        public string name;
        public string kind;
        public string archetype;
        public string rig;
        public string fbx;
        public SidecarMaterial material;
        public SidecarLod[] lods;
        public SidecarClip[] clips;
        public SidecarGear gear;
        public SidecarWeapon weapon;

        public bool IsWeapon => kind == "weapon";
        public bool IsGear => kind == "gear";
        public bool IsHumanoid => !IsWeapon && !IsGear && string.Equals(rig, "Humanoid", StringComparison.OrdinalIgnoreCase);
        public bool Animated => !IsWeapon && !IsGear && clips != null && clips.Length > 0;
    }

    public class AssetForgeImporter : AssetPostprocessor
    {
        public const int Version = 1;
        public const string Root = "Assets/Generated/";
        public const string ModelChild = "Model";
        const string Log = "[AssetForge] ";

        public override uint GetVersion() => Version;

        // ---- reading asset.json -------------------------------------------------

        /// <summary>The sidecar of an FBX inside an assetgen export folder, or null.</summary>
        public static Sidecar SidecarFor(string modelPath)
        {
            modelPath = modelPath.Replace('\\', '/');
            if (!modelPath.StartsWith(Root, StringComparison.Ordinal) ||
                !modelPath.EndsWith(".fbx", StringComparison.OrdinalIgnoreCase)) return null;
            var sidecar = ReadSidecar(Folder(modelPath));
            return sidecar != null && sidecar.fbx == Path.GetFileName(modelPath) ? sidecar : null;
        }

        public static Sidecar ReadSidecar(string folder)
        {
            var path = folder + "/asset.json";
            if (!File.Exists(path)) return null;
            try
            {
                var sidecar = JsonUtility.FromJson<Sidecar>(File.ReadAllText(path));
                return sidecar != null && sidecar.generator == "assetgen" && !string.IsNullOrEmpty(sidecar.fbx)
                    ? sidecar : null;
            }
            catch (Exception e)
            {
                Debug.LogWarning($"{Log}{path} could not be read: {e.Message}");
                return null;
            }
        }

        static string Folder(string path) => Path.GetDirectoryName(path)?.Replace('\\', '/');

        static string BaseName(Sidecar s) => Path.GetFileNameWithoutExtension(s.fbx);

        // ---- model import ---------------------------------------------------------

        void OnPreprocessModel()
        {
            var s = SidecarFor(assetPath);
            if (s == null) return;
            context.DependsOnSourceAsset(Folder(assetPath) + "/asset.json");
            var importer = (ModelImporter)assetImporter;
            importer.materialImportMode = ModelImporterMaterialImportMode.None;
            importer.globalScale = 1f;
            importer.useFileScale = true;
            importer.importCameras = false;
            importer.importLights = false;
            if (s.IsWeapon)
            {
                importer.animationType = ModelImporterAnimationType.None;
                importer.importAnimation = false;
                return;
            }
            // Gear has no avatar of its own: it is bound to the wearer's bones by name.
            importer.animationType = s.IsHumanoid ? ModelImporterAnimationType.Human : ModelImporterAnimationType.Generic;
            importer.avatarSetup = s.IsGear ? ModelImporterAvatarSetup.NoAvatar : ModelImporterAvatarSetup.CreateFromThisModel;
            importer.importAnimation = s.Animated;
            // Gear binds to bones by name, so no bones may be optimized away.
            importer.optimizeGameObjects = false;
        }

        void OnPreprocessAnimation()
        {
            var s = SidecarFor(assetPath);
            if (s == null || !s.Animated) return;
            var importer = (ModelImporter)assetImporter;
            var takes = importer.importedTakeInfos;
            var defaults = importer.defaultClipAnimations;
            var clips = new List<ModelImporterClipAnimation>();
            foreach (var clip in s.clips)
            {
                var take = takes.FirstOrDefault(t => TakeMatches(t.name, clip.name));
                if (string.IsNullOrEmpty(take.name))
                {
                    Debug.LogWarning($"{Log}{assetPath}: no take named \"{clip.name}\"; takes: " +
                                     string.Join(", ", takes.Select(t => t.name)));
                    continue;
                }
                var animation = defaults.FirstOrDefault(d => d.takeName == take.name) ??
                                new ModelImporterClipAnimation { takeName = take.name };
                var rate = take.sampleRate > 0 ? take.sampleRate : 30f;
                var first = take.startTime * rate;
                var last = take.stopTime * rate;
                if (clip.duration > 0)
                {
                    var wanted = first + clip.duration * rate;
                    if (wanted > last + 1f)
                        Debug.LogWarning($"{Log}{assetPath}: \"{clip.name}\" is {clip.duration:0.###}s in asset.json " +
                                         $"but its take is {(take.stopTime - take.startTime):0.###}s; using the take's length");
                    last = Mathf.Min(wanted, last);
                }
                animation.name = clip.name;
                animation.firstFrame = first;
                animation.lastFrame = last;
                animation.loopTime = clip.loop;
                clips.Add(animation);
            }
            importer.clipAnimations = clips.ToArray();
        }

        /// <summary>"idle" matches the takes "idle", "Armature|idle" and "mixamo.com|idle".</summary>
        public static bool TakeMatches(string take, string clip)
        {
            if (string.IsNullOrEmpty(take)) return false;
            if (string.Equals(take, clip, StringComparison.OrdinalIgnoreCase)) return true;
            var bar = take.LastIndexOf('|');
            return bar >= 0 && string.Equals(take.Substring(bar + 1), clip, StringComparison.OrdinalIgnoreCase);
        }

        void OnPostprocessModel(GameObject root)
        {
            if (SidecarFor(assetPath) == null) return;
            // The FBX importer makes its own LOD Group from the _LOD<n> names; the prefab has the real one.
            foreach (var group in root.GetComponentsInChildren<LODGroup>(true)) UnityEngine.Object.DestroyImmediate(group);
        }

        // ---- prefab -----------------------------------------------------------------

        static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
        {
            var folders = new SortedSet<string>(StringComparer.Ordinal);
            foreach (var path in imported)
            {
                if (!path.StartsWith(Root, StringComparison.Ordinal)) continue;
                if (path.EndsWith(".fbx", StringComparison.OrdinalIgnoreCase) && SidecarFor(path) != null)
                    folders.Add(Folder(path));
                else if (Path.GetFileName(path) == "asset.json" && ReadSidecar(Folder(path)) != null)
                    folders.Add(Folder(path));
            }
            foreach (var folder in folders)
            {
                try { Build(folder); }
                catch (Exception e) { Debug.LogError($"{Log}{folder}: {e}"); }
            }
        }

        /// <summary>Rebuilds the prefab of every export folder under Assets/Generated.</summary>
        [MenuItem("Tools/AssetForge/Rebuild All Prefabs")]
        public static void RebuildAll()
        {
            foreach (var json in Directory.GetFiles(Root, "asset.json", SearchOption.AllDirectories))
            {
                var folder = Folder(json);
                if (ReadSidecar(folder) == null) continue;
                AssetDatabase.ImportAsset(folder + "/" + ReadSidecar(folder).fbx, ImportAssetOptions.ForceUpdate);
                Build(folder);
            }
            AssetDatabase.SaveAssets();
        }

        /// <summary>Makes or updates the material, controller and prefab of one export folder.</summary>
        public static string Build(string folder)
        {
            var s = ReadSidecar(folder);
            if (s == null) return null;
            var name = BaseName(s);
            var modelPath = folder + "/" + s.fbx;
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(modelPath);
            if (model == null)
            {
                Debug.LogWarning($"{Log}{modelPath} is not imported yet");
                return null;
            }
            var material = BuildMaterial(folder, name, s);
            var controller = s.Animated ? BuildController(folder, name, s, modelPath) : null;
            var prefabPath = folder + "/" + name + ".prefab";

            var existing = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath) != null;
            GameObject root = null;
            if (existing)
            {
                try { root = PrefabUtility.LoadPrefabContents(prefabPath); }
                catch (Exception e)
                {
                    Debug.LogWarning($"{Log}{prefabPath} could not be opened ({e.Message}); making it anew");
                    existing = false;
                }
            }
            if (root == null) root = new GameObject(name);
            try
            {
                var instance = ModelInstance(root, model, modelPath);
                foreach (var renderer in instance.GetComponentsInChildren<Renderer>(true))
                    renderer.sharedMaterials = Enumerable.Repeat(material, Math.Max(1, renderer.sharedMaterials.Length)).ToArray();
                BuildLods(root, instance, s);
                var animator = instance.GetComponent<Animator>();
                if (s.Animated)
                {
                    if (animator == null) animator = instance.AddComponent<Animator>();
                    animator.runtimeAnimatorController = controller;
                    animator.avatar = AssetDatabase.LoadAllAssetsAtPath(modelPath).OfType<Avatar>().FirstOrDefault();
                }
                else if (animator != null)
                {
                    animator.runtimeAnimatorController = null;
                }
                if (s.IsWeapon) PlaceAtGrip(instance.transform, s.weapon != null ? s.weapon.grip : null);
                else instance.transform.SetLocalPositionAndRotation(Vector3.zero, Quaternion.identity);
                if (s.IsGear) BuildGear(root, instance, s, prefabPath);

                PrefabUtility.SaveAsPrefabAsset(root, prefabPath, out var saved);
                if (!saved) throw new Exception("the prefab could not be saved");
            }
            finally
            {
                if (existing) PrefabUtility.UnloadPrefabContents(root);
                else UnityEngine.Object.DestroyImmediate(root);
            }
            Debug.Log($"{Log}{(existing ? "Updated" : "Made")} {prefabPath}");
            return prefabPath;
        }

        /// <summary>The "Model" child: a nested instance of the FBX, kept when it already is one.</summary>
        static GameObject ModelInstance(GameObject root, GameObject model, string modelPath)
        {
            var current = root.transform.Find(ModelChild);
            if (current != null)
            {
                var source = PrefabUtility.GetCorrespondingObjectFromSource(current.gameObject);
                if (source != null && AssetDatabase.GetAssetPath(source) == modelPath &&
                    PrefabUtility.IsOutermostPrefabInstanceRoot(current.gameObject))
                    return current.gameObject;
                UnityEngine.Object.DestroyImmediate(current.gameObject);
            }
            var instance = (GameObject)PrefabUtility.InstantiatePrefab(model, root.scene);
            instance.name = ModelChild;
            instance.transform.SetParent(root.transform, false);
            instance.transform.SetAsFirstSibling();
            return instance;
        }

        static Material BuildMaterial(string folder, string name, Sidecar s)
        {
            var relative = s.material != null && !string.IsNullOrEmpty(s.material.file)
                ? s.material.file : $"Materials/{name}.mat";
            var path = folder + "/" + relative;
            EnsureFolder(Folder(path));
            var shader = Shader.Find("Universal Render Pipeline/Lit");
            if (shader == null)
            {
                Debug.LogWarning($"{Log}URP is not installed; {path} uses the Standard shader");
                shader = Shader.Find("Standard");
            }
            var material = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (material == null)
            {
                material = new Material(shader) { name = name };
                AssetDatabase.CreateAsset(material, path);
            }
            else if (material.shader != shader)
            {
                material.shader = shader;
            }
            var albedo = s.material != null && !string.IsNullOrEmpty(s.material.albedo)
                ? AssetDatabase.LoadAssetAtPath<Texture2D>(folder + "/" + s.material.albedo) : null;
            if (albedo == null) Debug.LogWarning($"{Log}{folder}: albedo texture not found");
            if (material.HasProperty("_BaseMap")) material.SetTexture("_BaseMap", albedo);
            if (material.HasProperty("_MainTex")) material.SetTexture("_MainTex", albedo);
            if (material.HasProperty("_BaseColor")) material.SetColor("_BaseColor", Color.white);
            if (material.HasProperty("_Smoothness")) material.SetFloat("_Smoothness", 0.2f);
            if (material.HasProperty("_Metallic")) material.SetFloat("_Metallic", 0f);
            EditorUtility.SetDirty(material);
            return material;
        }

        static AnimatorController BuildController(string folder, string name, Sidecar s, string modelPath)
        {
            var path = folder + "/" + name + ".controller";
            var controller = AssetDatabase.LoadAssetAtPath<AnimatorController>(path);
            if (controller == null) controller = AnimatorController.CreateAnimatorControllerAtPath(path);
            var clips = AssetDatabase.LoadAllAssetsAtPath(modelPath).OfType<AnimationClip>()
                .Where(c => !c.name.StartsWith("__preview__", StringComparison.Ordinal))
                .GroupBy(c => c.name).ToDictionary(g => g.Key, g => g.First());
            var machine = controller.layers[0].stateMachine;
            var states = machine.states.Select(c => c.state).ToList();
            var wanted = s.clips.Select(c => c.name).Where(clips.ContainsKey).ToList();
            foreach (var stale in states.Where(st => !wanted.Contains(st.name)).ToList())
            {
                machine.RemoveState(stale);
                states.Remove(stale);
            }
            for (var i = 0; i < wanted.Count; i++)
            {
                var state = states.FirstOrDefault(st => st.name == wanted[i]) ??
                            machine.AddState(wanted[i], new Vector3(300, 60 * i, 0));
                state.motion = clips[wanted[i]];
            }
            var defaultName = wanted.Contains("idle") ? "idle" : wanted.FirstOrDefault();
            machine.defaultState = machine.states.Select(c => c.state).FirstOrDefault(st => st.name == defaultName);
            EditorUtility.SetDirty(controller);
            return controller;
        }

        static readonly Regex LodName = new Regex(@"_LOD(\d+)$", RegexOptions.IgnoreCase);

        /// <summary>
        /// LOD Group on the prefab root. Level n shows down to a screen height of
        /// 0.5 × ratio(n + 1) / ratio(0) (so 0.25 and 0.1 for ratios 1, 0.5, 0.2);
        /// the last level culls at 1 %.
        /// </summary>
        static void BuildLods(GameObject root, GameObject instance, Sidecar s)
        {
            var byLevel = new SortedDictionary<int, List<Renderer>>();
            foreach (var renderer in instance.GetComponentsInChildren<Renderer>(true))
            {
                var match = LodName.Match(renderer.name);
                var level = match.Success ? int.Parse(match.Groups[1].Value) : 0;
                if (!byLevel.TryGetValue(level, out var list)) byLevel[level] = list = new List<Renderer>();
                list.Add(renderer);
            }
            var group = root.GetComponent<LODGroup>();
            if (group == null) group = root.AddComponent<LODGroup>();
            var ratios = (s.lods ?? new SidecarLod[0]).ToDictionary(l => l.level, l => l.ratio);
            var levels = byLevel.Keys.ToList();
            var top = ratios.TryGetValue(levels.FirstOrDefault(), out var r0) && r0 > 0 ? r0 : 1f;
            var lods = new LOD[levels.Count];
            var previous = 1f;
            for (var i = 0; i < levels.Count; i++)
            {
                float height;
                if (i == levels.Count - 1) height = 0.01f;
                else
                {
                    var next = ratios.TryGetValue(levels[i + 1], out var rn) && rn > 0 ? rn : top / (i + 2);
                    height = 0.5f * next / top;
                }
                height = Mathf.Clamp(height, 0.001f, previous - 0.001f);
                previous = height;
                lods[i] = new LOD(height, byLevel[levels[i]].ToArray());
            }
            group.SetLODs(lods);
            group.RecalculateBounds();
        }

        /// <summary>Puts the model so its grip point sits at the prefab root, turned by the grip rotation.</summary>
        static void PlaceAtGrip(Transform model, SidecarGrip grip)
        {
            var position = Vector(grip?.position);
            var rotation = Quaternion.Euler(Vector(grip?.rotation));
            var inverse = Quaternion.Inverse(rotation);
            model.SetLocalPositionAndRotation(inverse * -position, inverse);
        }

        static Vector3 Vector(float[] values) =>
            values != null && values.Length >= 3 ? new Vector3(values[0], values[1], values[2]) : Vector3.zero;

        static void BuildGear(GameObject root, GameObject instance, Sidecar s, string prefabPath)
        {
            var gear = root.GetComponent<AssetForgeGear>();
            if (gear == null) gear = root.AddComponent<AssetForgeGear>();
            gear.slot = s.gear?.slot;
            gear.skeleton = string.IsNullOrEmpty(s.gear?.skeleton) ? "biped" : s.gear.skeleton;
            gear.hideBody = s.gear?.hide_body ?? new string[0];
            var renderers = instance.GetComponentsInChildren<SkinnedMeshRenderer>(true);
            if (renderers.Length == 0)
            {
                Debug.LogWarning($"{Log}{prefabPath}: the gear has no skinned mesh, so it cannot follow a skeleton");
                return;
            }
            var known = new HashSet<string>((s.gear?.bones ?? new string[0]).Select(AssetForgeGear.BoneKey));
            if (known.Count == 0) return;
            var unknown = renderers.SelectMany(r => r.bones).Where(b => b != null)
                .Select(b => AssetForgeGear.BoneKey(b.name)).Where(n => !known.Contains(n)).Distinct().ToList();
            if (unknown.Count > 0)
                Debug.LogWarning($"{Log}{prefabPath}: bones not in the {gear.skeleton} skeleton: {string.Join(", ", unknown)}");
        }

        static void EnsureFolder(string path)
        {
            if (AssetDatabase.IsValidFolder(path)) return;
            EnsureFolder(Folder(path));
            AssetDatabase.CreateFolder(Folder(path), Path.GetFileName(path));
        }
    }
}
