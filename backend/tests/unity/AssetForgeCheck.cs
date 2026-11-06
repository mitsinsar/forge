// Check of the AssetForge import script, run in batch mode (not shipped to games).
// Copy into <UnityProject>/Assets/Editor/ and run
//   Unity -batchmode -nographics -projectPath <UnityProject> -executeMethod AssetForgeCheck.Run
//         -afReport <report.json> -quit -logFile -
// It writes what Unity made of every export folder under Assets/Generated to
// the report and exits with code 1 when a check fails. The first run also saves
// Assets/AssetForgeCheck.unity with an instance of every prefab; later runs (after
// a re-export) check that those instances still point at their prefabs.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AssetForge;
using AssetForge.Editor;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class AssetForgeCheck
{
    static readonly List<string> Failures = new List<string>();
    static readonly StringBuilder Json = new StringBuilder();

    static void Check(bool ok, string what)
    {
        if (!ok) Failures.Add(what);
    }

    static string Q(string s) => s == null ? "null" : "\"" + s.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    static string F(float f) => f.ToString("0.####", System.Globalization.CultureInfo.InvariantCulture);
    static string V(Vector3 v) => $"[{F(v.x)}, {F(v.y)}, {F(v.z)}]";

    public static void Run()
    {
        var args = Environment.GetCommandLineArgs();
        var at = Array.IndexOf(args, "-afReport");
        var reportPath = at >= 0 && at + 1 < args.Length ? args[at + 1] : "assetforge-report.json";
        Json.Append("{\n  \"assets\": [\n");
        var first = true;
        GameObject hero = null;
        foreach (var json in Directory.GetFiles(AssetForgeImporter.Root, "asset.json", SearchOption.AllDirectories).OrderBy(p => p))
        {
            var folder = Path.GetDirectoryName(json).Replace('\\', '/');
            var s = AssetForgeImporter.ReadSidecar(folder);
            if (s == null) continue;
            if (!first) Json.Append(",\n");
            first = false;
            var report = Inspect(folder, s);
            Json.Append(report.Item1);
            if (s.kind == "hero") hero = report.Item2;
        }
        Json.Append("\n  ],\n");
        Json.Append("  \"gear_binding\": " + GearBinding(hero) + ",\n");
        Json.Append("  \"scene\": " + SceneReferences() + ",\n");
        Json.Append("  \"failures\": [" + string.Join(", ", Failures.Select(Q)) + "]\n}\n");
        File.WriteAllText(reportPath, Json.ToString());
        Debug.Log($"AssetForgeCheck: {Failures.Count} failure(s); report at {reportPath}\n{Json}");
        EditorApplication.Exit(Failures.Count == 0 ? 0 : 1);
    }

    static Tuple<string, GameObject> Inspect(string folder, Sidecar s)
    {
        var name = Path.GetFileNameWithoutExtension(s.fbx);
        var prefabPath = folder + "/" + name + ".prefab";
        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
        var o = new StringBuilder();
        o.Append($"    {{\"kind\": {Q(s.kind)}, \"name\": {Q(name)}, \"prefab\": {Q(prefab ? prefabPath : null)}");
        Check(prefab != null, $"{name}: no prefab");
        if (prefab == null) return Tuple.Create(o.Append("}").ToString(), (GameObject)null);
        o.Append($", \"prefab_guid\": {Q(AssetDatabase.AssetPathToGUID(prefabPath))}");
        o.Append($", \"root_id\": {Q(GlobalObjectId.GetGlobalObjectIdSlow(prefab).ToString())}");

        var importer = (ModelImporter)AssetImporter.GetAtPath(folder + "/" + s.fbx);
        o.Append($", \"fbx_guid\": {Q(AssetDatabase.AssetPathToGUID(folder + "/" + s.fbx))}");
        o.Append($", \"animation_type\": {Q(importer.animationType.ToString())}");
        o.Append($", \"material_import\": {Q(importer.materialImportMode.ToString())}");

        var group = prefab.GetComponent<LODGroup>();
        Check(group != null, $"{name}: no LODGroup");
        if (group != null)
        {
            var lods = group.GetLODs();
            o.Append(", \"lods\": [" + string.Join(", ", lods.Select(l =>
                $"{{\"height\": {F(l.screenRelativeTransitionHeight)}, \"renderers\": [{string.Join(", ", l.renderers.Select(r => Q(r ? r.name : null)))}]}}")) + "]");
            Check(lods.Length == s.lods.Length, $"{name}: {lods.Length} LODs, asset.json has {s.lods.Length}");
            Check(prefab.GetComponentsInChildren<LODGroup>(true).Length == 1, $"{name}: more than one LODGroup");
        }

        var renderers = prefab.GetComponentsInChildren<Renderer>(true);
        var materials = renderers.SelectMany(r => r.sharedMaterials).Distinct().ToList();
        o.Append(", \"materials\": [" + string.Join(", ", materials.Select(m => m == null ? "null" :
            $"{{\"path\": {Q(AssetDatabase.GetAssetPath(m))}, \"shader\": {Q(m.shader.name)}, \"base_map\": {Q(m.GetTexture("_BaseMap") ? m.GetTexture("_BaseMap").name : null)}}}")) + "]");
        Check(materials.Count == 1 && materials[0] != null && materials[0].shader.name == "Universal Render Pipeline/Lit"
              && materials[0].GetTexture("_BaseMap") != null, $"{name}: not one URP Lit material with the albedo");

        var animator = prefab.GetComponentInChildren<Animator>(true);
        if (s.kind == "hero" || s.kind == "monster" || s.kind == "boss")
        {
            Check(animator != null && animator.runtimeAnimatorController != null, $"{name}: no Animator with a controller");
            if (animator != null && animator.runtimeAnimatorController is AnimatorController controller)
            {
                var machine = controller.layers[0].stateMachine;
                var states = machine.states.Select(c => c.state).ToList();
                o.Append($", \"avatar\": {{\"human\": {(animator.avatar && animator.avatar.isHuman ? "true" : "false")}, \"valid\": {(animator.avatar && animator.avatar.isValid ? "true" : "false")}}}");
                o.Append($", \"default_state\": {Q(machine.defaultState ? machine.defaultState.name : null)}");
                o.Append(", \"states\": [" + string.Join(", ", states.Select(st =>
                {
                    var clip = st.motion as AnimationClip;
                    return $"{{\"name\": {Q(st.name)}, \"clip\": {Q(clip ? clip.name : null)}, \"length\": {F(clip ? clip.length : 0)}, \"loop\": {(clip && clip.isLooping ? "true" : "false")}}}";
                })) + "]");
                Check(states.Select(st => st.name).SequenceEqual(s.clips.Select(c => c.name)), $"{name}: states are not every clip");
                Check(machine.defaultState && machine.defaultState.name == "idle", $"{name}: idle is not the default state");
                foreach (var c in s.clips)
                {
                    var clip = states.First(st => st.name == c.name).motion as AnimationClip;
                    Check(clip && clip.isLooping == c.loop, $"{name}: {c.name} loop flag");
                    Check(clip && Mathf.Abs(clip.length - c.duration) < 0.05f, $"{name}: {c.name} is {(clip ? clip.length : 0)}s, asset.json says {c.duration}s");
                }
                if (s.IsHumanoid)
                    Check(animator.avatar && animator.avatar.isHuman && animator.avatar.isValid, $"{name}: no valid Humanoid avatar");
            }
        }
        if (s.kind == "weapon")
        {
            var model = prefab.transform.Find(AssetForgeImporter.ModelChild);
            var grip = s.weapon.grip;
            o.Append($", \"socket\": {Q(s.weapon.socket)}, \"model_local_position\": {(model ? V(model.localPosition) : "null")}, \"grip\": {V(new Vector3(grip.position[0], grip.position[1], grip.position[2]))}");
            Check(model && (model.localPosition + new Vector3(grip.position[0], grip.position[1], grip.position[2])).magnitude < 1e-4f,
                  $"{name}: the model is not held at the grip");
            Check(importer.animationType == ModelImporterAnimationType.None, $"{name}: weapon imported with a rig");
        }
        if (s.kind == "gear")
        {
            var gear = prefab.GetComponent<AssetForgeGear>();
            var skinned = prefab.GetComponentsInChildren<SkinnedMeshRenderer>(true);
            o.Append($", \"gear\": {{\"slot\": {Q(gear ? gear.slot : null)}, \"hide_body\": [{string.Join(", ", (gear ? gear.hideBody : new string[0]).Select(Q))}], \"skinned_meshes\": {skinned.Length}, \"bones\": [{string.Join(", ", skinned.SelectMany(r => r.bones).Where(b => b).Select(b => Q(b.name)).Distinct())}]}}");
            Check(gear != null && gear.slot == s.gear.slot, $"{name}: no AssetForgeGear with the slot");
            Check(skinned.Length > 0, $"{name}: no skinned mesh");
        }
        return Tuple.Create(o.Append("}").ToString(), prefab);
    }

    const string ScenePath = "Assets/AssetForgeCheck.unity";

    /// <summary>Saves a scene with every prefab the first time; later, checks its instances are still linked.</summary>
    static string SceneReferences()
    {
        var prefabs = Directory.GetFiles(AssetForgeImporter.Root, "*.prefab", SearchOption.AllDirectories)
            .Select(p => p.Replace('\\', '/')).OrderBy(p => p).ToList();
        if (!File.Exists(ScenePath))
        {
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            foreach (var path in prefabs)
                PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(path), scene);
            EditorSceneManager.SaveScene(scene, ScenePath);
            return $"{{\"created\": true, \"instances\": {prefabs.Count}}}";
        }
        var opened = EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
        var linked = opened.GetRootGameObjects()
            .Select(go => PrefabUtility.GetCorrespondingObjectFromSource(go))
            .Where(src => src != null).Select(src => AssetDatabase.GetAssetPath(src)).OrderBy(p => p).ToList();
        Check(linked.SequenceEqual(prefabs), "scene instances lost their prefabs: " + string.Join(", ", linked));
        return $"{{\"created\": false, \"linked\": [{string.Join(", ", linked.Select(Q))}]}}";
    }

    /// <summary>Puts every gear prefab on the hero and binds it by bone names.</summary>
    static string GearBinding(GameObject heroPrefab)
    {
        if (heroPrefab == null) return "null";
        var results = new List<string>();
        foreach (var json in Directory.GetFiles(AssetForgeImporter.Root, "asset.json", SearchOption.AllDirectories))
        {
            var folder = Path.GetDirectoryName(json).Replace('\\', '/');
            var s = AssetForgeImporter.ReadSidecar(folder);
            if (s == null || s.kind != "gear") continue;
            var gearPrefab = AssetDatabase.LoadAssetAtPath<GameObject>(folder + "/" + Path.GetFileNameWithoutExtension(s.fbx) + ".prefab");
            if (gearPrefab == null) continue;
            var hero = (GameObject)PrefabUtility.InstantiatePrefab(heroPrefab);
            var gear = (GameObject)PrefabUtility.InstantiatePrefab(gearPrefab);
            var animator = hero.GetComponentInChildren<Animator>();
            gear.transform.SetParent(animator.transform, false);
            var character = AssetForgeGear.FindCharacter(gear.transform.parent);
            var missing = gear.GetComponent<AssetForgeGear>().BindTo(character);
            var bound = gear.GetComponentsInChildren<SkinnedMeshRenderer>(true).SelectMany(r => r.bones)
                .All(b => b != null && b.IsChildOf(hero.transform) && !b.IsChildOf(gear.transform));
            Check(missing == 0 && bound, $"{s.name}: not bound to the hero's bones ({missing} missing)");
            results.Add($"{{\"gear\": {Q(s.name)}, \"missing\": {missing}, \"all_bones_on_hero\": {(bound ? "true" : "false")}}}");
            UnityEngine.Object.DestroyImmediate(hero);
        }
        return "[" + string.Join(", ", results) + "]";
    }
}
