// Tests for the fourth review: real uv shapes, keeping the claim on Choose, a Move into the project stored relative, the library's own
// marker, custom_nodes kept, Continue only when the settings fit, safe defaults, no ledger entries inside the data folder.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
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
        ContinueNeedsTheSettingsToFitTheJournal();
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
        // cancelling after the switch points back, also relative when the old place lies inside the project
        var s2 = Movable();
        Check(Mover(s2, null).Start(Path.Combine(s2.Root, "local", "big2"), new Progress()) == null, "(a second move)");
        Mover(s2, null).Cancel();
        Eq(Path.GetFullPath(Layout.From(s2.Root, MachineFile.Load(s2.Root)).DataFolder).TrimEnd('\\').ToLowerInvariant(), s2.Data.TrimEnd('\\').ToLowerInvariant(), "an undo points back at the old place");
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
        Eq(string.Join(",", s.Comfy.Calls.ToArray()), "check,check,retarget,rebuild", "asked before copying and again before the first file, then retarget, then rebuild");
        Check(File.Exists(Path.Combine(to, "comfyui", ComfyGuard.MarkerName)) && Eq2(MoveRunner.Load(s.Root).State, "rebuilt"), "the install record came along and the move is at its end");
        // until a command succeeds: the move is refused before anything is copied
        var s2 = Movable(); string to2 = Path.Combine(Tmp("target"), "newbig2");
        s2.Comfy.Problem = "The app's installer for ComfyUI did not answer (is the app's Python there?).";
        string said = Mover(s2, null).Start(to2, new Progress());
        Check(said != null && said.Contains("did not answer") && !said.Contains("older version") && !Directory.Exists(to2) && MoveRunner.Load(s2.Root) == null, "refused, nothing copied, no journal, and the reason is the backend's: " + said);
        // retarget refused: the move stops at 'switched' with the reason; Continue tries again, and rebuild is not asked before retarget worked
        var s3 = Movable(); string to3 = Path.Combine(Tmp("target"), "newbig3");
        s3.Comfy.RetargetError = "The settings could not be updated.";
        said = Mover(s3, null).Start(to3, new Progress());
        Check(said != null && said.Contains("The settings could not be updated") && !s3.Comfy.Calls.Contains("rebuild"), "a refused retarget is reported and rebuild is not asked: " + said);
        Eq(MoveRunner.Load(s3.Root).State, "switched", "the move waits at 'switched'");
        s3.Comfy.RetargetError = null;
        Check(Mover(s3, null).Continue(new Progress()) == null && Eq2(MoveRunner.Load(s3.Root).State, "rebuilt"), "Continue finishes it once the backend agrees");
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

    static void ContinueNeedsTheSettingsToFitTheJournal()
    {
        foreach (string state in new[] { "copying", "copied", "verified" })
        {
            var s = Movable();
            string to = Path.Combine(Tmp("target"), "inuse-" + state);
            Put(Path.Combine(to, "comfyui", "models", "precious.bin"), "the person's model");
            Put(Path.Combine(to, "python", "p.bin"), "p");
            // a hand-edited record: the settings already use the new place, the record says the copy is not done
            var m = MachineFile.Load(s.Root); m.Set("starter", "dataFolder", to); m.Save();
            var d = new Dictionary<string, object>();
            d["from"] = s.Data; d["to"] = to; d["state"] = state; d["done"] = new List<string>(); d["created"] = new List<string> { "comfyui", "python" };
            Directory.CreateDirectory(Path.Combine(s.Root, "local"));
            File.WriteAllText(MoveRunner.JournalPath(s.Root), Json.Write(d));
            string said = Mover(s, null).Continue(new Progress());
            Check(File.Exists(Path.Combine(to, "comfyui", "models", "precious.bin")) && File.Exists(Path.Combine(to, "python", "p.bin")), "state " + state + ": the folder in use was not touched (" + said + ")");
            Check(MoveRunner.Load(s.Root, to) == null, "state " + state + ": the record is set aside");
        }
        // and the right way round still works: settings on the old place, before the switch
        var ok = Movable(); string to2 = Path.Combine(Tmp("target"), "newbig");
        SafeFs.Hook = delegate(string what, string p) { if (what == "move" && p == "before-rename:python") throw new Boom("stop"); };
        try { Mover(ok, null).Start(to2, new Progress()); } catch (Boom) { }
        SafeFs.Hook = null;
        Check(MoveRunner.Load(ok.Root, ok.Data) != null, "(an interrupted copy is a journal that fits)");
        Check(Mover(ok, null).Continue(new Progress()) == null && Directory.Exists(Path.Combine(to2, "python")), "and Continue finishes it");
        // after the switch the settings must be on the new place
        var sw = Movable(); string to3 = Path.Combine(Tmp("target"), "after");
        Check(Mover(sw, null).Start(to3, new Progress()) == null, "(a finished move)");
        var back = MachineFile.Load(sw.Root); back.Set("starter", "dataFolder", sw.Data); back.Save();
        Check(MoveRunner.Load(sw.Root, sw.Data) == null, "a finished-move record whose settings point at the old place is not acted on");
    }

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
        Check(Regex.Matches(text, @"MessageBoxDefaultButton \w+ = MessageBoxDefaultButton\.Button2;").Count == 5 && !text.Contains("= MessageBoxDefaultButton.Button1"), "and each of those defaults is the second button, No");
        foreach (string name in new[] { "DeleteOldCopy", "CancelMove", "Adopt", "PermanentDelete", "RunSteps" })
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
}
