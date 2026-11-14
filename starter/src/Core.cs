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

        public void Set(string section, string key, string value)
        {
            Section(section)[key] = value;
        }

        // Temporary file beside it, then swap; the old file stays as machine.json.bak.
        public void Save()
        {
            string dir = System.IO.Path.GetDirectoryName(Path);
            Directory.CreateDirectory(dir);
            string tmp = Path + ".tmp";
            File.WriteAllText(tmp, Json.Write(Data), new UTF8Encoding(false));
            if (File.Exists(Path))
            {
                File.Copy(Path, Path + ".bak", true);
                File.Delete(Path);
            }
            File.Move(tmp, Path);
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

        public void Line(string text)
        {
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

        public void Add(string what, string path, string kind, long bytes)
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
            Items.Add(e);
        }

        public void Save()
        {
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path));
            var root = new Dictionary<string, object>();
            root["items"] = Items;
            string tmp = Path + ".tmp";
            File.WriteAllText(tmp, Json.Write(root), new UTF8Encoding(false));
            if (File.Exists(Path)) File.Delete(Path);
            File.Move(tmp, Path);
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
}
