// Sandbox-only check of VgacSpringBones (not copied into users' projects). The prefab is moved the way a game moves it:
// it walks forward at 1.4 m/s (scaled with its size) for 2 s while its shared Humanoid walk clip plays, then stops for
// 1.5 s. It is run at 30, 60 and 144 frames a second and at sizes 0.5, 1 and 2. Per run: how far each chain tip trails
// from where its rest pose would put it, divided by the size (a scale-free number), sampled every 0.1 s, so runs can be
// compared. At 30 fps and size 1 it also renders side views at a few moments (with graphics only).
// Run: Unity.exe -batchmode -projectPath <sandbox> -executeMethod VgacSpringCheck.Run -vgacName <name> -vgacOut <dir>
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;

public static class VgacSpringCheck
{
    [Serializable] class Series { public string label = ""; public float fps, scale, maxTrail, trailWhileWalking, trailAfterStop; public List<float> trail = new List<float>(), tipBehind = new List<float>(); }
    [Serializable] class Doc { public string prefab = "", clip = ""; public List<Series> series = new List<Series>(); public List<string> files = new List<string>(); public string error = ""; }

    static string Arg(string k) { var a = Environment.GetCommandLineArgs(); for (int i = 0; i < a.Length - 1; i++) if (a[i] == k) return a[i + 1]; return null; }

    public static void Run()
    {
        var name = Arg("-vgacName");
        var outDir = Arg("-vgacOut") ?? ".";
        Directory.CreateDirectory(outDir);
        var doc = new Doc();
        try
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/VGAC/" + name + "/" + name + ".prefab");
            doc.prefab = name;
            var type = VgacImport.SpringType();
            foreach (var (fps, scale) in new[] { (30f, 1f), (60f, 1f), (144f, 1f), (60f, 2f), (60f, 0.5f) })
            {
                var go = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
                go.transform.position = Vector3.zero;
                go.transform.localScale = Vector3.one * scale;
                var animator = go.GetComponent<Animator>();
                var clip = animator.runtimeAnimatorController.animationClips
                    .Where(c => c.name.ToLowerInvariant().Contains("walk") && !c.name.ToLowerInvariant().Contains("crouch") && !c.name.ToLowerInvariant().Contains("formal"))
                    .OrderBy(c => c.name.Length).FirstOrDefault();
                doc.clip = clip != null ? clip.name : "";
                PlayableGraph graph = default;
                AnimationClipPlayable playable = default;
                if (Arg("-vgacNoClip") != null) clip = null;
                if (clip != null)
                {
                    graph = PlayableGraph.Create("check");
                    graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);
                    var output = AnimationPlayableOutput.Create(graph, "anim", animator);
                    playable = AnimationClipPlayable.Create(graph, clip);
                    output.SetSourcePlayable(playable);
                    graph.Play();
                }
                var comp = go.GetComponent(type);
                var step = type.GetMethod("Step");
                var tips = type.GetMethod("TipOffsets");
                var chains = (Array)type.GetField("chains").GetValue(comp);
                var tipBone = chains.Cast<object>().Select(c => ((Transform[])c.GetType().GetField("bones").GetValue(c)).Last()).First();
                var s = new Series { label = fps + " fps, size x" + scale, fps = fps, scale = scale };
                bool shoot = fps == 30f && scale == 1f && SystemInfo.graphicsDeviceType != UnityEngine.Rendering.GraphicsDeviceType.Null;
                Camera cam = null; RenderTexture rt = null;
                if (shoot)
                {
                    var camGo = new GameObject("checkcam");
                    cam = camGo.AddComponent<Camera>();
                    cam.fieldOfView = 30f;
                    cam.clearFlags = CameraClearFlags.SolidColor;
                    cam.backgroundColor = new Color(0.35f, 0.38f, 0.42f);
                    rt = new RenderTexture(384, 384, 24);
                    cam.targetTexture = rt;
                    var lightGo = new GameObject("checklight");
                    var l = lightGo.AddComponent<Light>(); l.type = LightType.Directional; lightGo.transform.rotation = Quaternion.Euler(40, -30, 0);
                }
                var shots = new[] { 0.0f, 0.4f, 0.8f, 1.2f, 1.6f, 2.0f, 2.3f, 2.6f, 3.0f };
                int si = 0;
                float dt = 1f / fps, total = 3.5f, speed = 1.4f * scale;
                int n = Mathf.RoundToInt(total * fps);
                float nextSample = 0f;
                for (int i = 0; i <= n; i++)
                {
                    float t = i * dt;
                    if (t <= 2f) go.transform.position += go.transform.forward * speed * dt;
                    if (Arg("-vgacNoClip") != null) clip = null;
                if (clip != null)
                    {
                        double ct = t <= 2f ? t % clip.length : 0.0;
                        playable.SetTime(ct); graph.Evaluate(0f); playable.SetTime(ct); graph.Evaluate(0f);
                    }
                    step.Invoke(comp, new object[] { dt });
                    if (t + 1e-4f >= nextSample)
                    {
                        float tr = ((float[])tips.Invoke(comp, null)).DefaultIfEmpty(0f).Max() / scale;
                        s.trail.Add((float)Math.Round(tr, 4));
                        s.tipBehind.Add((float)Math.Round(-go.transform.InverseTransformPoint(tipBone.position).z, 4));
                        nextSample += 0.1f;
                    }
                    if (shoot && si < shots.Length && t + 1e-4f >= shots[si])
                    {
                        var centre = go.transform.position + Vector3.up * 0.9f;
                        cam.transform.position = centre + go.transform.right * 4.2f + Vector3.up * 0.2f;
                        cam.transform.LookAt(centre);
                        cam.Render();
                        RenderTexture.active = rt;
                        var tex = new Texture2D(384, 384, TextureFormat.RGB24, false);
                        tex.ReadPixels(new Rect(0, 0, 384, 384), 0, 0);
                        tex.Apply();
                        RenderTexture.active = null;
                        var file = Path.Combine(outDir, "unity_" + si.ToString("00") + "_t" + shots[si].ToString("0.0", System.Globalization.CultureInfo.InvariantCulture) + "s.png");
                        File.WriteAllBytes(file, tex.EncodeToPNG());
                        doc.files.Add(file);
                        si++;
                    }
                }
                s.maxTrail = s.trail.Max();
                s.trailWhileWalking = s.trail.Skip(5).Take(15).Average();
                s.trailAfterStop = s.trail.Skip(33).DefaultIfEmpty(0f).Average();
                doc.series.Add(s);
                if (clip != null) graph.Destroy();
                UnityEngine.Object.DestroyImmediate(go);
                if (cam != null) { UnityEngine.Object.DestroyImmediate(cam.gameObject); var lg = GameObject.Find("checklight"); if (lg) UnityEngine.Object.DestroyImmediate(lg); }
            }
        }
        catch (Exception e) { doc.error = e.ToString(); }
        File.WriteAllText(Path.Combine(outDir, "spring_check.json"), JsonUtility.ToJson(doc, true));
        Debug.Log("VGAC_SPRING_CHECK " + (doc.error == "" ? "ok" : doc.error));
    }
}
