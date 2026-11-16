// What VGAC may call its own: ONE dedicated folder, the data folder. It carries one marker file holding a random id, and the same id
// is stored in local/machine.json. The folder is VGAC's only when both ids match; a missing marker, a damaged one or another id means
// "not ours": nothing in it is deleted or moved. VGAC claims a folder only when it is new or empty (or after the person confirmed an
// adoption of one that holds nothing but VGAC's own, recognisably shaped, folders). Inside a claimed folder the fixed names below are
// VGAC's; anything else in it is left alone. There are no per-folder markers and no name-by-name proof. C# 5 (see Core.cs).
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace Vgac.Starter
{
    public enum DataState { Ours, Unclaimed, NotOurs }

    public class DataStatus
    {
        public DataState State;
        public string Why = "";                                // plain words when the state is NotOurs
        public bool Adoptable;                                 // NotOurs, but holds nothing except VGAC's own folders (shown to the person)
        public List<string> AdoptList = new List<string>();    // what would be treated as VGAC's, "name  size"
        public List<string> Extras = new List<string>();       // entries that are not VGAC's fixed names
        public bool Problem;                                   // the place itself is shared (drive root, profile, ...): never claimable
    }

    public static class DataFolders
    {
        public const string MarkerName = ".vgac-data.json";

        // The only names VGAC makes at the top of the data folder.
        public static readonly string[] FixedNames = { "tools", "python", "uv-cache", "downloads", "webview2", "comfyui" };

        public static bool IsFixedName(string name)
        {
            foreach (string n in FixedNames) if (string.Equals(n, name, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }

        public static string SettingsId(MachineFile m)
        {
            return m.GetString("starter", "dataFolderId");
        }

        public static string ReadMarkerId(string folder)
        {
            try
            {
                string p = Path.Combine(folder, MarkerName);
                if (!File.Exists(p) || SafeFs.IsLink(p)) return null;
                var d = Json.ParseObject(File.ReadAllText(p, Encoding.UTF8));
                object who, id;
                if (!d.TryGetValue("made_by", out who) || Convert.ToString(who) != "VGAC") return null;
                return d.TryGetValue("id", out id) ? Convert.ToString(id) : null;
            }
            catch (Exception) { return null; }
        }

        public static string ReadMarkerMove(string folder)
        {
            try
            {
                string p = Path.Combine(folder, MarkerName);
                if (!File.Exists(p) || SafeFs.IsLink(p)) return null;
                var d = Json.ParseObject(File.ReadAllText(p, Encoding.UTF8));
                object who, mv;
                if (!d.TryGetValue("made_by", out who) || Convert.ToString(who) != "VGAC") return null;
                return d.TryGetValue("move", out mv) ? Convert.ToString(mv) : null;
            }
            catch (Exception) { return null; }
        }

        // Any of the fixed names is at the top of this folder (used to decide whether a refusal needs explaining).
        public static bool FixedNamesPresent(string folder)
        {
            try { foreach (string e in Directory.GetFileSystemEntries(folder)) if (IsFixedName(Path.GetFileName(e))) return true; } catch (Exception) { }
            return false;
        }

        // What is in the folder besides the marker.
        static List<string> Entries(string folder)
        {
            var res = new List<string>();
            foreach (string e in Directory.GetFileSystemEntries(folder))
                if (!string.Equals(Path.GetFileName(e), MarkerName, StringComparison.OrdinalIgnoreCase)) res.Add(e);
            return res;
        }

        // Decides, without changing anything. `projectRoot` is for the place checks and for recognising VGAC's own Python.
        public static DataStatus Check(string folder, MachineFile machine, string projectRoot)
        {
            var st = new DataStatus();
            string unfit = Proof.DataFolderProblem(folder, projectRoot);
            if (unfit != null) { st.State = DataState.NotOurs; st.Problem = true; st.Why = unfit; return st; }
            if (!Directory.Exists(folder)) { st.State = DataState.Unclaimed; return st; }
            if (SafeFs.IsLink(folder)) { st.State = DataState.NotOurs; st.Problem = true; st.Why = "That folder is a link."; return st; }
            string sid = SettingsId(machine), mid = ReadMarkerId(folder);
            if (!string.IsNullOrEmpty(sid) && sid == mid) { st.State = DataState.Ours; return st; }
            List<string> entries = Entries(folder);
            if (entries.Count == 0) { st.State = DataState.Unclaimed; return st; }
            st.State = DataState.NotOurs;
            st.Why = mid == null
                ? "VGAC cannot show that it made this folder: it has no VGAC marker."
                : "VGAC cannot show that it made this folder: its marker does not match this install's settings.";
            AssessInto(folder, projectRoot, st);
            return st;
        }

        // Writes the id into the settings FIRST, then the marker, so a crash between the two leaves an empty folder that the next
        // start simply finishes claiming (the settings' id is reused). Does not look at the folder's contents: callers have decided.
        public static void Stamp(string folder, MachineFile machine) { Stamp(folder, machine, null); }

        // `moveId`: the move that made this folder its new place (only a move that carries the same id may remove what it copied there).
        public static void Stamp(string folder, MachineFile machine, string moveId)
        {
            string id = SettingsId(machine);
            if (string.IsNullOrEmpty(id))
            {
                id = Guid.NewGuid().ToString("N");
                machine.Set("starter", "dataFolderId", id);
                machine.Save();
            }
            Directory.CreateDirectory(folder);
            var d = new Dictionary<string, object>();
            d["made_by"] = "VGAC"; d["id"] = id;
            if (!string.IsNullOrEmpty(moveId)) d["move"] = moveId;
            SafeFs.WriteAtomic(Path.Combine(folder, MarkerName), Json.Write(d), null);
        }

        // The person picked a folder. The claim is kept when that folder carries this install's own marker (the same folder re-picked
        // after an interrupted setup, or the same folder at another drive letter); any other folder starts without a claim.
        public static void Choose(MachineFile machine, string folder, string projectRoot)
        {
            string sid = SettingsId(machine), mid = ReadMarkerId(folder);
            machine.Set("starter", "dataFolder", Layout.Stored(projectRoot, folder));
            if (string.IsNullOrEmpty(sid) || sid != mid) machine.Set("starter", "dataFolderId", "");
            machine.Save();
        }

        // New or empty: VGAC claims it now (before any step puts anything in it).
        public static void Claim(string folder, MachineFile machine, string projectRoot)
        {
            DataStatus st = Check(folder, machine, projectRoot);
            if (st.State == DataState.Ours) return;
            if (st.State != DataState.Unclaimed) throw new NotClaimedError(folder, st);
            Stamp(folder, machine);
        }

        // The person confirmed: the folder holds only VGAC's own folders (checked again here), so it is claimed.
        public static void Adopt(string folder, MachineFile machine, string projectRoot)
        {
            DataStatus st = Check(folder, machine, projectRoot);
            if (st.State == DataState.Ours) return;
            if (st.State == DataState.Unclaimed) { Stamp(folder, machine); return; }
            if (!st.Adoptable) throw new NotClaimedError(folder, st);
            Stamp(folder, machine);
        }

        // ---- adoption: names AND shapes ----------------------------------------------------------------------------------------

        static void AssessInto(string folder, string projectRoot, DataStatus st)
        {
            var problems = new List<string>();
            foreach (string e in Entries(folder))
            {
                string name = Path.GetFileName(e);
                if (!IsFixedName(name))
                {
                    st.Extras.Add(name);
                    continue;
                }
                string bad = ShapeProblem(name.ToLowerInvariant(), e, projectRoot);
                if (bad != null) problems.Add(name + ": " + bad);
                else st.AdoptList.Add(name + "   " + RemovalSummary.Size(SafeFs.Size(e, null)));
            }
            if (st.Extras.Count > 0)
            {
                bool hidden = true; foreach (string x in st.Extras) if (!x.StartsWith(".")) hidden = false;
                st.Why += hidden ? " It holds only hidden files that are not VGAC's (" + string.Join(", ", st.Extras.ToArray()) + ")." : " It also holds things that are not VGAC's (" + string.Join(", ", st.Extras.ToArray()) + ").";
            }
            if (problems.Count > 0) st.Why += " These do not look like what VGAC's setup makes: " + string.Join("; ", problems.ToArray()) + ".";
            st.Adoptable = st.Extras.Count == 0 && problems.Count == 0 && st.AdoptList.Count > 0;
        }

        // The shape only VGAC's setup produces for each fixed name. null: fine.
        static string ShapeProblem(string name, string path, string projectRoot)
        {
            if (SafeFs.IsLink(path) || !Directory.Exists(path)) return "it is a link or not a folder";
            switch (name)
            {
                case "tools":
                    foreach (string e in Directory.GetFileSystemEntries(path))
                    {
                        string n = Path.GetFileName(e).ToLowerInvariant();
                        bool ok = n == "uv" || n == "node" || n == "uv.new" || n == "uv.old" || n == "node.new" || n == "node.old";
                        if (!ok) return "it holds " + Path.GetFileName(e);
                    }
                    if (Directory.Exists(Path.Combine(path, "uv")) && !RealHost.LooksLikeProgram(Path.Combine(path, "uv", "uv.exe"))) return "uv is not the package tool";
                    if (Directory.Exists(Path.Combine(path, "node")) && !RealHost.LooksLikeProgram(Path.Combine(path, "node", "node.exe"))) return "node is not Node.js";
                    if (Directory.Exists(Path.Combine(path, "uv")) && !File.Exists(Path.Combine(path, "uv", "uv.exe"))) return "uv has no uv.exe";
                    if (Directory.Exists(Path.Combine(path, "node")) && !File.Exists(Path.Combine(path, "node", "node.exe"))) return "node has no node.exe";
                    return null;
                case "python":
                    return PythonProblem(path, projectRoot);
                case "uv-cache":
                    try
                    {
                        string tag = Path.Combine(path, "CACHEDIR.TAG");
                        return File.Exists(tag) && File.ReadAllText(tag).Contains("8a477f597d28d172789f06886806bc55") ? null : "it has no uv cache tag";
                    }
                    catch (IOException) { return "its tag cannot be read"; }
                case "downloads":
                    var allowed = new List<string>();
                    foreach (string url in new[] { Pins.UvUrl, Pins.NodeUrl })
                    {
                        string f = Path.GetFileName(new Uri(url).LocalPath).ToLowerInvariant();
                        allowed.Add(f); allowed.Add(f + ".part"); allowed.Add(f + ".bad");
                    }
                    foreach (string e in Directory.GetFileSystemEntries(path))
                        if (Directory.Exists(e) || !allowed.Contains(Path.GetFileName(e).ToLowerInvariant())) return "it holds " + Path.GetFileName(e);
                    return null;
                case "webview2":
                    foreach (string e in Directory.GetFileSystemEntries(path)) if (!string.Equals(Path.GetFileName(e), "EBWebView", StringComparison.OrdinalIgnoreCase)) return "it holds " + Path.GetFileName(e);
                    return Directory.Exists(Path.Combine(path, "EBWebView")) ? null : "it has no EBWebView folder";
                case "comfyui":
                    return ComfyGuard.MarkerPresent(path) ? null : "it has no VGAC install marker";
            }
            return "unknown";
        }

        // uv's own Python folder: cpython-* folders (and uv's housekeeping) with a python.exe, and VGAC's own app Python (the venv in
        // the project) points into it. A folder of somebody's pythons that happens to look similar is not claimed.
        static string PythonProblem(string path, string projectRoot)
        {
            bool found = false;
            foreach (string e in Directory.GetFileSystemEntries(path))
            {
                string n = Path.GetFileName(e).ToLowerInvariant();
                if (n.StartsWith("cpython-") && Directory.Exists(e) && File.Exists(Path.Combine(e, "python.exe"))) { found = true; continue; }
                // what uv 0.8 really writes beside its cpython-* folders: .temp (folder), .lock, .gitignore (checked by running uv here)
                if (n == ".lock" || n == ".cache" || n == ".temp" || n == ".tmp" || n == ".gitignore") continue;
                return "it holds " + Path.GetFileName(e);
            }
            if (!found) return "it holds no Python";
            if (projectRoot == null) return "the project is not known";
            string cfg = Path.Combine(projectRoot, "backend", ".venv", "pyvenv.cfg");
            try
            {
                if (!File.Exists(cfg)) return "the app's own Python environment (backend\\.venv) is not there to show that it uses this Python";
                string real = SafeFs.Real(path);
                foreach (string line in File.ReadAllLines(cfg))
                {
                    if (!line.TrimStart().StartsWith("home", StringComparison.OrdinalIgnoreCase)) continue;
                    int eq = line.IndexOf('=');
                    if (eq < 0) continue;
                    string home = SafeFs.Real(line.Substring(eq + 1).Trim());
                    if (real != null && home != null && home.StartsWith(real + "\\")) return null;
                }
                return "the app's own Python environment does not use it";
            }
            catch (IOException) { return "the app's Python environment cannot be read"; }
        }

        public static string ExplainNotClaimed(string folder, DataStatus st)
        {
            return "The folder for the big files (" + folder + ") is not one VGAC will set up in, move or remove from. " + st.Why;
        }
    }

    // The library (the person's own work) gets the same proof as the data folder before anything in it is removed: a marker file with a
    // random id that is also in the settings (starter.libraryId). Names alone prove nothing: a person's own "projects" folder has a name
    // the app uses too.
    public static class LibraryFolders
    {
        public const string MarkerName = ".vgac-library.json";

        public static string SettingsId(MachineFile m) { return m.GetString("starter", "libraryId"); }

        public static string ReadMarkerId(string folder)
        {
            try
            {
                string p = Path.Combine(folder, MarkerName);
                if (!File.Exists(p) || SafeFs.IsLink(p)) return null;
                var d = Json.ParseObject(File.ReadAllText(p, Encoding.UTF8));
                object who, id;
                if (!d.TryGetValue("made_by", out who) || Convert.ToString(who) != "VGAC") return null;
                return d.TryGetValue("id", out id) ? Convert.ToString(id) : null;
            }
            catch (Exception) { return null; }
        }

        public static bool Ours(string folder, MachineFile m)
        {
            string sid = SettingsId(m);
            return !string.IsNullOrEmpty(sid) && Directory.Exists(folder) && !SafeFs.IsLink(folder) && sid == ReadMarkerId(folder);
        }

        // Id into the settings first, then the marker (a crash between the two is finished by the next start).
        public static void Stamp(string folder, MachineFile m)
        {
            string id = SettingsId(m);
            if (string.IsNullOrEmpty(id)) { id = Guid.NewGuid().ToString("N"); m.Set("starter", "libraryId", id); m.Save(); }
            Directory.CreateDirectory(folder);
            var d = new Dictionary<string, object>();
            d["made_by"] = "VGAC"; d["id"] = id; d["kind"] = "library";
            File.WriteAllText(Path.Combine(folder, MarkerName), Json.Write(d), new UTF8Encoding(false));
        }

        // At every start, before the app can put anything there: a library folder that is new or empty (and a fit place) is claimed.
        // A folder that already holds things is never claimed here; it can be adopted by the person's click when it holds only what the
        // app makes (see AdoptProblem).
        public static void ClaimIfNew(Layout l, MachineFile m)
        {
            string lib = RemovalPlanner.LibraryFolder(l, m);
            if (Ours(lib, m)) return;
            if (RemovalPlanner.WorkPlaceProblem(l, lib) != null) return;
            if (Directory.Exists(lib))
            {
                if (SafeFs.IsLink(lib)) return;
                foreach (string e in Directory.GetFileSystemEntries(lib)) if (!string.Equals(Path.GetFileName(e), MarkerName, StringComparison.OrdinalIgnoreCase)) return;
            }
            Stamp(lib, m);
        }

        // null: this existing, unmarked library holds a real VGAC database and nothing but what the app makes, so it may be adopted.
        public static string AdoptProblem(Layout l, MachineFile m)
        {
            string lib = RemovalPlanner.LibraryFolder(l, m);
            if (!Directory.Exists(lib)) return "there is no library folder";
            string why = RemovalPlanner.LibraryProblem(l, lib);
            if (why != null) return why;
            foreach (string e in Directory.GetFileSystemEntries(lib))
            {
                string n = Path.GetFileName(e);
                if (string.Equals(n, MarkerName, StringComparison.OrdinalIgnoreCase)) continue;
                if (!RemovalPlanner.IsLibraryName(n)) return "it also holds " + n + ", which the app does not make";
                if (SafeFs.IsLink(e)) return n + " is a link";
            }
            return null;
        }

        public static void Adopt(Layout l, MachineFile m)
        {
            string why = AdoptProblem(l, m);
            if (why != null) throw new StarterError("The library folder cannot be adopted: " + why + ".", "Nothing was changed.");
            Stamp(RemovalPlanner.LibraryFolder(l, m), m);
        }
    }

    // Setup, Move and Remove stop with this when the data folder is not VGAC's; the screen then offers adoption or another folder.
    public class NotClaimedError : StarterError
    {
        public readonly string Folder;
        public readonly DataStatus Status;
        public NotClaimedError(string folder, DataStatus st)
            : base(DataFolders.ExplainNotClaimed(folder, st),
                   st.Problem ? "Choose another folder for the big files." : (st.Adoptable ? "If this folder holds nothing but VGAC's own files, you can let VGAC use it. Otherwise choose another folder." : "Choose another folder for the big files, or move the other things out of this one."))
        { Folder = folder; Status = st; }
    }

    public static class Proof
    {
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

        // null when the place is acceptable. Everything is compared as real paths: a junction, a short (8.3) name or a redirected
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
            // the usual shared folders of ANY profile on this computer (another user's Documents, Public\Documents), not only this one's
            if (usersRoot != null)
            {
                string parent = Path.GetDirectoryName(r);
                string grand = parent == null ? null : Path.GetDirectoryName(parent);
                string leaf = Path.GetFileName(r);
                string[] shared = { "documents", "desktop", "downloads", "pictures", "music", "videos", "appdata", "onedrive", "saved games", "contacts", "favorites", "links", "searches", "3d objects" };
                string great = grand == null ? null : Path.GetDirectoryName(grand);
                string pname = parent == null ? "" : Path.GetFileName(parent);
                if (grand != null && grand == usersRoot.ToLowerInvariant() && leaf.StartsWith("onedrive"))
                    return "That folder is a synced OneDrive folder of a user profile. Choose or make a folder just for VGAC.";
                if (great != null && great == usersRoot.ToLowerInvariant() && pname.StartsWith("onedrive") && Array.IndexOf(shared, leaf) >= 0)
                    return "That folder is one of the shared folders of a user profile's OneDrive (" + leaf + "). Choose or make a folder just for VGAC.";
                if (grand != null && grand == usersRoot.ToLowerInvariant() && Array.IndexOf(shared, leaf) >= 0)
                    return "That folder is one of the shared folders of a user profile (" + leaf + "). Choose or make a folder just for VGAC.";
            }
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
