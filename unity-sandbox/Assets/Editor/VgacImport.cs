// Batch-mode validation importer for Video Game Asset Creator.
// Run: Unity.exe -batchmode -nographics -quit -projectPath <sandbox> -executeMethod VgacImport.Run -vgacInput <dir> -vgacName <name> [-vgacHumanoid]
// Expects in <dir>: <name>.fbx (meshes named <name>_LOD0..n, optional <name>_collider, optional armature), textures
// base_color.png, normal.png, metallic.png, roughness.png, ao.png (any subset). Writes <dir>/unity_report.json.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

public static class VgacImport
{
    [Serializable]
    class Report
    {
        public bool ok;
        public string error = "";
        public string prefab = "";
        public int lodCount;
        public List<int> lodTriangles = new List<int>();
        public int bones;
        public string avatar = "";
        public bool humanoidValid;
        public int colliderVertices;
        public List<string> texturesAssigned = new List<string>();
        public List<string> warnings = new List<string>();
        public int clipCount;
        public List<string> clips = new List<string>();
        public string controller = "";
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
        var report = new Report();
        try
        {
            if (input == null || name == null) throw new Exception("missing -vgacInput or -vgacName");
            var assetDir = "Assets/VGAC/" + name;
            Directory.CreateDirectory(assetDir);
            var fbxSrc = Path.Combine(input, name + ".fbx");
            if (!File.Exists(fbxSrc)) fbxSrc = Directory.GetFiles(input, "*.fbx").FirstOrDefault() ?? fbxSrc;
            if (File.Exists(fbxSrc)) File.Copy(fbxSrc, Path.Combine(assetDir, name + ".fbx"), true);
            foreach (var f in new[] { "base_color.png", "normal.png", "metallic.png", "roughness.png", "ao.png" })
            {
                var src = Path.Combine(input, f);
                if (File.Exists(src)) File.Copy(src, Path.Combine(assetDir, f), true);
            }
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);

            var fbxPath = assetDir + "/" + name + ".fbx";
            var importer = AssetImporter.GetAtPath(fbxPath) as ModelImporter;
            if (importer == null) throw new Exception("FBX not imported: " + fbxPath);
            importer.materialImportMode = ModelImporterMaterialImportMode.None;
            importer.importNormals = ModelImporterNormals.Import;
            importer.importTangents = ModelImporterTangents.CalculateMikk;
            importer.animationType = humanoid ? ModelImporterAnimationType.Human : ModelImporterAnimationType.Generic;
            importer.importAnimation = true;
            importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
            importer.SaveAndReimport();

            // Texture import settings.
            foreach (var t in new[] { "normal.png", "metallic.png", "roughness.png", "ao.png" })
            {
                var ti = AssetImporter.GetAtPath(assetDir + "/" + t) as TextureImporter;
                if (ti == null) continue;
                ti.sRGBTexture = false;
                if (t == "normal.png") ti.textureType = TextureImporterType.NormalMap;
                ti.SaveAndReimport();
            }

            var model = AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath);
            if (model == null) throw new Exception("model load failed");

            // Material: URP Lit if present, else Standard.
            var shader = Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("Standard");
            var mat = new Material(shader);
            var baseTex = AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/base_color.png");
            var normalTex = AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/normal.png");
            var aoTex = AssetDatabase.LoadAssetAtPath<Texture2D>(assetDir + "/ao.png");
            if (baseTex) { mat.SetTexture(mat.HasProperty("_BaseMap") ? "_BaseMap" : "_MainTex", baseTex); report.texturesAssigned.Add("base_color"); }
            if (normalTex) { mat.SetTexture("_BumpMap", normalTex); mat.EnableKeyword("_NORMALMAP"); report.texturesAssigned.Add("normal"); }
            if (aoTex) { mat.SetTexture("_OcclusionMap", aoTex); report.texturesAssigned.Add("ao"); }
            AssetDatabase.CreateAsset(mat, assetDir + "/" + name + ".mat");

            var instance = (GameObject)PrefabUtility.InstantiatePrefab(model);
            instance.name = name;
            var renderers = instance.GetComponentsInChildren<Renderer>(true);
            foreach (var r in renderers) r.sharedMaterial = mat;

            // Collider: convex MeshCollider from <name>_collider, renderer disabled.
            var colObj = instance.GetComponentsInChildren<MeshFilter>(true).FirstOrDefault(m => m.name.EndsWith("_collider"));
            if (colObj != null)
            {
                var mc = instance.AddComponent<MeshCollider>();
                mc.sharedMesh = colObj.sharedMesh;
                mc.convex = true;
                report.colliderVertices = colObj.sharedMesh.vertexCount;
                var cr = colObj.GetComponent<Renderer>();
                if (cr) cr.enabled = false;
            }
            else report.warnings.Add("no collider mesh");

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

            // Rig and avatar.
            var animator = instance.GetComponent<Animator>() ?? instance.AddComponent<Animator>();
            var avatar = AssetDatabase.LoadAllAssetsAtPath(fbxPath).OfType<Avatar>().FirstOrDefault();
            if (avatar != null)
            {
                animator.avatar = avatar;
                report.avatar = avatar.name;
                report.humanoidValid = avatar.isHuman && avatar.isValid;
            }
            var smrs = instance.GetComponentsInChildren<SkinnedMeshRenderer>(true);
            if (smrs.Length > 0) report.bones = smrs[0].bones.Length;

            // Optional animation library: import as Humanoid and build an AnimatorController with its clips.
            var clipsSrc = Arg("-vgacClips");
            if (clipsSrc != null && File.Exists(clipsSrc) && avatar != null && avatar.isHuman)
            {
                var clipsPath = assetDir + "/clips.fbx";
                File.Copy(clipsSrc, clipsPath, true);
                AssetDatabase.ImportAsset(clipsPath, ImportAssetOptions.ForceSynchronousImport);
                var ci = AssetImporter.GetAtPath(clipsPath) as ModelImporter;
                ci.animationType = ModelImporterAnimationType.Human;
                ci.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
                ci.importAnimation = true;
                ci.materialImportMode = ModelImporterMaterialImportMode.None;
                ci.SaveAndReimport();
                var clipAssets = AssetDatabase.LoadAllAssetsAtPath(clipsPath).OfType<AnimationClip>().Where(c => !c.name.StartsWith("__preview")).ToList();
                var controller = UnityEditor.Animations.AnimatorController.CreateAnimatorControllerAtPath(assetDir + "/" + name + ".controller");
                int added = 0;
                foreach (var clip in clipAssets)
                {
                    var state = controller.layers[0].stateMachine.AddState(clip.name);
                    state.motion = clip;
                    report.clips.Add(clip.name);
                    if (++added >= 200) break;
                }
                animator.runtimeAnimatorController = controller;
                report.clipCount = added;
                report.controller = AssetDatabase.GetAssetPath(controller);
            }
            else
            {
                if (clipsSrc != null) report.warnings.Add("clips library skipped: avatar not humanoid or file missing");
                // Generic rigs: use the clips baked into the model FBX itself (procedural idle and walk).
                var ownClips = AssetDatabase.LoadAllAssetsAtPath(fbxPath).OfType<AnimationClip>().Where(c => !c.name.StartsWith("__preview")).ToList();
                if (ownClips.Count > 0)
                {
                    var controller = UnityEditor.Animations.AnimatorController.CreateAnimatorControllerAtPath(assetDir + "/" + name + ".controller");
                    foreach (var clip in ownClips)
                    {
                        var state = controller.layers[0].stateMachine.AddState(clip.name);
                        state.motion = clip;
                        report.clips.Add(clip.name);
                    }
                    animator.runtimeAnimatorController = controller;
                    report.clipCount = ownClips.Count;
                    report.controller = AssetDatabase.GetAssetPath(controller);
                }
            }

            var prefabPath = assetDir + "/" + name + ".prefab";
            PrefabUtility.SaveAsPrefabAsset(instance, prefabPath);
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

    // Render frames of the prefab's clips to PNG without entering play mode.
    // Run: Unity.exe -batchmode -quit -projectPath <sandbox> -executeMethod VgacImport.Snapshot -vgacName <name> -vgacOut <dir> [-vgacClipFilter walk,idle] [-vgacFrames 4] [-vgacSize 512]
    // Needs graphics (do not pass -nographics).
    public static void Snapshot()
    {
        var name = Arg("-vgacName");
        var outDir = Arg("-vgacOut") ?? ".";
        var filter = (Arg("-vgacClipFilter") ?? "").ToLowerInvariant().Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries);
        int frames = int.Parse(Arg("-vgacFrames") ?? "4");
        int size = int.Parse(Arg("-vgacSize") ?? "512");
        var log = new List<string>();
        try
        {
            Directory.CreateDirectory(outDir);
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/VGAC/" + name + "/" + name + ".prefab");
            if (prefab == null) throw new Exception("prefab missing for " + name);
            var scene = UnityEditor.SceneManagement.EditorSceneManager.NewScene(UnityEditor.SceneManagement.NewSceneSetup.DefaultGameObjects, UnityEditor.SceneManagement.NewSceneMode.Single);
            var go = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
            var bounds = new Bounds(go.transform.position, Vector3.one);
            foreach (var r in go.GetComponentsInChildren<Renderer>()) if (r.enabled) bounds.Encapsulate(r.bounds);
            var camGo = new GameObject("snapcam");
            var cam = camGo.AddComponent<Camera>();
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = new Color(0.45f, 0.45f, 0.45f, 1f);
            float dist = bounds.extents.magnitude * 2.2f;
            camGo.transform.position = bounds.center + new Vector3(0.9f, 0.35f, -1f).normalized * dist;
            camGo.transform.LookAt(bounds.center);
            cam.nearClipPlane = 0.01f; cam.farClipPlane = dist * 10f;
            var rt = new RenderTexture(size, size, 24);
            cam.targetTexture = rt;
            var animator = go.GetComponent<Animator>();
            var clips = animator != null && animator.runtimeAnimatorController != null ? animator.runtimeAnimatorController.animationClips : new AnimationClip[0];
            var picked = clips.Where(c => filter.Length == 0 || filter.Any(f => c.name.ToLowerInvariant().Contains(f))).Take(6).ToList();
            if (picked.Count == 0) picked.Add(null);
            foreach (var clip in picked)
            {
                for (int i = 0; i < frames; i++)
                {
                    if (clip != null)
                    {
                        UnityEditor.AnimationMode.StartAnimationMode();
                        UnityEditor.AnimationMode.BeginSampling();
                        UnityEditor.AnimationMode.SampleAnimationClip(go, clip, clip.length * i / Math.Max(1, frames - 1));
                        UnityEditor.AnimationMode.EndSampling();
                    }
                    cam.Render();
                    RenderTexture.active = rt;
                    var tex = new Texture2D(size, size, TextureFormat.RGB24, false);
                    tex.ReadPixels(new Rect(0, 0, size, size), 0, 0);
                    tex.Apply();
                    RenderTexture.active = null;
                    var clipName = clip != null ? clip.name.Replace("|", "_").Replace(" ", "_") : "rest";
                    var file = Path.Combine(outDir, $"{clipName}_{i:00}.png");
                    File.WriteAllBytes(file, tex.EncodeToPNG());
                    log.Add(file);
                    if (clip != null) UnityEditor.AnimationMode.StopAnimationMode();
                }
            }
            File.WriteAllText(Path.Combine(outDir, "snapshot_report.json"), JsonUtility.ToJson(new SnapReport { ok = true, files = log }, true));
            Debug.Log("VGAC_SNAPSHOT ok " + log.Count);
        }
        catch (Exception e)
        {
            File.WriteAllText(Path.Combine(outDir, "snapshot_report.json"), JsonUtility.ToJson(new SnapReport { ok = false, error = e.ToString(), files = log }, true));
            Debug.Log("VGAC_SNAPSHOT fail " + e);
            EditorApplication.Exit(1);
        }
    }

    [Serializable] class SnapReport { public bool ok; public string error = ""; public List<string> files = new List<string>(); }
}
