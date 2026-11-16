// Removing what VGAC added. The plan is built from the install ledger and a few fixed, named folders, every item is checked
// again just before it is deleted, links are unlinked and never followed, and the person's own work is a separate, typed
// confirmation. The rules are in the phase 9 design note. C# 5 (see Core.cs).
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
        public string Kind;        // app-folder | comfy-owned | ledger-file | ledger-pack | settings | work | data-folder
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

        // The Recycle Bin only for a local fixed drive and a size it can keep; bigger is deleted for good, and said so.
        public static bool UseRecycleBin(long bytes, bool fixedDrive) { return fixedDrive && bytes <= RecycleLimit; }

        public static bool TypedWordOk(string typed) { return typed != null && typed.Trim() == TypedWord; }

        public static bool IsFixedDrive(string path)
        {
            try { return new DriveInfo(Path.GetPathRoot(Path.GetFullPath(path))).DriveType == DriveType.Fixed; }
            catch (Exception) { return false; }
        }
    }

    public interface IRecycler
    {
        bool Send(string path);   // false when the Recycle Bin did not take it
    }

    public class WindowsRecycler : IRecycler
    {
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode, Pack = 2)]
        struct SHFILEOPSTRUCT
        {
            public IntPtr hwnd; public uint wFunc; public string pFrom; public string pTo; public ushort fFlags;
            [MarshalAs(UnmanagedType.Bool)] public bool fAnyOperationsAborted; public IntPtr hNameMappings; public string lpszProgressTitle;
        }
        [DllImport("shell32.dll", CharSet = CharSet.Unicode)] static extern int SHFileOperation(ref SHFILEOPSTRUCT op);

        public bool Send(string path)
        {
            var op = new SHFILEOPSTRUCT();
            op.wFunc = 3;                    // FO_DELETE
            op.pFrom = path + "\0\0";
            op.fFlags = 0x40 | 0x10 | 0x4 | 0x400;   // ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
            try { return SHFileOperation(ref op) == 0 && !op.fAnyOperationsAborted; }
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
            return Absolute(l, string.IsNullOrEmpty(v) ? "exports" : v);
        }

        static string Absolute(Layout l, string p) { return Path.GetFullPath(Path.IsPathRooted(p) ? p : Path.Combine(l.ProjectRoot, p)); }

        static Dictionary<string, object> Entry(object o) { return o as Dictionary<string, object>; }
        static string S(Dictionary<string, object> d, string k) { object v; return d != null && d.TryGetValue(k, out v) && v != null ? v.ToString() : ""; }
        static long N(Dictionary<string, object> d, string k) { object v; long n; return d != null && d.TryGetValue(k, out v) && v != null && long.TryParse(v.ToString(), out n) ? n : 0; }

        // The fixed folders VGAC itself makes, with a check of what is inside so a folder that only has the same name is not taken.
        public static string AppFolderProblem(string kind, string path)
        {
            if (!Directory.Exists(path)) return null;      // nothing there: nothing to remove
            if (SafeFs.IsLink(path)) return "it is a link, not a folder VGAC made";
            switch (kind)
            {
                case "venv": return File.Exists(System.IO.Path.Combine(path, "pyvenv.cfg")) ? null : "it does not look like a Python environment";
                case "python": return FindFile(path, "python.exe", 3) ? null : "it does not hold a Python";
                case "uv": return File.Exists(System.IO.Path.Combine(path, "uv.exe")) ? null : "it does not hold uv.exe";
                case "node": return File.Exists(System.IO.Path.Combine(path, "node.exe")) ? null : "it does not hold node.exe";
                case "uv-cache": return File.Exists(System.IO.Path.Combine(path, "CACHEDIR.TAG")) ? null : "it does not look like uv's cache";
                case "downloads":
                    return Directory.GetDirectories(path).Length == 0 ? null : "it holds folders, which VGAC's downloads folder never does";
                case "webview2": return Directory.GetFileSystemEntries(path).Length == 0 || Directory.Exists(System.IO.Path.Combine(path, "EBWebView")) ? null : "it does not look like the app window's data";
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

        static void AddApp(RemovalPlan plan, RemovalGroup g, string id, string kind, string path, string what)
        {
            if (!Directory.Exists(path) && !File.Exists(path)) return;
            // A folder whose removal was cut off is half empty and no longer looks like what it was; the journal remembers it.
            bool wasBeingRemoved = resuming != null && resuming.Contains(SafeFs.Norm(path));
            string problem = wasBeingRemoved ? (SafeFs.IsLink(path) ? "it is a link, not a folder VGAC made" : null) : AppFolderProblem(kind, path);
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
            AddApp(plan, g1, "venv", "venv", l.Venv, "The app's Python environment");
            AddApp(plan, g1, "python", "python", l.PythonDir, "The private Python");
            AddApp(plan, g1, "uv", "uv", System.IO.Path.GetDirectoryName(l.UvExe), "The package tool (uv)");
            AddApp(plan, g1, "node", "node", l.NodeDir, "Node.js (used to build the screens)");
            AddApp(plan, g1, "uvcache", "uv-cache", l.UvCache, "uv's download cache");
            AddApp(plan, g1, "downloads", "downloads", l.Downloads, "Files being downloaded");
            AddApp(plan, g1, "webview2", "webview2", System.IO.Path.Combine(data, "webview2"), "The app window's data");
            AddApp(plan, g1, "node_modules", "node_modules", System.IO.Path.Combine(l.FrontendDir, "node_modules"), "The screens' packages");
            AddApp(plan, g1, "dist", "dist", System.IO.Path.Combine(l.FrontendDir, "dist"), "The built screens");
            foreach (string left in Repair.Leftovers(l))
                if (Directory.Exists(left) || File.Exists(left)) AddApp(plan, g1, "left:" + System.IO.Path.GetFileName(left), "leftover", left, "A leftover of an interrupted job");
            plan.Groups.Add(g1);

            // 2. the app's own ComfyUI: by the install record's owned list only
            var g2 = new RemovalGroup(); g2.Id = "comfy"; g2.Title = "VGAC's own ComfyUI"; g2.DefaultOn = true;
            g2.Sentence = "The ComfyUI VGAC installed for itself. Your models, outputs and your own custom nodes inside it stay.";
            if (comfy.Present(data))
            {
                if (!comfy.Offered(data)) plan.Notes.Add("VGAC's own ComfyUI is not removed here yet: its install record does not list what VGAC made.");
                else
                {
                    string root = comfy.Root(data);
                    var seen = new HashSet<string>();
                    foreach (string p in comfy.Owned(data))
                    {
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
            var owned = new List<string>();
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

            // 6. the person's own work: separate, behind a typed word
            var g6 = new RemovalGroup(); g6.Id = "work"; g6.Title = "My projects and exported characters"; g6.DefaultOn = false; g6.NeedsTypedWord = true;
            g6.Sentence = "Everything you made with VGAC: the library (projects, pictures, models) and the exports. Nothing here is VGAC's to delete unless you say so.";
            foreach (string[] w in new[] { new[] { "library", LibraryFolder(l, machine), "Your library (projects)" }, new[] { "exports", ExportFolder(l, machine), "Your exported characters" } })
            {
                string p = w[1];
                if (!Directory.Exists(p) || SafeFs.IsLink(p)) continue;
                if (SafeFs.Inside(l.ProjectRoot, p)) { plan.NotRemoved.Add(p + ": it holds the project folder itself"); continue; }
                var it = new RemovalItem(); it.Id = "work:" + w[0]; it.GroupId = "work"; it.Kind = "work"; it.Path = p; it.What = w[2]; it.Bytes = SafeFs.Size(p, null);
                g6.Items.Add(it);
            }
            plan.Groups.Add(g6);
            return plan;
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
                    return AppFolderProblem(it.Tag, it.Path);
                case "comfy-owned":
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
                case "work":
                    return SafeFs.IsLink(it.Path) ? "it is a link" : (SafeFs.Inside(l.ProjectRoot, it.Path) ? "it holds the project folder" : null);
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
                bool recycle = RemovalRules.UseRecycleBin(g.Bytes, RemovalRules.IsFixedDrive(l.ProjectRoot));
                sb.AppendLine(g.Title + " (" + Size(g.Bytes) + "): " + (recycle ? "goes to the Recycle Bin." : "is deleted for good and cannot be restored."));
                var byFolder = new Dictionary<string, long>(); var counts = new Dictionary<string, int>();
                foreach (RemovalItem it in g.Items)
                {
                    total += it.Bytes;
                    if (it.IsFile && g.Id == "models")
                    {
                        string dir = System.IO.Path.GetDirectoryName(it.Path);
                        long b; byFolder.TryGetValue(dir, out b); byFolder[dir] = b + it.Bytes;
                        int c; counts.TryGetValue(dir, out c); counts[dir] = c + 1;
                    }
                    else sb.AppendLine("   " + it.Path + "   " + Size(it.Bytes));
                }
                foreach (var kv in byFolder) sb.AppendLine("   " + kv.Key + ": " + counts[kv.Key] + " file(s), " + Size(kv.Value));
            }
            sb.AppendLine();
            sb.AppendLine("Total: " + Size(total) + " will be freed.");
            sb.AppendLine("Links are unlinked, never followed: nothing outside the folders listed is touched.");
            sb.AppendLine("Not touched: the project folder, VGAC.exe, your own ComfyUI, Blender, Unity, models you added yourself, anything VGAC did not make.");
            if (plan.NotRemoved.Count > 0) { sb.AppendLine(); sb.AppendLine("Left alone because VGAC did not make it, or it changed:"); foreach (string n in plan.NotRemoved) sb.AppendLine("   " + n); }
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
        public long Bytes;
        public bool Complete { get { return Skipped.Count == 0 && Locked.Count == 0; } }
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
        // (built from what is still on disk) holds only what remains. `work` needs `typed` to equal the word.
        public static RemovalResult Run(RemovalPlan plan, ICollection<string> ticked, string typed, Layout l, MachineFile machine, Ledger ledger,
            IComfyInstall comfy, IRecycler recycler, SetupLog log, Action<string> say)
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
            foreach (RemovalGroup g in plan.Groups)
            {
                if (!ticked.Contains(g.Id) || g.Items.Count == 0) continue;
                if (g.Id == "settings") { settingsTicked = true; continue; }   // after everything else
                bool recycle = RemovalRules.UseRecycleBin(g.Bytes, RemovalRules.IsFixedDrive(l.ProjectRoot)) && recycler != null;
                foreach (RemovalItem it in g.Items)
                {
                    if (say != null) say("Removing " + it.What + "...");
                    string why = RemovalPlanner.Revalidate(it, l, comfy, ledger, machine);
                    if (why != null) { res.Skipped.Add(it.Path + ": " + why); log.Line("remove: skipped " + it.Path + ": " + why); continue; }
                    Mark(l.ProjectRoot, it.Path, true);
                    RemoveOne(it, recycle, recycler, res, log);
                    if (!File.Exists(it.Path) && !Directory.Exists(it.Path)) { Mark(l.ProjectRoot, it.Path, false); ledger.RemoveUnder(it.Path); ledger.Save(); }
                }
                if (g.Id == "tools") TidyEmptyParents(l, ledger, log);
            }
            if (settingsTicked)
            {
                RemovalGroup g = plan.Get("settings");
                string ledgerFile = ledger.Path;
                bool recycle = RemovalRules.UseRecycleBin(g.Bytes, RemovalRules.IsFixedDrive(l.ProjectRoot)) && recycler != null;
                foreach (RemovalItem it in g.Items)
                {
                    if (string.Equals(it.Path, ledgerFile, StringComparison.OrdinalIgnoreCase) || it.What == "remove.json") continue;   // very last
                    string why = RemovalPlanner.Revalidate(it, l, comfy, ledger, machine);
                    if (why != null) { res.Skipped.Add(it.Path + ": " + why); continue; }
                    RemoveOne(it, recycle, recycler, res, log);
                }
                foreach (string last in new[] { ledgerFile, JournalPath(l.ProjectRoot) })
                    try { if (File.Exists(last)) File.Delete(last); } catch (Exception) { }
                TryRemoveEmpty(System.IO.Path.Combine(l.ProjectRoot, "local"));
                TryRemoveEmpty(System.IO.Path.Combine(l.ProjectRoot, "runtime"));
                return res;
            }
            try { File.Delete(JournalPath(l.ProjectRoot)); } catch (Exception) { }
            return res;
        }

        static void RemoveOne(RemovalItem it, bool recycle, IRecycler recycler, RemovalResult res, SetupLog log)
        {
            if (!File.Exists(it.Path) && !Directory.Exists(it.Path)) return;
            // The Recycle Bin is not trusted with a folder that has a link inside: that is deleted here, link by link.
            if (recycle && Directory.Exists(it.Path) && SafeFs.ContainsLink(it.Path)) recycle = false;
            if (recycle && recycler.Send(it.Path) && !File.Exists(it.Path) && !Directory.Exists(it.Path))
            {
                res.Removed.Add(it.Path); res.Bytes += it.Bytes; log.Line("remove: " + it.Path + " to the Recycle Bin");
                return;
            }
            FsReport r = SafeFs.DeleteTree(it.Path);
            res.Bytes += r.Bytes;
            res.Unlinked.AddRange(r.Unlinked);
            foreach (string lk in r.Locked) { res.Locked.Add(lk); log.Line("remove: could not delete " + lk); }
            if (!File.Exists(it.Path) && !Directory.Exists(it.Path)) { res.Removed.Add(it.Path); log.Line("remove: deleted " + it.Path + (r.Unlinked.Count > 0 ? " (" + r.Unlinked.Count + " link(s) unlinked, not followed)" : "")); }
        }

        // The data folder's `tools` parent, and the data folder itself when VGAC made it and nothing else is in it.
        static void TidyEmptyParents(Layout l, Ledger ledger, SetupLog log)
        {
            TryRemoveEmpty(System.IO.Path.Combine(l.DataFolder, "tools"));
            if (ledger.Has(l.DataFolder, "folder") && TryRemoveEmpty(l.DataFolder)) { ledger.RemoveUnder(l.DataFolder); ledger.Save(); log.Line("remove: the empty folder VGAC made for the big files was removed"); }
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
