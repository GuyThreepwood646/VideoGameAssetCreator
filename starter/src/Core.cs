// Starter core: plain errors, the machine file, the setup log and the install ledger. C# 5 on purpose (the compiler
// that ships with Windows is C# 5), so no string interpolation, no ?. and no expression-bodied members.
using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;

namespace Vgac.Starter
{
    // An error with a sentence a person can read and, when there is one, what to do about it.
    public class StarterError : Exception
    {
        public string Fix;
        public StarterError(string message, string fix) : base(message) { Fix = fix; }
        public StarterError(string message) : base(message) { Fix = null; }
    }

    public static class Json
    {
        public static Dictionary<string, object> ParseObject(string text)
        {
            var ser = new JavaScriptSerializer();
            ser.MaxJsonLength = int.MaxValue;
            object o = ser.DeserializeObject(text.TrimStart('﻿'));
            var d = o as Dictionary<string, object>;
            if (d == null) throw new FormatException("the top level is not an object");
            return d;
        }

        // Pretty, stable output: two-space indent.
        public static string Write(object value)
        {
            var ser = new JavaScriptSerializer();
            ser.MaxJsonLength = int.MaxValue;
            return Indent(ser.Serialize(value));
        }

        static string Indent(string json)
        {
            var sb = new StringBuilder();
            int depth = 0;
            bool inStr = false;
            for (int i = 0; i < json.Length; i++)
            {
                char c = json[i];
                if (inStr)
                {
                    sb.Append(c);
                    if (c == '\\' && i + 1 < json.Length) { sb.Append(json[++i]); }
                    else if (c == '"') inStr = false;
                    continue;
                }
                switch (c)
                {
                    case '"': inStr = true; sb.Append(c); break;
                    case '{':
                    case '[':
                        if (i + 1 < json.Length && (json[i + 1] == '}' || json[i + 1] == ']')) { sb.Append(c).Append(json[++i]); break; }
                        sb.Append(c); depth++; sb.Append('\n').Append(' ', depth * 2); break;
                    case '}':
                    case ']': depth--; sb.Append('\n').Append(' ', depth * 2).Append(c); break;
                    case ',': sb.Append(c).Append('\n').Append(' ', depth * 2); break;
                    case ':': sb.Append(": "); break;
                    default: sb.Append(c); break;
                }
            }
            return sb.ToString() + "\n";
        }
    }

    public static class Hashing
    {
        public static string Sha256File(string path)
        {
            using (var f = File.OpenRead(path))
            using (var h = SHA256.Create())
                return Hex(h.ComputeHash(f));
        }

        public static string Sha256Text(string text)
        {
            using (var h = SHA256.Create())
                return Hex(h.ComputeHash(Encoding.UTF8.GetBytes(text)));
        }

        static string Hex(byte[] b)
        {
            var sb = new StringBuilder();
            foreach (byte x in b) sb.Append(x.ToString("x2"));
            return sb.ToString();
        }
    }

    // The one named lock for the files VGAC rewrites in local/ (machine.json, installed.json). Held as a file, local/state.lock,
    // created exclusively; the backend takes the same lock (backend/vgac/state_lock.py), so a read-modify-write is never
    // overwritten by the other program. A lock left by a process that died is broken after StaleSeconds (a save takes milliseconds).
    public static class StateLock
    {
        public const string FileName = "state.lock";
        public const int StaleSeconds = 60;
        public static int WaitMs = 10000;   // a variable so the tests can shorten the wait

        public static void Hold(string localDir, Action body)
        {
            Directory.CreateDirectory(localDir);
            string lockPath = Path.Combine(localDir, FileName);
            DateTime deadline = DateTime.UtcNow.AddMilliseconds(WaitMs);
            FileStream fs = null;
            for (;;)
            {
                try { fs = new FileStream(lockPath, FileMode.CreateNew, FileAccess.Write, FileShare.None); break; }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
                if (IsStale(lockPath)) { try { File.Delete(lockPath); } catch (Exception) { } }
                if (DateTime.UtcNow >= deadline)
                    throw new StarterError("Another VGAC window or process is saving its settings (local\\state.lock).",
                        "Wait a moment and try again. Nothing was changed.");
                System.Threading.Thread.Sleep(50);
            }
            try
            {
                byte[] who = Encoding.ASCII.GetBytes("starter " + System.Diagnostics.Process.GetCurrentProcess().Id + "\r\n");
                fs.Write(who, 0, who.Length);
                fs.Close(); fs = null;
                body();
            }
            finally
            {
                if (fs != null) fs.Close();
                try { File.Delete(lockPath); } catch (Exception) { }
            }
        }

        static bool IsStale(string lockPath)
        {
            try { return (DateTime.UtcNow - File.GetLastWriteTimeUtc(lockPath)).TotalSeconds > StaleSeconds; }
            catch (Exception) { return false; }
        }
    }

    // local/machine.json: one file for this computer, shared with the backend ("backend" section) and the launcher
    // ("desktop" section). The starter owns only the "starter" section and keeps every other section as it found it.
    public class MachineFile
    {
        public string Path;
        public Dictionary<string, object> Data = new Dictionary<string, object>();

        public static MachineFile Load(string projectRoot)
        {
            var m = new MachineFile();
            m.Path = System.IO.Path.Combine(projectRoot, "local", "machine.json");
            if (!File.Exists(m.Path)) return m;
            try { m.Data = Json.ParseObject(File.ReadAllText(m.Path, Encoding.UTF8)); }
            catch (Exception e)
            {
                throw new StarterError(
                    "The settings file for this computer (" + m.Path + ") could not be read: " + e.Message + ".",
                    "Fix it, or put machine.json.bak (beside it) back in its place, then start VGAC again. Nothing was changed.");
            }
            return m;
        }

        public Dictionary<string, object> Section(string name)
        {
            object o;
            if (Data.TryGetValue(name, out o))
            {
                var d = o as Dictionary<string, object>;
                if (d != null) return d;
            }
            var fresh = new Dictionary<string, object>();
            Data[name] = fresh;
            return fresh;
        }

        public string GetString(string section, string key)
        {
            object o;
            if (Data.TryGetValue(section, out o))
            {
                var d = o as Dictionary<string, object>;
                object v;
                if (d != null && d.TryGetValue(key, out v) && v != null) return v.ToString();
            }
            return null;
        }

        // A value two levels down: section, then a sub-table, then the key (desktop.comfyA.desktopExe).
        public string GetString2(string section, string sub, string key)
        {
            object o;
            if (!Data.TryGetValue(section, out o)) return null;
            var d = o as Dictionary<string, object>;
            if (d == null || !d.TryGetValue(sub, out o)) return null;
            d = o as Dictionary<string, object>;
            object v;
            if (d != null && d.TryGetValue(key, out v) && v != null) return v.ToString();
            return null;
        }

        readonly List<string[]> changes = new List<string[]>();

        public void Set(string section, string key, string value)
        {
            Section(section)[key] = value;
            changes.Add(new[] { section, key, value });
        }

        // Temporary file beside it, then swap; the old file stays as machine.json.bak. Only what THIS object changed (Set) is laid over
        // the file as it is now, so a copy held across something that rewrote the file (the backend's own settings, another window) never
        // puts old values back.
        public void Save()
        {
            StateLock.Hold(System.IO.Path.GetDirectoryName(Path), SaveUnlocked);
        }

        void SaveUnlocked()
        {
            if (File.Exists(Path))
            {
                try
                {
                    Data = Json.ParseObject(File.ReadAllText(Path, Encoding.UTF8));
                    foreach (string[] c in changes) Section(c[0])[c[1]] = c[2];
                }
                catch (Exception) { /* unreadable now: what is in memory is written, as before */ }
            }
            changes.Clear();
            SafeFs.WriteAtomic(Path, Json.Write(Data), Path + ".bak", true);
        }
    }

    // runtime/logs/setup.log: one line per action, never sent anywhere.
    public class SetupLog
    {
        public string Path;
        public SetupLog(string projectRoot)
        {
            Path = System.IO.Path.Combine(projectRoot, "runtime", "logs", "setup.log");
        }

        static readonly object gate = new object();

        public void Line(string text)
        {
            lock (gate)
            try
            {
                Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path));
                File.AppendAllText(Path, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + text + "\r\n", Encoding.UTF8);
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    // local/installed.json: what VGAC added ("added") and what it only linked to ("linked").
    public class Ledger
    {
        public string Path;
        public List<object> Items = new List<object>();

        public static Ledger Load(string projectRoot)
        {
            var l = new Ledger();
            l.Path = System.IO.Path.Combine(projectRoot, "local", "installed.json");
            if (File.Exists(l.Path))
            {
                try
                {
                    var d = Json.ParseObject(File.ReadAllText(l.Path, Encoding.UTF8));
                    object items;
                    if (d.TryGetValue("items", out items))
                    {
                        var arr = items as System.Collections.IEnumerable;
                        if (arr != null) foreach (object o in arr) l.Items.Add(o);
                    }
                }
                catch (Exception) { /* a damaged ledger starts again; the log keeps the history */ }
            }
            return l;
        }

        // What this object changed, laid over the file as it is now when it is saved (see MachineFile.Save).
        readonly List<Action> ops = new List<Action>();

        public void Add(string what, string path, string kind, long bytes)
        {
            Action op = delegate { DoAdd(what, path, kind, bytes); };
            ops.Add(op); op();
        }

        void DoAdd(string what, string path, string kind, long bytes)
        {
            foreach (object o in Items)
            {
                var d = o as Dictionary<string, object>;
                if (d != null && d.ContainsKey("path") && string.Equals(d["path"] as string, path, StringComparison.OrdinalIgnoreCase)) return;
            }
            var e = new Dictionary<string, object>();
            e["what"] = what;
            e["path"] = path;
            e["kind"] = kind;
            e["bytes"] = bytes;
            e["when"] = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ");
            e["file_id"] = SafeFs.FileId(path);   // A2: which folder this is, so removal can tell it is still the one VGAC added
            Items.Add(e);
        }

        // Drops the entries for a path and everything below it (after that path has been deleted).
        public void RemoveUnder(string path)
        {
            Action op = delegate { DoRemoveUnder(path); };
            ops.Add(op); op();
        }

        // Entries for oldRoot, or below it, now name newRoot (a folder that moved).
        public void RewritePaths(string oldRoot, string newRoot)
        {
            Action op = delegate { DoRewritePaths(oldRoot, newRoot); };
            ops.Add(op); op();
        }

        void DoRewritePaths(string oldRoot, string newRoot)
        {
            string o = System.IO.Path.GetFullPath(oldRoot).TrimEnd('\\').ToLowerInvariant();
            for (int i = 0; i < Items.Count; i++)
            {
                var d = Items[i] as Dictionary<string, object>;
                if (d == null || !d.ContainsKey("path")) continue;
                string np = System.IO.Path.GetFullPath(Convert.ToString(d["path"])).TrimEnd('\\');
                if (np.ToLowerInvariant() == o || np.ToLowerInvariant().StartsWith(o + "\\")) d["path"] = newRoot.TrimEnd('\\') + np.Substring(oldRoot.TrimEnd('\\').Length);
            }
        }

        void DoRemoveUnder(string path)
        {
            string p = System.IO.Path.GetFullPath(path).TrimEnd(System.IO.Path.DirectorySeparatorChar).ToLowerInvariant();
            Items.RemoveAll(delegate(object o)
            {
                var d = o as Dictionary<string, object>;
                if (d == null || !d.ContainsKey("path")) return false;
                string e;
                try { e = System.IO.Path.GetFullPath(Convert.ToString(d["path"])).TrimEnd(System.IO.Path.DirectorySeparatorChar).ToLowerInvariant(); } catch (Exception) { return false; }
                return e == p || e.StartsWith(p + System.IO.Path.DirectorySeparatorChar);
            });
        }

        public bool Has(string path, string kind)
        {
            foreach (object o in Items)
            {
                var d = o as Dictionary<string, object>;
                if (d != null && d.ContainsKey("path") && string.Equals(Convert.ToString(d["path"]), path, StringComparison.OrdinalIgnoreCase)
                    && (kind == null || (d.ContainsKey("kind") && Convert.ToString(d["kind"]) == kind))) return true;
            }
            return false;
        }

        public void Save()
        {
            StateLock.Hold(System.IO.Path.GetDirectoryName(Path), SaveUnlocked);
        }

        void SaveUnlocked()
        {
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path));
            if (File.Exists(Path))
            {
                try
                {
                    var d = Json.ParseObject(File.ReadAllText(Path, Encoding.UTF8));
                    object items; var fresh = new List<object>();
                    if (d.TryGetValue("items", out items)) { var arr = items as System.Collections.IEnumerable; if (arr != null) foreach (object o in arr) fresh.Add(o); }
                    Items = fresh;                                   // the file as it is now (the backend writes it too) ...
                    foreach (Action op in ops) op();                 // ... with this object's own changes laid over it
                }
                catch (Exception) { /* a damaged file is written again from memory */ }
            }
            ops.Clear();
            var root = new Dictionary<string, object>();
            root["items"] = Items;
            SafeFs.WriteAtomic(Path, Json.Write(root), null, true);
        }
    }

    public static class ProjectFinder
    {
        public static string Find(string start)
        {
            string dir = Path.GetFullPath(start).TrimEnd('\\');
            for (;;)
            {
                if (File.Exists(Path.Combine(dir, "backend", "vgac", "config.py"))) return dir;
                string up = Path.GetDirectoryName(dir);
                if (up == null || up == dir) return null;
                dir = up;
            }
        }
    }

    // Where the WebView2 files are, and making them findable. No WebView2 type is named here, so this always loads.
    public static class WebBin
    {
        // starterin beside the exe, or the exe's own folder (the test build); null when the files are not there.
        public static string Find(string baseDir)
        {
            foreach (string d in new[] { Path.Combine(baseDir, "starter", "bin"), baseDir })
                if (File.Exists(Path.Combine(d, "Microsoft.Web.WebView2.Core.dll")) && File.Exists(Path.Combine(d, "WebView2Loader.dll"))) return d;
            return null;
        }

        // The managed files are loaded from there, and the native loader is found through PATH (this process only).
        public static void Prepare(string binDir)
        {
            Environment.SetEnvironmentVariable("PATH", binDir + ";" + Environment.GetEnvironmentVariable("PATH"));
            AppDomain.CurrentDomain.AssemblyResolve += delegate(object s, ResolveEventArgs a)
            {
                string f = Path.Combine(binDir, new System.Reflection.AssemblyName(a.Name).Name + ".dll");
                return File.Exists(f) ? System.Reflection.Assembly.LoadFrom(f) : null;
            };
        }
    }
}
