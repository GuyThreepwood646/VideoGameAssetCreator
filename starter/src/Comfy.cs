// The app's own ComfyUI, as the maintenance jobs see it. The backend (backend/vgac/comfy_install.py) is the one owner of everything
// about it: what VGAC made there, what stays, how it is removed and how it is moved. The starter keeps no second model of that: it
// asks the backend's command line (`python -m vgac.comfy_install <command>`, usable with the backend not running) and shows what it
// answers. Each command prints one json line and exits non-zero with a plain reason when it refuses.
//
//   check                    is there a ComfyUI VGAC made here (marker and ledger)? what does remove keep? (code "adoptable": it is
//                            VGAC's by its id but its folder was renamed or changed drive letter)
//   adopt                    points the ledger at the renamed folder (the backend's proof: the id in the marker, on one ledger entry, in
//                            a data folder that is VGAC's by its id)
//   remove                   removes it (models kept)
//   retarget <old> <new>     the data folder moved: the settings now point into the new place
//   rebuild-venv             makes its Python environment again (fetches its packages: needs the internet)
//
// Until a command succeeds the starter does the safe thing: a move is refused and nothing is listed for removal. Every call has a
// time limit, runs with a clean environment, and can be stopped. C# 5 (see Core.cs).
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading;

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
        ComfyState State(string dataFolder);              // runs `check` (a recent answer may be reused; adopts a renamed install first)
        void Forget();                                    // the next State asks again
        string Remove();                                  // runs `remove`: null when done, else a plain sentence
        string Retarget(string oldData, string newData);  // runs `retarget`: the same
        string RebuildVenv(string newData);               // runs `rebuild-venv`: the same
        Func<bool> Cancelled { get; set; }                // asked while a command runs: true stops it
        Action<string> Say { get; set; }                  // progress words for the window
    }

    public interface IAbortable { void AbortCurrent(); }

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
        // One argument for a Windows command line, so that the program's own parser reads it back as exactly this text (a trailing
        // backslash is doubled, a quote is escaped).
        public static string Quote(string s)
        {
            var sb = new StringBuilder("\"");
            int bs = 0;
            foreach (char c in s)
            {
                if (c == '\\') { bs++; continue; }
                if (c == '"') { sb.Append('\\', bs * 2 + 1); sb.Append('"'); bs = 0; continue; }
                sb.Append('\\', bs); bs = 0;
                sb.Append(c);
            }
            sb.Append('\\', bs * 2);
            sb.Append('"');
            return sb.ToString();
        }

        static string Fail(string code, string reason) { return "{\"ok\": false, \"code\": \"" + code + "\", \"reason\": " + Json.Write(reason) + "}"; }

        // Runs `python -m vgac.comfy_install <args>` with the app's own Python in the backend folder, with a clean environment (none of
        // the starter's VGAC_*, UV_*, PIP_*, PYTHON* variables), output into a log, a time limit, and a way out. Returns the last json
        // result line the command printed; a stop, a time-out or no answer is returned as a refusal line with a plain reason.
        public static string Run(IHost host, Layout lay, string projectRoot, string args, int timeoutMs, Func<bool> cancelled)
        {
            if (lay == null || !File.Exists(lay.VenvPython)) return null;
            string logFile = Path.Combine(projectRoot, "runtime", "logs", "comfy-install-cli.log");
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(logFile));
                if (File.Exists(logFile)) File.Delete(logFile);
            }
            catch (Exception) { return null; }
            var env = new Dictionary<string, string>();
            env["VGAC_CLEAN_ENVIRONMENT"] = "1";
            Exception failed = null;
            var th = new Thread(delegate()
            {
                try { host.Run(lay.VenvPython, "-m vgac.comfy_install " + args, Path.Combine(projectRoot, "backend"), env, logFile); }
                catch (Exception e) { failed = e; }
            });
            th.IsBackground = true;
            th.Start();
            DateTime end = DateTime.UtcNow.AddMilliseconds(timeoutMs);
            while (!th.Join(200))
            {
                bool stop = cancelled != null && cancelled();
                if (!stop && DateTime.UtcNow < end) continue;
                var ab = host as IAbortable;
                if (ab != null) ab.AbortCurrent();
                th.Join(5000);
                return stop ? Fail("stopped", "It was stopped. Nothing is half done that the app cannot recover from: run it again, or go back.")
                            : Fail("timeout", "The app's installer for ComfyUI did not finish within " + (timeoutMs / 60000 > 0 ? (timeoutMs / 60000) + " minutes" : (timeoutMs / 1000) + " seconds") + " and was stopped.");
            }
            if (failed != null) return null;
            try
            {
                string last = null;
                foreach (string line in File.ReadAllLines(logFile, Encoding.UTF8))
                    if (line.StartsWith("{\"ok\"")) last = line;
                return last;
            }
            catch (Exception) { return null; }
        }
    }

    // The real one. `runCli(args, timeoutMs, cancelled)` returns the command's one json line (null when nothing could be run).
    public class BackendComfyInstall : IComfyInstall
    {
        public const int CheckMs = 60 * 1000, RemoveMs = 10 * 60 * 1000, RetargetMs = 60 * 1000, RebuildMs = 60 * 60 * 1000;
        readonly Func<string, int, Func<bool>, string> runCli;
        ComfyState cached; string cachedFor; DateTime cachedAt;
        public Func<bool> Cancelled { get; set; }
        public Action<string> Say { get; set; }

        public BackendComfyInstall(Func<string, int, Func<bool>, string> runCli) { this.runCli = runCli; }

        public string Root(string data) { return Path.Combine(data, "comfyui"); }

        public void Forget() { cached = null; }

        string Ask(string args, int ms) { return runCli(args, ms, Cancelled); }

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

        static bool Adoptable(Dictionary<string, object> r)
        {
            object a;
            return r != null && r.TryGetValue("adoptable", out a) && a is bool && (bool)a;
        }

        public ComfyState State(string data)
        {
            if (cached != null && cachedFor == data && (DateTime.UtcNow - cachedAt).TotalSeconds < 30) return cached;
            var st = new ComfyState();
            st.Present = ComfyGuard.MarkerPresent(Root(data));
            if (!st.Present) { st.Problem = "There is no ComfyUI of VGAC's here."; return st; }
            Dictionary<string, object> r;
            st.Problem = Reason(Ask("check", CheckMs), out r);
            if (st.Problem != null && Adoptable(r))
            {
                // renamed folder or a drive letter that changed: the backend proves it is VGAC's by its id and moves its ledger entry
                Dictionary<string, object> r2;
                string adopt = Reason(Ask("adopt", CheckMs), out r2);
                if (adopt == null) st.Problem = Reason(Ask("check", CheckMs), out r);
                else st.Problem = adopt;                       // its refusal is the reason to show
            }
            if (st.Problem == null && r != null)
            {
                object keeps;
                if (r.TryGetValue("keeps", out keeps) && keeps is System.Collections.IEnumerable && !(keeps is string))
                    foreach (object k in (System.Collections.IEnumerable)keeps) if (k != null) st.Keeps.Add(k.ToString());
            }
            cached = st; cachedFor = data; cachedAt = DateTime.UtcNow;
            return st;
        }

        string Tell(string words) { Action<string> s = Say; if (s != null) s(words); return null; }

        public string Remove()
        {
            Forget();
            Tell("Removing VGAC's own ComfyUI with its installer...");
            Dictionary<string, object> r; return Reason(Ask("remove", RemoveMs), out r);
        }

        public string Retarget(string oldData, string newData)
        {
            Forget();
            Dictionary<string, object> r;
            return Reason(Ask("retarget " + ComfyCli.Quote(oldData) + " " + ComfyCli.Quote(newData), RetargetMs), out r);
        }

        public string RebuildVenv(string newData)
        {
            Forget();
            Tell("Making VGAC's own ComfyUI's Python again. This fetches its packages, needs the internet and can take several minutes. Stop cancels it.");
            Dictionary<string, object> r; return Reason(Ask("rebuild-venv", RebuildMs), out r);
        }
    }
}
