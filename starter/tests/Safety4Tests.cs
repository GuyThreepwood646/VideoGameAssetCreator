// Tests for the fourth review: real uv shapes, keeping the claim on Choose, a Move into the project stored relative, the library's own
// marker, custom_nodes kept, Continue only when the settings fit, safe defaults, no ledger entries inside the data folder.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using Vgac.Starter;

public static partial class T
{
    static void Safety4All()
    {
        RealUvShapeIsAdoptable();
        ChooseKeepsTheClaimOfTheSameFolder();
        AMoveIntoTheProjectIsStoredRelative();
        TheLibraryNeedsItsOwnMarker();
        VgacsOwnComfyUIWentThroughTheBackendEverywhere();
        TheFifthReviewsFixes();
        TheSixthReviewsFixes();
        ALoadedLibraryDoesNotLeaveAHalfUpdatedTree();
        DialogsAnswerNoByDefault();
        SetupWritesNoLedgerEntriesInsideTheDataFolder();
        TheRemoveLogSaysWhatWindowsDid();
        RealOnADriveRootIsTheRoot();
    }

    // ---- B1 ----------------------------------------------------------------------------------------------------------------------

    static void RealUvShapeIsAdoptable()
    {
        // MergedBuild now builds python with .temp, .lock and .gitignore beside the cpython folder, as uv 0.8.2 does on this machine
        var s = MergedBuild(false);
        Check(Directory.Exists(Path.Combine(s.L.PythonDir, ".temp")) && File.Exists(Path.Combine(s.L.PythonDir, ".gitignore")) && File.Exists(Path.Combine(s.L.PythonDir, ".lock")), "(the fixture has uv's real extra names)");
        DataStatus st = DataFolders.Check(s.L.DataFolder, s.M, s.Root);
        Check(st.Adoptable, "an install with uv's real files is adoptable: " + st.Why);
        // the widening is only those names: something else beside the cpython folder still refuses
        Put(Path.Combine(s.L.PythonDir, "mine.txt"), "x");
        Check(!DataFolders.Check(s.L.DataFolder, s.M, s.Root).Adoptable, "any other name in the Python folder still refuses");
        File.Delete(Path.Combine(s.L.PythonDir, "mine.txt"));
        // the stand-in uv leaves the real shape too, so a first setup is adoptable after the marker is lost
        string root = NewProject(); var m = MachineFile.Load(root); var l = Layout.From(root, m);
        new SetupRunner(l, new FlakyHost(), m, Ledger.Load(root), new SetupLog(root)).Run(PlanOf(l, new FakeHost()), new Progress());
        File.Delete(Path.Combine(l.DataFolder, DataFolders.MarkerName));
        var m2 = MachineFile.Load(root); m2.Set("starter", "dataFolderId", ""); m2.Save();
        DataStatus lost = DataFolders.Check(l.DataFolder, MachineFile.Load(root), root);
        Check(lost.Adoptable, "a finished setup whose marker was lost can be adopted: " + lost.Why);
    }

    // ---- S1 ----------------------------------------------------------------------------------------------------------------------

    static void ChooseKeepsTheClaimOfTheSameFolder()
    {
        // an interrupted first setup, then the person picks the same folder again
        string root = NewProject();
        string data = Path.Combine(Tmp("rechoose"), "Dev");
        var m0 = MachineFile.Load(root); m0.Set("starter", "dataFolder", data); m0.Save();
        var m = MachineFile.Load(root); var l = Layout.From(root, m);
        var h = new FlakyHost(); h.FailPip = true;
        try { new SetupRunner(l, h, m, Ledger.Load(root), new SetupLog(root)).Run(PlanOf(l, h.Inner), new Progress()); } catch (StarterError) { }
        string id = DataFolders.SettingsId(MachineFile.Load(root));
        Check(!string.IsNullOrEmpty(id), "(the first setup claimed the folder)");
        var pick = MachineFile.Load(root);
        DataFolders.Choose(pick, data, root);
        Eq(DataFolders.SettingsId(MachineFile.Load(root)), id, "choosing the same folder keeps this install's claim");
        Check(DataFolders.Check(data, MachineFile.Load(root), root).State == DataState.Ours, "and the folder is still ours (no adoption needed)");
        h.FailPip = false;
        var m2 = MachineFile.Load(root); var l2 = Layout.From(root, m2);
        string why = null;
        try { new SetupRunner(l2, h, m2, Ledger.Load(root), new SetupLog(root)).Run(PlanOf(l2, h.Inner), new Progress()); } catch (StarterError e) { why = e.Message; }
        Check(why == null, "and the setup is continued: " + why);

        // the same folder at another place (a drive letter that changed): the environment's home points at the old place, the id matches
        string root2 = NewProject(); var q = MachineFile.Load(root2); var l3 = Layout.From(root2, q);
        string dataB = Path.Combine(Tmp("letterB"), "Dev");
        var q0 = MachineFile.Load(root2); q0.Set("starter", "dataFolder", dataB); q0.Save();
        var q1 = MachineFile.Load(root2); var lB = Layout.From(root2, q1);
        new SetupRunner(lB, new FlakyHost(), q1, Ledger.Load(root2), new SetupLog(root2)).Run(PlanOf(lB, new FakeHost()), new Progress());
        File.WriteAllText(Path.Combine(lB.Venv, "pyvenv.cfg"), "home = Z:\\old-letter\\Dev\\python\\cpython-3.13.1-windows-x86_64-none\r\n");   // points at the old letter
        string dataE = Path.Combine(Tmp("letterE"), "Dev");
        Directory.CreateDirectory(Path.GetDirectoryName(dataE)); Directory.Move(dataB, dataE);
        DataFolders.Choose(MachineFile.Load(root2), dataE, root2);
        Check(DataFolders.Check(dataE, MachineFile.Load(root2), root2).State == DataState.Ours, "picking the folder at its new place keeps it ours although the venv points at the old one");

        // another folder, with another install's marker, does not inherit the claim
        string foreign = Path.Combine(Tmp("foreign"), "Dev");
        Put(Path.Combine(foreign, DataFolders.MarkerName), "{\"made_by\":\"VGAC\",\"id\":\"another-install\"}"); Put(Path.Combine(foreign, "downloads", "x.zip"), "x");
        DataFolders.Choose(MachineFile.Load(root2), foreign, root2);
        Check(string.IsNullOrEmpty(DataFolders.SettingsId(MachineFile.Load(root2))) && DataFolders.Check(foreign, MachineFile.Load(root2), root2).State == DataState.NotOurs, "a folder with another install's marker starts without a claim");
        // a folder inside the project is stored relative
        DataFolders.Choose(MachineFile.Load(root2), Path.Combine(root2, "local", "elsewhere"), root2);
        Eq(MachineFile.Load(root2).GetString("starter", "dataFolder"), "local\\elsewhere", "a choice inside the project is written relative to it");
    }

    // ---- S2 ----------------------------------------------------------------------------------------------------------------------

    static void AMoveIntoTheProjectIsStoredRelative()
    {
        var s = Movable();
        string to = Path.Combine(s.Root, "local", "bigfiles");
        string said = Mover(s, null).Start(to, new Progress());
        Check(said == null, "the move into the project's local folder runs: " + said);
        Eq(MachineFile.Load(s.Root).GetString("starter", "dataFolder"), "local\\bigfiles", "the setting is relative to the project");
        string root2 = Path.Combine(Tmp("movedproject"), "VGAC copy");
        SafeFs.CopyTree(s.Root, root2, null, new FsReport());
        var m2 = MachineFile.Load(root2); var l2 = Layout.From(root2, m2);
        Check(l2.DataFolder.StartsWith(root2) && Directory.Exists(Path.Combine(l2.DataFolder, "python")), "after the project is moved the setting still finds the moved copy");
        Check(DataFolders.Check(l2.DataFolder, m2, root2).State == DataState.Ours, "and it is still ours");
    }

    // ---- S3 ----------------------------------------------------------------------------------------------------------------------

    static void TheLibraryNeedsItsOwnMarker()
    {
        // no marker: nothing is offered and nothing is removed, even with the typed word
        var s = Installed();
        string lib = Path.Combine(s.Root, "library");
        File.Delete(Path.Combine(lib, LibraryFolders.MarkerName));
        var plan = RemovalPlanner.Build(s.L, MachineFile.Load(s.Root), s.Led, s.Comfy);
        Check(plan.Get("work").Items.All(i => i.Kind != "work-library") && plan.NotRemoved.Any(n => n.Contains("library marker")), "a library with the app's names but no marker offers nothing, and says why");
        RemovalRunner.Run(plan, new List<string> { "work" }, "REMOVE", s.L, MachineFile.Load(s.Root), s.Led, s.Comfy, new FakeRecycler(), new SetupLog(s.Root), null, null);
        Check(File.Exists(Path.Combine(lib, "vgac.sqlite")), "so the database is still there after a removal run");
        // the person clicks adopt: it holds a real database and only the app's names
        Check(LibraryFolders.AdoptProblem(s.L, MachineFile.Load(s.Root)) == null, "(it can be adopted)");
        LibraryFolders.Adopt(s.L, MachineFile.Load(s.Root));
        var plan2 = RemovalPlanner.Build(s.L, MachineFile.Load(s.Root), s.Led, s.Comfy);
        Check(plan2.Get("work").Items.Any(i => i.Kind == "work-library"), "after adopting, Remove lists it");
        // an item revalidated after the marker is gone is refused
        var item = plan2.Get("work").Items.First(i => i.Kind == "work-library");
        File.Delete(Path.Combine(lib, LibraryFolders.MarkerName));
        Check(RemovalPlanner.Revalidate(item, s.L, s.Comfy, s.Led, MachineFile.Load(s.Root)) != null, "just before deleting, a missing marker refuses the item");
        // another id
        Put(Path.Combine(lib, LibraryFolders.MarkerName), "{\"made_by\":\"VGAC\",\"id\":\"someone-else\"}");
        Check(!LibraryFolders.Ours(lib, MachineFile.Load(s.Root)), "a marker with another id is not ours");

        // the person's own folder called projects, no database: never adoptable, never claimed
        string root = NewProject(); string work = Path.Combine(Tmp("work"), "Work");
        Put(Path.Combine(work, "projects", "mine", "a.txt"), "the person's own");
        var m = MachineFile.Load(root); m.Set("backend", "library_path", work); m.Save();
        var l = Layout.From(root, MachineFile.Load(root));
        LibraryFolders.ClaimIfNew(l, MachineFile.Load(root));
        Check(!File.Exists(Path.Combine(work, LibraryFolders.MarkerName)), "a folder that already holds things is not claimed at start");
        Check(LibraryFolders.AdoptProblem(l, MachineFile.Load(root)) != null, "and cannot be adopted");
        var pr = RemovalPlanner.Build(l, MachineFile.Load(root), Ledger.Load(root), new StandInComfy());
        RemovalRunner.Run(pr, new List<string> { "work" }, "REMOVE", l, MachineFile.Load(root), Ledger.Load(root), new StandInComfy(), new FakeRecycler(), new SetupLog(root), null, null);
        Check(File.Exists(Path.Combine(work, "projects", "mine", "a.txt")), "the person's own projects survive the typed word");

        // a new or empty folder is claimed at start; a missing one is made
        string fresh = Path.Combine(Tmp("fresh"), "library");
        var m3 = MachineFile.Load(root); m3.Set("backend", "library_path", fresh); m3.Save();
        LibraryFolders.ClaimIfNew(Layout.From(root, MachineFile.Load(root)), MachineFile.Load(root));
        Check(LibraryFolders.Ours(fresh, MachineFile.Load(root)), "a library that does not exist yet is claimed before the app uses it");
        string empty = Path.Combine(Tmp("empty"), "library"); Directory.CreateDirectory(empty);
        var m4 = MachineFile.Load(root); m4.Set("backend", "library_path", empty); m4.Save();
        LibraryFolders.ClaimIfNew(Layout.From(root, MachineFile.Load(root)), MachineFile.Load(root));
        Check(LibraryFolders.Ours(empty, MachineFile.Load(root)), "and an empty one");
        // a claimed library that the person then also keeps their own files in: the whole folder is left
        Put(Path.Combine(empty, "vgac.sqlite"), "x"); WriteSqlite(Path.Combine(empty, "vgac.sqlite")); Put(Path.Combine(empty, "notes.txt"), "mine");
        var pl = RemovalPlanner.Build(Layout.From(root, MachineFile.Load(root)), MachineFile.Load(root), Ledger.Load(root), new StandInComfy());
        Check(pl.Get("work").Items.All(i => i.Kind != "work-library") && pl.NotRemoved.Any(n => n.Contains("notes.txt")), "a claimed library that also holds the person's own file is left whole");
    }

    // ---- VGAC's own ComfyUI: only through the backend's commands ----------------------------------------------------------------

    static void VgacsOwnComfyUIWentThroughTheBackendEverywhere()
    {
        // a move with VGAC's own ComfyUI present, end to end on fakes: check before copying, retarget, then rebuild
        var s = Movable(); string to = Path.Combine(Tmp("target"), "newbig");
        Check(Mover(s, null).Start(to, new Progress()) == null, "the move runs");
        Check(string.Join(",", s.Comfy.Calls.ToArray()).EndsWith("retarget,rebuild"), "asked before copying, then retarget, then rebuild: " + string.Join(",", s.Comfy.Calls.ToArray()));
        Check(File.Exists(Path.Combine(to, "comfyui", ComfyGuard.MarkerName)) && MoveRunner.Load(s.Root) == null && MoveRules.RestartPending(s.Root), "the install record came along, the move is at its end and a restart is needed");
        // until a command succeeds: the move is refused before anything is copied
        var s2 = Movable(); string to2 = Path.Combine(Tmp("target"), "newbig2");
        s2.Comfy.Problem = "The app's installer for ComfyUI did not answer (is the app's Python there?).";
        string said = Mover(s2, null).Start(to2, new Progress());
        Check(said != null && said.Contains("did not answer") && !said.Contains("older version") && !Directory.Exists(to2) && MoveRunner.Load(s2.Root) == null, "refused, nothing copied, no journal, and the reason is the backend's: " + said);
        // retarget refused: the move waits at 'switching' with the reason; Continue tries again, and rebuild is not asked before retarget worked
        var s3 = Movable(); string to3 = Path.Combine(Tmp("target"), "newbig3");
        s3.Comfy.RetargetError = "The settings could not be updated.";
        said = Mover(s3, null).Start(to3, new Progress());
        Check(said != null && said.Contains("The settings could not be updated") && !s3.Comfy.Calls.Contains("rebuild"), "a refused retarget is reported and rebuild is not asked: " + said);
        Eq(MoveRunner.Load(s3.Root).State, "switching", "the move waits at 'switching'");
        s3.Comfy.RetargetError = null;
        Check(Mover(s3, null).Continue(new Progress()) == null && MoveRunner.Load(s3.Root) == null, "Continue finishes it once the backend agrees");
        // the starter has no list of what is owned or kept any more
        string src = FindSource("src\\Comfy.cs");
        if (src != null) { string text = File.ReadAllText(src); Check(!text.Contains("\"owned\"") && !text.Contains("Kept(") && !text.Contains("Owned("), "Comfy.cs has no owned or kept handling"); }
        // Remove: the plan after a removal that kept the models lists nothing more to do
        var s4 = Installed();
        var plan = RemovalPlanner.Build(s4.L, s4.M, s4.Led, s4.Comfy);
        RemovalRunner.Run(plan, new List<string> { "comfy" }, null, s4.L, MachineFile.Load(s4.Root), s4.Led, s4.Comfy, new FakeRecycler(), new SetupLog(s4.Root), null, null);
        Check(RemovalPlanner.Build(s4.L, MachineFile.Load(s4.Root), s4.Led, s4.Comfy).Get("comfy").Items.Count == 0, "what is left (the models and the install record) is not offered for removal again");
        Check(Directory.Exists(Path.Combine(s4.Data, "comfyui", "models")), "and the models are there");
    }

    static bool Eq2(string a, string b) { return a == b; }

    // ---- S5 ----------------------------------------------------------------------------------------------------------------------

    // ---- defaults, ledger, log, Real ------------------------------------------------------------------------------------------------

    static string FindSource(string rel)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        for (int i = 0; i < 6 && dir != null; i++)
        {
            string p = Path.Combine(dir, rel);
            if (File.Exists(p)) return p;
            dir = Path.GetDirectoryName(dir.TrimEnd('\\'));
        }
        string cwd = Path.Combine(Directory.GetCurrentDirectory(), rel);
        return File.Exists(cwd) ? cwd : null;
    }

    static void DialogsAnswerNoByDefault()
    {
        string src = FindSource("src\\Maintenance.cs");
        if (src == null) { Console.WriteLine("SKIPPED: Maintenance.cs not found from the test folder"); return; }
        string text = File.ReadAllText(src);
        int bad = 0, total = 0;
        foreach (Match mm in Regex.Matches(text, @"MessageBox\.Show\((?:[^;]|\n)*?MessageBoxButtons\.YesNo(?:[^;]|\n)*?;"))
        {
            total++;
            string call = mm.Value;
            bool restart = call.Contains("Restart VGAC now?");
            if (!restart && !call.Contains("MaintenanceDefaults.")) { bad++; Console.WriteLine("  a yes/no box without a default: " + call.Substring(0, Math.Min(90, call.Length))); }
        }
        Check(total >= 6 && bad == 0, "every yes/no question about changing things names its default (" + total + " boxes, " + bad + " without)");
        Check(Regex.Matches(text, @"MessageBoxDefaultButton \w+ = MessageBoxDefaultButton\.Button2;").Count == 6 && !text.Contains("= MessageBoxDefaultButton.Button1"), "and each of those defaults is the second button, No");
        foreach (string name in new[] { "AbandonMove", "FinishAnyway", "RestartAfterMove", "Adopt", "PermanentDelete", "RunSteps" })
            Check(text.Contains("MessageBoxDefaultButton " + name + " = MessageBoxDefaultButton.Button2;"), name + " defaults to No");
    }

    static void SetupWritesNoLedgerEntriesInsideTheDataFolder()
    {
        string root = NewProject(); var m = MachineFile.Load(root); var l = Layout.From(root, m);
        new SetupRunner(l, new FlakyHost(), m, Ledger.Load(root), new SetupLog(root)).Run(PlanOf(l, new FakeHost()), new Progress());
        var paths = Ledger.Load(root).Items.Select(o => Convert.ToString(((Dictionary<string, object>)o)["path"])).ToList();
        Check(paths.All(p => SafeFs.Norm(p) == SafeFs.Norm(l.DataFolder) || !SafeFs.Inside(p, l.DataFolder)), "nothing inside the data folder is in the ledger (the marker and the fixed names say what is VGAC's)");
        Check(paths.Any(p => SafeFs.Norm(p) == SafeFs.Norm(l.Venv)), "(the app's Python environment, which lives in the project, still is)");
    }

    static void TheRemoveLogSaysWhatWindowsDid()
    {
        var s = Installed();
        var plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        RemovalRunner.Run(plan, new List<string> { "tools" }, null, s.L, MachineFile.Load(s.Root), s.Led, s.Comfy, new FakeRecycler(), new SetupLog(s.Root), null, null);
        string log = File.ReadAllText(Path.Combine(s.Root, "runtime", "logs", "setup.log"));
        Check(log.Contains("remove:") && !log.Contains("to the Recycle Bin"), "the log never claims the Recycle Bin took something (Windows may have deleted it for good)");
    }

    static void RealOnADriveRootIsTheRoot()
    {
        string rootDir = Path.GetPathRoot(Path.GetTempPath());
        string here = Directory.GetCurrentDirectory();
        string other = Tmp("cwd");
        try
        {
            Directory.SetCurrentDirectory(other);
            string real = SafeFs.Real(rootDir);
            Check(real != null && real.TrimEnd('\\') == rootDir.TrimEnd('\\').ToLowerInvariant(), "a drive root resolves to itself, not to the current folder on that drive (" + real + ")");
        }
        finally { Directory.SetCurrentDirectory(here); }
    }

    // ---- the fifth review ----------------------------------------------------------------------------------------------------

    // A host whose Run does what the test says, in the starter's own way: writes into the log file it was given.
    class ScriptedHost : IHost, IAbortable
    {
        public Func<string, string> Answer;          // args -> text for the log (null: nothing)
        public int HangMs;
        public string WriteFirst;
        public int ExitCode;
        public bool Aborted;
        public string LastArgs; public Dictionary<string, string> LastEnv;
        public void Download(string u, string d, string s, Action<long, long> p) { }
        public void Unzip(string z, string d) { }
        public int Run(string exe, string args, string cwd, Dictionary<string, string> env, string log)
        {
            LastArgs = args; LastEnv = env;
            if (WriteFirst != null) File.WriteAllText(log, WriteFirst);
            if (HangMs > 0) { int waited = 0; while (waited < HangMs && !Aborted) { Thread.Sleep(50); waited += 50; } }
            string text = Answer == null ? null : Answer(args);
            if (text != null) File.WriteAllText(log, text);
            return ExitCode;
        }
        public string Capture(string e, string a) { return null; }
        public long FreeBytes(string f) { return 1L << 40; }
        public void AbortCurrent() { Aborted = true; }
    }

    static Layout ProjectWithPython(out string root)
    {
        root = NewProject();
        var l = Layout.From(root, MachineFile.Load(root));
        Directory.CreateDirectory(Path.GetDirectoryName(l.VenvPython)); File.WriteAllText(l.VenvPython, "py");
        return l;
    }

    [System.Runtime.InteropServices.DllImport("shell32.dll", SetLastError = true)]
    static extern IntPtr CommandLineToArgvW([System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.LPWStr)] string cmd, out int argc);

    static List<string> WindowsArgs(string commandLine)
    {
        int n; IntPtr p = CommandLineToArgvW(commandLine, out n);
        var res = new List<string>();
        for (int i = 0; i < n; i++) res.Add(System.Runtime.InteropServices.Marshal.PtrToStringUni(System.Runtime.InteropServices.Marshal.ReadIntPtr(p, i * IntPtr.Size)));
        return res;
    }

    // What the starter does after the person picked another folder but the ledger still names the old one: scripted answers of the real
    // commands, in the order the real backend gives them.
    class RenamedInstall
    {
        public bool Adopted; public List<string> Asked = new List<string>(); public string RefuseAdopt;
        public string Run(string args, int ms, Func<bool> c)
        {
            string cmd = args.Split(' ')[0]; Asked.Add(cmd);
            if (cmd == "check") return Adopted ? "{\"ok\": true, \"keeps\": []}" : "{\"ok\": false, \"code\": \"adoptable\", \"reason\": \"This ComfyUI is VGAC's (its id matches), but the ledger still names its old place.\", \"adoptable\": true}";
            if (cmd == "adopt") { if (RefuseAdopt != null) return "{\"ok\": false, \"code\": \"not_ours\", \"reason\": \"" + RefuseAdopt + "\", \"adoptable\": false}"; Adopted = true; return "{\"ok\": true, \"adopted\": true}"; }
            return "{\"ok\": true}";
        }
    }

    static void TheFifthReviewsFixes()
    {
        // another user's shared folders are refused like this user's
        string usersRoot = Path.GetDirectoryName(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile));
        string anyRoot = NewProject();
        Check(Proof.DataFolderProblem(Path.Combine(usersRoot, "Somebody Else", "Documents"), anyRoot) != null, "another user's Documents is refused");
        Check(Proof.DataFolderProblem(Path.Combine(usersRoot, "Public", "Documents"), anyRoot) != null, "Public\\Documents is refused");
        Check(Proof.DataFolderProblem(Path.Combine(usersRoot, "Somebody Else", "Downloads"), anyRoot) != null, "another user's Downloads is refused");

        // N1: a renamed data folder is adopted through the backend's command, then everything goes on
        var ren = new RenamedInstall();
        var s = Movable();
        var real = new BackendComfyInstall(ren.Run);
        var mv = new MoveRunner(s.L, MachineFile.Load(s.Root), Ledger.Load(s.Root), real, new NoProcs(), new SetupLog(s.Root));
        string to = Path.Combine(Tmp("target"), "after-rename");
        string said = mv.Start(to, new Progress());
        Check(said == null, "the move with a renamed ComfyUI install goes through: " + said);
        Eq(string.Join(",", ren.Asked.ToArray()).Substring(0, 20), "check,adopt,check,re", "check, adopt, check again, then the move itself");
        // and when the backend will not adopt (a copied marker, a data folder that is not VGAC's), the move is refused with its reason
        var ren2 = new RenamedInstall(); ren2.RefuseAdopt = "VGAC cannot show that it made the ComfyUI there.";
        var s2 = Movable();
        var mv2 = new MoveRunner(s2.L, MachineFile.Load(s2.Root), Ledger.Load(s2.Root), new BackendComfyInstall(ren2.Run), new NoProcs(), new SetupLog(s2.Root));
        said = mv2.Start(Path.Combine(Tmp("target"), "refused"), new Progress());
        Check(said != null && said.Contains("VGAC cannot show that it made the ComfyUI there") && MoveRunner.Load(s2.Root) == null, "refused, nothing started: " + said);
        var plan = RemovalPlanner.Build(s2.L, MachineFile.Load(s2.Root), s2.Led, new BackendComfyInstall(ren2.Run));
        Check(plan.Get("comfy").Items.Count == 0 && plan.Notes.Any(n => n.Contains("cannot show that it made")), "Remove lists nothing for it and says why");

        // the switch moves ComfyUI's engine settings too; when its Python cannot be made again the move waits at "switching" and says what works
        var c1 = Movable(); string t1 = Path.Combine(Tmp("target"), "switch");
        Check(Mover(c1, null).Start(t1, new Progress()) == null, "(a finished move)");
        Eq(c1.Comfy.EngineAt, t1, "the engine settings were pointed at the new place");
        Check(MoveRunner.Load(c1.Root) == null && MoveRules.RestartPending(c1.Root), "the journal is gone and VGAC has to be restarted");
        var c3 = Movable(); string t3 = Path.Combine(Tmp("target"), "offline");
        c3.Comfy.RebuildError = "VGAC could not reach the internet.";
        string off = Mover(c3, null).Start(t3, new Progress());
        Check(off != null && off.Contains("VGAC itself works") && off.Contains("needs the internet") && off.Contains("Continue") && !off.Contains("Cancel"), "the message says what works and what does not, and offers only Continue: " + off);
        Eq(MoveRunner.Load(c3.Root, t3).State, "switching", "(the move waits at 'switching')");
        c3.Comfy.RebuildError = null;
        Check(Mover(c3, null).Continue(new Progress()) == null && MoveRunner.Load(c3.Root) == null, "Continue finishes it once it can");

        // N3: a failed ComfyUI removal keeps the Python its installer needs
        var r1 = Installed();
        r1.Comfy.RemoveError = "VGAC's ComfyUI is still running on port 8288.";
        var rp = RemovalPlanner.Build(r1.L, r1.M, r1.Led, r1.Comfy);
        var rr = RemovalRunner.Run(rp, new List<string> { "comfy", "tools" }, null, r1.L, MachineFile.Load(r1.Root), r1.Led, r1.Comfy, new FakeRecycler(), new SetupLog(r1.Root), null, null);
        Check(Directory.Exists(r1.L.Venv) && Directory.Exists(r1.L.PythonDir), "the app's Python (venv and its Python) stays when VGAC's own ComfyUI could not be removed");
        Check(rr.Skipped.Any(x => x.Contains("kept, because VGAC's own ComfyUI could not be removed")), "and the screen says why");
        Check(!Directory.Exists(r1.L.NodeDir) && !Directory.Exists(Path.Combine(r1.L.FrontendDir, "node_modules")), "the rest of the group was removed");
        r1.Comfy.RemoveError = null;
        var rr2 = RemovalRunner.Run(RemovalPlanner.Build(r1.L, MachineFile.Load(r1.Root), r1.Led, r1.Comfy), new List<string> { "comfy", "tools" }, null, r1.L, MachineFile.Load(r1.Root), r1.Led, r1.Comfy, new FakeRecycler(), new SetupLog(r1.Root), null, null);
        Check(!Directory.Exists(r1.L.Venv) && !Directory.Exists(r1.L.PythonDir) && !File.Exists(Path.Combine(r1.Data, "comfyui", "main.py")), "a second run, once it works, removes everything");

        // N4 and N6: a command that hangs, prints garbage, exits non-zero without a result, or is stopped
        string root; var lay = ProjectWithPython(out root);
        var h = new ScriptedHost(); h.HangMs = 20000;
        var sw = Stopwatch.StartNew();
        string line = ComfyCli.Run(h, lay, root, "check", 700, null);
        Check(sw.ElapsedMilliseconds < 8000 && h.Aborted && line != null && line.Contains("\"timeout\""), "a command that hangs is stopped at its time limit (" + sw.ElapsedMilliseconds + " ms): " + line);
        Check(h.LastEnv != null && h.LastEnv.ContainsKey("VGAC_CLEAN_ENVIRONMENT"), "and every call asks for a clean environment");
        h = new ScriptedHost(); h.HangMs = 20000;
        line = ComfyCli.Run(h, lay, root, "rebuild-venv", 60000, delegate { return true; });
        Check(h.Aborted && line != null && line.Contains("\"stopped\""), "Stop ends a long command: " + line);
        h = new ScriptedHost(); h.Answer = delegate(string a) { return "Traceback (most recent call last)\r\nValueError: nope\r\n{ not json\r\n"; }; h.ExitCode = 1;
        Check(ComfyCli.Run(h, lay, root, "check", 5000, null) == null, "garbage and a non-zero exit without a result line is no answer");
        Check(!new BackendComfyInstall(delegate(string a, int ms, Func<bool> c) { return ComfyCli.Run(h, lay, root, a, ms, c); }).Remove("x").Contains("older version"), "which is a refusal with a plain reason");
        h = new ScriptedHost(); h.Answer = delegate(string a) { return "some log line\r\n{\"ok\": true, \"rebuilt\": true}\r\n"; };
        Check((ComfyCli.Run(h, lay, root, "rebuild-venv", 5000, null) ?? "").Contains("rebuilt"), "the last result line is the answer");
        // the state is remembered briefly, so the window does not ask on every redraw, and forgotten when anything changes
        int asked = 0;
        var memo = new BackendComfyInstall(delegate(string a, int ms, Func<bool> c) { asked++; return "{\"ok\": true, \"keeps\": []}"; });
        Directory.CreateDirectory(Path.Combine(s.Data, "x"));
        string cdata = Tmp("memo"); Put(Path.Combine(cdata, "comfyui", ComfyGuard.MarkerName), "{\"made_by\":\"VGAC\"}");
        memo.State(cdata); memo.State(cdata);
        Eq(asked.ToString(), "1", "two looks in a row ask once");
        memo.Forget(); memo.State(cdata);
        Eq(asked.ToString(), "2", "and again after Forget");

        // N5: argument quoting
        foreach (string arg in new[] { "D:\\VGACdata\\", "E:\\new place\\", "C:\\plain\\path", "with \"quote\"", "trail\\\\", "x y", "" })
            Eq(string.Join("|", WindowsArgs("prog.exe " + ComfyCli.Quote(arg)).Skip(1).ToArray()), arg, "Windows reads back exactly: " + arg);
        var m5 = MachineFile.Load(NewProject()); Environment.SetEnvironmentVariable("VGACSTARTERTEST", "C:\\from\\env");
        m5.Set("starter", "dataFolder", "%VGACSTARTERTEST%\\big");
        Eq(Layout.From("C:\\proj", m5).DataFolder, "C:\\from\\env\\big", "%NAME% in the data folder setting is expanded (as the backend does)");
        m5.Set("starter", "dataFolder", "$VGACSTARTERTEST\\big");
        Check(Layout.From("C:\\proj", m5).DataFolder.Contains("$VGACSTARTERTEST"), "and nothing else is");
        Environment.SetEnvironmentVariable("VGACSTARTERTEST", null);

        // N6: the real host passes none of the starter's own variables to a command that asks for a clean environment
        Environment.SetEnvironmentVariable("VGAC_MACHINE", "C:\\somewhere\\else.json"); Environment.SetEnvironmentVariable("PYTHONHOME", "C:\\nowhere"); Environment.SetEnvironmentVariable("UV_CACHE_DIR", "C:\\nocache");
        string logA = Path.Combine(Tmp("env"), "a.log"), logB = Path.Combine(Tmp("env"), "b.log");
        string cmd = Path.Combine(Environment.SystemDirectory, "cmd.exe");
        var rh = new RealHost();
        var clean = new Dictionary<string, string>(); clean["VGAC_CLEAN_ENVIRONMENT"] = "1"; clean["MY_OWN"] = "kept";
        rh.Run(cmd, "/c set", Tmp("env"), clean, logA);
        rh.Run(cmd, "/c set", Tmp("env"), null, logB);
        string a1 = File.ReadAllText(logA), b1 = File.ReadAllText(logB);
        Check(!a1.Contains("VGAC_MACHINE") && !a1.Contains("PYTHONHOME") && !a1.Contains("UV_CACHE_DIR") && !a1.Contains("VGAC_CLEAN_ENVIRONMENT") && a1.Contains("MY_OWN=kept") && a1.ToUpperInvariant().Contains("PATH="), "a clean call has none of them, keeps PATH and what it was given");
        Check(b1.Contains("VGAC_MACHINE") && b1.Contains("PYTHONHOME"), "(an ordinary call still inherits them)");
        Environment.SetEnvironmentVariable("VGAC_MACHINE", null); Environment.SetEnvironmentVariable("PYTHONHOME", null); Environment.SetEnvironmentVariable("UV_CACHE_DIR", null);

        RealCommandsAgainstAFakeInstall();
    }

    // One end-to-end run of the real `python -m vgac.comfy_install`, through the starter's own host, against a fake install in a temp
    // project (a copy of the backend's package, the real Python environment read through a link). Skipped when no environment is found.
    static void RealCommandsAgainstAFakeInstall()
    {
        string venv = null;
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        for (int i = 0; i < 10 && dir != null && venv == null; i++)
        {
            string cand = Path.Combine(dir, "backend", ".venv"); if (File.Exists(Path.Combine(cand, "Scripts", "python.exe"))) venv = cand;
            dir = Path.GetDirectoryName(dir.TrimEnd('\\'));
        }
        if (venv == null) venv = @"F:\Projects\VideoGameAssetCreator\backend\.venv";
        string pkg = FindSource("backend\\vgac\\comfy_install.py");
        if (!File.Exists(Path.Combine(venv, "Scripts", "python.exe")) || pkg == null) { Console.WriteLine("SKIPPED: no Python environment or backend package found for the real-command test"); return; }
        string root = Tmp("e2e");
        SafeFs.CopyTree(Path.GetDirectoryName(pkg), Path.Combine(root, "backend", "vgac"), new[] { "__pycache__" }, new FsReport());
        Junction(Path.Combine(root, "backend", ".venv"), venv);
        Directory.CreateDirectory(Path.Combine(root, "setup")); File.Copy(Path.Combine(Path.GetDirectoryName(Path.GetDirectoryName(pkg)), "config.yaml"), Path.Combine(root, "backend", "config.yaml"), true);
        string data = Path.Combine(root, "local", "renamed data");
        Put(Path.Combine(root, "local", "machine.json"), "{\"schema\":1,\"starter\":{\"dataFolder\":\"" + data.Replace("\\", "\\\\") + "\",\"dataFolderId\":\"data-id-9\"}}");
        Put(Path.Combine(data, ".vgac-data.json"), "{\"made_by\":\"VGAC\",\"id\":\"data-id-9\"}");
        string croot = Path.Combine(data, "comfyui");
        Put(Path.Combine(croot, "vgac-install.json"), "{\"made_by\":\"VGAC\",\"install_id\":\"abc123\",\"steps\":{}}");
        Put(Path.Combine(croot, "ComfyUI", "main.py"), "print()"); Put(Path.Combine(croot, "ComfyUI", "models", "m.safetensors"), "weights"); Put(Path.Combine(croot, "output", "o.png"), "pic");
        string oldPlace = Path.Combine(root, "local", "old data", "comfyui");     // where the ledger still says it is: gone now
        Put(Path.Combine(root, "local", "installed.json"), "{\"items\":[{\"what\":\"VGAC's own ComfyUI\",\"path\":\"" + oldPlace.Replace("\\", "\\\\") + "\",\"kind\":\"added\",\"bytes\":0,\"id\":\"abc123\"}]}");
        var m = MachineFile.Load(root); var lay = Layout.From(root, m);
        var host = new RealHost();
        var comfy = new BackendComfyInstall(delegate(string a, int ms, Func<bool> c) { return ComfyCli.Run(host, lay, root, a, ms, c); });
        ComfyState st = comfy.State(data);
        Check(st.Present && st.Problem == null, "the real check, after the real adopt of a renamed install, confirms it: " + st.Problem);
        Check(st.Keeps.Count == 2 && st.Keeps.Exists(k => k.EndsWith("models")) && st.Keeps.Exists(k => k.EndsWith("output")), "and reports what it keeps (models, and the person's output with files in it): " + string.Join(",", st.Keeps.ToArray()));
        Check(File.ReadAllText(Path.Combine(root, "local", "installed.json")).Replace("\\\\", "\\").Contains(croot), "the ledger now names the folder the settings name");
        // a copied marker in somebody else's data folder is refused by the real command
        string root2 = Tmp("e2e2");
        SafeFs.CopyTree(Path.GetDirectoryName(pkg), Path.Combine(root2, "backend", "vgac"), new[] { "__pycache__" }, new FsReport());
        Junction(Path.Combine(root2, "backend", ".venv"), venv);
        File.Copy(Path.Combine(Path.GetDirectoryName(Path.GetDirectoryName(pkg)), "config.yaml"), Path.Combine(root2, "backend", "config.yaml"), true);
        string theirs = Path.Combine(root2, "Their data");
        Put(Path.Combine(root2, "local", "machine.json"), "{\"schema\":1,\"starter\":{\"dataFolder\":\"" + theirs.Replace("\\", "\\\\") + "\"}}");
        Put(Path.Combine(theirs, "comfyui", "vgac-install.json"), "{\"made_by\":\"VGAC\",\"install_id\":\"abc123\"}"); Put(Path.Combine(theirs, "comfyui", "theirs.txt"), "not VGAC's");
        Put(Path.Combine(root2, "local", "installed.json"), "{\"items\":[{\"what\":\"VGAC's own ComfyUI\",\"path\":\"C:\\\\gone\\\\comfyui\",\"kind\":\"added\",\"bytes\":0,\"id\":\"abc123\"}]}");
        var lay2 = Layout.From(root2, MachineFile.Load(root2));
        var comfy2 = new BackendComfyInstall(delegate(string a, int ms, Func<bool> c) { return ComfyCli.Run(host, lay2, root2, a, ms, c); });
        ComfyState st2 = comfy2.State(theirs);
        Check(st2.Problem != null && !st2.Problem.Contains("older version"), "a copied marker in a folder that is not VGAC's is refused by the real command: " + st2.Problem);
        Check(comfy2.Remove(Path.Combine(theirs, "comfyui")) != null && File.Exists(Path.Combine(theirs, "comfyui", "theirs.txt")), "and the real remove deletes nothing there");
        // the real retarget and remove, from here on the folder is VGAC's: retarget (nothing to change), then remove keeps the models
        Check(comfy.Retarget(Path.Combine(root, "local", "old data"), data) == null, "the real retarget answers ok");
        Check(comfy.Remove(croot) == null, "the real remove succeeds");
        Check(File.Exists(Path.Combine(croot, "ComfyUI", "models", "m.safetensors")) && !File.Exists(Path.Combine(croot, "ComfyUI", "main.py")) && File.Exists(Path.Combine(croot, "output", "o.png")), "it kept the models and the person's output (A1), and removed the rest of VGAC's own files");
    }


    // ---- the seventh review: each finding as a test that fails on e3fc6b9 --------------------------------------------------------------

    static string RootOf(InstallScene s) { return s.Root; }

    // ---- E5: a pull that changes a library the running program has loaded ---------------------------------------------------------------

    [System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode, SetLastError = true)]
    static extern IntPtr LoadLibraryW(string path);
    [System.Runtime.InteropServices.DllImport("kernel32.dll")]
    static extern bool FreeLibrary(IntPtr h);

    static void ALoadedLibraryDoesNotLeaveAHalfUpdatedTree()
    {
        if (!GitAvailable()) { Console.WriteLine("SKIPPED: the loaded-library pull test (git is not installed here)"); return; }
        string sys = Environment.SystemDirectory;
        string dllOld = Path.Combine(sys, "version.dll"), dllNew = Path.Combine(sys, "winmm.dll");
        if (!File.Exists(dllOld) || !File.Exists(dllNew)) { Console.WriteLine("SKIPPED: no system libraries to stand in"); return; }
        string b = Tmp("gitdll");
        string remote = Path.Combine(b, "remote.git"); Directory.CreateDirectory(remote); Git(remote, "init --bare -q --initial-branch=main");
        string project = Path.Combine(b, "project");
        Git(b, "clone -q \"" + remote + "\" project");
        File.Copy(PingExe, Path.Combine(project, "VGAC.exe"));
        Put(Path.Combine(project, "starter", "CHECKSUMS.txt"), Hashing.Sha256File(Path.Combine(project, "VGAC.exe")) + "  VGAC.exe  (published)\n");
        Directory.CreateDirectory(Path.Combine(project, "starter", "bin"));
        string lib = Path.Combine(project, "starter", "bin", "Microsoft.Web.WebView2.Core.dll");
        File.Copy(dllOld, lib); Put(Path.Combine(project, "readme.txt"), "one");
        Git(project, "add -A"); Git(project, "commit -q -m one"); Git(project, "push -q -u origin main");
        string dev = Path.Combine(b, "dev"); Git(b, "clone -q \"" + remote + "\" dev");
        File.Copy(dllNew, Path.Combine(dev, "starter", "bin", "Microsoft.Web.WebView2.Core.dll"), true); Put(Path.Combine(dev, "readme.txt"), "two");
        Git(dev, "add -A"); Git(dev, "commit -q -m two"); Git(dev, "push -q origin main");
        string oldSha = Hashing.Sha256File(dllOld), newSha = Hashing.Sha256File(dllNew);
        IntPtr h = LoadLibraryW(lib);
        Check(h != IntPtr.Zero, "(the stand-in library is loaded, so it cannot be overwritten)");
        try
        {
            UpdateResult r = GitUpdate.Pull(project, Path.Combine(project, "VGAC.exe"), new RealGit());
            string readme = File.ReadAllText(Path.Combine(project, "readme.txt"));
            bool allNew = readme == "two" && Hashing.Sha256File(lib) == newSha;
            bool allOld = readme == "one" && Hashing.Sha256File(lib) == oldSha && Git(project, "status --porcelain").Trim().Length == 0;
            Check(allNew || allOld, "E5: after a pull with a loaded library the tree is wholly new or wholly old (readme " + readme + ", library " + (Hashing.Sha256File(lib) == newSha ? "new" : "old") + "): " + r.Message);
            Check(!r.Updated || allNew, "E5: it says updated only when it is");
            if (!r.Updated) Check(allOld || !r.Message.Contains("Nothing of yours was changed"), "E5: and never claims nothing changed when something did");
        }
        finally { FreeLibrary(h); }
    }

    // ---- the sixth review --------------------------------------------------------------------------------------------------------

    class StopHere : Exception { }

    // A tiny stand-in for the real backend commands that, like the real ones, read and rewrite the ledger FILE (adoption) and refuse while
    // the ledger names another place. `Engine` is where its engine settings point.
    class LedgerAwareBackend
    {
        public string ProjectRoot, Engine; public int Adoptions;
        public string Run(string args, int ms, Func<bool> c)
        {
            string cmd = args.Split(' ')[0];
            var led = Ledger.Load(ProjectRoot);
            string root = Path.Combine(Layout.From(ProjectRoot, MachineFile.Load(ProjectRoot)).DataFolder, "comfyui");
            bool named = false; string oldPath = null;
            foreach (object o in led.Items) { var d = (Dictionary<string, object>)o; if (Convert.ToString(d["what"]).Contains("own ComfyUI")) { oldPath = Convert.ToString(d["path"]); named = SafeFs.Norm(oldPath) == SafeFs.Norm(root); } }
            if (cmd == "adopt")
            {
                if (named) return "{\"ok\": true, \"already\": true}";
                led.RewritePaths(oldPath, root); led.Save(); Adoptions++;
                Engine = Path.GetDirectoryName(root);                                // and the engine settings that named the old place follow it           // what the real command does to installed.json
                return "{\"ok\": true, \"adopted\": true}";
            }
            if (!named) return "{\"ok\": false, \"code\": \"adoptable\", \"reason\": \"The ledger still names its old place.\", \"adoptable\": true}";
            if (cmd == "retarget")
            {
                var parts = WindowsArgs("x " + args.Substring(cmd.Length));
                if (SafeFs.Norm(parts[2]) != SafeFs.Norm(Layout.From(ProjectRoot, MachineFile.Load(ProjectRoot)).DataFolder)) return "{\"ok\": false, \"code\": \"not_current\", \"reason\": \"The settings do not name that place.\"}";
                if (Engine != null && SafeFs.Norm(Engine) == SafeFs.Norm(parts[1])) Engine = parts[2];
                return "{\"ok\": true, \"changed\": 1}";
            }
            return "{\"ok\": true, \"keeps\": []}";
        }
    }

    static void TheSixthReviewsFixes()
    {
        // F1: a renamed install is adopted while the move starts; the runner's own ledger copy must not undo it
        var s = Movable();
        var gone = Path.Combine(Tmp("renamed-away"), "bigfiles");
        { var led = Ledger.Load(s.Root); led.RewritePaths(Path.Combine(s.Data, "comfyui"), Path.Combine(gone, "comfyui")); led.Save(); }
        var be = new LedgerAwareBackend { ProjectRoot = s.Root, Engine = gone };
        var comfy = new BackendComfyInstall(be.Run);
        var mv = new MoveRunner(s.L, MachineFile.Load(s.Root), Ledger.Load(s.Root), comfy, new NoProcs(), new SetupLog(s.Root));   // the ledger is loaded BEFORE the adoption
        string to = Path.Combine(Tmp("target"), "after-adopt");
        string said = mv.Start(to, new Progress());
        Check(said == null && be.Adoptions == 1, "the move with a renamed install goes through, adopting once (" + be.Adoptions + "): " + said);
        Check(MoveRunner.Load(s.Root) == null && MoveRules.RestartPending(s.Root), "all the way to the end (retarget was not refused as 'adoptable'), and a restart is needed");
        Check(SafeFs.Norm(Convert.ToString(((Dictionary<string, object>)Ledger.Load(s.Root).Items.Cast<object>().First(o => Convert.ToString(((Dictionary<string, object>)o)["what"]).Contains("own ComfyUI")))["path"])) == SafeFs.Norm(Path.Combine(to, "comfyui")), "the ledger names the new place");
        Check(SafeFs.Norm(be.Engine) == SafeFs.Norm(to), "and the engine settings are there too");

        // the same pattern, anywhere else: a copy of the settings or the ledger held across a write by somebody else never undoes it
        string root = NewProject();
        var held = MachineFile.Load(root); held.Set("starter", "a", "1");
        var other = MachineFile.Load(root); other.Set("backend", "engine", "E"); other.Save();      // e.g. the backend's own command
        held.Save();
        var after = MachineFile.Load(root);
        Check(after.GetString("backend", "engine") == "E" && after.GetString("starter", "a") == "1", "a settings copy saved late keeps what was written meanwhile");
        var lh = Ledger.Load(root); lh.Add("mine", Path.Combine(root, "x"), "added", 1);
        var lo = Ledger.Load(root); lo.Add("theirs", Path.Combine(root, "y"), "added", 1); lo.Save();
        lh.Save();
        var lf = Ledger.Load(root);
        Check(lf.Has(Path.Combine(root, "x"), "added") && lf.Has(Path.Combine(root, "y"), "added"), "a ledger copy saved late keeps the other writer's entries");
        lf.RemoveUnder(Path.Combine(root, "x")); lf.Save();
        Check(!Ledger.Load(root).Has(Path.Combine(root, "x"), null) && Ledger.Load(root).Has(Path.Combine(root, "y"), null), "and removing is replayed too");

        // F3: its check failed (timeout, refused, stopped): no ComfyUI item is planned, and the app's Python stays for the retry
        var r = Installed();
        r.Comfy.Problem = "The app's installer for ComfyUI did not finish within 1 minutes and was stopped.";
        var plan = RemovalPlanner.Build(r.L, r.M, r.Led, r.Comfy);
        Check(plan.Get("comfy").Items.Count == 0 && plan.ComfyUnconfirmed, "no ComfyUI item is planned when its installer did not confirm it");
        var res = RemovalRunner.Run(plan, new List<string> { "tools" }, null, r.L, MachineFile.Load(r.Root), r.Led, r.Comfy, new FakeRecycler(), new SetupLog(r.Root), null, null);
        Check(Directory.Exists(r.L.Venv) && Directory.Exists(r.L.PythonDir) && res.Skipped.Any(x => x.Contains("installer did not confirm")), "the Python stays, and the screen says why");
        Check(!Directory.Exists(r.L.NodeDir), "the rest of the group goes");
        // after a completed removal that kept only the models, nothing is left to confirm and the Python may go
        var r2 = Installed();
        RemovalRunner.Run(RemovalPlanner.Build(r2.L, r2.M, r2.Led, r2.Comfy), new List<string> { "comfy" }, null, r2.L, MachineFile.Load(r2.Root), r2.Led, r2.Comfy, new FakeRecycler(), new SetupLog(r2.Root), null, null);
        RemovalRunner.Run(RemovalPlanner.Build(r2.L, MachineFile.Load(r2.Root), r2.Led, r2.Comfy), new List<string> { "tools" }, null, r2.L, MachineFile.Load(r2.Root), r2.Led, r2.Comfy, new FakeRecycler(), new SetupLog(r2.Root), null, null);
        Check(!Directory.Exists(r2.L.Venv) && !Directory.Exists(r2.L.PythonDir), "a later removal of the rest, with only the kept models left in ComfyUI's folder, takes the Python too");

        // F4: the answer of `check` says when the environment is stale, and the starter keeps it
        var stale = new BackendComfyInstall(delegate(string a, int ms, Func<bool> c) { return "{\"ok\": true, \"keeps\": [], \"venv_stale\": true}"; });
        string cdata = Tmp("stale"); Put(Path.Combine(cdata, "comfyui", ComfyGuard.MarkerName), "{\"made_by\":\"VGAC\"}");
        Check(stale.State(cdata).VenvStale, "venv_stale in the answer reaches the screen");

        // F5: one command of the installer at a time, so Stop always reaches the running one
        string proot; var lay = ProjectWithPython(out proot);
        var slow = new ScriptedHost(); slow.HangMs = 4000; slow.Answer = delegate(string a) { return "{\"ok\": true}\r\n"; };
        string firstLine = null;
        var th = new Thread(delegate() { firstLine = ComfyCli.Run(slow, lay, proot, "rebuild-venv", 60000, null); });
        th.Start(); Thread.Sleep(400);
        var quick = new ScriptedHost(); quick.Answer = delegate(string a) { return "{\"ok\": true}\r\n"; };
        string secondLine = ComfyCli.Run(quick, lay, proot, "check", 5000, null);
        Check(secondLine != null && secondLine.Contains("\"busy\"") && !slow.Aborted, "a second command does not start beside a running one, and does not touch it");
        th.Join();
        Check(firstLine != null && firstLine.Contains("\"ok\": true"), "the first one finished on its own");

        // F6: one rule for %NAME% (the backend has the same test)
        Environment.SetEnvironmentVariable("VGAC_SOMETHING", "X:\\nope"); Environment.SetEnvironmentVariable("PYTHONHOME", "X:\\nope2"); Environment.SetEnvironmentVariable("VGACOK", "C:\\ok");
        Eq(Layout.ExpandSetting("%VGAC_SOMETHING%\\big"), "%VGAC_SOMETHING%\\big", "VGAC_ names are not expanded");
        Eq(Layout.ExpandSetting("%PYTHONHOME%\\big"), "%PYTHONHOME%\\big", "nor PYTHON*");
        Eq(Layout.ExpandSetting("%VGACOK%\\big"), "C:\\ok\\big", "an ordinary variable is");
        string real = Path.Combine(Tmp("pct"), "%VGACOK%"); Directory.CreateDirectory(real);
        Eq(Layout.ExpandSetting(real), real, "a folder that really has that name is never expanded");
        Environment.SetEnvironmentVariable("VGAC_SOMETHING", null); Environment.SetEnvironmentVariable("PYTHONHOME", null); Environment.SetEnvironmentVariable("VGACOK", null);

        // the usual shared folders of OneDrive
        string usersRoot = Path.GetDirectoryName(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile));
        Check(Proof.DataFolderProblem(Path.Combine(usersRoot, "Somebody Else", "OneDrive", "Documents"), root) != null, "another user's OneDrive\\Documents is refused");
        Check(Proof.DataFolderProblem(Path.Combine(usersRoot, "Somebody Else", "OneDrive - Company"), root) != null, "and their OneDrive folder");

    }

    // ---- the state machine, crashed after every write ----------------------------------------------------------------------------------
    //
    // Every write of a move (the journal, the settings file, the ledger, the engine settings, the marker, the files) is followed by a
    // point (SafeFs.Point), and the saves have one inside them too (the new text written, the old file not yet replaced). Each scenario is
    // run once to list its points, then again once per point with a crash right after it, and the next start recovers it: by Continue, by
    // Cancel and (after "Delete the old copy" began) by pressing Delete again. After recovery the settings, the ledger and the engine
    // settings must all name the same place, that place must hold the files and carry this install's marker, and the journal must be
    // gone or at its end. The points of "move" and "save" are all tried; the very many "copy" and "delete" points (one per file) are
    // sampled (at most 10 per scenario, spread over the whole run), because trying each of thousands would take hours and they all
    // lie inside one journalled step.

    static readonly string[] Scenarios = { "forward", "undo", "cancel-early", "undo-from-switching", "undo-from-switched", "cancel-part", "undo-refused", "deleteold", "forget", "stale" };
}
