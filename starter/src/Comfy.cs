// The app's own ComfyUI, as the maintenance jobs see it. The backend (backend/vgac/comfy_install.py) is the one owner of everything
// about it: what VGAC made there, what stays, how it is removed and how it is moved. The starter keeps no second model of that: it
// asks the backend's command line (`python -m vgac.comfy_install <command>`, usable with the backend not running) and shows what it
// answers. Each command prints one json line and exits non-zero with a plain reason when it refuses.
//
//   check                    is there a ComfyUI VGAC made here (marker and ledger)? what does remove keep?
//   remove                   removes it (models kept)
//   retarget <old> <new>     the data folder moved: the settings now point into the new place
//   rebuild-venv             makes its Python environment again
//
// Until a command succeeds the starter does the safe thing: a move is refused and nothing is listed for removal. C# 5 (see Core.cs).
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace Vgac.Starter
{
    // What `check` said (or why it could not).
    public class ComfyState
    {
        public bool Present;                     // the install folder has a VGAC marker (a hint only; the backend decides)
        public string Problem;                   // null: the backend confirmed it is VGAC's and can be handled; else a plain sentence
        public List<string> Keeps = new List<string>();   // what a removal keeps (absolute paths), as the backend says
    }

    public interface IComfyInstall
    {
        string Root(string dataFolder);                   // <data>/comfyui
        ComfyState State(string dataFolder);              // runs `check`
        string Remove();                                  // runs `remove`: null when done, else a plain sentence
        string Retarget(string oldData, string newData);  // runs `retarget`: the same
        string RebuildVenv(string newData);               // runs `rebuild-venv`: the same
    }

    public static class ComfyGuard
    {
        public const string MarkerName = "vgac-install.json";

        // Only a hint that a folder might be VGAC's ComfyUI (used to tell what to show); the backend's own proof (marker AND ledger) decides.
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
    }

    public static class ComfyCli
    {
        // Runs `python -m vgac.comfy_install <args>` with the app's own Python in the backend folder, output into a log, and returns the
        // last json result line the command printed, or null (no Python, could not start, no result).
        public static string Run(IHost host, Layout lay, string projectRoot, string args)
        {
            if (lay == null || !File.Exists(lay.VenvPython)) return null;
            string logFile = Path.Combine(projectRoot, "runtime", "logs", "comfy-install-cli.log");
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(logFile));
                if (File.Exists(logFile)) File.Delete(logFile);
                host.Run(lay.VenvPython, "-m vgac.comfy_install " + args, Path.Combine(projectRoot, "backend"), null, logFile);
                string last = null;
                foreach (string line in File.ReadAllLines(logFile, Encoding.UTF8))
                    if (line.StartsWith("{\"ok\"")) last = line;
                return last;
            }
            catch (Exception) { return null; }
        }
    }

    // The real one. `runCli` runs the command and returns its one json line (null when nothing could be run).
    public class BackendComfyInstall : IComfyInstall
    {
        readonly Func<string, string> runCli;

        public BackendComfyInstall(Func<string, string> runCli) { this.runCli = runCli; }

        public string Root(string data) { return Path.Combine(data, "comfyui"); }

        // null: ok. Else the plain reason the command gave, or why it could not be asked.
        static string Reason(string line, out Dictionary<string, object> result)
        {
            result = null;
            if (string.IsNullOrEmpty(line)) return "The app's installer for ComfyUI did not answer (is the app's Python there?).";
            try
            {
                result = Json.ParseObject(line);
                object ok, why;
                if (result.TryGetValue("ok", out ok) && ok is bool && (bool)ok) return null;
                return result.TryGetValue("reason", out why) && Convert.ToString(why).Length > 0 ? Convert.ToString(why) : "The app's installer for ComfyUI refused.";
            }
            catch (Exception) { return "The app's installer for ComfyUI gave an answer VGAC could not read."; }
        }

        public ComfyState State(string data)
        {
            var st = new ComfyState();
            st.Present = ComfyGuard.MarkerPresent(Root(data));
            if (!st.Present) { st.Problem = "There is no ComfyUI of VGAC's here."; return st; }
            Dictionary<string, object> r;
            st.Problem = Reason(runCli("check"), out r);
            if (st.Problem == null && r != null)
            {
                object keeps;
                if (r.TryGetValue("keeps", out keeps) && keeps is System.Collections.IEnumerable && !(keeps is string))
                    foreach (object k in (System.Collections.IEnumerable)keeps) if (k != null) st.Keeps.Add(k.ToString());
            }
            return st;
        }

        public string Remove() { Dictionary<string, object> r; return Reason(runCli("remove"), out r); }

        public string Retarget(string oldData, string newData)
        {
            Dictionary<string, object> r;
            return Reason(runCli("retarget \"" + oldData + "\" \"" + newData + "\""), out r);
        }

        public string RebuildVenv(string newData) { Dictionary<string, object> r; return Reason(runCli("rebuild-venv"), out r); }
    }
}
