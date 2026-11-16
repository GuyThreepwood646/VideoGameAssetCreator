// Removing what VGAC added. The plan is built from the install ledger and a few fixed, named folders, and nothing is listed
// unless it is proven VGAC's (the ledger, or a marker VGAC wrote, checked by content). Every item is checked again just before
// it is deleted, links are unlinked and never followed, the Recycle Bin is never silently replaced by permanent deletion, and
// the person's own work is a separate, typed confirmation limited to what is proven to be VGAC's library and exports.
// C# 5 (see Core.cs).
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace Vgac.Starter
{
    public class RemovalItem
    {
        public string Id;          // stable name of the item
        public string GroupId;
        public string Kind;        // app-folder | comfy-owned | ledger-file | ledger-pack | settings | work-library | work-export
        public string Path;
        public string What;
        public long Bytes;
        public bool IsFile;
        public string Tag;         // the kind of app folder, or the size the ledger recorded
    }

    public class RemovalGroup
    {
        public string Id, Title, Sentence;
        public bool DefaultOn;
        public bool NeedsTypedWord;
        public List<RemovalItem> Items = new List<RemovalItem>();
        public long Bytes { get { long n = 0; foreach (RemovalItem i in Items) n += i.Bytes; return n; } }
    }

    public class RemovalPlan
    {
        public List<RemovalGroup> Groups = new List<RemovalGroup>();
        public List<string> NotRemoved = new List<string>();     // "path: why", shown to the person
        public List<string> Notes = new List<string>();
        public RemovalGroup Get(string id) { foreach (RemovalGroup g in Groups) if (g.Id == id) return g; return null; }
    }

    public static class RemovalRules
    {
        public const long RecycleLimit = 2L * 1024 * 1024 * 1024;
        public const string TypedWord = "REMOVE";

        // Tests replace this to stand in for a network or removable drive.
        public static Func<string, bool> FixedDriveCheck = delegate(string path)
        {
            try { return new DriveInfo(System.IO.Path.GetPathRoot(System.IO.Path.GetFullPath(path))).DriveType == DriveType.Fixed; }
            catch (Exception) { return false; }
        };

        // The Recycle Bin only for a local fixed drive and a size it can keep; bigger is deleted for good, and said so.
        public static bool UseRecycleBin(long bytes, bool fixedDrive) { return fixedDrive && bytes <= RecycleLimit; }

        public static bool TypedWordOk(string typed) { return typed != null && typed.Trim() == TypedWord; }

        public static bool IsFixedDrive(string path) { return FixedDriveCheck(path); }

        // Whether this item goes to the Recycle Bin: judged on the item's own drive and the group's size.
        public static bool ItemUsesBin(RemovalGroup g, RemovalItem it) { return UseRecycleBin(g.Bytes, IsFixedDrive(it.Path)); }
    }

    public interface IRecycler
    {
        bool Send(string path);   // false when the Recycle Bin did not take it
    }

    public class WindowsRecycler : IRecycler
    {
        // SHFILEOPSTRUCT has different packing in 32 and 64 bit programs (the Windows header packs it to 1 byte only in 32 bit).
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct Op64
        {
            public IntPtr hwnd; public uint wFunc; public string pFrom; public string pTo; public ushort fFlags;
            [MarshalAs(UnmanagedType.Bool)] public bool fAnyOperationsAborted; public IntPtr hNameMappings; public string lpszProgressTitle;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode, Pack = 1)]
        struct Op32
        {
            public IntPtr hwnd; public uint wFunc; public string pFrom; public string pTo; public ushort fFlags;
            [MarshalAs(UnmanagedType.Bool)] public bool fAnyOperationsAborted; public IntPtr hNameMappings; public string lpszProgressTitle;
        }

        [DllImport("shell32.dll", CharSet = CharSet.Unicode, EntryPoint = "SHFileOperationW")] static extern int Do64(ref Op64 op);
        [DllImport("shell32.dll", CharSet = CharSet.Unicode, EntryPoint = "SHFileOperationW")] static extern int Do32(ref Op32 op);

        // ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI | WANTNUKEWARNING (a warning instead of silent permanent deletion)
        const ushort Flags = 0x40 | 0x10 | 0x4 | 0x400 | 0x4000;

        public bool Send(string path)
        {
            try
            {
                string from = System.IO.Path.GetFullPath(path) + "\0\0";
                if (IntPtr.Size == 8)
                {
                    var op = new Op64(); op.wFunc = 3; op.pFrom = from; op.fFlags = Flags;
                    return Do64(ref op) == 0 && !op.fAnyOperationsAborted && !File.Exists(path) && !Directory.Exists(path);
                }
                else
                {
                    var op = new Op32(); op.wFunc = 3; op.pFrom = from; op.fFlags = Flags;
                    return Do32(ref op) == 0 && !op.fAnyOperationsAborted && !File.Exists(path) && !Directory.Exists(path);
                }
            }
            catch (Exception) { return false; }
        }
    }

    public static class RemovalPlanner
    {
        public static string LibraryFolder(Layout l, MachineFile m)
        {
            string v = m.GetString("backend", "library_path");
            return Absolute(l, string.IsNullOrEmpty(v) ? "library" : v);
        }

        public static string ExportFolder(Layout l, MachineFile m)
        {
            string v = m.GetString("backend", "export_path");
            return string.IsNullOrEmpty(v) ? System.IO.Path.Combine(System.IO.Path.GetDirectoryName(LibraryFolder(l, m)), "exports") : Absolute(l, v);
        }

        static string Absolute(Layout l, string p) { return System.IO.Path.GetFullPath(System.IO.Path.IsPathRooted(p) ? p : System.IO.Path.Combine(l.ProjectRoot, p)); }

        static Dictionary<string, object> Entry(object o) { return o as Dictionary<string, object>; }
        static string S(Dictionary<string, object> d, string k) { object v; return d != null && d.TryGetValue(k, out v) && v != null ? v.ToString() : ""; }
        static long N(Dictionary<string, object> d, string k) { object v; long n; return d != null && d.TryGetValue(k, out v) && v != null && long.TryParse(v.ToString(), out n) ? n : 0; }

        // ---- the person's own work: only what its own content proves is VGAC's ----------------------------------------------

        // null when `folder` is acceptable as a place VGAC's library or exports may be removed from.
        static string WorkPlaceProblem(Layout l, string folder)
        {
            string p = Proof.DataFolderProblem(folder, l.ProjectRoot);
            if (p != null && !p.Contains("inside a part of the project")) return "it is " + (p.Contains("whole drive") ? "a whole drive" : (p.Contains("contains or is the project") ? "or holds the project folder" : "a shared place"));
            string f = SafeFs.Norm(folder), pr = SafeFs.Norm(l.ProjectRoot);
            if (f == pr || pr.StartsWith(f + "\\")) return "it is or holds the project folder";
            foreach (string part in new[] { ".git", "backend", "frontend", "desktop", "starter", "docs", "setup" })
                if (f == pr + "\\" + part || f.StartsWith(pr + "\\" + part + "\\")) return "it is inside a part of the project that is not data";
            if (SafeFs.IsLink(folder) || !SafeFs.PathHasNoLink(folder)) return "it is, or is reached through, a link";
            return null;
        }

        // The library: a folder that holds vgac.sqlite, the app's own database.
        public static string LibraryProblem(Layout l, string folder)
        {
            if (!Directory.Exists(folder)) return null;
            string p = WorkPlaceProblem(l, folder);
            if (p != null) return p;
            if (!File.Exists(System.IO.Path.Combine(folder, "vgac.sqlite"))) return "it has no vgac.sqlite, so it is not shown to be a VGAC library";
            return null;
        }

        // An export: one folder holding VGAC's manifest.json (with the lineage, models and seeds VGAC writes).
        public static bool IsVgacExport(string dir)
        {
            try
            {
                string mf = System.IO.Path.Combine(dir, "manifest.json");
                if (!File.Exists(mf) || SafeFs.IsLink(mf) || new FileInfo(mf).Length > 50 * 1024 * 1024) return false;
                var d = Json.ParseObject(File.ReadAllText(mf, Encoding.UTF8));
                return d.ContainsKey("lineage") && d.ContainsKey("models") && d.ContainsKey("seeds");
            }
            catch (Exception) { return false; }
        }

        // ---- the fixed folders VGAC makes ------------------------------------------------------------------------------------

        // A folder VGAC made: proven (ledger or marker) and shaped like what it should be. Returns why not, or null.
        public static string AppFolderProblem(string kind, string path, Ledger ledger)
        {
            if (!Directory.Exists(path)) return null;
            if (SafeFs.IsLink(path)) return "it is a link, not a folder VGAC made";
            bool needsProof = kind != "node_modules" && kind != "dist" && kind != "leftover";
            bool contentProof = kind == "webview2" && Directory.Exists(System.IO.Path.Combine(path, "EBWebView"));   // what WebView2 itself writes
            if (needsProof && !contentProof && !Proof.Ours(ledger, path, kind)) return "VGAC cannot show that it made this folder (no record and no VGAC marker in it)";
            switch (kind)
            {
                case "venv": return File.Exists(System.IO.Path.Combine(path, "pyvenv.cfg")) ? null : "it does not look like a Python environment";
                case "python": return FindFile(path, "python.exe", 3) ? null : "it does not hold a Python";
                case "uv": return File.Exists(System.IO.Path.Combine(path, "uv.exe")) ? null : "it does not hold uv.exe";
                case "node": return File.Exists(System.IO.Path.Combine(path, "node.exe")) ? null : "it does not hold node.exe";
                case "uv-cache": return null;
                case "downloads": return Directory.GetDirectories(path).Length == 0 ? null : "it holds folders, which VGAC's downloads folder never does";
                case "webview2": return null;
                case "node_modules": return File.Exists(System.IO.Path.Combine(path, ".package-lock.json")) || Directory.Exists(System.IO.Path.Combine(path, ".bin")) ? null : "it does not look like the screens' packages";
                case "dist": return File.Exists(System.IO.Path.Combine(path, "index.html")) ? null : "it does not hold the built screens";
                case "leftover": return null;
            }
            return "unknown kind";
        }

        static bool FindFile(string dir, string name, int depth)
        {
            try
            {
                if (File.Exists(System.IO.Path.Combine(dir, name))) return true;
                if (depth == 0) return false;
                foreach (string d in Directory.GetDirectories(dir)) if (!SafeFs.IsLink(d) && FindFile(d, name, depth - 1)) return true;
            }
            catch (Exception) { }
            return false;
        }

        [ThreadStatic] static HashSet<string> resuming;

        static void AddApp(RemovalPlan plan, RemovalGroup g, Ledger ledger, string id, string kind, string path, string what)
        {
            if (!Directory.Exists(path) && !File.Exists(path)) return;
            // A folder whose removal was cut off is half empty and no longer looks like what it was, and its marker may be gone;
            // the journal remembers that this fixed path was proven when the removal began. Links are still refused.
            bool wasBeingRemoved = resuming != null && resuming.Contains(SafeFs.Norm(path));
            string problem = wasBeingRemoved ? (SafeFs.IsLink(path) ? "it is a link, not a folder VGAC made" : null) : AppFolderProblem(kind, path, ledger);
            if (problem != null) { plan.NotRemoved.Add(path + ": " + problem); return; }
            var it = new RemovalItem(); it.Id = id; it.GroupId = g.Id; it.Kind = "app-folder"; it.Path = path; it.What = what;
            it.Bytes = SafeFs.Size(path, null);
            it.Tag = kind;
            g.Items.Add(it);
        }

        public static RemovalPlan Build(Layout l, MachineFile machine, Ledger ledger, IComfyInstall comfy)
        {
            var plan = new RemovalPlan();
            string data = l.DataFolder;
            resuming = new HashSet<string>();
            foreach (string p in RemovalRunner.InProgress(l.ProjectRoot)) resuming.Add(SafeFs.Norm(p));

            // 1. the app's Python, tools and screens
            var g1 = new RemovalGroup(); g1.Id = "tools"; g1.Title = "The app's Python, tools and screens"; g1.DefaultOn = true;
            g1.Sentence = "What VGAC set up so the app can run. VGAC.exe makes all of it again next time.";
            AddApp(plan, g1, ledger, "venv", "venv", l.Venv, "The app's Python environment");
            AddApp(plan, g1, ledger, "python", "python", l.PythonDir, "The private Python");
            AddApp(plan, g1, ledger, "uv", "uv", System.IO.Path.GetDirectoryName(l.UvExe), "The package tool (uv)");
            AddApp(plan, g1, ledger, "node", "node", l.NodeDir, "Node.js (used to build the screens)");
            AddApp(plan, g1, ledger, "uvcache", "uv-cache", l.UvCache, "uv's download cache");
            AddApp(plan, g1, ledger, "downloads", "downloads", l.Downloads, "Files being downloaded");
            AddApp(plan, g1, ledger, "webview2", "webview2", System.IO.Path.Combine(data, "webview2"), "The app window's data");
            AddApp(plan, g1, ledger, "node_modules", "node_modules", System.IO.Path.Combine(l.FrontendDir, "node_modules"), "The screens' packages");
            AddApp(plan, g1, ledger, "dist", "dist", System.IO.Path.Combine(l.FrontendDir, "dist"), "The built screens");
            foreach (string left in Repair.Leftovers(l, ledger))
                if (Directory.Exists(left) || File.Exists(left)) AddApp(plan, g1, ledger, "left:" + System.IO.Path.GetFileName(left), "leftover", left, "A leftover of an interrupted job");
            plan.Groups.Add(g1);

            // 2. the app's own ComfyUI: by the install record's owned list only, never what it keeps
            var g2 = new RemovalGroup(); g2.Id = "comfy"; g2.Title = "VGAC's own ComfyUI"; g2.DefaultOn = true;
            g2.Sentence = "The ComfyUI VGAC installed for itself. Your models, outputs and your own custom nodes inside it stay.";
            if (comfy.Present(data))
            {
                if (!comfy.Offered(data)) plan.Notes.Add("VGAC's own ComfyUI is not removed here yet: its install record does not list what VGAC made.");
                else
                {
                    string root = comfy.Root(data);
                    var seen = new HashSet<string>();
                    List<string> kept = comfy.Kept(data);
                    foreach (string p in comfy.Owned(data))
                    {
                        string keptWhy = KeptProblem(p, root, kept);
                        if (keptWhy != null) { plan.NotRemoved.Add(p + ": " + keptWhy); continue; }
                        if (!ComfyGuard.Allows(p, root, ForeignComfy(ledger))) { plan.NotRemoved.Add(p + ": it is not inside VGAC's own ComfyUI folder"); continue; }
                        if (!seen.Add(SafeFs.Norm(p)) || (!Directory.Exists(p) && !File.Exists(p))) continue;
                        if (SafeFs.IsLink(p) || !SafeFs.PathHasNoLink(p)) { plan.NotRemoved.Add(p + ": it is, or is reached through, a link"); continue; }
                        var it = new RemovalItem(); it.Id = "comfy:" + p; it.GroupId = "comfy"; it.Kind = "comfy-owned"; it.Path = p;
                        it.What = System.IO.Path.GetFileName(p); it.IsFile = File.Exists(p); it.Bytes = SafeFs.Size(p, null);
                        g2.Items.Add(it);
                    }
                }
            }
            plan.Groups.Add(g2);

            // 3 and 4: models and packs VGAC downloaded, and node packs it put into a linked ComfyUI (ledger kind added)
            var g3 = new RemovalGroup(); g3.Id = "models"; g3.Title = "Models and packs VGAC downloaded"; g3.DefaultOn = false;
            g3.Sentence = "Files VGAC downloaded. Other programs may use them. Models you added yourself are never listed.";
            var g4 = new RemovalGroup(); g4.Id = "packs"; g4.Title = "Node packs VGAC put into a ComfyUI you linked"; g4.DefaultOn = false;
            g4.Sentence = "One folder per pack, inside that ComfyUI's custom_nodes. The Python packages they installed there are not removed.";
            foreach (object o in ledger.Items)
            {
                var d = Entry(o);
                if (d == null) continue;
                string kind = S(d, "kind"), path = S(d, "path");
                if (kind == "pip-note") { plan.Notes.Add("Python packages installed into a linked ComfyUI by " + S(d, "what") + " are not removed: " + S(d, "packages")); continue; }
                if (kind != "added" || path.Length == 0) continue;
                if (!File.Exists(path) && !Directory.Exists(path)) continue;
                if (SafeFs.Norm(path) == SafeFs.Norm(comfy.Root(data)) || IsKnownAppPath(l, path)) continue;   // handled above
                if (!SafeFs.PathHasNoLink(path)) { plan.NotRemoved.Add(path + ": it is, or is reached through, a link"); continue; }
                if (File.Exists(path))
                {
                    long have = new FileInfo(path).Length, want = N(d, "bytes");
                    if (want > 0 && want != have) { plan.NotRemoved.Add(path + ": it is not the file VGAC added any more (its size changed)"); continue; }
                    DateTime when;
                    if (DateTime.TryParse(S(d, "when"), null, System.Globalization.DateTimeStyles.AdjustToUniversal | System.Globalization.DateTimeStyles.AssumeUniversal, out when)
                        && File.GetLastWriteTimeUtc(path) > when.AddMinutes(5)) { plan.NotRemoved.Add(path + ": it was changed after VGAC added it"); continue; }
                    var it = new RemovalItem(); it.Id = "file:" + path; it.GroupId = "models"; it.Kind = "ledger-file"; it.Path = path; it.What = S(d, "what"); it.IsFile = true; it.Bytes = have;
                    it.Tag = want.ToString();
                    g3.Items.Add(it);
                }
                else if (System.IO.Path.GetFileName(System.IO.Path.GetDirectoryName(path)) == "custom_nodes")
                {
                    var it = new RemovalItem(); it.Id = "pack:" + path; it.GroupId = "packs"; it.Kind = "ledger-pack"; it.Path = path; it.What = S(d, "what"); it.Bytes = SafeFs.Size(path, null);
                    g4.Items.Add(it);
                }
                else plan.NotRemoved.Add(path + ": VGAC added it, but it is not a file or a node pack, so it is left for you");
            }
            plan.Groups.Add(g3); plan.Groups.Add(g4);

            // 5. VGAC's settings and records (last)
            var g5 = new RemovalGroup(); g5.Id = "settings"; g5.Title = "VGAC's settings and records"; g5.DefaultOn = false;
            g5.Sentence = "This computer's settings file, what VGAC remembers about the install, and its logs.";
            string local = System.IO.Path.Combine(l.ProjectRoot, "local");
            foreach (string n in new[] { "machine.json", "machine.json.bak", "state.json", "starter-owned.json", "exe-aside.json", "update.json", "move.json", "remove.json", "installed.json" })
            {
                string p = System.IO.Path.Combine(local, n);
                if (!File.Exists(p) || SafeFs.IsLink(p)) continue;
                var it = new RemovalItem(); it.Id = "settings:" + n; it.GroupId = "settings"; it.Kind = "settings"; it.Path = p; it.What = n; it.IsFile = true; it.Bytes = new FileInfo(p).Length;
                g5.Items.Add(it);
            }
            string logs = System.IO.Path.Combine(l.ProjectRoot, "runtime", "logs");
            if (Directory.Exists(logs) && !SafeFs.IsLink(logs))
            {
                var it = new RemovalItem(); it.Id = "settings:logs"; it.GroupId = "settings"; it.Kind = "settings"; it.Path = logs; it.What = "Logs"; it.Bytes = SafeFs.Size(logs, null);
                g5.Items.Add(it);
            }
            plan.Groups.Add(g5);

            // 6. the person's own work: separate, behind a typed word, and only what its own content proves is VGAC's
            var g6 = new RemovalGroup(); g6.Id = "work"; g6.Title = "My projects and exported characters"; g6.DefaultOn = false; g6.NeedsTypedWord = true;
            g6.Sentence = "Your library (the folder with VGAC's database) and each export folder VGAC wrote (the ones with VGAC's manifest). The folder an export sits in is never removed.";
            string lib = LibraryFolder(l, machine);
            if (Directory.Exists(lib))
            {
                string why = LibraryProblem(l, lib);
                if (why != null) plan.NotRemoved.Add(lib + ": " + why);
                else
                {
                    var it = new RemovalItem(); it.Id = "work:library"; it.GroupId = "work"; it.Kind = "work-library"; it.Path = lib; it.What = "Your library (projects)"; it.Bytes = SafeFs.Size(lib, null);
                    g6.Items.Add(it);
                }
            }
            string exp = ExportFolder(l, machine);
            if (Directory.Exists(exp))
            {
                string why = WorkPlaceProblem(l, exp);
                if (why != null) plan.NotRemoved.Add(exp + ": " + why);
                else
                {
                    int found = 0;
                    foreach (string child in Directory.GetDirectories(exp))
                    {
                        if (SafeFs.IsLink(child) || !IsVgacExport(child)) continue;
                        var it = new RemovalItem(); it.Id = "work:export:" + child; it.GroupId = "work"; it.Kind = "work-export"; it.Path = child; it.What = "An export: " + System.IO.Path.GetFileName(child); it.Bytes = SafeFs.Size(child, null);
                        g6.Items.Add(it); found++;
                    }
                    if (found > 0) plan.Notes.Add("Exports are removed one export folder at a time (only the ones holding VGAC's manifest); " + exp + " itself and anything else in it stay.");
                }
            }
            plan.Groups.Add(g6);
            return plan;
        }

        // An owned entry that is, holds or sits in what the ComfyUI install keeps (models, output, input, user) is refused.
        public static string KeptProblem(string path, string root, List<string> kept)
        {
            string p = SafeFs.Norm(path), r = SafeFs.Norm(root);
            if (p == r) return "it is the whole install folder, which also holds what you keep";
            foreach (string k in kept)
            {
                string kp = SafeFs.Norm(System.IO.Path.Combine(root, k));
                if (p == kp || p.StartsWith(kp + "\\")) return "it is something you keep (" + k + ")";
            }
            return null;
        }

        // Paths of the fixed app folders (so a ledger item for one of them is not listed twice).
        static bool IsKnownAppPath(Layout l, string path)
        {
            foreach (string p in new[] { l.Venv, l.PythonDir, System.IO.Path.GetDirectoryName(l.UvExe), l.NodeDir, l.UvCache, l.Downloads,
                System.IO.Path.Combine(l.DataFolder, "webview2"), System.IO.Path.Combine(l.FrontendDir, "node_modules"), System.IO.Path.Combine(l.FrontendDir, "dist") })
                if (SafeFs.Inside(path, p)) return true;
            return false;
        }

        // Linked tools' folders are fenced off from anything the app's own ComfyUI work might reach.
        static List<string> ForeignComfy(Ledger ledger)
        {
            var res = new List<string>();
            foreach (object o in ledger.Items)
            {
                var d = Entry(o);
                if (d != null && S(d, "kind") == "linked" && S(d, "path").Length > 0) res.Add(S(d, "path"));
            }
            return res;
        }

        // Checked again just before an item is deleted. null: still fine; else the plain reason it is skipped.
        public static string Revalidate(RemovalItem it, Layout l, IComfyInstall comfy, Ledger ledger, MachineFile machine)
        {
            if (!File.Exists(it.Path) && !Directory.Exists(it.Path)) return null;
            switch (it.Kind)
            {
                case "app-folder":
                    if (RemovalRunner.InProgress(l.ProjectRoot).Exists(delegate(string p) { return SafeFs.Norm(p) == SafeFs.Norm(it.Path); }))
                        return SafeFs.IsLink(it.Path) ? "it is a link, not a folder VGAC made" : null;
                    return AppFolderProblem(it.Tag, it.Path, ledger);
                case "comfy-owned":
                    string kw = KeptProblem(it.Path, comfy.Root(l.DataFolder), comfy.Kept(l.DataFolder));
                    if (kw != null) return kw;
                    if (!ComfyGuard.Allows(it.Path, comfy.Root(l.DataFolder), ForeignComfy(ledger))) return "it is not inside VGAC's own ComfyUI folder";
                    return SafeFs.IsLink(it.Path) || !SafeFs.PathHasNoLink(it.Path) ? "it is, or is reached through, a link" : null;
                case "ledger-file":
                    if (!File.Exists(it.Path)) return "it is not a file now";
                    if (!SafeFs.PathHasNoLink(it.Path)) return "it is, or is reached through, a link";
                    long want; long.TryParse(it.Tag ?? "0", out want);
                    return want > 0 && new FileInfo(it.Path).Length != want ? "it is not the file VGAC added any more" : null;
                case "ledger-pack": return SafeFs.PathHasNoLink(it.Path) && System.IO.Path.GetFileName(System.IO.Path.GetDirectoryName(it.Path)) == "custom_nodes" ? null : "it is not a node pack folder, or goes through a link";
                case "settings":
                    return SafeFs.Inside(it.Path, l.ProjectRoot) && !SafeFs.IsLink(it.Path) ? null : "it is not inside the project folder";
                case "work-library": return LibraryProblem(l, it.Path);
                case "work-export":
                    if (!IsVgacExport(it.Path)) return "it no longer holds VGAC's manifest";
                    return WorkPlaceProblem(l, System.IO.Path.GetDirectoryName(it.Path)) ?? (SafeFs.IsLink(it.Path) ? "it is a link" : null);
            }
            return "unknown item";
        }
    }

    // What the person is shown before anything is deleted.
    public static class RemovalSummary
    {
        public static string Size(long b)
        {
            if (b >= (1L << 30)) return (b / (double)(1L << 30)).ToString("0.0") + " GB";
            if (b >= (1L << 20)) return (b >> 20) + " MB";
            return Math.Max(1, b >> 10) + " KB";
        }

        public static string Text(RemovalPlan plan, ICollection<string> ticked, Layout l)
        {
            var sb = new StringBuilder();
            long total = 0;
            foreach (RemovalGroup g in plan.Groups)
            {
                if (!ticked.Contains(g.Id) || g.Items.Count == 0) continue;
                int bin = 0;
                foreach (RemovalItem it in g.Items) if (RemovalRules.ItemUsesBin(g, it)) bin++;
                string way = bin == g.Items.Count ? "goes to the Recycle Bin." : (bin == 0 ? "is deleted for good and cannot be restored." : "partly goes to the Recycle Bin and partly is deleted for good (marked below).");
                sb.AppendLine(g.Title + " (" + Size(g.Bytes) + "): " + way);
                var byFolder = new Dictionary<string, long>(); var counts = new Dictionary<string, int>();
                foreach (RemovalItem it in g.Items)
                {
                    total += it.Bytes;
                    string mark = bin != 0 && bin != g.Items.Count ? (RemovalRules.ItemUsesBin(g, it) ? "   [Recycle Bin]" : "   [deleted for good]") : "";
                    if (it.IsFile && g.Id == "models")
                    {
                        string dir = System.IO.Path.GetDirectoryName(it.Path);
                        long b; byFolder.TryGetValue(dir, out b); byFolder[dir] = b + it.Bytes;
                        int c; counts.TryGetValue(dir, out c); counts[dir] = c + 1;
                    }
                    else sb.AppendLine("   " + it.Path + "   " + Size(it.Bytes) + mark);
                }
                foreach (var kv in byFolder) sb.AppendLine("   " + kv.Key + ": " + counts[kv.Key] + " file(s), " + Size(kv.Value));
            }
            sb.AppendLine();
            sb.AppendLine("Total: " + Size(total) + " will be freed.");
            sb.AppendLine("If the Recycle Bin cannot be used for an item, it is not deleted: you are asked whether to delete it for good.");
            sb.AppendLine("Links are unlinked, never followed: nothing outside the folders listed is touched.");
            sb.AppendLine("Not touched: the project folder, VGAC.exe, your own ComfyUI, Blender, Unity, models you added yourself, anything VGAC did not make.");
            if (plan.NotRemoved.Count > 0) { sb.AppendLine(); sb.AppendLine("Left alone because VGAC did not make it, cannot show that it did, or it changed:"); foreach (string n in plan.NotRemoved) sb.AppendLine("   " + n); }
            foreach (string n in plan.Notes) sb.AppendLine(n);
            return sb.ToString();
        }
    }

    public class RemovalResult
    {
        public List<string> Removed = new List<string>();
        public List<string> Skipped = new List<string>();       // "path: reason"
        public List<string> Locked = new List<string>();
        public List<string> Unlinked = new List<string>();
        public List<string> RecycleFailed = new List<string>(); // paths the Recycle Bin did not take; nothing was deleted for these
        public long Bytes;
        public bool Complete { get { return Skipped.Count == 0 && Locked.Count == 0 && RecycleFailed.Count == 0; } }
    }

    public static class RemovalRunner
    {
        public static string JournalPath(string root) { return System.IO.Path.Combine(root, "local", "remove.json"); }

        // Paths whose removal was started and not finished (so a half-deleted folder is still recognised next time).
        public static List<string> InProgress(string root)
        {
            var res = new List<string>();
            try
            {
                var d = Json.ParseObject(File.ReadAllText(JournalPath(root), Encoding.UTF8));
                object o;
                if (d.TryGetValue("inprogress", out o) && o is System.Collections.IEnumerable && !(o is string))
                    foreach (object x in (System.Collections.IEnumerable)o) res.Add(Convert.ToString(x));
            }
            catch (Exception) { }
            return res;
        }

        static void Mark(string root, string path, bool on)
        {
            try
            {
                var list = InProgress(root);
                string n = SafeFs.Norm(path);
                list.RemoveAll(delegate(string p) { return SafeFs.Norm(p) == n; });
                if (on) list.Add(path);
                string jp = JournalPath(root);
                Dictionary<string, object> d;
                try { d = Json.ParseObject(File.ReadAllText(jp, Encoding.UTF8)); } catch (Exception) { d = new Dictionary<string, object>(); }
                d["inprogress"] = list;
                Directory.CreateDirectory(System.IO.Path.GetDirectoryName(jp));
                File.WriteAllText(jp, Json.Write(d), new UTF8Encoding(false));
            }
            catch (Exception) { }
        }

        // Deletes the ticked groups, in order, settings last. Resumable: run it again after an interruption and the plan
        // (built from what is still on disk) holds only what remains. `work` needs `typed` to equal the word. An item meant
        // for the Recycle Bin that the bin does not take is NOT deleted unless `allowPermanent` is true.
        public static RemovalResult Run(RemovalPlan plan, ICollection<string> ticked, string typed, Layout l, MachineFile machine, Ledger ledger,
            IComfyInstall comfy, IRecycler recycler, SetupLog log, Action<string> say, bool allowPermanent)
        {
            var res = new RemovalResult();
            RemovalGroup work = plan.Get("work");
            if (ticked.Contains("work") && work != null && work.Items.Count > 0 && !RemovalRules.TypedWordOk(typed))
            {
                res.Skipped.Add("my projects and exported characters: the confirmation word was not typed");
                var t = new List<string>(ticked); t.Remove("work"); ticked = t;
            }
            WriteJournal(l.ProjectRoot, ticked);
            bool settingsTicked = false;
            var parentsTouched = new HashSet<string>();
            foreach (RemovalGroup g in plan.Groups)
            {
                if (!ticked.Contains(g.Id) || g.Items.Count == 0) continue;
                if (g.Id == "settings") { settingsTicked = true; continue; }   // after everything else
                foreach (RemovalItem it in g.Items)
                {
                    if (say != null) say("Removing " + it.What + "...");
                    string why = RemovalPlanner.Revalidate(it, l, comfy, ledger, machine);
                    if (why != null) { res.Skipped.Add(it.Path + ": " + why); log.Line("remove: skipped " + it.Path + ": " + why); continue; }
                    Mark(l.ProjectRoot, it.Path, true);
                    RemoveOne(g, it, recycler, allowPermanent, res, log);
                    if (!File.Exists(it.Path) && !Directory.Exists(it.Path)) { Mark(l.ProjectRoot, it.Path, false); ledger.RemoveUnder(it.Path); ledger.Save(); parentsTouched.Add(SafeFs.Norm(System.IO.Path.GetDirectoryName(it.Path))); }
                }
                if (g.Id == "tools") TidyEmptyParents(l, ledger, log, parentsTouched);
            }
            if (settingsTicked)
            {
                RemovalGroup g = plan.Get("settings");
                string ledgerFile = ledger.Path;
                foreach (RemovalItem it in g.Items)
                {
                    if (string.Equals(it.Path, ledgerFile, StringComparison.OrdinalIgnoreCase) || it.What == "remove.json") continue;   // very last
                    string why = RemovalPlanner.Revalidate(it, l, comfy, ledger, machine);
                    if (why != null) { res.Skipped.Add(it.Path + ": " + why); continue; }
                    RemoveOne(g, it, recycler, allowPermanent, res, log);
                }
                if (res.RecycleFailed.Count == 0)
                {
                    foreach (string last in new[] { ledgerFile, JournalPath(l.ProjectRoot) })
                        try { if (File.Exists(last)) File.Delete(last); } catch (Exception) { }
                    TryRemoveEmpty(System.IO.Path.Combine(l.ProjectRoot, "local"));
                    TryRemoveEmpty(System.IO.Path.Combine(l.ProjectRoot, "runtime"));
                }
                return res;
            }
            if (res.RecycleFailed.Count == 0) { try { File.Delete(JournalPath(l.ProjectRoot)); } catch (Exception) { } }
            return res;
        }

        static void RemoveOne(RemovalGroup g, RemovalItem it, IRecycler recycler, bool allowPermanent, RemovalResult res, SetupLog log)
        {
            if (!File.Exists(it.Path) && !Directory.Exists(it.Path)) return;
            bool wantBin = RemovalRules.ItemUsesBin(g, it);
            // The Recycle Bin is not trusted with a folder that has a link inside: that is deleted here, link by link.
            if (wantBin && Directory.Exists(it.Path) && SafeFs.ContainsLink(it.Path)) wantBin = false;
            if (wantBin)
            {
                if (recycler != null && recycler.Send(it.Path) && !File.Exists(it.Path) && !Directory.Exists(it.Path))
                {
                    res.Removed.Add(it.Path); res.Bytes += it.Bytes; log.Line("remove: " + it.Path + " to the Recycle Bin");
                    return;
                }
                // The bin did not take it. Never fall back to permanent deletion silently: stop for this item and let the person choose.
                if (!allowPermanent)
                {
                    res.RecycleFailed.Add(it.Path);
                    log.Line("remove: the Recycle Bin did not take " + it.Path + "; not deleted");
                    return;
                }
            }
            FsReport r = SafeFs.DeleteTree(it.Path);
            res.Bytes += r.Bytes;
            res.Unlinked.AddRange(r.Unlinked);
            foreach (string lk in r.Locked) { res.Locked.Add(lk); log.Line("remove: could not delete " + lk); }
            if (!File.Exists(it.Path) && !Directory.Exists(it.Path)) { res.Removed.Add(it.Path); log.Line("remove: deleted " + it.Path + (r.Unlinked.Count > 0 ? " (" + r.Unlinked.Count + " link(s) unlinked, not followed)" : "")); }
        }

        // The data folder's `tools` parent (only when this run just emptied something in it), and the data folder itself
        // when VGAC made it (the ledger says so) and nothing else is in it.
        static void TidyEmptyParents(Layout l, Ledger ledger, SetupLog log, HashSet<string> touched)
        {
            string tools = System.IO.Path.Combine(l.DataFolder, "tools");
            if (touched.Contains(SafeFs.Norm(tools))) TryRemoveEmpty(tools);
            if (ledger.Has(l.DataFolder, "folder") && !Proof.IsDangerousFolder(l.DataFolder) && TryRemoveEmpty(l.DataFolder)) { ledger.RemoveUnder(l.DataFolder); ledger.Save(); log.Line("remove: the empty folder VGAC made for the big files was removed"); }
        }

        static bool TryRemoveEmpty(string dir)
        {
            try
            {
                if (!Directory.Exists(dir) || SafeFs.IsLink(dir) || Directory.GetFileSystemEntries(dir).Length > 0) return false;
                Directory.Delete(dir, false);
                return true;
            }
            catch (Exception) { return false; }
        }

        static void WriteJournal(string root, ICollection<string> ticked)
        {
            try
            {
                string p = JournalPath(root);
                Directory.CreateDirectory(System.IO.Path.GetDirectoryName(p));
                var d = new Dictionary<string, object>();
                d["state"] = "removing"; d["groups"] = new List<string>(ticked); d["when"] = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ");
                d["inprogress"] = InProgress(root);
                File.WriteAllText(p, Json.Write(d), new UTF8Encoding(false));
            }
            catch (Exception) { }
        }
    }
}
