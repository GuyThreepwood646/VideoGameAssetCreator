// Batch-mode validation importer for Video Game Asset Creator.
// Run: Unity.exe -batchmode -nographics -quit -projectPath <sandbox> -executeMethod VgacImport.Run -vgacInput <dir> -vgacName <name>
//      [-vgacHumanoid] [-vgacClips <library.fbx>] [-vgacSpeedScale <f>] [-vgacKeepPose]
//      [-vgacHitboxes <hitboxes.json>] [-vgacMovementShape auto|upright|along|box|none] [-vgacScale <f>] [-vgacClipInfo <anim_clips.json>]
// Expects in <dir>: <name>.fbx (meshes named <name>_LOD0..n, optional <name>_collider, optional armature), textures
// base_color.png, normal.png, metallic.png, roughness.png, ao.png and T_<name>_MetallicSmoothness.png (any subset).
// Writes <dir>/unity_report.json.
//
// Version: raise it with every change. The backend (stages/export.py) copies this file into a user's Unity project
// when the copy there is missing or has a lower Version (docs/GREAT-ASSETS-PLAN.md 2.6).
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;
using UnityEngine.Rendering;

public static class VgacImport
{
    public const string Version = "4";

    [Serializable]
    class ClipInfo
    {
        public string name = "";
        public bool loop;          // Loop Time as imported (read back from the clip)
        public bool defaultLoop;   // Loop Time Unity chose by itself before this script set it
        public bool bakedInPlace;  // root rotation, height and XZ baked into the pose
    }

    [Serializable]
    class ControllerInfo
    {
        public string path = "";
        public List<string> states = new List<string>();
        public List<string> parameters = new List<string>();
        public List<string> locomotion = new List<string>();  // "clip@speed" for each blend tree child
        public List<string> roles = new List<string>();       // "Attack=Rig|Sword_Attack" and so on
        public int moreMoves;
        public bool applyRootMotion;
    }

    [Serializable]
    class Report
    {
        public bool ok;
        public string error = "";
        public string importer_version = Version;
        public string prefab = "";
        public int lodCount;
        public List<int> lodTriangles = new List<int>();
        public int bones;
        public string avatar = "";
        public bool humanoidValid;
        public string avatar_mapping = "";     // explicit | auto | generic | none
        public string avatar_mapping_by = "";  // names | shape (explicit only)
        public int humanBonesMapped;
        public bool tPoseApplied;
        public int colliderVertices;
        public string shader = "";
        public List<string> texturesAssigned = new List<string>();
        public List<string> textureSettings = new List<string>();
        public List<string> warnings = new List<string>();
        public int clipCount;
        public List<ClipInfo> clips = new List<ClipInfo>();
        public ControllerInfo controller = new ControllerInfo();
        public bool clipInfoUsed;              // loop flags came from anim_clips.json, not from the name rule
        public int hitboxCount;
        public string movementShape = "";      // upright | along | box | none; "" when no hit-box file was given
        public float hitboxMaxBoneError;       // metres: worst bone-position mismatch between the file and the prefab
    }

    // hitboxes.json (backend/vgac/blender/hitboxes.py to_json): glTF model space, Y up, metres.
    [Serializable]
    class HitShape
    {
        public string name = "", region = "", label = "", bone = "", kind = "";
        public float[] center = new float[3], axis = new float[3], forward = new float[3], size = new float[3];
        public float[] bone_head = new float[3], offset = new float[3];
        public float radius, height;
        public int direction = 1;
    }

    [Serializable]
    class HitMovement
    {
        public string @default = "";
        public HitShape upright, along, box;
    }

    [Serializable]
    class HitDoc
    {
        public List<HitShape> hitboxes = new List<HitShape>();
        public HitMovement movement;
    }

    static string Arg(string key)
    {
        var a = Environment.GetCommandLineArgs();
        for (int i = 0; i < a.Length - 1; i++) if (a[i] == key) return a[i + 1];
        return null;
    }

    static bool Flag(string key) => Environment.GetCommandLineArgs().Contains(key);

    public static void Run()
    {
        var input = Arg("-vgacInput");
        var name = Arg("-vgacName");
        var humanoid = Flag("-vgacHumanoid");
        float speedScale = 1f;
        if (Arg("-vgacSpeedScale") != null) float.TryParse(Arg("-vgacSpeedScale"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out speedScale);
        if (speedScale <= 0.01f) speedScale = 1f;
        var report = new Report();
        try
        {
            if (input == null || name == null) throw new Exception("missing -vgacInput or -vgacName");
            var assetDir = "Assets/VGAC/" + name;
            Directory.CreateDirectory(assetDir);
            var fbxSrc = Path.Combine(input, name + ".fbx");
            if (!File.Exists(fbxSrc)) fbxSrc = Directory.GetFiles(input, "*.fbx").FirstOrDefault() ?? fbxSrc;
            if (File.Exists(fbxSrc)) File.Copy(fbxSrc, Path.Combine(assetDir, name + ".fbx"), true);
            var msName = "T_" + name + "_MetallicSmoothness.png";
            foreach (var f in new[] { "base_color.png", "normal.png", "metallic.png", "roughness.png", "ao.png" })
            {
                var src = Path.Combine(input, f);
                if (File.Exists(src)) File.Copy(src, Path.Combine(assetDir, f), true);
            }
            var msSrc = Path.Combine(input, msName);
            if (!File.Exists(msSrc)) msSrc = Path.Combine(input, "metallic_smoothness.png");
            if (File.Exists(msSrc)) File.Copy(msSrc, Path.Combine(assetDir, msName), true);
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

            ConfigureTextures(assetDir, msName, report);

            // First import as Generic: the rest transforms it gives are what the explicit human mapping is built from.
            var fbxPath = assetDir + "/" + name + ".fbx";
            var importer = AssetImporter.GetAtPath(fbxPath) as ModelImporter;
            if (importer == null) throw new Exception("FBX not imported: " + fbxPath);
            importer.materialImportMode = ModelImporterMaterialImportMode.None;
            importer.importNormals = ModelImporterNormals.Import;
            importer.importTangents = ModelImporterTangents.CalculateMikk;
            importer.animationType = ModelImporterAnimationType.Generic;
            importer.importAnimation = true;
            importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
            importer.clipAnimations = new ModelImporterClipAnimation[0];
            importer.SaveAndReimport();

            if (humanoid)
            {
                report.avatar_mapping = MakeHumanoid(importer, fbxPath, report, Flag("-vgacKeepPose"));
                humanoid = report.avatar_mapping != "generic";
            }
            else report.avatar_mapping = "none";

            var model = AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath);
            if (model == null) throw new Exception("model load failed");

            var mat = BuildMaterial(assetDir, msName, report);
            AssetDatabase.CreateAsset(mat, assetDir + "/" + name + ".mat");

            var instance = (GameObject)PrefabUtility.InstantiatePrefab(model);
            instance.name = name;
            var renderers = instance.GetComponentsInChildren<Renderer>(true);
            foreach (var r in renderers) r.sharedMaterial = mat;

            // Hit boxes and the body shape for moving (G34): a rigged character gets trigger shapes on its bones and a
            // simple body shape on the root, which replaces the convex hull (a hull does not follow the animation).
            bool rigged = instance.GetComponentsInChildren<SkinnedMeshRenderer>(true).Any(s => s.bones.Length > 0);
            bool bodyShape = false;
            var hitPath = Arg("-vgacHitboxes");
            if (hitPath != null && File.Exists(hitPath) && rigged)
                bodyShape = AddHitboxes(instance, hitPath, Arg("-vgacMovementShape") ?? "auto", ParseFloat(Arg("-vgacScale"), 1f), report);

            // Collider: convex MeshCollider from <name>_collider, renderer disabled. Props keep it as their collider.
            var colObj = instance.GetComponentsInChildren<MeshFilter>(true).FirstOrDefault(m => m.name.EndsWith("_collider"));
            if (colObj != null)
            {
                if (!bodyShape)
                {
                    var mc = instance.AddComponent<MeshCollider>();
                    mc.sharedMesh = colObj.sharedMesh;
                    mc.convex = true;
                    report.colliderVertices = colObj.sharedMesh.vertexCount;
                }
                var cr = colObj.GetComponent<Renderer>();
                if (cr) cr.enabled = false;
            }
            else if (!bodyShape) report.warnings.Add("no collider mesh");

            // LODGroup: Unity creates it from _LODn names; verify, else build one.
            var lodGroup = instance.GetComponentInChildren<LODGroup>();
            var lodRenderers = renderers.Where(r => r.name.Contains("_LOD")).OrderBy(r => r.name).ToList();
            if (lodGroup == null && lodRenderers.Count > 0)
            {
                lodGroup = instance.AddComponent<LODGroup>();
                var lods = new LOD[lodRenderers.Count];
                float[] heights = { 0.6f, 0.3f, 0.12f, 0.03f };
                for (int i = 0; i < lodRenderers.Count; i++)
                    lods[i] = new LOD(i < heights.Length ? heights[i] : 0.01f, new[] { lodRenderers[i] });
                lodGroup.SetLODs(lods);
                lodGroup.RecalculateBounds();
                report.warnings.Add("LODGroup built by script, not by importer naming");
            }
            if (lodGroup != null)
            {
                report.lodCount = lodGroup.lodCount;
                foreach (var lod in lodGroup.GetLODs())
                {
                    int tris = 0;
                    foreach (var r in lod.renderers)
                    {
                        var mf = r.GetComponent<MeshFilter>(); var smr = r as SkinnedMeshRenderer;
                        var mesh = smr ? smr.sharedMesh : (mf ? mf.sharedMesh : null);
                        if (mesh) tris += mesh.triangles.Length / 3;
                    }
                    report.lodTriangles.Add(tris);
                }
            }
            else report.warnings.Add("no LODGroup");

            // Rig and avatar. In-place cycles with root motion off: the game moves the object, the clips animate it.
            var animator = instance.GetComponent<Animator>() ?? instance.AddComponent<Animator>();
            animator.applyRootMotion = false;
            var avatar = AssetDatabase.LoadAllAssetsAtPath(fbxPath).OfType<Avatar>().FirstOrDefault();
            if (avatar != null)
            {
                animator.avatar = avatar;
                report.avatar = avatar.name;
                report.humanoidValid = avatar.isHuman && avatar.isValid;
            }
            var smrs = instance.GetComponentsInChildren<SkinnedMeshRenderer>(true);
            if (smrs.Length > 0) report.bones = smrs[0].bones.Length;

            // Clips: the shared library for a valid Humanoid, else the clips baked into the model FBX itself.
            List<AnimationClip> clipSet = null;
            var clipsSrc = Arg("-vgacClips");
            if (clipsSrc != null && File.Exists(clipsSrc) && avatar != null && avatar.isHuman && avatar.isValid)
            {
                var clipsPath = assetDir + "/clips.fbx";
                File.Copy(clipsSrc, clipsPath, true);
                AssetDatabase.ImportAsset(clipsPath, ImportAssetOptions.ForceSynchronousImport);
                var ci = AssetImporter.GetAtPath(clipsPath) as ModelImporter;
                ci.animationType = ModelImporterAnimationType.Human;
                ci.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
                ci.importAnimation = true;
                ci.materialImportMode = ModelImporterMaterialImportMode.None;
                ci.clipAnimations = new ModelImporterClipAnimation[0];
                ci.SaveAndReimport();
                SetClipLoops(ci, report, null);
                clipSet = LoadClips(clipsPath);
            }
            else
            {
                if (clipsSrc != null) report.warnings.Add("clips library skipped: avatar not humanoid or file missing");
                SetClipLoops(importer, report, ReadClipInfo(Arg("-vgacClipInfo"), report));
                clipSet = LoadClips(fbxPath);
            }
            // Read Loop Time back from the imported clips: that is what the game gets.
            foreach (var info in report.clips)
            {
                var c = clipSet.FirstOrDefault(x => x.name == info.name);
                if (c != null) info.loop = AnimationUtility.GetAnimationClipSettings(c).loopTime;
            }
            if (clipSet.Count > 0)
            {
                var ctrl = BuildController(assetDir + "/" + name + ".controller", clipSet, humanoid, speedScale, report.controller);
                animator.runtimeAnimatorController = ctrl;
                report.clipCount = clipSet.Count;
            }
            report.controller.applyRootMotion = animator.applyRootMotion;

            var prefabPath = assetDir + "/" + name + ".prefab";
            PrefabUtility.SaveAsPrefabAsset(instance, prefabPath);
            UnityEngine.Object.DestroyImmediate(instance);
            report.prefab = prefabPath;
            report.ok = true;
        }
        catch (Exception e)
        {
            report.ok = false;
            report.error = e.ToString();
        }
        var json = JsonUtility.ToJson(report, true);
        File.WriteAllText(Path.Combine(input ?? ".", "unity_report.json"), json);
        Debug.Log("VGAC_REPORT " + json);
        if (!report.ok) EditorApplication.Exit(1);
    }

    static float ParseFloat(string s, float fallback) =>
        s != null && float.TryParse(s, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var v) ? v : fallback;

    // ---------------------------------------------------------------------------------------------------------------
    // Hit boxes (G34)

    // glTF model space to Unity model space for this FBX (Blender export, axis_forward -Z, axis_up Y): x flips.
    static Vector3 U(float[] v, float scale) => v == null || v.Length < 3 ? Vector3.zero : new Vector3(-v[0], v[1], v[2]) * scale;

    static bool AddHitboxes(GameObject root, string path, string choice, float scale, Report report)
    {
        var doc = JsonUtility.FromJson<HitDoc>(File.ReadAllText(path));
        if (doc == null) { report.warnings.Add("hit boxes: file not readable"); return false; }
        var all = root.GetComponentsInChildren<Transform>(true);
        Transform Find(string n) => string.IsNullOrEmpty(n) ? null
            : (all.FirstOrDefault(t => t.name == n) ?? all.FirstOrDefault(t => StripName(t.name) == StripName(n)));
        var rt = root.transform;
        float worst = 0f;
        foreach (var h in doc.hitboxes ?? new List<HitShape>())
        {
            var bone = Find(h.bone);
            if (bone == null)
            {
                if (!string.IsNullOrEmpty(h.bone)) report.warnings.Add("hit box " + h.name + ": bone " + h.bone + " not found; placed on the root");
                bone = rt;
            }
            var go = new GameObject(string.IsNullOrEmpty(h.name) ? "Hit" : h.name);
            go.transform.SetParent(bone, false);
            go.transform.localPosition = bone.InverseTransformPoint(rt.TransformPoint(U(h.center, scale)));
            var fwd = U(h.forward, 1f); var up = U(h.axis, 1f);
            if (up.sqrMagnitude < 1e-8f) up = Vector3.up;
            if (fwd.sqrMagnitude < 1e-8f || Vector3.Cross(fwd, up).sqrMagnitude < 1e-8f) fwd = Vector3.Cross(up, Vector3.right).sqrMagnitude > 1e-6f ? Vector3.Cross(Vector3.right, up) : Vector3.forward;
            go.transform.rotation = rt.rotation * Quaternion.LookRotation(fwd, up);
            // Undo any scale on the bone so collider sizes are in metres.
            var ls = bone.lossyScale;
            go.transform.localScale = new Vector3(1f / Mathf.Max(1e-6f, ls.x), 1f / Mathf.Max(1e-6f, ls.y), 1f / Mathf.Max(1e-6f, ls.z));
            if (h.kind == "sphere")
            {
                var c = go.AddComponent<SphereCollider>(); c.radius = h.radius * scale; c.isTrigger = true;
            }
            else if (h.kind == "box")
            {
                var c = go.AddComponent<BoxCollider>(); c.size = new Vector3(h.size[0], h.size[1], h.size[2]) * scale; c.isTrigger = true;
            }
            else
            {
                var c = go.AddComponent<CapsuleCollider>(); c.direction = 1; c.radius = h.radius * scale; c.height = h.height * scale; c.isTrigger = true;
            }
            // Check: the file's bone head should sit where the prefab's bone is.
            if (bone != rt && h.bone_head != null && h.bone_head.Length == 3)
                worst = Mathf.Max(worst, (rt.TransformPoint(U(h.bone_head, scale)) - bone.position).magnitude);
            report.hitboxCount++;
        }
        report.hitboxMaxBoneError = worst;

        var pick = string.IsNullOrEmpty(choice) || choice == "auto" ? (doc.movement != null ? doc.movement.@default : "none") : choice;
        HitShape m = doc.movement == null ? null : pick == "upright" ? doc.movement.upright : pick == "along" ? doc.movement.along : pick == "box" ? doc.movement.box : null;
        if (m == null)
        {
            if (pick != "none") report.warnings.Add("movement shape " + pick + " is not in the hit-box file");
            report.movementShape = "none";
            return false;
        }
        report.movementShape = pick;
        if (pick == "box")
        {
            var b = root.AddComponent<BoxCollider>();
            b.center = U(m.center, scale);
            b.size = new Vector3(Mathf.Abs(m.size[0]), Mathf.Abs(m.size[1]), Mathf.Abs(m.size[2])) * scale;
        }
        else
        {
            var c = root.AddComponent<CapsuleCollider>();
            c.center = U(m.center, scale);
            c.radius = m.radius * scale;
            c.height = m.height * scale;
            c.direction = Mathf.Clamp(m.direction, 0, 2);
        }
        return true;
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Textures and material (G01, G02)

    static void ConfigureTextures(string assetDir, string msName, Report report)
    {
        foreach (var t in new[] { "base_color.png", "normal.png", "metallic.png", "roughness.png", "ao.png", msName })
        {
            var ti = AssetImporter.GetAtPath(assetDir + "/" + t) as TextureImporter;
            if (ti == null) continue;
            if (t == "base_color.png")
            {
                ti.textureType = TextureImporterType.Default;
                ti.sRGBTexture = true;
            }
            else if (t == "normal.png")
            {
                ti.textureType = TextureImporterType.NormalMap;
                ti.sRGBTexture = false;
            }
            else
            {
                // Data maps: linear, alpha kept as authored (smoothness lives in the alpha of the metal-and-shine map).
                ti.textureType = TextureImporterType.Default;
                ti.sRGBTexture = false;
                ti.alphaSource = TextureImporterAlphaSource.FromInput;
                ti.alphaIsTransparency = false;
            }
            ti.SaveAndReimport();
            report.textureSettings.Add(t + ": " + ti.textureType + (ti.sRGBTexture ? ", sRGB" : ", linear") + (t == msName ? ", alpha " + ti.alphaSource : ""));
        }
    }

    static Shader PickShader()
    {
        // URP Lit when a URP pipeline is active, HDRP Lit for HDRP, else the built-in Standard shader. A project that
        // merely has the URP package but no active pipeline renders with built-in, so Standard is right there.
        var rp = GraphicsSettings.currentRenderPipeline;
        if (rp != null)
        {
            var typeName = rp.GetType().Name;
            if (typeName.Contains("HD")) return Shader.Find("HDRP/Lit") ?? Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("Standard");
            return Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("Standard");
        }
        return Shader.Find("Standard");
    }

    static Material BuildMaterial(string assetDir, string msName, Report report)
    {
        var shader = PickShader();
        var mat = new Material(shader);
        report.shader = shader.name;
        bool urp = shader.name.StartsWith("Universal Render Pipeline");
        var baseTex = AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/base_color.png");
        var normalTex = AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/normal.png");
        var aoTex = AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/ao.png");
        var msTex = AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/" + msName);
        if (baseTex)
        {
            if (mat.HasProperty("_BaseMap")) mat.SetTexture("_BaseMap", baseTex);
            if (mat.HasProperty("_MainTex")) mat.SetTexture("_MainTex", baseTex);
            report.texturesAssigned.Add("base_color");
        }
        if (normalTex)
        {
            mat.SetTexture("_BumpMap", normalTex);
            mat.EnableKeyword("_NORMALMAP");
            report.texturesAssigned.Add("normal");
        }
        if (aoTex)
        {
            mat.SetTexture("_OcclusionMap", aoTex);
            if (mat.HasProperty("_OcclusionStrength")) mat.SetFloat("_OcclusionStrength", 1f);
            mat.EnableKeyword("_OCCLUSIONMAP");
            report.texturesAssigned.Add("ao");
        }
        if (msTex && mat.HasProperty("_MetallicGlossMap"))
        {
            // Metallic in R, smoothness (1 - roughness) in A. URP Lit: _METALLICSPECGLOSSMAP, smoothness from the
            // metallic map's alpha (channel 0), _Smoothness scales the alpha. Standard: _METALLICGLOSSMAP, _GlossMapScale.
            mat.SetTexture("_MetallicGlossMap", msTex);
            if (mat.HasProperty("_Metallic")) mat.SetFloat("_Metallic", 1f);
            if (mat.HasProperty("_SmoothnessTextureChannel")) mat.SetFloat("_SmoothnessTextureChannel", 0f);
            mat.DisableKeyword("_SMOOTHNESS_TEXTURE_ALBEDO_CHANNEL_A");
            if (urp)
            {
                mat.SetFloat("_Smoothness", 1f);
                mat.EnableKeyword("_METALLICSPECGLOSSMAP");
            }
            else
            {
                if (mat.HasProperty("_GlossMapScale")) mat.SetFloat("_GlossMapScale", 1f);
                if (mat.HasProperty("_Glossiness")) mat.SetFloat("_Glossiness", 1f);
                if (mat.HasProperty("_Smoothness")) mat.SetFloat("_Smoothness", 1f);
                mat.EnableKeyword("_METALLICGLOSSMAP");
            }
            report.texturesAssigned.Add("metallic_smoothness");
        }
        return mat;
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Humanoid (G26): an explicit HumanDescription from the bone names, or from the skeleton's shape when the names
    // do not describe a person; then Unity's auto-mapping; then Generic.

    static readonly Dictionary<string, HumanBodyBones> NameMap = new Dictionary<string, HumanBodyBones>
    {
        { "Hips", HumanBodyBones.Hips }, { "Spine", HumanBodyBones.Spine }, { "Spine1", HumanBodyBones.Chest },
        { "Spine2", HumanBodyBones.UpperChest }, { "Neck", HumanBodyBones.Neck }, { "Head", HumanBodyBones.Head },
        { "LeftShoulder", HumanBodyBones.LeftShoulder }, { "RightShoulder", HumanBodyBones.RightShoulder },
        { "LeftArm", HumanBodyBones.LeftUpperArm }, { "RightArm", HumanBodyBones.RightUpperArm },
        { "LeftForeArm", HumanBodyBones.LeftLowerArm }, { "RightForeArm", HumanBodyBones.RightLowerArm },
        { "LeftHand", HumanBodyBones.LeftHand }, { "RightHand", HumanBodyBones.RightHand },
        { "LeftUpLeg", HumanBodyBones.LeftUpperLeg }, { "RightUpLeg", HumanBodyBones.RightUpperLeg },
        { "LeftLeg", HumanBodyBones.LeftLowerLeg }, { "RightLeg", HumanBodyBones.RightLowerLeg },
        { "LeftFoot", HumanBodyBones.LeftFoot }, { "RightFoot", HumanBodyBones.RightFoot },
        { "LeftToeBase", HumanBodyBones.LeftToes }, { "RightToeBase", HumanBodyBones.RightToes },
    };

    static readonly string[] FingerNames = { "Thumb", "Index", "Middle", "Ring", "Pinky" };
    static readonly string[] FingerEnum = { "Thumb", "Index", "Middle", "Ring", "Little" };
    static readonly string[] Phalanx = { "Proximal", "Intermediate", "Distal" };

    static HumanBodyBones FingerBone(bool left, int finger, int phalanx) =>
        (HumanBodyBones)Enum.Parse(typeof(HumanBodyBones), (left ? "Left" : "Right") + FingerEnum[finger] + Phalanx[phalanx]);

    static string StripName(string n)
    {
        var s = n;
        int bar = s.LastIndexOf('|');
        if (bar >= 0) s = s.Substring(bar + 1);
        if (s.StartsWith("mixamorig", StringComparison.OrdinalIgnoreCase))
        {
            s = s.Substring(9);
            if (s.StartsWith(":") || s.StartsWith("_")) s = s.Substring(1);
        }
        int colon = s.LastIndexOf(':');
        if (colon >= 0) s = s.Substring(colon + 1);
        return s;
    }

    static bool IsAncestor(Transform a, Transform b)
    {
        for (var p = b.parent; p != null; p = p.parent) if (p == a) return true;
        return false;
    }

    class Frame { public Vector3 up = Vector3.up, fwd = Vector3.forward, right = Vector3.right; public Vector3 origin; }

    static List<Transform> BoneChildren(Transform t, HashSet<Transform> bones) =>
        t.Cast<Transform>().Where(c => bones.Contains(c)).ToList();

    static IEnumerable<Transform> Subtree(Transform t, HashSet<Transform> bones)
    {
        yield return t;
        foreach (var c in BoneChildren(t, bones)) foreach (var d in Subtree(c, bones)) yield return d;
    }

    // Follow a single-child path; include the first bone with zero or several children and stop there.
    static List<Transform> Chain(Transform start, HashSet<Transform> bones)
    {
        var chain = new List<Transform> { start };
        var cur = start;
        while (true)
        {
            var kids = BoneChildren(cur, bones);
            if (kids.Count != 1) break;
            cur = kids[0];
            chain.Add(cur);
        }
        return chain;
    }

    // Follow the child with the largest subtree to the end (legs: up leg, leg, foot, toes).
    static List<Transform> LongChain(Transform start, HashSet<Transform> bones)
    {
        var chain = new List<Transform> { start };
        var cur = start;
        while (true)
        {
            var kids = BoneChildren(cur, bones);
            if (kids.Count == 0) break;
            cur = kids.OrderByDescending(k => Subtree(k, bones).Count()).First();
            chain.Add(cur);
        }
        return chain;
    }

    static Frame MakeFrame(Transform hips, List<List<Transform>> legs)
    {
        var f = new Frame { origin = hips.position };
        var fwd = Vector3.zero;
        foreach (var leg in legs)
            if (leg.Count >= 4) fwd += Vector3.ProjectOnPlane(leg[3].position - leg[2].position, Vector3.up);
        if (fwd.sqrMagnitude < 1e-8f) fwd = Vector3.ProjectOnPlane(hips.root.forward, Vector3.up);
        f.fwd = fwd.normalized;
        f.up = Vector3.up;
        f.right = Vector3.Cross(f.up, f.fwd).normalized;  // Unity is left-handed: Cross(up, forward) = right
        return f;
    }

    static float Lateral(Frame f, Vector3 p) => Vector3.Dot(p - f.origin, f.right);

    static void AddFingers(Dictionary<HumanBodyBones, Transform> map, Transform hand, bool left, HashSet<Transform> bones)
    {
        // Finger kind and joint number come from the names (LeftHandIndex1 and so on); the side comes from the hand
        // the finger hangs from, because some generated rigs carry the wrong side in the name.
        foreach (var child in BoneChildren(hand, bones))
        {
            var n = StripName(child.name);
            int finger = -1;
            for (int i = 0; i < FingerNames.Length; i++) if (n.Contains("Hand" + FingerNames[i])) finger = i;
            if (finger < 0) continue;
            var chain = Chain(child, bones);
            for (int j = 0; j < Math.Min(3, chain.Count); j++)
            {
                var fb = FingerBone(left, finger, j);
                if (!map.ContainsKey(fb)) map[fb] = chain[j];
            }
        }
    }

    static Dictionary<HumanBodyBones, Transform> MapByNames(Transform root, HashSet<Transform> bones)
    {
        var map = new Dictionary<HumanBodyBones, Transform>();
        foreach (var t in bones)
        {
            var n = StripName(t.name);
            if (n.StartsWith("Extra_", StringComparison.OrdinalIgnoreCase)) continue;
            if (NameMap.TryGetValue(n, out var hb)) { if (!map.ContainsKey(hb)) map[hb] = t; continue; }
            for (int side = 0; side < 2; side++)
            {
                var prefix = (side == 0 ? "Left" : "Right") + "Hand";
                if (!n.StartsWith(prefix)) continue;
                var rest = n.Substring(prefix.Length);
                for (int i = 0; i < FingerNames.Length; i++)
                {
                    if (!rest.StartsWith(FingerNames[i])) continue;
                    if (int.TryParse(rest.Substring(FingerNames[i].Length), out var num) && num >= 1 && num <= 3)
                    {
                        var fb = FingerBone(side == 0, i, num - 1);
                        if (!map.ContainsKey(fb)) map[fb] = t;
                    }
                }
            }
        }
        return map;
    }

    // Does the mapping describe a person standing in this frame: hierarchy right, head on the centre line above the
    // shoulders, left limbs on the left and right limbs on the right?
    static string CheckMapping(Dictionary<HumanBodyBones, Transform> map, Frame f)
    {
        for (int i = 0; i < HumanTrait.BoneCount; i++)
            if (HumanTrait.RequiredBone(i) && !map.ContainsKey((HumanBodyBones)i)) return "missing " + HumanTrait.BoneName[i];
        foreach (var kv in map)
        {
            int p = HumanTrait.GetParentBone((int)kv.Key);
            while (p >= 0 && !map.ContainsKey((HumanBodyBones)p)) p = HumanTrait.GetParentBone(p);
            if (p >= 0 && !IsAncestor(map[(HumanBodyBones)p], kv.Value)) return "hierarchy: " + kv.Key + " is not under " + (HumanBodyBones)p;
        }
        var head = map[HumanBodyBones.Head].position;
        var la = map[HumanBodyBones.LeftUpperArm].position; var ra = map[HumanBodyBones.RightUpperArm].position;
        float width = Mathf.Abs(Lateral(f, ra) - Lateral(f, la));
        if (Mathf.Abs(Lateral(f, head)) > 0.25f * width) return "head is off the centre line";
        if (head.y <= la.y || head.y <= ra.y) return "head is below the shoulders";
        // Upright: the head stands well above the hips (at least half the leg length) and the arms start above the
        // hips. A four-legged skeleton has a similar tree but fails this.
        var hipY = map[HumanBodyBones.Hips].position.y;
        float leg = hipY - 0.5f * (map[HumanBodyBones.LeftFoot].position.y + map[HumanBodyBones.RightFoot].position.y);
        if (leg <= 0f || head.y - hipY < 0.5f * leg) return "not upright (head not well above the hips)";
        if (la.y <= hipY || ra.y <= hipY) return "not upright (arms start below the hips)";
        foreach (var pair in new[] { (HumanBodyBones.LeftUpperArm, -1), (HumanBodyBones.LeftHand, -1), (HumanBodyBones.LeftUpperLeg, -1), (HumanBodyBones.LeftFoot, -1),
                                     (HumanBodyBones.RightUpperArm, 1), (HumanBodyBones.RightHand, 1), (HumanBodyBones.RightUpperLeg, 1), (HumanBodyBones.RightFoot, 1) })
            if (Mathf.Sign(Lateral(f, map[pair.Item1].position)) != pair.Item2) return pair.Item1 + " is on the wrong side";
        return null;
    }

    // Read the person from the skeleton's shape: legs go down from the hips, the spine goes up to a branch with the
    // neck in the middle and an arm on each side.
    static Dictionary<HumanBodyBones, Transform> MapByShape(Transform hips, HashSet<Transform> bones, Frame f, out string why)
    {
        why = null;
        var map = new Dictionary<HumanBodyBones, Transform>();
        var kids = BoneChildren(hips, bones);
        if (kids.Count < 3) { why = "hips have fewer than three children"; return null; }
        var spineRoot = kids.OrderByDescending(k => Subtree(k, bones).Max(t => t.position.y)).First();
        var legRoots = kids.Where(k => k != spineRoot).OrderBy(k => Subtree(k, bones).Min(t => t.position.y)).Take(2).ToList();
        foreach (var lr in legRoots)
        {
            var leg = LongChain(lr, bones);
            if (leg.Count < 3) { why = "a leg has fewer than three bones"; return null; }
            bool left = Lateral(f, lr.position) < 0;
            map[left ? HumanBodyBones.LeftUpperLeg : HumanBodyBones.RightUpperLeg] = leg[0];
            map[left ? HumanBodyBones.LeftLowerLeg : HumanBodyBones.RightLowerLeg] = leg[1];
            map[left ? HumanBodyBones.LeftFoot : HumanBodyBones.RightFoot] = leg[2];
            if (leg.Count >= 4) map[left ? HumanBodyBones.LeftToes : HumanBodyBones.RightToes] = leg[3];
        }
        map[HumanBodyBones.Hips] = hips;
        var spine = Chain(spineRoot, bones);
        var branch = spine[spine.Count - 1];
        var top = BoneChildren(branch, bones);
        if (top.Count < 3) { why = "no branch with a neck and two arms"; return null; }
        map[HumanBodyBones.Spine] = spine[0];
        if (spine.Count >= 2) map[HumanBodyBones.Chest] = spine[1];
        if (spine.Count >= 3) map[HumanBodyBones.UpperChest] = spine[spine.Count - 1];
        var byLat = top.Select(c => (c, lat: Subtree(c, bones).Average(t => Lateral(f, t.position)))).OrderBy(x => x.lat).ToList();
        var leftRoot = byLat.First().c; var rightRoot = byLat.Last().c;
        var neckRoot = top.Where(c => c != leftRoot && c != rightRoot).OrderBy(c => Mathf.Abs(byLat.First(x => x.c == c).lat)).First();
        var neck = Chain(neckRoot, bones);
        if (neck.Count >= 2) { map[HumanBodyBones.Neck] = neck[0]; map[HumanBodyBones.Head] = neck[1]; }
        else map[HumanBodyBones.Head] = neck[0];
        foreach (var (armRoot, left) in new[] { (leftRoot, true), (rightRoot, false) })
        {
            var arm = Chain(armRoot, bones);
            if (arm.Count < 3) { why = "an arm has fewer than three bones"; return null; }
            int o = arm.Count >= 4 ? 1 : 0;
            if (o == 1) map[left ? HumanBodyBones.LeftShoulder : HumanBodyBones.RightShoulder] = arm[0];
            map[left ? HumanBodyBones.LeftUpperArm : HumanBodyBones.RightUpperArm] = arm[o];
            map[left ? HumanBodyBones.LeftLowerArm : HumanBodyBones.RightLowerArm] = arm[o + 1];
            map[left ? HumanBodyBones.LeftHand : HumanBodyBones.RightHand] = arm[o + 2];
            AddFingers(map, arm[o + 2], left, bones);
        }
        return map;
    }

    static void Align(Transform bone, Vector3 target, Vector3 dir)
    {
        var from = target - bone.position;
        if (from.sqrMagnitude < 1e-10f) return;
        bone.rotation = Quaternion.FromToRotation(from, dir) * bone.rotation;
    }

    // SkinToken rigs rest close to an A-pose (arms well below horizontal). Unity's humanoid retargeting treats the
    // skeleton pose in the HumanDescription as the T-pose, so arms are turned out level and legs straight down.
    static void EnforceTPose(Dictionary<HumanBodyBones, Transform> map, Frame f)
    {
        Transform T(HumanBodyBones b) => map.TryGetValue(b, out var t) ? t : null;
        foreach (var left in new[] { true, false })
        {
            var dir = left ? -f.right : f.right;
            var ua = T(left ? HumanBodyBones.LeftUpperArm : HumanBodyBones.RightUpperArm);
            var la = T(left ? HumanBodyBones.LeftLowerArm : HumanBodyBones.RightLowerArm);
            var hand = T(left ? HumanBodyBones.LeftHand : HumanBodyBones.RightHand);
            var mid = T(left ? HumanBodyBones.LeftMiddleProximal : HumanBodyBones.RightMiddleProximal);
            if (ua && la) Align(ua, la.position, dir);
            if (la && hand) Align(la, hand.position, dir);
            if (hand && mid) Align(hand, mid.position, dir);
            var ul = T(left ? HumanBodyBones.LeftUpperLeg : HumanBodyBones.RightUpperLeg);
            var ll = T(left ? HumanBodyBones.LeftLowerLeg : HumanBodyBones.RightLowerLeg);
            var foot = T(left ? HumanBodyBones.LeftFoot : HumanBodyBones.RightFoot);
            if (ul && ll) Align(ul, ll.position, -f.up);
            if (ll && foot) Align(ll, foot.position, -f.up);
        }
    }

    static bool AvatarOk(string fbxPath)
    {
        var a = AssetDatabase.LoadAllAssetsAtPath(fbxPath).OfType<Avatar>().FirstOrDefault();
        return a != null && a.isHuman && a.isValid;
    }

    static string MakeHumanoid(ModelImporter importer, string fbxPath, Report report, bool keepPose)
    {
        var model = AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath);
        GameObject clone = null;
        try
        {
            if (model != null)
            {
                clone = UnityEngine.Object.Instantiate(model);
                clone.name = model.name;
                var smr = clone.GetComponentsInChildren<SkinnedMeshRenderer>(true).OrderByDescending(s => s.bones.Length).FirstOrDefault();
                var bones = new HashSet<Transform>(smr != null ? smr.bones.Where(b => b != null) : new Transform[0]);
                // Bones the skin does not reference (end bones) are still part of the skeleton.
                foreach (var b in bones.ToList()) foreach (var d in b.GetComponentsInChildren<Transform>(true)) if (d.GetComponent<Renderer>() == null) bones.Add(d);
                var hips = bones.Where(b => b.parent == null || !bones.Contains(b.parent)).OrderByDescending(b => Subtree(b, bones).Count()).FirstOrDefault();
                if (hips != null)
                {
                    var legs = BoneChildren(hips, bones).Select(k => LongChain(k, bones)).Where(c => c.Count >= 3)
                        .OrderBy(c => c.Min(t => t.position.y)).Take(2).ToList();
                    var frame = MakeFrame(hips, legs);
                    var map = MapByNames(clone.transform, bones);
                    var problem = CheckMapping(map, frame);
                    string by = "names";
                    if (problem != null)
                    {
                        report.warnings.Add("bone names do not describe a person (" + problem + "); mapped from the skeleton's shape");
                        map = MapByShape(hips, bones, frame, out var why);
                        by = "shape";
                        problem = map == null ? why : CheckMapping(map, frame);
                    }
                    if (problem == null)
                    {
                        if (!keepPose) { EnforceTPose(map, frame); report.tPoseApplied = true; }
                        var desc = importer.humanDescription;
                        desc.human = map.Select(kv => new HumanBone
                        {
                            humanName = HumanTrait.BoneName[(int)kv.Key],
                            boneName = kv.Value.name,
                            limit = new HumanLimit { useDefaultValues = true },
                        }).ToArray();
                        desc.skeleton = clone.GetComponentsInChildren<Transform>(true).Select(t => new SkeletonBone
                        {
                            name = t.name,
                            position = t.localPosition,
                            rotation = t.localRotation,
                            scale = t.localScale,
                        }).ToArray();
                        if (desc.upperArmTwist <= 0f) { desc.upperArmTwist = 0.5f; desc.lowerArmTwist = 0.5f; desc.upperLegTwist = 0.5f; desc.lowerLegTwist = 0.5f; }
                        if (desc.armStretch <= 0f) { desc.armStretch = 0.05f; desc.legStretch = 0.05f; }
                        desc.hasTranslationDoF = false;
                        importer.animationType = ModelImporterAnimationType.Human;
                        importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
                        importer.humanDescription = desc;
                        importer.SaveAndReimport();
                        if (AvatarOk(fbxPath))
                        {
                            report.avatar_mapping_by = by;
                            report.humanBonesMapped = map.Count;
                            return "explicit";
                        }
                        report.warnings.Add("explicit human mapping (" + by + ") was not accepted by Unity");
                        report.tPoseApplied = false;
                    }
                    else report.warnings.Add("explicit human mapping failed: " + problem);
                }
                else report.warnings.Add("explicit human mapping failed: no skinned skeleton");
            }
        }
        finally
        {
            if (clone != null) UnityEngine.Object.DestroyImmediate(clone);
        }

        // Unity's own auto-mapping.
        var empty = importer.humanDescription;
        empty.human = new HumanBone[0];
        empty.skeleton = new SkeletonBone[0];
        importer.humanDescription = empty;
        importer.animationType = ModelImporterAnimationType.Human;
        importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
        importer.SaveAndReimport();
        if (AvatarOk(fbxPath)) return "auto";

        report.warnings.Add("humanoid mapping failed; imported as Generic");
        importer.animationType = ModelImporterAnimationType.Generic;
        importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
        importer.SaveAndReimport();
        return "generic";
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Clips (G32) and the controller (G33)

    static readonly HashSet<string> CyclicWords = new HashSet<string>
    {
        "idle", "walk", "run", "trot", "jog", "sprint", "swim", "slither", "crawl", "fly", "flap", "glide", "hover",
        "gallop", "canter", "strafe", "breathe", "breathing",
    };
    static readonly HashSet<string> OneShotWords = new HashSet<string> { "attack", "hit", "death", "die", "dying", "dead" };
    static readonly HashSet<string> NeutralWords = new HashSet<string> { "loop", "fwd", "forward", "inplace" };

    static string ShortName(string clip)
    {
        int bar = clip.LastIndexOf('|');
        return bar >= 0 ? clip.Substring(bar + 1) : clip;
    }

    static List<string> Words(string clip)
    {
        var s = ShortName(clip);
        var words = new List<string>();
        var cur = new System.Text.StringBuilder();
        for (int i = 0; i < s.Length; i++)
        {
            char ch = s[i];
            bool boundary = !char.IsLetter(ch) || (char.IsUpper(ch) && i > 0 && char.IsLower(s[i - 1]));
            if (boundary && cur.Length > 0) { words.Add(cur.ToString().ToLowerInvariant()); cur.Clear(); }
            if (char.IsLetter(ch)) cur.Append(ch);
        }
        if (cur.Length > 0) words.Add(cur.ToString().ToLowerInvariant());
        return words;
    }

    static bool IsCyclic(string clip)
    {
        var w = Words(clip);
        if (w.Any(OneShotWords.Contains)) return false;
        return w.Any(CyclicWords.Contains) || clip.IndexOf("loop", StringComparison.OrdinalIgnoreCase) >= 0;
    }

    // anim_clips.json from the rig stage (procanim.py): the loop flag per clip, which the GLB and FBX do not carry.
    [Serializable] class SidecarClip { public string name = ""; public bool loop; public bool hold_last; public bool in_place = true; }
    [Serializable] class Sidecar { public int version; public string motion = ""; public List<SidecarClip> clips = new List<SidecarClip>(); }

    static Dictionary<string, SidecarClip> ReadClipInfo(string path, Report report)
    {
        if (path == null || !File.Exists(path)) return null;
        try
        {
            var doc = JsonUtility.FromJson<Sidecar>(File.ReadAllText(path));
            var map = new Dictionary<string, SidecarClip>(StringComparer.OrdinalIgnoreCase);
            foreach (var c in doc.clips ?? new List<SidecarClip>()) if (!string.IsNullOrEmpty(c.name)) map[c.name] = c;
            report.clipInfoUsed = map.Count > 0;
            return map;
        }
        catch (Exception e) { report.warnings.Add("clip info not readable: " + e.Message); return null; }
    }

    static void SetClipLoops(ModelImporter mi, Report report, Dictionary<string, SidecarClip> sidecar)
    {
        var defs = mi.defaultClipAnimations;
        if (defs == null || defs.Length == 0) return;
        foreach (var d in defs)
        {
            var info = new ClipInfo { name = d.name, defaultLoop = d.loopTime };
            SidecarClip side = null;
            if (sidecar != null) sidecar.TryGetValue(ShortName(d.name), out side);
            bool cyclic = side != null ? side.loop : IsCyclic(d.name);
            bool inPlace = side != null ? side.in_place : !Words(d.name).Contains("rm");
            if (cyclic)
            {
                d.loopTime = true;
                d.loopPose = true;
                if (inPlace)
                {
                    // In-place cycle: bake root rotation, height and XZ into the pose, based on the original root.
                    d.lockRootRotation = true; d.keepOriginalOrientation = true;
                    d.lockRootHeightY = true; d.keepOriginalPositionY = true;
                    d.lockRootPositionXZ = true; d.keepOriginalPositionXZ = true;
                    info.bakedInPlace = true;
                }
            }
            info.loop = d.loopTime;
            report.clips.Add(info);
        }
        mi.clipAnimations = defs;
        mi.SaveAndReimport();
    }

    static List<AnimationClip> LoadClips(string path) =>
        AssetDatabase.LoadAllAssetsAtPath(path).OfType<AnimationClip>().Where(c => !c.name.StartsWith("__preview")).ToList();

    // Best clip whose words include one of `want`: fewest extra words (Idle_Loop beats Sword_Idle), then shortest name.
    static AnimationClip Pick(List<AnimationClip> clips, HashSet<AnimationClip> used, params string[] want)
    {
        return clips.Where(c => !used.Contains(c))
            .Select(c => (c, w: Words(c.name)))
            .Where(x => x.w.Any(want.Contains) && !x.w.Contains("crouch") && !x.w.Contains("swim") && !x.w.Contains("sitting"))
            .OrderBy(x => x.w.Count(t => !want.Contains(t) && !NeutralWords.Contains(t)))
            .ThenBy(x => x.c.name.Length).ThenBy(x => x.c.name, StringComparer.Ordinal)
            .Select(x => x.c).FirstOrDefault();
    }

    static AnimatorController BuildController(string path, List<AnimationClip> clips, bool humanoid, float speedScale, ControllerInfo info)
    {
        AssetDatabase.DeleteAsset(path);
        var ctrl = AnimatorController.CreateAnimatorControllerAtPath(path);
        var sm = ctrl.layers[0].stateMachine;
        var used = new HashSet<AnimationClip>();
        AnimationClip Take(AnimationClip c) { if (c != null) used.Add(c); return c; }

        var idle = Take(Pick(clips, used, "idle"));
        var walk = Take(Pick(clips, used, "walk") ?? Pick(clips, used, "slither"));
        var run = Take(Pick(clips, used, "run") ?? Pick(clips, used, "jog") ?? Pick(clips, used, "sprint"));
        var attack = Take(Pick(clips, used, "attack") ?? Pick(clips, used, "punch", "slash", "bite", "strike"));
        var hit = Take(Pick(clips, used, "hit", "hurt", "damage", "impact"));
        var die = Take(Pick(clips, used, "death", "die", "dying"));
        AnimationClip jump = null, jumpLand = null;
        if (humanoid)
        {
            jump = clips.FirstOrDefault(c => !used.Contains(c) && Words(c.name).SequenceEqual(new[] { "jump" }));
            if (jump == null)
            {
                jump = clips.FirstOrDefault(c => !used.Contains(c) && Words(c.name).Contains("jump") && Words(c.name).Contains("start"));
                jumpLand = clips.FirstOrDefault(c => !used.Contains(c) && Words(c.name).Contains("jump") && Words(c.name).Contains("land"));
            }
            Take(jump); Take(jumpLand);
        }
        AnimationClip Exact(params string[] words) => clips.FirstOrDefault(c => !used.Contains(c) && words.All(w => Words(c.name).Contains(w)));
        var turnLeft = Take(Exact("turn", "left"));
        var turnRight = Take(Exact("turn", "right"));
        var flap = Take(Pick(clips, used, "flap"));
        AnimationClip glide = null, takeoff = null, land = null;
        if (flap)
        {
            glide = Take(Pick(clips, used, "glide"));
            takeoff = Take(Pick(clips, used, "takeoff") ?? Exact("take", "off"));
            land = Take(Pick(clips, used, "land", "landing"));
        }
        foreach (var (role, c) in new[] { ("Idle", idle), ("Walk", walk), ("Run", run), ("Attack", attack), ("Hit", hit), ("Die", die), ("Jump", jump), ("JumpLand", jumpLand),
                                          ("TurnLeft", turnLeft), ("TurnRight", turnRight), ("Flap", flap), ("Glide", glide), ("Takeoff", takeoff), ("Land", land) })
            if (c != null) info.roles.Add(role + "=" + c.name);

        AnimatorState loco = null;
        var cycle = new List<(AnimationClip clip, float speed)>();
        if (idle) cycle.Add((idle, 0f));
        if (walk) cycle.Add((walk, 1.4f * speedScale));
        if (run) cycle.Add((run, 3.5f * speedScale));
        if (cycle.Count >= 2)
        {
            ctrl.AddParameter("Speed", AnimatorControllerParameterType.Float);
            var tree = new BlendTree
            {
                name = "Locomotion",
                blendType = BlendTreeType.Simple1D,
                blendParameter = "Speed",
                useAutomaticThresholds = false,
                hideFlags = HideFlags.HideInHierarchy,
            };
            AssetDatabase.AddObjectToAsset(tree, ctrl);
            foreach (var (clip, speed) in cycle) { tree.AddChild(clip, speed); info.locomotion.Add(clip.name + "@" + speed.ToString("0.##", System.Globalization.CultureInfo.InvariantCulture)); }
            loco = sm.AddState("Locomotion", new Vector3(300, 0, 0));
            loco.motion = tree;
        }
        else if (cycle.Count == 1)
        {
            loco = sm.AddState("Locomotion", new Vector3(300, 0, 0));
            loco.motion = cycle[0].clip;
            info.locomotion.Add(cycle[0].clip.name + "@0");
        }

        var actionStates = new List<AnimatorState>();
        if (loco != null)
        {
            sm.defaultState = loco;
            AnimatorState OneShot(string state, string trigger, AnimationClip clip, Vector3 pos, IEnumerable<AnimatorState> from)
            {
                if (ctrl.parameters.All(p => p.name != trigger)) ctrl.AddParameter(trigger, AnimatorControllerParameterType.Trigger);
                var st = sm.AddState(state, pos);
                st.motion = clip;
                foreach (var src in from)
                {
                    var t = src.AddTransition(st);
                    t.AddCondition(AnimatorConditionMode.If, 0, trigger);
                    t.hasExitTime = false;
                    t.duration = 0.1f;
                }
                return st;
            }
            void Back(AnimatorState st, AnimatorState to)
            {
                var b = st.AddTransition(to);
                b.hasExitTime = true;
                b.exitTime = 0.9f;
                b.duration = 0.1f;
            }
            AnimatorState attackSt = null, jumpSt = null, landSt = null, hitSt = null;
            if (attack) { attackSt = OneShot("Attack", "Attack", attack, new Vector3(600, -120, 0), new[] { loco }); Back(attackSt, loco); actionStates.Add(attackSt); }
            if (jump)
            {
                jumpSt = OneShot("Jump", "Jump", jump, new Vector3(600, 120, 0), new[] { loco });
                actionStates.Add(jumpSt);
                if (jumpLand)
                {
                    landSt = sm.AddState("Jump Land", new Vector3(850, 120, 0));
                    landSt.motion = jumpLand;
                    Back(jumpSt, landSt);
                    Back(landSt, loco);
                    actionStates.Add(landSt);
                }
                else Back(jumpSt, loco);
            }
            if (turnLeft) { var st = OneShot("Turn Left", "TurnLeft", turnLeft, new Vector3(600, 240, 0), new[] { loco }); Back(st, loco); actionStates.Add(st); }
            if (turnRight) { var st = OneShot("Turn Right", "TurnRight", turnRight, new Vector3(600, 360, 0), new[] { loco }); Back(st, loco); actionStates.Add(st); }
            if (flap)
            {
                // Flying: Bool "Flying"; Locomotion -> Takeoff -> Fly while true, Fly -> Land -> Locomotion when false.
                ctrl.AddParameter("Flying", AnimatorControllerParameterType.Bool);
                var fly = sm.AddState("Fly", new Vector3(300, -250, 0));
                if (glide)
                {
                    if (ctrl.parameters.All(p => p.name != "Speed")) ctrl.AddParameter("Speed", AnimatorControllerParameterType.Float);
                    var ftree = new BlendTree { name = "Fly", blendType = BlendTreeType.Simple1D, blendParameter = "Speed", useAutomaticThresholds = false, hideFlags = HideFlags.HideInHierarchy };
                    AssetDatabase.AddObjectToAsset(ftree, ctrl);
                    ftree.AddChild(flap, 0f);
                    ftree.AddChild(glide, 3.5f * speedScale);
                    fly.motion = ftree;
                }
                else fly.motion = flap;
                AnimatorStateTransition When(AnimatorState a, AnimatorState b, bool flying)
                {
                    var t = a.AddTransition(b);
                    t.AddCondition(flying ? AnimatorConditionMode.If : AnimatorConditionMode.IfNot, 0, "Flying");
                    t.hasExitTime = false; t.duration = 0.15f;
                    return t;
                }
                if (takeoff) { var st = sm.AddState("Takeoff", new Vector3(50, -250, 0)); st.motion = takeoff; When(loco, st, true); Back(st, fly); actionStates.Add(st); }
                else When(loco, fly, true);
                if (land) { var st = sm.AddState("Land", new Vector3(550, -250, 0)); st.motion = land; When(fly, st, false); Back(st, loco); actionStates.Add(st); }
                else When(fly, loco, false);
                actionStates.Add(fly);
            }
            if (hit)
            {
                var from = new List<AnimatorState> { loco };
                if (attackSt != null) from.Add(attackSt);
                hitSt = OneShot("Hit", "Hit", hit, new Vector3(600, 0, 0), from);
                Back(hitSt, loco);
                actionStates.Add(hitSt);
            }
            if (die)
            {
                var from = new List<AnimatorState> { loco };
                from.AddRange(actionStates);
                OneShot("Dead", "Die", die, new Vector3(900, -120, 0), from);  // no way out
            }
        }
        else
        {
            // No idle, walk or run: nothing to blend; every clip becomes a plain state as before.
            foreach (var c in clips.Where(c => !used.Contains(c)).Concat(used)) { var st = sm.AddState(UniqueState(sm, ShortName(c.name))); st.motion = c; }
            used = new HashSet<AnimationClip>(clips);
        }

        // Every other clip is available in "More moves", not connected.
        var rest = clips.Where(c => !used.Contains(c)).Take(200).ToList();
        if (rest.Count > 0)
        {
            var more = sm.AddStateMachine("More moves", new Vector3(300, 250, 0));
            int i = 0;
            foreach (var c in rest)
            {
                var st = more.AddState(UniqueState(more, ShortName(c.name)), new Vector3(250 + 250 * (i % 4), 60 * (i / 4), 0));
                st.motion = c;
                i++;
            }
            info.moreMoves = rest.Count;
        }

        info.path = path;
        info.states = sm.states.Select(s => s.state.name).ToList();
        if (rest.Count > 0) info.states.Add("More moves");
        info.parameters = ctrl.parameters.Select(p => p.name + ":" + p.type).ToList();
        EditorUtility.SetDirty(ctrl);
        AssetDatabase.SaveAssets();
        return ctrl;
    }

    static string UniqueState(AnimatorStateMachine sm, string name)
    {
        var n = name; int k = 2;
        while (sm.states.Any(s => s.state.name == n)) n = name + " " + k++;
        return n;
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Render frames of the prefab's clips to PNG without entering play mode.
    // Run: Unity.exe -batchmode -quit -projectPath <sandbox> -executeMethod VgacImport.Snapshot -vgacName <name> -vgacOut <dir>
    //      [-vgacClipFilter walk,idle] [-vgacFrames 4] [-vgacSize 512]
    // Needs graphics (do not pass -nographics). Clips are evaluated through a PlayableGraph, which honours Loop Time
    // and Humanoid retargeting; each clip also gets one probe frame at 1.5 times its length: a looping clip shows a
    // different pose there than at frame 0 (snapshot_report.json, loopProbes, metres the bones moved).
    public static void Snapshot()
    {
        var name = Arg("-vgacName");
        var outDir = Arg("-vgacOut") ?? ".";
        var filter = (Arg("-vgacClipFilter") ?? "").ToLowerInvariant().Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries);
        int frames = int.Parse(Arg("-vgacFrames") ?? "4");
        int size = int.Parse(Arg("-vgacSize") ?? "512");
        var log = new List<string>();
        var probes = new List<string>();
        try
        {
            Directory.CreateDirectory(outDir);
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/VGAC/" + name + "/" + name + ".prefab");
            if (prefab == null) throw new Exception("prefab missing for " + name);
            UnityEditor.SceneManagement.EditorSceneManager.NewScene(UnityEditor.SceneManagement.NewSceneSetup.DefaultGameObjects, UnityEditor.SceneManagement.NewSceneMode.Single);
            var go = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
            var bounds = new Bounds(go.transform.position, Vector3.one * 0.1f);
            foreach (var r in go.GetComponentsInChildren<Renderer>()) if (r.enabled) bounds.Encapsulate(r.bounds);
            var camGo = new GameObject("snapcam");
            var cam = camGo.AddComponent<Camera>();
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = new Color(0.45f, 0.45f, 0.45f, 1f);
            float dist = bounds.extents.magnitude * 2.4f;
            camGo.transform.position = bounds.center + new Vector3(0.9f, 0.35f, -1f).normalized * dist;
            camGo.transform.LookAt(bounds.center);
            cam.nearClipPlane = 0.01f; cam.farClipPlane = dist * 10f;
            var rt = new RenderTexture(size, size, 24);
            cam.targetTexture = rt;
            var animator = go.GetComponent<Animator>();
            var clips = animator != null && animator.runtimeAnimatorController != null ? animator.runtimeAnimatorController.animationClips.Distinct().ToArray() : new AnimationClip[0];
            var picked = clips.Select(c => (c, idx: filter.Length == 0 ? 0 : Array.FindIndex(filter, f => c.name.ToLowerInvariant().Contains(f))))
                .Where(x => x.idx >= 0)
                .OrderBy(x => x.idx).ThenBy(x => Words(x.c.name).Count(t => !NeutralWords.Contains(t))).ThenBy(x => x.c.name.Length)
                .Select(x => x.c).Take(6).ToList();
            if (picked.Count == 0) picked.Add(null);
            var boneTs = go.GetComponentsInChildren<Transform>(true);

            void Shoot(string file)
            {
                cam.Render();
                RenderTexture.active = rt;
                var tex = new Texture2D(size, size, TextureFormat.RGB24, false);
                tex.ReadPixels(new Rect(0, 0, size, size), 0, 0);
                tex.Apply();
                RenderTexture.active = null;
                File.WriteAllBytes(file, tex.EncodeToPNG());
                log.Add(file);
            }

            foreach (var clip in picked)
            {
                if (clip == null) { Shoot(Path.Combine(outDir, "rest_00.png")); continue; }
                var graph = PlayableGraph.Create("vgac_snapshot");
                graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);
                var output = AnimationPlayableOutput.Create(graph, "anim", animator);
                var playable = AnimationClipPlayable.Create(graph, clip);
                output.SetSourcePlayable(playable);
                graph.Play();
                var clipName = clip.name.Replace("|", "_").Replace(" ", "_");
                Vector3[] Sample(double t)
                {
                    // Evaluate twice: the first evaluation of a fresh Humanoid graph can still show the bind pose.
                    playable.SetTime(t);
                    graph.Evaluate(0f);
                    playable.SetTime(t);
                    graph.Evaluate(0f);
                    return boneTs.Select(b => b.position).ToArray();
                }
                Sample(0);  // warm-up: the first evaluation after the graph is built can still show the bind pose
                var lf = animator.isHuman ? animator.GetBoneTransform(HumanBodyBones.LeftFoot) : null;
                var rf = animator.isHuman ? animator.GetBoneTransform(HumanBodyBones.RightFoot) : null;
                var strides = new List<string>();
                Vector3[] first = null;
                for (int i = 0; i < frames; i++)
                {
                    var pose = Sample(clip.length * i / Math.Max(1, frames));
                    if (i == 0) first = pose;
                    if (lf && rf) strides.Add((lf.position - rf.position).magnitude.ToString("0.00", System.Globalization.CultureInfo.InvariantCulture));
                    Shoot(Path.Combine(outDir, $"{clipName}_{i:00}.png"));
                }
                var probe = Sample(clip.length * 1.5);
                Shoot(Path.Combine(outDir, $"{clipName}_x1.5.png"));
                float moved = 0f;
                for (int b = 0; b < probe.Length; b++) moved = Mathf.Max(moved, (probe[b] - first[b]).magnitude);
                probes.Add(clip.name + " loop=" + clip.isLooping + " moved_m=" + moved.ToString("0.000", System.Globalization.CultureInfo.InvariantCulture)
                           + (strides.Count > 0 ? " feet_apart_m=" + string.Join("/", strides) : ""));
                graph.Destroy();
            }
            File.WriteAllText(Path.Combine(outDir, "snapshot_report.json"), JsonUtility.ToJson(new SnapReport { ok = true, files = log, loopProbes = probes }, true));
            Debug.Log("VGAC_SNAPSHOT ok " + log.Count);
        }
        catch (Exception e)
        {
            File.WriteAllText(Path.Combine(outDir, "snapshot_report.json"), JsonUtility.ToJson(new SnapReport { ok = false, error = e.ToString(), files = log, loopProbes = probes }, true));
            Debug.Log("VGAC_SNAPSHOT fail " + e);
            EditorApplication.Exit(1);
        }
    }

    [Serializable] class SnapReport { public bool ok; public string error = ""; public List<string> files = new List<string>(); public List<string> loopProbes = new List<string>(); }
}
