// VgacSpringBones Version 1
// Spring bones for characters made by Video Game Asset Creator: parts that hang free (a ponytail, a braid, a cape, a
// tail) sway when the character moves. Our own small script, no package needed. The VGAC importer (VgacImport.cs)
// adds it to the prefab and calls Setup with springs.json while the prefab stands in its bind pose; Setup finds the
// chain bones by name and stores their rest rotations, the hit boxes as capsules on their bones, and how far each
// chain's rest pose already sits inside a loose hit box.
//
// Every frame (LateUpdate, after the Animator) the chain is simulated in fixed steps of 1/120 s, the same steps the
// backend bakes into the character's own clips (backend/vgac/blender/springs.py simulate): each joint is pulled toward
// where its rest pose would put it (stiffness in Hz, damping as a share of critical damping), slowed by the air (drag,
// 1/s, which makes the part trail when the character moves), pulled by gravity less the part of it across the bone at
// rest (the part keeps its built shape while the character stands upright), pushed out of the hit boxes and kept at
// its length. The parent's pose is interpolated across the steps of a frame, so the result does not depend on the
// frame rate; gravity, lengths and radii scale with the character's size, so it looks the same at every size.
//
// The chains are not humanoid bones, so the shared Humanoid clips never move them: this script is what moves them
// there. In the character's own clips the sway is also baked in; the script replaces it with sway that reacts.
using System;
using System.Collections.Generic;
using UnityEngine;

[DisallowMultipleComponent]
public class VgacSpringBones : MonoBehaviour
{
    public const string Version = "1";
    public const float StepSeconds = 1f / 120f;

    [Serializable]
    public class Chain
    {
        public string name = "", kind = "";
        public Transform[] bones = new Transform[0];
        public Quaternion[] restLocal = new Quaternion[0];   // each bone's local rotation in the bind pose
        public Vector3[] segLocal = new Vector3[0];          // each bone's tail in its own local space (unscaled)
        public Vector3[] gravityAcross = new Vector3[0];     // gravity across each bone at rest, in its local space, per m/s^2
        public float[] colliderRadius = new float[0];        // per collider: the radius this chain uses (at scale 1)
        public float radius = 0.02f;
        public float stiffness = 1.8f, damping = 0.35f, drag = 1.6f, gravity = 9.81f;
        [NonSerialized] public Vector3[] p, prev, tprev;
        [NonSerialized] public Vector3 lastRoot;
        [NonSerialized] public Quaternion lastParent;
        [NonSerialized] public bool started;
    }

    [Serializable]
    public class Capsule
    {
        public string name = "";
        public Transform bone;
        public Vector3 a, b;     // ends in the bone's local space
        public float radius;     // metres at scale 1
    }

    public Chain[] chains = new Chain[0];
    public Capsule[] colliders = new Capsule[0];
    [Tooltip("Off: the chains keep their bind pose (or what the clip keys).")]
    public bool simulate = true;
    public float scaleAtSetup = 1f;
    float acc;

    // --- springs.json -----------------------------------------------------------------------------------------------
    [Serializable] class JChain { public string name = "", kind = "", parent = ""; public string[] bones = new string[0]; public JVec[] joints_list; public float radius = 0.02f, stiffness = 1.8f, damping = 0.35f, drag = 1.6f, gravity = 9.81f; }
    [Serializable] class JVec { public float[] v; }
    [Serializable] class JCol { public string name = "", bone = "", kind = ""; public float[] center = new float[3], axis = new float[3], size = new float[3]; public float radius, height; }
    [Serializable] class JDoc { public List<JChain> chains = new List<JChain>(); public List<JCol> colliders = new List<JCol>(); }

    static Vector3 U(float[] v, float scale) => v == null || v.Length < 3 ? Vector3.zero : new Vector3(-v[0], v[1], v[2]) * scale;

    // JsonUtility cannot read nested arrays: "joints": [[x,y,z],...] is rewritten to "joints_list": [{"v":[...]},...].
    static string FlattenJoints(string json)
    {
        var sb = new System.Text.StringBuilder();
        int i = 0;
        while (i < json.Length)
        {
            int k = json.IndexOf("\"joints\"", i, StringComparison.Ordinal);
            if (k < 0) { sb.Append(json, i, json.Length - i); break; }
            sb.Append(json, i, k - i);
            int open = json.IndexOf('[', k);
            int depth = 0, end = open;
            for (; end < json.Length; end++)
            {
                if (json[end] == '[') depth++;
                else if (json[end] == ']') { depth--; if (depth == 0) break; }
            }
            var inner = json.Substring(open + 1, end - open - 1);
            var items = new List<string>();
            int d = 0, s = -1;
            for (int j = 0; j < inner.Length; j++)
            {
                if (inner[j] == '[') { if (d == 0) s = j; d++; }
                else if (inner[j] == ']') { d--; if (d == 0) items.Add("{\"v\":" + inner.Substring(s, j - s + 1) + "}"); }
            }
            sb.Append("\"joints_list\":[" + string.Join(",", items) + "]");
            i = end + 1;
        }
        return sb.ToString();
    }

    // Called by the importer with the prefab instance in its bind pose. Returns a short summary ("2 chains, 6 bones";
    // a missing bone is skipped and named).
    public string Setup(string springsJson, float scale)
    {
        var doc = JsonUtility.FromJson<JDoc>(FlattenJoints(springsJson));
        var all = GetComponentsInChildren<Transform>(true);
        Transform Find(string n)
        {
            if (string.IsNullOrEmpty(n)) return null;
            foreach (var t in all) if (t.name == n) return t;
            var tail = n.Substring(n.LastIndexOf(':') + 1);
            foreach (var t in all) if (t.name.EndsWith(tail, StringComparison.Ordinal) && (t.name.Length == tail.Length || t.name[t.name.Length - tail.Length - 1] == ':')) return t;
            return null;
        }
        var rt = transform;
        scaleAtSetup = Mathf.Max(1e-6f, rt.lossyScale.y);
        var cols = new List<Capsule>();
        foreach (var c in doc.colliders ?? new List<JCol>())
        {
            var bone = Find(c.bone);
            if (bone == null) continue;
            var centre = rt.TransformPoint(U(c.center, scale));
            var axis = rt.TransformDirection(U(c.axis, 1f)).normalized;
            float r, half;
            if (c.kind == "box") { r = Mathf.Min(c.size[0], c.size[2]) * 0.5f * scale; half = Mathf.Max(c.size[1] * 0.5f * scale - r, 0f); }
            else if (c.kind == "sphere") { r = c.radius * scale; half = 0f; }
            else { r = c.radius * scale; half = Mathf.Max(c.height * 0.5f * scale - r, 0f); }
            cols.Add(new Capsule { name = c.name, bone = bone, a = bone.InverseTransformPoint(centre - axis * half), b = bone.InverseTransformPoint(centre + axis * half), radius = r });
        }
        colliders = cols.ToArray();
        var made = new List<Chain>();
        var missing = new List<string>();
        int nb = 0;
        foreach (var jc in doc.chains ?? new List<JChain>())
        {
            var bones = new List<Transform>();
            foreach (var n in jc.bones) { var t = Find(n); if (t == null) { missing.Add(n); break; } bones.Add(t); }
            if (bones.Count != jc.bones.Length || bones.Count == 0 || jc.joints_list == null || jc.joints_list.Length != bones.Count + 1) continue;
            var ch = new Chain { name = jc.name, kind = jc.kind, bones = bones.ToArray(), radius = jc.radius * scale, stiffness = jc.stiffness, damping = jc.damping, drag = jc.drag, gravity = jc.gravity };
            int n2 = bones.Count;
            ch.restLocal = new Quaternion[n2];
            ch.segLocal = new Vector3[n2];
            ch.gravityAcross = new Vector3[n2];
            var g = Vector3.down;
            for (int k = 0; k < n2; k++)
            {
                var b = bones[k];
                ch.restLocal[k] = b.localRotation;
                var tailWorld = k + 1 < n2 ? bones[k + 1].position : rt.TransformPoint(U(jc.joints_list[n2].v, scale));
                ch.segLocal[k] = b.InverseTransformPoint(tailWorld);
                var dir = (tailWorld - b.position).normalized;
                var across = g - Vector3.Dot(g, dir) * dir;   // per m/s^2 of gravity
                ch.gravityAcross[k] = Quaternion.Inverse(b.rotation) * across;
            }
            // the radius each collider may have for this chain: never more than keeps the rest pose just outside it
            ch.colliderRadius = new float[colliders.Length];
            for (int i = 0; i < colliders.Length; i++)
            {
                var c = colliders[i];
                var A = c.bone.TransformPoint(c.a); var B = c.bone.TransformPoint(c.b);
                float best = float.MaxValue;
                for (int k = 0; k < n2; k++)
                {
                    var q = k + 1 < n2 ? bones[k + 1].position : bones[k].TransformPoint(ch.segLocal[k]);
                    best = Mathf.Min(best, (q - Closest(q, A, B)).magnitude);
                }
                ch.colliderRadius[i] = Mathf.Max(0f, Mathf.Min(c.radius, best - ch.radius - 0.002f * scale)) / scaleAtSetup;
            }
            ch.radius /= scaleAtSetup;
            made.Add(ch);
            nb += n2;
        }
        foreach (var c in colliders) c.radius /= scaleAtSetup;
        chains = made.ToArray();
        return made.Count + " chains, " + nb + " bones" + (missing.Count > 0 ? "; bones not found: " + string.Join(", ", missing) : "");
    }

    static Vector3 Closest(Vector3 p, Vector3 a, Vector3 b)
    {
        var ab = b - a;
        float d = Vector3.Dot(ab, ab);
        float t = d < 1e-12f ? 0f : Mathf.Clamp01(Vector3.Dot(p - a, ab) / d);
        return a + ab * t;
    }

    // --- running ----------------------------------------------------------------------------------------------------
    void OnEnable() { acc = 0f; foreach (var c in chains) c.started = false; }

    void LateUpdate()
    {
        if (!simulate) return;
        Step(Mathf.Min(Time.deltaTime, 0.1f));
    }

    // Advance by dt seconds (fixed 1/120 s steps inside). Public so a tool can drive it frame by frame (the importer's
    // snapshot does). Call it after the pose of the frame has been set.
    public void Step(float dt)
    {
        acc += dt;
        int n = Mathf.Min(24, Mathf.FloorToInt(acc / StepSeconds));
        acc -= n * StepSeconds;
        float scale = Mathf.Max(1e-6f, transform.lossyScale.y);
        foreach (var c in chains) Run(c, n, scale);
    }

    void Run(Chain c, int steps, float scale)
    {
        int nb = c.bones.Length;
        if (nb == 0 || c.bones[0] == null) return;
        var parent = c.bones[0].parent;
        // the pose this frame: bones back to rest under their (animated) parent
        for (int k = 0; k < nb; k++) c.bones[k].localRotation = c.restLocal[k];
        var rootNow = c.bones[0].position;
        var parentNow = parent != null ? parent.rotation : Quaternion.identity;
        if (!c.started || c.p == null || c.p.Length != nb + 1)
        {
            c.p = new Vector3[nb + 1]; c.prev = new Vector3[nb + 1]; c.tprev = new Vector3[nb + 1];
            c.p[0] = rootNow;
            for (int k = 0; k < nb; k++) c.p[k + 1] = c.bones[k].TransformPoint(c.segLocal[k]);
            Array.Copy(c.p, c.prev, nb + 1); Array.Copy(c.p, c.tprev, nb + 1);
            c.lastRoot = rootNow; c.lastParent = parentNow; c.started = true;
        }
        float h = StepSeconds;
        float w = 2f * Mathf.PI * c.stiffness;
        var g = Vector3.down * c.gravity * scale;
        var restParentLocal = c.restLocal[0];
        for (int s = 0; s < steps; s++)
        {
            float a = (s + 1f) / steps;
            var root = Vector3.Lerp(c.lastRoot, rootNow, a);
            var pr = Quaternion.Slerp(c.lastParent, parentNow, a);
            var Q = pr * restParentLocal;      // bone 0's world rotation with its rest local rotation
            var np = new Vector3[nb + 1];
            np[0] = root;
            for (int k = 0; k < nb; k++)
            {
                var segW = Q * Vector3.Scale(c.segLocal[k], c.bones[k].lossyScale);
                var target = np[k] + segW;
                var vel = (c.p[k + 1] - c.prev[k + 1]) / h;
                var vt = (target - c.tprev[k + 1]) / h;
                var gEff = g - Q * c.gravityAcross[k] * c.gravity * scale;
                var accel = w * w * (target - c.p[k + 1]) - 2f * c.damping * w * (vel - vt) - c.drag * vel + gEff;
                var x = c.p[k + 1] + vel * h + accel * h * h;
                float rad = c.radius * scale * (k + 1) / nb;
                for (int i = 0; i < colliders.Length; i++)
                {
                    var col = colliders[i];
                    if (col.bone == null) continue;
                    var A = col.bone.TransformPoint(col.a); var B = col.bone.TransformPoint(col.b);
                    var q = Closest(x, A, B);
                    var dvec = x - q;
                    float lim = (i < c.colliderRadius.Length ? c.colliderRadius[i] : col.radius) * scale + rad;
                    float dist = dvec.magnitude;
                    if (dist < lim && lim > 0f) x = q + (dist > 1e-6f ? dvec / dist : Vector3.up) * lim;
                }
                float len = segW.magnitude;
                var dir = x - np[k];
                x = np[k] + (dir.sqrMagnitude > 1e-12f ? dir.normalized : segW.normalized) * len;
                c.tprev[k + 1] = target;
                np[k + 1] = x;
                Q = Quaternion.FromToRotation(segW, x - np[k]) * Q;
                if (k + 1 < nb) Q = Q * c.restLocal[k + 1];  // the next bone at rest under this one as solved
            }
            Array.Copy(c.p, c.prev, nb + 1);
            c.p = np;
        }
        if (steps > 0) { c.lastRoot = rootNow; c.lastParent = parentNow; }
        // pose the bones along the joints
        for (int k = 0; k < nb; k++)
        {
            var b = c.bones[k];
            var cur = b.TransformPoint(c.segLocal[k]) - b.position;
            var want = c.p[k + 1] - c.p[k];
            if (cur.sqrMagnitude > 1e-12f && want.sqrMagnitude > 1e-12f) b.rotation = Quaternion.FromToRotation(cur, want) * b.rotation;
        }
    }

    // How far each chain's tip is from where its rest pose would put it, in metres (for checks and tests).
    public float[] TipOffsets()
    {
        var o = new float[chains.Length];
        for (int i = 0; i < chains.Length; i++)
        {
            var c = chains[i];
            if (c.p == null || c.bones.Length == 0) continue;
            int nb = c.bones.Length;
            var parent = c.bones[0].parent;
            var pr = parent != null ? parent.rotation : Quaternion.identity;
            var Q = pr * c.restLocal[0];
            var pos = c.bones[0].position;
            for (int k = 0; k < nb; k++)
            {
                pos += Q * Vector3.Scale(c.segLocal[k], c.bones[k].lossyScale);
                if (k + 1 < nb) Q = Q * c.restLocal[k + 1];
            }
            o[i] = (c.p[nb] - pos).magnitude;
        }
        return o;
    }
}
