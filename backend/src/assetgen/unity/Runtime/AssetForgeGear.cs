// assetgen-importer-version: 1
// Written by assetgen (Forge Of Figures). Replaced when a newer version ships; do not edit.
//
// Sits on every generated gear prefab. When the gear is parented under a
// character (or BindTo is called), its skinned meshes are bound to that
// character's skeleton by bone name, so it follows the character's animation.
using System.Collections.Generic;
using UnityEngine;

namespace AssetForge
{
    [DisallowMultipleComponent]
    public class AssetForgeGear : MonoBehaviour
    {
        [Tooltip("Gear slot from assetgen: head, chest, legs, hands, feet or cape.")]
        public string slot;

        [Tooltip("Skeleton the gear is skinned to (biped).")]
        public string skeleton = "biped";

        [Tooltip("Body parts the gear covers, for the game to hide.")]
        public string[] hideBody = new string[0];

        [Tooltip("Bind to the parent character's skeleton when the scene starts.")]
        public bool bindOnStart = true;

        void Start()
        {
            if (!bindOnStart || transform.parent == null) return;
            var target = FindCharacter(transform.parent);
            if (target != null) BindTo(target);
        }

        /// <summary>The character a gear piece parented under <paramref name="from"/> belongs to.</summary>
        public static Transform FindCharacter(Transform from)
        {
            var animator = from.GetComponentInParent<Animator>();
            return animator != null ? animator.transform : from.root;
        }

        /// <summary>Strips an importer prefix such as "mixamorig:" so both sides compare alike.</summary>
        public static string BoneKey(string name)
        {
            var colon = name.LastIndexOf(':');
            return colon >= 0 ? name.Substring(colon + 1) : name;
        }

        /// <summary>
        /// Points every skinned mesh of this gear at the bones of the same name
        /// under <paramref name="skeletonRoot"/>. Returns the number of bones
        /// that had no match (0 when the gear is fully bound).
        /// </summary>
        public int BindTo(Transform skeletonRoot)
        {
            var bones = new Dictionary<string, Transform>();
            foreach (var t in skeletonRoot.GetComponentsInChildren<Transform>(true))
            {
                if (t.IsChildOf(transform)) continue;
                var key = BoneKey(t.name);
                if (!bones.ContainsKey(key)) bones[key] = t;
            }
            var missing = 0;
            foreach (var renderer in GetComponentsInChildren<SkinnedMeshRenderer>(true))
            {
                var mapped = renderer.bones;
                for (var i = 0; i < mapped.Length; i++)
                {
                    if (mapped[i] != null && bones.TryGetValue(BoneKey(mapped[i].name), out var bone)) mapped[i] = bone;
                    else missing++;
                }
                renderer.bones = mapped;
                if (renderer.rootBone != null && bones.TryGetValue(BoneKey(renderer.rootBone.name), out var root))
                    renderer.rootBone = root;
            }
            if (missing > 0) Debug.LogWarning($"{name}: {missing} bone(s) not found under {skeletonRoot.name}", this);
            return missing;
        }
    }
}
