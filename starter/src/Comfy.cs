// The app's own ComfyUI, as the maintenance jobs see it. Everything here is behind IComfyInstall so the jobs can be built and
// tested before the backend side (backend/vgac/comfy_install.py) carries the pieces below. The text of what is needed from
// that side (N1 to N5) is in the phase 9 design note and is the specification:
//
//   N1  the marker `<data>/comfyui/vgac-install.json` lists `owned` (paths, relative to the install folder, of everything VGAC
//       made there: source files by the pinned list, .venv, wheels, custom_nodes/ComfyUI-SkinToken, custom_nodes/vgac-nodes,
//       logs) and `kept` (top-level names that are never VGAC's to delete: models, output, input, user), updated per step.
//   N2  the guard rules (inside the install folder by real path, marker present, outside every foreign ComfyUI) as shared test
//       cases in `setup/guard-cases.json`, used by the Python tests and by these C# tests.
//   N3  `python -m vgac.comfy_install retarget <old_data> <new_data>` and `python -m vgac.comfy_install rebuild-venv`.
//   N4  engine entries written relative to the data folder where possible.
//   N5  comfy_find records, for a node pack VGAC put into a linked ComfyUI, the linked ComfyUI's root and the Python packages
//       it installed there (ledger kind `pip-note`).
//
// Remove needs N1, N2, N5. Move needs N1, N2, N3, N4. C# 5 (see Core.cs).
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace Vgac.Starter
{
    public interface IComfyInstall
    {
        bool Present(string dataFolder);                  // the folder has VGAC's marker
        bool Offered(string dataFolder);                  // the marker has the N1 lists, so remove and move may touch it
        string Root(string dataFolder);                   // <data>/comfyui
        List<string> Owned(string dataFolder);            // N1: absolute paths of what VGAC made inside the root (empty when not offered)
        List<string> Kept(string dataFolder);             // N1: names that are never deleted
        string Retarget(string oldData, string newData);  // N3: null when done, else a plain sentence
        string RebuildVenv(string newData);               // N3: the same
    }

    // The guard (N2): a path may be written or deleted only when it is inside the install folder (real path), the install
    // folder has VGAC's marker, and it is not inside, or around, a ComfyUI VGAC did not install.
    public static class ComfyGuard
    {
        public const string MarkerName = "vgac-install.json";

        public static bool MarkerPresent(string root)
        {
            try
            {
                var d = Json.ParseObject(File.ReadAllText(Path.Combine(root, MarkerName), Encoding.UTF8));
                object m;
                return d.TryGetValue("made_by", out m) && Convert.ToString(m) == "VGAC";
            }
            catch (Exception) { return false; }
        }

        public static bool Allows(string target, string root, IEnumerable<string> foreignFolders)
        {
            if (!SafeFs.Inside(target, root)) return false;
            if (!MarkerPresent(root)) return false;
            if (foreignFolders != null)
                foreach (string f in foreignFolders)
                    if (!string.IsNullOrEmpty(f) && (SafeFs.Inside(target, f) || SafeFs.Inside(f, target))) return false;
            return true;
        }
    }

    // The real one: reads the marker. Until the backend writes the N1 lists, nothing in the install folder is offered.
    public class MarkerComfyInstall : IComfyInstall
    {
        readonly Func<string, string, string> runCli;   // (arguments, working folder) -> null when done, else a plain sentence

        public MarkerComfyInstall(Func<string, string, string> runCli) { this.runCli = runCli; }

        public string Root(string data) { return Path.Combine(data, "comfyui"); }

        public bool Present(string data) { return ComfyGuard.MarkerPresent(Root(data)); }

        Dictionary<string, object> Marker(string data)
        {
            try { return Json.ParseObject(File.ReadAllText(Path.Combine(Root(data), ComfyGuard.MarkerName), Encoding.UTF8)); }
            catch (Exception) { return null; }
        }

        static List<string> Strings(object o)
        {
            var res = new List<string>();
            var arr = o as System.Collections.IEnumerable;
            if (arr == null || o is string) return null;
            foreach (object x in arr) if (x != null) res.Add(x.ToString());
            return res;
        }

        public bool Offered(string data)
        {
            var m = Marker(data);
            object owned;
            return m != null && Present(data) && m.TryGetValue("owned", out owned) && Strings(owned) != null;
        }

        public List<string> Owned(string data)
        {
            var res = new List<string>();
            var m = Marker(data); object o;
            if (m == null || !Offered(data) || !m.TryGetValue("owned", out o)) return res;
            string root = Root(data);
            List<string> kept = Kept(data);
            foreach (string rel in Strings(o))
            {
                if (rel.Length == 0 || Path.IsPathRooted(rel) || rel.Contains("..")) continue;   // only names inside the install folder
                string full = Path.Combine(root, rel.Replace('/', '\\'));
                if (RemovalPlanner.KeptProblem(full, root, kept) != null) continue;               // the folder itself, or anything it keeps
                res.Add(full);
            }
            return res;
        }

        public List<string> Kept(string data)
        {
            // What is kept is at least models, output, input, user and custom_nodes; a marker can add to that, never take from it.
            var kept = new List<string>(new[] { "models", "output", "input", "user", "custom_nodes" });
            var m = Marker(data); object o;
            if (m != null && m.TryGetValue("kept", out o))
            {
                var k = Strings(o);
                if (k != null) foreach (string x in k) if (!kept.Contains(x) && x.Length > 0 && !Path.IsPathRooted(x) && !x.Contains("..")) kept.Add(x);
            }
            return kept;
        }

        public string Retarget(string oldData, string newData)
        {
            if (!Offered(newData)) return "The app's ComfyUI cannot be moved yet: its install record is from an older version.";
            return runCli("retarget \"" + oldData + "\" \"" + newData + "\"", null);
        }

        public string RebuildVenv(string newData)
        {
            if (!Offered(newData)) return "The app's ComfyUI cannot be rebuilt yet: its install record is from an older version.";
            return runCli("rebuild-venv", null);
        }
    }
}
