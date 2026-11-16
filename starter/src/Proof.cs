// What VGAC may call its own. A folder is VGAC's only when it is proven: the install ledger lists that exact path as added,
// or VGAC wrote a marker file inside it that names what it is. A name alone (a folder called "python" or "downloads") proves
// nothing. Nothing is deleted, moved or cleaned up unless it is proven. C# 5 (see Core.cs).
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace Vgac.Starter
{
    public static class Proof
    {
        public const string MarkerName = ".vgac-made.json";

        public static void Write(string dir, string what)
        {
            Directory.CreateDirectory(dir);
            var d = new Dictionary<string, object>();
            d["made_by"] = "VGAC"; d["what"] = what;
            string p = Path.Combine(dir, MarkerName);
            File.WriteAllText(p, Json.Write(d), new UTF8Encoding(false));
        }

        // What the marker inside `dir` says it is, or null (no marker, not VGAC's, or the marker is a link).
        public static string Read(string dir)
        {
            try
            {
                string p = Path.Combine(dir, MarkerName);
                if (!File.Exists(p) || SafeFs.IsLink(p)) return null;
                var d = Json.ParseObject(File.ReadAllText(p, Encoding.UTF8));
                object m, w;
                if (!d.TryGetValue("made_by", out m) || Convert.ToString(m) != "VGAC") return null;
                return d.TryGetValue("what", out w) ? Convert.ToString(w) : null;
            }
            catch (Exception) { return null; }
        }

        // Proven: the ledger lists this exact path as added, or the folder carries VGAC's marker for `what`.
        public static bool Ours(Ledger ledger, string dir, string what)
        {
            if (!Directory.Exists(dir) || SafeFs.IsLink(dir)) return false;
            if (ledger != null && ledger.Has(dir, "added")) return true;
            return Read(dir) == what;
        }

        // Places that can never be a folder for VGAC's big files: they are shared with everything else on the computer.
        // null when the folder is acceptable.
        public static string DataFolderProblem(string folder, string projectRoot)
        {
            string full;
            try { full = Path.GetFullPath(folder).TrimEnd('\\'); } catch (Exception) { return "That is not a usable folder name."; }
            string n = full.ToLowerInvariant();
            string root = (Path.GetPathRoot(full) ?? "").TrimEnd('\\').ToLowerInvariant();
            if (n == root || n.Length <= 3) return "That is a whole drive. Choose or make a folder just for VGAC.";
            foreach (Environment.SpecialFolder sf in new[] { Environment.SpecialFolder.UserProfile, Environment.SpecialFolder.MyDocuments, Environment.SpecialFolder.Desktop,
                Environment.SpecialFolder.ProgramFiles, Environment.SpecialFolder.ProgramFilesX86, Environment.SpecialFolder.Windows, Environment.SpecialFolder.ApplicationData,
                Environment.SpecialFolder.LocalApplicationData, Environment.SpecialFolder.CommonApplicationData, Environment.SpecialFolder.System })
            {
                string s = Environment.GetFolderPath(sf);
                if (string.IsNullOrEmpty(s)) continue;
                string sn = s.TrimEnd('\\').ToLowerInvariant();
                if (n == sn || sn.StartsWith(n + "\\")) return "That folder is shared with everything else on this computer (" + s + "). Choose or make a folder just for VGAC.";
            }
            string down = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Downloads").ToLowerInvariant();
            if (n == down) return "That is your Downloads folder. Choose or make a folder just for VGAC.";
            if (projectRoot != null)
            {
                string pr = Path.GetFullPath(projectRoot).TrimEnd('\\').ToLowerInvariant();
                if (n == pr || pr.StartsWith(n + "\\")) return "That folder contains or is the project folder.";
                if (n.StartsWith(pr + "\\.git") || n.StartsWith(pr + "\\backend") || n.StartsWith(pr + "\\frontend") || n.StartsWith(pr + "\\desktop") || n.StartsWith(pr + "\\starter"))
                    return "That is inside a part of the project that is not for the big files.";
            }
            return null;
        }

        // The same check for a folder that may be removed or emptied by a clean-up: never a drive root or a system place.
        public static bool IsDangerousFolder(string folder)
        {
            return DataFolderProblem(folder, null) != null;
        }
    }
}
