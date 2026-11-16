// What VGAC may call its own. A folder is VGAC's only when it is proven: the install ledger lists that exact path as added, or
// VGAC wrote a marker file inside it that names what it is AND records the folder's own real path (so a marker copied into
// another folder proves nothing there). A name alone (a folder called "python" or "downloads") proves nothing. VGAC stamps or
// records a folder only when it made the folder itself (it did not exist, or was empty, at that moment); a folder that already
// held somebody's files is never claimed. C# 5 (see Core.cs).
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace Vgac.Starter
{
    public static class Proof
    {
        public const string MarkerName = ".vgac-made.json";

        // Writes the marker for `dir` (which must exist): what it is, and where it is, by real path.
        public static void Write(string dir, string what)
        {
            Directory.CreateDirectory(dir);
            var d = new Dictionary<string, object>();
            d["made_by"] = "VGAC"; d["what"] = what; d["path"] = SafeFs.Real(dir) ?? "";
            File.WriteAllText(Path.Combine(dir, MarkerName), Json.Write(d), new UTF8Encoding(false));
        }

        // What the marker inside `dir` says it is, or null: no marker, not VGAC's, a link, or written for another place.
        public static string Read(string dir)
        {
            try
            {
                string p = Path.Combine(dir, MarkerName);
                if (!File.Exists(p) || SafeFs.IsLink(p)) return null;
                var d = Json.ParseObject(File.ReadAllText(p, Encoding.UTF8));
                object m, w, where;
                if (!d.TryGetValue("made_by", out m) || Convert.ToString(m) != "VGAC") return null;
                string real = SafeFs.Real(dir);
                if (real == null || !d.TryGetValue("path", out where) || !string.Equals(Convert.ToString(where), real, StringComparison.OrdinalIgnoreCase)) return null;
                return d.TryGetValue("what", out w) ? Convert.ToString(w) : null;
            }
            catch (Exception) { return null; }
        }

        // Proven: the ledger lists this exact path as added, or the folder carries VGAC's marker for `what` that belongs to it.
        public static bool Ours(Ledger ledger, string dir, string what)
        {
            if (!Directory.Exists(dir) || SafeFs.IsLink(dir)) return false;
            if (ledger != null && ledger.Has(dir, "added")) return true;
            return Read(dir) == what;
        }

        // VGAC may stamp or record `dir` only when it makes it now: it does not exist, it is empty (a marker of its own does not
        // count as content), or it already is VGAC's. Anything else is somebody's folder.
        public static bool CanClaim(string dir, Ledger ledger, string what)
        {
            if (!Directory.Exists(dir)) return true;
            if (SafeFs.IsLink(dir)) return false;
            if (Ours(ledger, dir, what)) return true;
            try
            {
                foreach (string e in Directory.GetFileSystemEntries(dir))
                    if (!string.Equals(Path.GetFileName(e), MarkerName, StringComparison.OrdinalIgnoreCase)) return false;
            }
            catch (Exception) { return false; }
            return true;
        }

        public static string ClaimRefusal(string dir, string kindOfThing)
        {
            return "The folder " + dir + " already exists and has files in it that VGAC did not put there, so VGAC will not use it for " + kindOfThing + ".";
        }

        // ---- places that can never be a folder for VGAC's big files ---------------------------------------------------------

        [DllImport("shell32.dll")] static extern int SHGetKnownFolderPath([MarshalAs(UnmanagedType.LPStruct)] Guid id, uint flags, IntPtr token, [MarshalAs(UnmanagedType.LPWStr)] out string path);
        static readonly Guid DownloadsId = new Guid("374DE290-123F-4565-9164-39C4925E467B");

        static string KnownDownloads()
        {
            try { string p; if (SHGetKnownFolderPath(DownloadsId, 0, IntPtr.Zero, out p) == 0) return p; } catch (Exception) { }
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Downloads");
        }

        // Shared places, as real paths (so a junction or a redirected folder to them is caught too).
        static List<string> SharedPlaces()
        {
            var res = new List<string>();
            foreach (Environment.SpecialFolder sf in new[] { Environment.SpecialFolder.UserProfile, Environment.SpecialFolder.MyDocuments, Environment.SpecialFolder.Desktop,
                Environment.SpecialFolder.MyPictures, Environment.SpecialFolder.MyMusic, Environment.SpecialFolder.MyVideos, Environment.SpecialFolder.ApplicationData,
                Environment.SpecialFolder.LocalApplicationData, Environment.SpecialFolder.CommonApplicationData })
            {
                string s = Environment.GetFolderPath(sf);
                if (!string.IsNullOrEmpty(s)) res.Add(s);
            }
            res.Add(KnownDownloads());
            string pub = Environment.GetEnvironmentVariable("PUBLIC");
            if (!string.IsNullOrEmpty(pub)) res.Add(pub);
            return res;
        }

        static List<string> NoGoInside()   // nothing may be under these either
        {
            var res = new List<string>();
            foreach (Environment.SpecialFolder sf in new[] { Environment.SpecialFolder.ProgramFiles, Environment.SpecialFolder.ProgramFilesX86, Environment.SpecialFolder.Windows, Environment.SpecialFolder.System })
            {
                string s = Environment.GetFolderPath(sf);
                if (!string.IsNullOrEmpty(s)) res.Add(s);
            }
            return res;
        }

        static bool SameOrInside(string child, string parent) { return child == parent || child.StartsWith(parent + "\\"); }

        // null when the folder is acceptable. Everything is compared as real paths: a junction, a short (8.3) name or a redirected
        // folder that leads to a drive root, a profile or a system place is refused like the place itself. A path that cannot be
        // resolved is refused (never guessed).
        public static string DataFolderProblem(string folder, string projectRoot)
        {
            string r;
            try { r = SafeFs.Real(folder); } catch (Exception) { r = null; }
            if (r == null) return "VGAC cannot tell where that folder really is.";
            string root = SafeFs.Real(Path.GetPathRoot(Path.GetFullPath(folder)));
            if (root != null && (r == root.TrimEnd('\\') || r + "\\" == root)) return "That is a whole drive. Choose or make a folder just for VGAC.";
            if (r.Length <= 3) return "That is a whole drive. Choose or make a folder just for VGAC.";
            string profile = SafeFs.Real(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile));
            string usersRoot = profile == null ? null : Path.GetDirectoryName(profile);
            if (usersRoot != null && (r == usersRoot.ToLowerInvariant() || Path.GetDirectoryName(r) == usersRoot.ToLowerInvariant()))
                return "That is a user profile folder (or the folder of all of them). Choose or make a folder just for VGAC.";
            foreach (string s in SharedPlaces())
            {
                string sr = SafeFs.Real(s);
                if (sr == null) continue;
                if (SameOrInside(sr, r)) return "That folder is shared with everything else on this computer (" + s + "). Choose or make a folder just for VGAC.";
            }
            foreach (string s in NoGoInside())
            {
                string sr = SafeFs.Real(s);
                if (sr == null) continue;
                if (SameOrInside(r, sr) || SameOrInside(sr, r)) return "That is in a system place (" + s + ").";
            }
            if (projectRoot != null)
            {
                string pr = SafeFs.Real(projectRoot);
                if (pr == null) return "VGAC cannot tell where the project folder really is.";
                if (SameOrInside(pr, r)) return "That folder contains or is the project folder.";
                foreach (string part in new[] { ".git", "backend", "frontend", "desktop", "starter", "docs", "setup" })
                    if (SameOrInside(r, pr + "\\" + part)) return "That is inside a part of the project that is not for the big files.";
            }
            return null;
        }

        // The same check without a project folder: never a drive root or a system place.
        public static bool IsDangerousFolder(string folder)
        {
            return DataFolderProblem(folder, null) != null;
        }
    }
}
