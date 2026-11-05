// Batch-mode validation importer for Video Game Asset Creator.
// Run: Unity.exe -batchmode -nographics -quit -projectPath <sandbox> -executeMethod VgacImport.Run -vgacInput <dir> -vgacName <name>
//      [-vgacHumanoid] [-vgacClips <library.fbx>] [-vgacSpeedScale <f>] [-vgacKeepPose]
//      [-vgacHitboxes <hitboxes.json>] [-vgacMovementShape auto|upright|along|box|none] [-vgacScale <f>] [-vgacClipInfo <anim_clips.json>]
//      [-vgacCategory <category>] [-vgacColliders <colliders.json>]
//      [-vgacFilter bilinear|point] [-vgacMipmaps on|off] [-vgacCompress on|off] [-vgacNormalMap on|off]
//      [-vgacSockets <sockets.json>]
// Expects in <dir>: <name>.fbx (meshes named <name>_LOD0..n, optional collision meshes <name>_collider_00.._nn or
// <name>_collider_mesh (older files: <name>_collider), optional armature), textures
// base_color.png, normal.png, metallic.png, roughness.png, ao.png and T_<name>_MetallicSmoothness.png (any subset).
// Writes <dir>/unity_report.json.
//
// Hand-off to an open Editor (Version 6): Unity will not open a project in batch mode while the Editor has it open, so
// the backend then copies the same inputs into Assets/VGAC/<name>/_incoming/ with a request file vgac_import.json
// (request_id, importer_version, args: the batch arguments). VgacImportWatcher below picks it up inside the open
// Editor (after an asset import and once at load), runs the same Import routine, writes unity_report.json next to the
// request with the request id and renames the request to vgac_import.done.json. Menu: Tools/VGAC/Import pending assets.
// Batch check of the same sweep: -executeMethod VgacImport.ImportPending.
//
// Recipes (Version 7): the style's engine settings. -vgacFilter point (crisp pixel looks) sets every texture to Point
// filtering, -vgacMipmaps off and -vgacCompress off keep the small textures exact; -vgacNormalMap off (looks without
// surface bumps) leaves the material's normal slot empty and removes a normal.png an earlier import of the same name
// left in Assets/VGAC/<name>. The report records textureFilter, textureMipmaps, textureCompression and normalMap.
// Without these arguments the importer keeps Unity's defaults (bilinear, mipmaps, compression) as before.
//
// Sockets (Version 8): a rigged character's FBX carries attachment points, empties named Socket_Hand_R, Socket_Back
// and so on, parented to their bones (backend/vgac/sockets.py). They arrive as plain child transforms under the bones
// and stay in the prefab, with nothing visible added. With -vgacSockets the importer checks each one against
// sockets.json (the right bone, and its position and axes, glTF model space like hitboxes.json), records how far the
// FBX's own placement was off (socketMaxError in metres, socketMaxAngle in degrees), then sets the file's placement
// exactly: forward +Z and up +Y as the file gives them. A socket the FBX lacks is added from the file. The Humanoid
// mapping never treats a socket as a bone. The report lists the sockets found in the saved prefab (sockets) and
// their bones (socketBones, "Socket_Hand_R>mixamorig:RightHand").
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
    public const string Version = "8";

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
        public string request_id = "";         // set for an Editor hand-off request; "" for a batch import
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
        public int colliderCount;              // collision components under the Colliders child (G16)
        public List<string> colliderKinds = new List<string>();
        public int colliderTrianglesMax;       // most triangles in one convex MeshCollider (Unity's cap is 255)
        public float colliderBoundsError;      // metres between the shapes' combined box and the LOD0 renderer's box
        public bool secondaryUV;               // lightmap UVs generated on import (G17)
        public bool secondaryUVOnMesh;         // LOD0's mesh really has a UV1 channel after import
        public string secondaryUVSettings = "";
        public List<string> staticFlags = new List<string>();
        public string lodFadeMode = "";        // G20: CrossFade for foliage
        public float lodFadeWidth;
        public string textureFilter = "";      // recipes: Bilinear or Point, as set on the textures
        public bool textureMipmaps = true;
        public string textureCompression = "";
        public bool normalMap = true;          // false when the look has no surface bumps
        public List<string> sockets = new List<string>();      // Socket_* transforms in the saved prefab
        public List<string> socketBones = new List<string>();  // "Socket_Hand_R>mixamorig:RightHand"
        public float socketMaxError;           // metres: worst gap between the FBX's socket and sockets.json
        public float socketMaxAngle;           // degrees: worst forward or up mismatch between the FBX and the file
        public int socketsAdded;               // sockets the FBX lacked, added from the file
    }

    // sockets.json (backend/vgac/sockets.py to_json): glTF model space, Y up, metres; forward +Z, up +Y.
    [Serializable]
    class SocketItem
    {
        public string name = "", label = "", bone = "";
        public float[] position = new float[3], forward = new float[3], up = new float[3];
    }

    [Serializable]
    class SocketDoc
    {
        public List<SocketItem> sockets = new List<SocketItem>();
    }

    static bool IsSocketName(string n) => n.StartsWith("Socket_", StringComparison.Ordinal);

    // colliders.json (backend/vgac/stages/export.py _write_colliders): the kind of each collision part.
    [Serializable]
    class ColPart
    {
        public string name = "", kind = "";
        public int triangles, vertices;
    }

    [Serializable]
    class ColDoc
    {
        public string chosen = "";
        public List<ColPart> parts = new List<ColPart>();
    }

    // G17: categories that get lightmap UVs (environment also gets static flags); G20: categories that cross-fade.
    static readonly string[] LightmapCategories = { "environment", "prop_rigid" };
    static readonly string[] CrossFadeCategories = { "foliage" };
    const float CrossFadeWidth = 0.25f;

    static bool IsColliderName(string n) => n.Contains("_collider");

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

    // Everything VgacImport.Run reads from its command line. The batch entry point and the Editor hand-off both build
    // one of these and call Import, so the two paths cannot drift apart.
    public class ImportArgs
    {
        public string input, name, category = "", hitboxes, movementShape, clipInfo, clips, colliders, sockets;
        public bool humanoid, keepPose;
        public string filter = "bilinear";     // recipes: bilinear | point
        public bool mipmaps = true, compress = true, normalMap = true;
        public float speedScale = 1f, scale = 1f;
        public string requestId = "";

        // argv: "-vgacName", "X", "-vgacHumanoid", ... (the batch call's own arguments). baseDir, when given, is where
        // relative file arguments are resolved (the request's folder); the batch path passes null.
        public static ImportArgs Parse(IList<string> argv, string baseDir)
        {
            string A(string key)
            {
                for (int i = 0; i < argv.Count - 1; i++) if (argv[i] == key) return argv[i + 1];
                return null;
            }
            string F(string key)
            {
                var v = A(key);
                if (v == null || baseDir == null || Path.IsPathRooted(v)) return v;
                return Path.GetFullPath(Path.Combine(baseDir, v));
            }
            var a = new ImportArgs
            {
                input = baseDir != null ? Path.GetFullPath(baseDir) : A("-vgacInput"),
                name = A("-vgacName"),
                humanoid = argv.Contains("-vgacHumanoid"),
                keepPose = argv.Contains("-vgacKeepPose"),
                category = A("-vgacCategory") ?? "",
                hitboxes = F("-vgacHitboxes"),
                movementShape = A("-vgacMovementShape"),
                clipInfo = F("-vgacClipInfo"),
                clips = F("-vgacClips"),
                colliders = F("-vgacColliders"),
                sockets = F("-vgacSockets"),
                scale = ParseFloat(A("-vgacScale"), 1f),
                speedScale = ParseFloat(A("-vgacSpeedScale"), 1f),
                filter = (A("-vgacFilter") ?? "bilinear").ToLowerInvariant() == "point" ? "point" : "bilinear",
                mipmaps = (A("-vgacMipmaps") ?? "on").ToLowerInvariant() != "off",
                compress = (A("-vgacCompress") ?? "on").ToLowerInvariant() != "off",
                normalMap = (A("-vgacNormalMap") ?? "on").ToLowerInvariant() != "off",
            };
            if (a.speedScale <= 0.01f) a.speedScale = 1f;
            return a;
        }
    }

    public static void Run()
    {
        var a = ImportArgs.Parse(Environment.GetCommandLineArgs(), null);
        var report = Import(a);
        if (!report.ok) EditorApplication.Exit(1);
    }

    // The import itself, shared by Run (batch) and the Editor hand-off. Never throws; writes <input>/unity_report.json
    // and returns the report.
    static Report Import(ImportArgs a)
    {
        var input = a.input;
        var name = a.name;
        var humanoid = a.humanoid;
        var category = a.category ?? "";
        float speedScale = a.speedScale;
        var report = new Report { request_id = a.requestId ?? "" };
        GameObject built = null;
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
                if (f == "normal.png" && !a.normalMap)
                {
                    // A look without surface bumps: no normal map, and none left over from an earlier import.
                    var stale = assetDir + "/" + f;
                    if (File.Exists(stale)) AssetDatabase.DeleteAsset(stale);
                    continue;
                }
                var src = Path.Combine(input, f);
                if (File.Exists(src)) File.Copy(src, Path.Combine(assetDir, f), true);
            }
            var msSrc = Path.Combine(input, msName);
            if (!File.Exists(msSrc)) msSrc = Path.Combine(input, "metallic_smoothness.png");
            if (File.Exists(msSrc)) File.Copy(msSrc, Path.Combine(assetDir, msName), true);
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

            ConfigureTextures(assetDir, msName, report, a);

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
            // G17: lightmap UVs (UV1) for scenery and still props. Unity's defaults for the unwrap, with the margin
            // calculated from the lightmap resolution (40 texels per unit, objects at scale 1) so charts do not bleed.
            importer.generateSecondaryUV = LightmapCategories.Contains(category);
            if (importer.generateSecondaryUV)
            {
                importer.secondaryUVHardAngle = 88f;
                importer.secondaryUVAngleDistortion = 8f;
                importer.secondaryUVAreaDistortion = 15f;
                importer.secondaryUVMarginMethod = ModelImporterSecondaryUVMarginMethod.Calculate;
                importer.secondaryUVMinLightmapResolution = 40f;
                importer.secondaryUVMinObjectScale = 1f;
            }
            importer.SaveAndReimport();
            report.secondaryUV = importer.generateSecondaryUV;
            if (report.secondaryUV)
                report.secondaryUVSettings = "margin " + importer.secondaryUVMarginMethod + " (min lightmap resolution " + importer.secondaryUVMinLightmapResolution
                    + ", min object scale " + importer.secondaryUVMinObjectScale + "), hard angle " + importer.secondaryUVHardAngle
                    + ", angle distortion " + importer.secondaryUVAngleDistortion + ", area distortion " + importer.secondaryUVAreaDistortion;

            if (humanoid)
            {
                report.avatar_mapping = MakeHumanoid(importer, fbxPath, report, a.keepPose);
                humanoid = report.avatar_mapping != "generic";
            }
            else report.avatar_mapping = "none";

            var model = AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath);
            if (model == null) throw new Exception("model load failed");

            var mat = BuildMaterial(assetDir, msName, report);
            AssetDatabase.CreateAsset(mat, assetDir + "/" + name + ".mat");

            var instance = (GameObject)PrefabUtility.InstantiatePrefab(model);
            built = instance;  // removed below even when a later step throws (an open Editor keeps its scene)
            instance.name = name;
            var renderers = instance.GetComponentsInChildren<Renderer>(true);
            foreach (var r in renderers) r.sharedMaterial = mat;
            if (report.secondaryUV)
            {
                var r0 = renderers.FirstOrDefault(r => r.name.EndsWith("_LOD0")) ?? renderers.FirstOrDefault();
                var m0 = r0 is SkinnedMeshRenderer s0 ? s0.sharedMesh : r0?.GetComponent<MeshFilter>()?.sharedMesh;
                var uv1 = new List<Vector2>();
                if (m0 != null) m0.GetUVs(1, uv1);
                report.secondaryUVOnMesh = uv1.Count > 0;
            }

            // Hit boxes and the body shape for moving (G34): a rigged character gets trigger shapes on its bones and a
            // simple body shape on the root, which replaces the convex hull (a hull does not follow the animation).
            bool rigged = instance.GetComponentsInChildren<SkinnedMeshRenderer>(true).Any(s => s.bones.Length > 0);
            bool bodyShape = false;
            var hitPath = a.hitboxes;
            if (hitPath != null && File.Exists(hitPath) && rigged)
                bodyShape = AddHitboxes(instance, hitPath, a.movementShape ?? "auto", a.scale, report);
            // Sockets: child transforms of the bones from the FBX, checked and placed from sockets.json.
            if (rigged) PlaceSockets(instance, a.sockets, a.scale, report);

            // Collision (G16): one component per collision mesh under a Colliders child of the root, of the kind
            // colliders.json names: box, sphere and capsule become primitive colliders fitted to the mesh's bounds; hull
            // and cylinder a convex MeshCollider; mesh (static scenery) a non-convex MeshCollider. The source meshes stay
            // in the model, switched off. A rigged character with a body shape for moving (G34) gets none of them.
            var colMeshes = instance.GetComponentsInChildren<MeshFilter>(true).Where(m => IsColliderName(m.name)).OrderBy(m => m.name).ToList();
            if (colMeshes.Count > 0)
            {
                if (!bodyShape) AddColliders(instance, colMeshes, ReadColliders(a.colliders, report), report);
                foreach (var mf in colMeshes) mf.gameObject.SetActive(false);
            }
            else if (!bodyShape) report.warnings.Add("no collider mesh");

            // LODGroup: Unity creates it from _LODn names; verify, else build one.
            var lodGroup = instance.GetComponentInChildren<LODGroup>();
            var lodRenderers = renderers.Where(r => r.name.Contains("_LOD") && !IsColliderName(r.name)).OrderBy(r => r.name).ToList();
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
            // G20 part one: foliage fades between detail levels instead of popping. Not time-animated: each level
            // fades over the last quarter of its screen-height range.
            if (lodGroup != null && CrossFadeCategories.Contains(category))
            {
                lodGroup.fadeMode = LODFadeMode.CrossFade;
                lodGroup.animateCrossFading = false;
                var fl = lodGroup.GetLODs();
                for (int i = 0; i < fl.Length; i++) fl[i].fadeTransitionWidth = CrossFadeWidth;
                lodGroup.SetLODs(fl);
                report.lodFadeWidth = CrossFadeWidth;
            }
            if (lodGroup != null) report.lodFadeMode = lodGroup.fadeMode.ToString();

            // G17: environment pieces are static for baked light and batching.
            if (category == "environment")
            {
                var flags = StaticEditorFlags.ContributeGI | StaticEditorFlags.BatchingStatic;
                foreach (var t in instance.GetComponentsInChildren<Transform>(true)) GameObjectUtility.SetStaticEditorFlags(t.gameObject, flags);
                report.staticFlags.Add("ContributeGI");
                report.staticFlags.Add("BatchingStatic");
            }

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
            var clipsSrc = a.clips;
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
                SetClipLoops(importer, report, ReadClipInfo(a.clipInfo, report));
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
            built = null;
            // What the game gets: the sockets as saved in the prefab.
            var saved = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
            if (saved != null)
                foreach (var t in saved.GetComponentsInChildren<Transform>(true).Where(t => IsSocketName(t.name)).OrderBy(t => t.name))
                {
                    report.sockets.Add(t.name);
                    report.socketBones.Add(t.name + ">" + (t.parent != null ? t.parent.name : ""));
                    if (t.GetComponents<Component>().Length > 1) report.warnings.Add("socket " + t.name + " carries components");
                }
            report.prefab = prefabPath;
            report.ok = true;
        }
        catch (Exception e)
        {
            report.ok = false;
            report.error = e.ToString();
        }
        finally
        {
            if (built != null) UnityEngine.Object.DestroyImmediate(built);
        }
        var json = JsonUtility.ToJson(report, true);
        try { File.WriteAllText(Path.Combine(input ?? ".", "unity_report.json"), json); }
        catch (Exception e) { Debug.LogWarning("VGAC: could not write unity_report.json: " + e.Message); }
        Debug.Log("VGAC_REPORT " + json);
        return report;
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Editor hand-off (Version 6): requests the backend leaves in Assets/VGAC/<name>/_incoming/vgac_import.json.

    public const string RequestFile = "vgac_import.json";
    public const string DoneFile = "vgac_import.done.json";
    const string RequestRoot = "Assets/VGAC";

    [Serializable]
    class Request
    {
        public string request_id = "";
        public string importer_version = "";
        public string name = "";
        public List<string> args = new List<string>();
    }

    static bool sweeping;

    // Pending request files under Assets/VGAC, as project-relative paths. Cheap: no Assets/VGAC folder, no work.
    public static List<string> PendingRequests()
    {
        if (!Directory.Exists(RequestRoot)) return new List<string>();
        return Directory.GetFiles(RequestRoot, RequestFile, SearchOption.AllDirectories)
            .Select(p => p.Replace('\\', '/')).OrderBy(p => p, StringComparer.Ordinal).ToList();
    }

    // Run every pending request. Safe to call again: a finished request has been renamed, so it is not found twice.
    // Never throws. -> the number of requests handled.
    public static int Sweep()
    {
        if (sweeping) return 0;
        int done = 0;
        sweeping = true;
        try
        {
            foreach (var path in PendingRequests())
            {
                try { if (HandleRequest(path)) done++; }
                catch (Exception e) { Debug.LogError("VGAC: request " + path + " failed: " + e); }
            }
            if (done > 0) AssetDatabase.Refresh();
        }
        catch (Exception e) { Debug.LogError("VGAC: sweep failed: " + e); }
        finally { sweeping = false; }
        return done;
    }

    static bool HandleRequest(string path)
    {
        if (!File.Exists(path)) return false;  // handled already
        var dir = Path.GetDirectoryName(path).Replace('\\', '/');
        Request req;
        Report report;
        try { req = JsonUtility.FromJson<Request>(File.ReadAllText(path)); }
        catch (Exception e) { req = null; Debug.LogError("VGAC: " + path + " is not readable: " + e.Message); }
        if (req == null || req.args == null || req.args.Count == 0)
        {
            report = new Report { ok = false, error = "request file not readable: " + path, request_id = req?.request_id ?? "" };
            File.WriteAllText(Path.Combine(dir, "unity_report.json"), JsonUtility.ToJson(report, true));
        }
        else
        {
            var a = ImportArgs.Parse(req.args, dir);
            a.requestId = req.request_id ?? "";
            if (string.IsNullOrEmpty(a.name)) a.name = req.name;
            Debug.Log("VGAC: importing " + a.name + " (request " + a.requestId + ")");
            report = Import(a);  // writes <dir>/unity_report.json with the request id
            if (report.ok)
            {
                // The prefab has its own copies now; drop the incoming model and pictures so the project does not
                // carry a second, unconfigured copy of the model. The small JSON files stay as the record.
                foreach (var f in Directory.GetFiles(dir).Where(f => f.EndsWith(".fbx", StringComparison.OrdinalIgnoreCase)
                                                                     || f.EndsWith(".png", StringComparison.OrdinalIgnoreCase)))
                {
                    var ap = f.Replace('\\', '/');
                    if (!AssetDatabase.DeleteAsset(ap)) { File.Delete(ap); if (File.Exists(ap + ".meta")) File.Delete(ap + ".meta"); }
                }
            }
        }
        MarkDone(path, dir + "/" + DoneFile);
        Debug.Log("VGAC: request " + (req?.request_id ?? "?") + (report.ok ? " imported: " + report.prefab : " failed: " + report.error));
        return true;
    }

    static void MarkDone(string from, string to)
    {
        if (File.Exists(to)) { if (!AssetDatabase.DeleteAsset(to)) { File.Delete(to); if (File.Exists(to + ".meta")) File.Delete(to + ".meta"); } }
        var err = AssetDatabase.MoveAsset(from, to);
        if (string.IsNullOrEmpty(err)) return;
        File.Move(from, to);  // the request was never imported as an asset (no refresh yet)
        if (File.Exists(from + ".meta")) File.Delete(from + ".meta");
    }

    // Batch check of the hand-off: the same sweep the Editor runs. Exit code 1 when any handled request failed.
    // Unity.exe -batchmode -quit -projectPath <project> -executeMethod VgacImport.ImportPending
    public static void ImportPending()
    {
        var pending = PendingRequests();
        int n = Sweep();
        bool failed = false;
        foreach (var p in pending)
        {
            var rp = Path.Combine(Path.GetDirectoryName(p), "unity_report.json");
            try { if (File.Exists(rp) && !JsonUtility.FromJson<Report>(File.ReadAllText(rp)).ok) failed = true; }
            catch (Exception) { failed = true; }
        }
        Debug.Log("VGAC_PENDING handled " + n + " of " + pending.Count);
        if (failed) EditorApplication.Exit(1);
    }

    [MenuItem("Tools/VGAC/Import pending assets")]
    static void ImportPendingMenu()
    {
        int n = Sweep();
        Debug.Log(n == 0 ? "VGAC: no pending assets." : "VGAC: imported " + n + " pending asset(s); see the Console for each result.");
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Collision shapes (G16, G35)

    static ColDoc ReadColliders(string path, Report report)
    {
        if (path == null || !File.Exists(path)) return null;
        try { return JsonUtility.FromJson<ColDoc>(File.ReadAllText(path)); }
        catch (Exception e) { report.warnings.Add("colliders.json not readable: " + e.Message); return null; }
    }

    static void AddColliders(GameObject root, List<MeshFilter> meshes, ColDoc doc, Report report)
    {
        var holder = new GameObject("Colliders");
        holder.transform.SetParent(root.transform, false);
        foreach (var mf in meshes)
        {
            var mesh = mf.sharedMesh;
            if (mesh == null) continue;
            var part = doc?.parts?.FirstOrDefault(p => p.name == mf.name);
            var kind = part != null && !string.IsNullOrEmpty(part.kind) ? part.kind : (mf.name.EndsWith("_collider_mesh") ? "mesh" : "hull");
            var go = new GameObject(mf.name);
            go.transform.SetParent(holder.transform, false);
            go.transform.SetPositionAndRotation(mf.transform.position, mf.transform.rotation);
            var ls = mf.transform.lossyScale; var hs = holder.transform.lossyScale;
            go.transform.localScale = new Vector3(ls.x / Mathf.Max(1e-6f, hs.x), ls.y / Mathf.Max(1e-6f, hs.y), ls.z / Mathf.Max(1e-6f, hs.z));
            var b = mesh.bounds;
            if (kind == "box")
            {
                var c = go.AddComponent<BoxCollider>(); c.center = b.center; c.size = b.size;
            }
            else if (kind == "sphere")
            {
                var c = go.AddComponent<SphereCollider>(); c.center = b.center; c.radius = Mathf.Max(b.extents.x, Mathf.Max(b.extents.y, b.extents.z));
            }
            else if (kind == "capsule")
            {
                var s = b.size;
                int dir = s.x >= s.y && s.x >= s.z ? 0 : (s.y >= s.z ? 1 : 2);
                var c = go.AddComponent<CapsuleCollider>();
                c.center = b.center; c.direction = dir; c.height = s[dir];
                c.radius = Mathf.Max(s[(dir + 1) % 3], s[(dir + 2) % 3]) / 2f;
            }
            else
            {
                var c = go.AddComponent<MeshCollider>();
                c.sharedMesh = mesh;
                c.convex = kind != "mesh";
                if (c.convex) report.colliderTrianglesMax = Math.Max(report.colliderTrianglesMax, mesh.triangles.Length / 3);
            }
            report.colliderVertices += mesh.vertexCount;
            report.colliderCount++;
            if (!report.colliderKinds.Contains(kind)) report.colliderKinds.Add(kind);
        }
        // Check: the shapes together should span the drawn model (metres, worst side of the two boxes).
        Physics.SyncTransforms();
        var cols = holder.GetComponentsInChildren<Collider>(true);
        var lod0 = root.GetComponentsInChildren<Renderer>(true).FirstOrDefault(r => r.name.EndsWith("_LOD0") || r.name == root.name);
        if (cols.Length > 0 && lod0 != null)
        {
            var cb = cols[0].bounds;
            foreach (var c in cols) cb.Encapsulate(c.bounds);
            var rb = lod0.bounds;
            report.colliderBoundsError = Mathf.Max((cb.min - rb.min).magnitude, (cb.max - rb.max).magnitude);
        }
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
    // Sockets (Version 8)

    static void PlaceSockets(GameObject root, string path, float scale, Report report)
    {
        var rt = root.transform;
        var all = rt.GetComponentsInChildren<Transform>(true);
        if (path == null || !File.Exists(path)) return;  // the FBX's own sockets stay as they are
        SocketDoc doc = null;
        try { doc = JsonUtility.FromJson<SocketDoc>(File.ReadAllText(path)); }
        catch (Exception e) { report.warnings.Add("sockets.json not readable: " + e.Message); return; }
        if (doc == null || doc.sockets == null) return;
        Transform Find(string n) => string.IsNullOrEmpty(n) ? null
            : (all.FirstOrDefault(t => t.name == n) ?? all.FirstOrDefault(t => StripName(t.name) == StripName(n)));
        float worst = 0f, worstAngle = 0f;
        foreach (var s in doc.sockets)
        {
            if (string.IsNullOrEmpty(s.name)) continue;
            var bone = Find(s.bone);
            if (bone == null)
            {
                report.warnings.Add("socket " + s.name + ": bone " + s.bone + " not found; placed on the root");
                bone = rt;
            }
            var want = rt.TransformPoint(U(s.position, scale));
            var fwd = rt.TransformDirection(U(s.forward, 1f));
            var up = rt.TransformDirection(U(s.up, 1f));
            if (fwd.sqrMagnitude < 1e-8f) fwd = rt.forward;
            if (up.sqrMagnitude < 1e-8f || Vector3.Cross(fwd, up).sqrMagnitude < 1e-8f) up = rt.up;
            var t = all.FirstOrDefault(x => x.name == s.name);
            if (t == null)
            {
                t = new GameObject(s.name).transform;
                report.socketsAdded++;
                report.warnings.Add("socket " + s.name + " was not in the FBX; added from sockets.json");
            }
            else
            {
                worst = Mathf.Max(worst, (t.position - want).magnitude);
                worstAngle = Mathf.Max(worstAngle, Mathf.Max(Vector3.Angle(t.forward, fwd), Vector3.Angle(t.up, up)));
                if (t.parent != bone) report.warnings.Add("socket " + s.name + " was under " + (t.parent ? t.parent.name : "nothing") + "; moved to " + bone.name);
            }
            t.SetParent(bone, true);
            t.position = want;
            t.rotation = Quaternion.LookRotation(fwd, up);
            // An item parented here keeps its own size: undo any scale on the bone.
            var ls = bone.lossyScale;
            t.localScale = new Vector3(1f / Mathf.Max(1e-6f, ls.x), 1f / Mathf.Max(1e-6f, ls.y), 1f / Mathf.Max(1e-6f, ls.z));
        }
        report.socketMaxError = worst;
        report.socketMaxAngle = worstAngle;
    }

    // ---------------------------------------------------------------------------------------------------------------
    // Textures and material (G01, G02)

    static void ConfigureTextures(string assetDir, string msName, Report report, ImportArgs a)
    {
        var point = a != null && a.filter == "point";
        var mips = a == null || a.mipmaps;
        var compress = a == null || a.compress;
        report.normalMap = a == null || a.normalMap;
        report.textureFilter = point ? "Point" : "Bilinear";
        report.textureMipmaps = mips;
        report.textureCompression = compress ? "Compressed" : "Uncompressed";
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
            // Recipes: crisp looks sample whole texels (Point) with no mipmaps or compression, so a 256 px pixel-art
            // texture stays exact; other looks keep Unity's defaults (Bilinear, mipmaps, compressed).
            ti.filterMode = point ? FilterMode.Point : FilterMode.Bilinear;
            ti.mipmapEnabled = mips;
            ti.textureCompression = compress ? TextureImporterCompression.Compressed : TextureImporterCompression.Uncompressed;
            if (point) ti.npotScale = TextureImporterNPOTScale.None;
            ti.SaveAndReimport();
            report.textureSettings.Add(t + ": " + ti.textureType + (ti.sRGBTexture ? ", sRGB" : ", linear") + (t == msName ? ", alpha " + ti.alphaSource : "")
                + ", " + ti.filterMode + (ti.mipmapEnabled ? ", mipmaps" : ", no mipmaps") + ", " + ti.textureCompression);
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
        var normalTex = report.normalMap ? AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/normal.png") : null;
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
                // Attachment sockets hang under bones but are not bones: they must not change the shape-based mapping.
                bones.RemoveWhere(t => IsSocketName(t.name));
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

// Runs inside an open Editor: after any import that brings in a hand-off request, and once after each script load
// (which also covers a newer VgacImport.cs dropped into the open project). The work is deferred to delayCall, outside
// the import, because importing assets from inside OnPostprocessAllAssets is not allowed. Batch mode is left to
// VgacImport.Run and VgacImport.ImportPending. Nothing here throws, and nothing runs in a project with no requests.
class VgacImportWatcher : AssetPostprocessor
{
    static bool scheduled;

    static void OnPostprocessAllAssets(string[] importedAssets, string[] deletedAssets, string[] movedAssets, string[] movedFromAssetPaths)
    {
        try
        {
            if (Application.isBatchMode || importedAssets == null) return;
            foreach (var p in importedAssets)
                if (p != null && p.EndsWith("/" + VgacImport.RequestFile, StringComparison.Ordinal) && p.StartsWith("Assets/VGAC/", StringComparison.Ordinal))
                {
                    Schedule();
                    return;
                }
        }
        catch (Exception e) { Debug.LogError("VGAC: watcher failed: " + e); }
    }

    [InitializeOnLoadMethod]
    static void AtLoad()
    {
        try
        {
            if (Application.isBatchMode) return;
            if (VgacImport.PendingRequests().Count > 0) Schedule();
        }
        catch (Exception e) { Debug.LogError("VGAC: watcher failed at load: " + e); }
    }

    static void Schedule()
    {
        if (scheduled) return;
        scheduled = true;
        EditorApplication.delayCall += () =>
        {
            scheduled = false;
            try
            {
                if (EditorApplication.isCompiling || EditorApplication.isUpdating || EditorApplication.isPlayingOrWillChangePlaymode) { Schedule(); return; }
                VgacImport.Sweep();
            }
            catch (Exception e) { Debug.LogError("VGAC: pending import failed: " + e); }
        };
    }
}
