// Tests for the one-dedicated-folder rule (third review). The folder for the big files is VGAC's only when its marker's id matches the
// id in local/machine.json. Temporary folders and stand-ins only; the stand-in host makes python and uv-cache the way the real uv does.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using Vgac.Starter;

public class FlakyHost : IHost
{
    public FakeHost Inner = new FakeHost();
    public bool FailPip { get { return Inner.FailPip; } set { Inner.FailPip = value; } }
    public void Download(string u, string d, string s, Action<long, long> p) { Inner.Download(u, d, s, p); }
    public void Unzip(string z, string d) { Inner.Unzip(z, d); }
    public int Run(string e, string a, string c, Dictionary<string, string> env, string log) { return Inner.Run(e, a, c, env, log); }
    public string Capture(string e, string a) { return Inner.Capture(e, a); }
    public long FreeBytes(string f) { return Inner.FreeBytes(f); }
}

public static partial class T
{
    static void Safety3All()
    {
        TheFolderIsClaimedBeforeAnyStep();
        InterruptedFirstSetupIsContinuedNotRefused();
        AMergedBuildInstallIsAdoptedByClickNotSilently();
        TheProjectFolderMovedByHandKeepsWorking();
        AdoptionNeedsTheRightShapesNotJustNames();
        IdMismatchMissingAndDamagedMarkersAreNotOurs();
        ACopiedDataFolderCountsOnlyWhereTheSettingsPoint();
        APersonsFilesBesideTheFixedNamesSurviveRemoveAndMove();
        ALibraryIsTouchedOnlyWhenDedicated();
        LeftoversAreRecoveredInsideTheClaimedFolderOnly();
        TheRestoreNoteGoesWhenVgacExeIsThere();
    }

    static byte[] Mz() { return FakeHost.Mz(); }

    // ---- claiming -----------------------------------------------------------------------------------------------------------

    static void TheFolderIsClaimedBeforeAnyStep()
    {
        string root = NewProject();
        var m = MachineFile.Load(root); var l = Layout.From(root, m);
        var h = new FlakyHost(); h.Inner.Free = 1L << 20;       // setup stops on room, right after the claim: no step ran
        try { new SetupRunner(l, h, m, Ledger.Load(root), new SetupLog(root)).Run(PlanOf(l, h.Inner), new Progress()); } catch (StarterError) { }
        Check(DataFolders.ReadMarkerId(l.DataFolder) != null, "the folder for the big files carries VGAC's marker before the first step (it was claimed up front)");
        string id = DataFolders.SettingsId(MachineFile.Load(root));
        Check(!string.IsNullOrEmpty(id) && id == DataFolders.ReadMarkerId(l.DataFolder), "and the same id is in the settings");
        Check(!Directory.Exists(Path.GetDirectoryName(l.UvExe)), "no step had run");
        // a crash between saving the id and writing the marker: the empty folder is simply finished, with the same id
        string root2 = NewProject();
        var m2 = MachineFile.Load(root2); m2.Set("starter", "dataFolderId", "abc123"); m2.Save();
        var l2 = Layout.From(root2, MachineFile.Load(root2));
        Directory.CreateDirectory(l2.DataFolder);
        DataFolders.Claim(l2.DataFolder, MachineFile.Load(root2), root2);
        Eq(DataFolders.ReadMarkerId(l2.DataFolder), "abc123", "a settings id without a marker in an empty folder is finished with that id, not replaced");
    }

    static void InterruptedFirstSetupIsContinuedNotRefused()
    {
        string root = NewProject();
        var m = MachineFile.Load(root); var l = Layout.From(root, m);
        var h = new FlakyHost(); h.FailPip = true;
        string why = null;
        try { new SetupRunner(l, h, m, Ledger.Load(root), new SetupLog(root)).Run(PlanOf(l, h.Inner), new Progress()); } catch (StarterError e) { why = e.Message; }
        Check(why != null && why.Contains("packages"), "(the first setup stopped when the packages failed: " + why + ")");
        Check(Directory.Exists(l.PythonDir) && Directory.Exists(l.UvCache) && File.Exists(l.VenvPython), "the app's Python, uv's cache and the venv are there, as the real tools leave them");
        Check(File.Exists(Path.Combine(l.UvCache, "CACHEDIR.TAG")), "(the stand-in makes uv-cache with its tag file)");
        // the next start
        var m2 = MachineFile.Load(root); var l2 = Layout.From(root, m2);
        h.FailPip = false;
        string again = null;
        try { new SetupRunner(l2, h, m2, Ledger.Load(root), new SetupLog(root)).Run(PlanOf(l2, h.Inner), new Progress()); } catch (StarterError e) { again = e.Message; }
        Check(again == null && File.Exists(l2.StatePath), "starting again continues and finishes (no 'files VGAC did not put there'): " + again);
        // the same when the person cancelled in the middle
        string root3 = NewProject();
        var m3 = MachineFile.Load(root3); var l3 = Layout.From(root3, m3);
        int says = 0; var prog = new Progress(); prog.Cancelled = delegate { return ++says > 3; };
        try { new SetupRunner(l3, new FlakyHost(), m3, Ledger.Load(root3), new SetupLog(root3)).Run(PlanOf(l3, new FakeHost()), prog); } catch (StarterError) { }
        var m4 = MachineFile.Load(root3); var l4 = Layout.From(root3, m4);
        string after = null;
        try { new SetupRunner(l4, new FlakyHost(), m4, Ledger.Load(root3), new SetupLog(root3)).Run(PlanOf(l4, new FakeHost()), new Progress()); } catch (StarterError e) { after = e.Message; }
        Check(after == null, "a setup that was cancelled is continued by the next start: " + after);
    }

    // ---- installs made by the merged build ---------------------------------------------------------------------------------------

    // The data folder as the merged build leaves it: no marker, no id in the settings, uv's Python and cache, tools, the window's data,
    // and a venv in the project whose pyvenv.cfg points into that Python.
    static InstallScene MergedBuild(bool customFolder)
    {
        var s = new InstallScene();
        s.Root = NewProject();
        s.Data = customFolder ? Path.Combine(Tmp("dev"), "bigfiles") : Path.Combine(s.Root, "local", "data");
        if (customFolder) { var m0 = MachineFile.Load(s.Root); m0.Set("starter", "dataFolder", s.Data); m0.Save(); }
        s.M = MachineFile.Load(s.Root); s.L = Layout.From(s.Root, s.M);
        var l = s.L;
        string home = FakeHost.MakeUvPython(l.PythonDir, "3.13.1");     // with uv's own .temp, .lock and .gitignore, as the real tool leaves it
        FakeHost.MakeUvCache(l.UvCache);
        Directory.CreateDirectory(Path.GetDirectoryName(l.UvExe)); File.WriteAllBytes(l.UvExe, Mz());
        Directory.CreateDirectory(l.NodeDir); File.WriteAllBytes(Path.Combine(l.NodeDir, "node.exe"), Mz());
        Directory.CreateDirectory(l.Downloads);
        Put(Path.Combine(s.Data, "webview2", "EBWebView", "Default", "a"), "w");
        Directory.CreateDirectory(Path.Combine(l.Venv, "Scripts")); File.WriteAllText(l.VenvPython, "py");
        File.WriteAllText(Path.Combine(l.Venv, "pyvenv.cfg"), "home = " + home + "\r\nversion = 3.13.1\r\n");
        Put(Path.Combine(l.FrontendDir, "dist", "index.html"), "<html>");
        // the stamps of a finished install
        var d = new Dictionary<string, object>();
        d["project"] = s.Root; d["requirements"] = Hashing.Sha256File(l.Requirements); d["lock"] = Hashing.Sha256File(l.Lock);
        d["source"] = ProbeReader.SourceHash(l.FrontendDir); d["dataFolder"] = s.Data;
        Directory.CreateDirectory(Path.GetDirectoryName(l.StatePath)); File.WriteAllText(l.StatePath, Json.Write(d));
        s.Led = Ledger.Load(s.Root);
        s.Led.Add("uv", Path.GetDirectoryName(l.UvExe), "added", 1); s.Led.Add("The app's Python environment", l.Venv, "added", 0); s.Led.Save();
        return s;
    }

    static void AMergedBuildInstallIsAdoptedByClickNotSilently()
    {
        foreach (bool custom in new[] { false, true })
        {
            var s = MergedBuild(custom);
            Check(PlanOf(s.L, new FakeHost()).Count == 0, "(the merged-build install needs nothing: " + (custom ? "custom folder" : "default folder") + ")");
            DataStatus st = DataFolders.Check(s.L.DataFolder, s.M, s.Root);
            Check(st.State == DataState.NotOurs && st.Adoptable, "without a marker it is not ours, but it can be adopted: " + st.Why);
            Check(st.AdoptList.Count == 5 && st.AdoptList.Any(x => x.StartsWith("python")) && st.AdoptList.Any(x => x.StartsWith("uv-cache")) && st.AdoptList.Any(x => x.StartsWith("tools")), "the list shows exactly what would be treated as VGAC's (" + st.AdoptList.Count + " entries)");
            // a requirements change: setup stops and offers the adoption, it does not claim silently and does not crash
            File.WriteAllText(s.L.Requirements, "fastapi==2\n");
            var steps = PlanOf(s.L, new FakeHost());
            Check(steps.Contains("packages"), "(the change makes a packages step)");
            NotClaimedError err = null;
            try { new SetupRunner(s.L, new FlakyHost(), s.M, s.Led, new SetupLog(s.Root)).Run(steps, new Progress()); } catch (NotClaimedError e) { err = e; }
            Check(err != null && err.Status.Adoptable && !err.Status.Problem, "setup stops with an adoptable not-claimed error (the screen then offers the choice)");
            Check(!File.Exists(Path.Combine(s.L.DataFolder, DataFolders.MarkerName)) && string.IsNullOrEmpty(DataFolders.SettingsId(MachineFile.Load(s.Root))), "nothing was claimed or written");
            // the person says yes
            DataFolders.Adopt(s.L.DataFolder, MachineFile.Load(s.Root), s.Root);
            var m = MachineFile.Load(s.Root);
            Check(DataFolders.Check(s.L.DataFolder, m, s.Root).State == DataState.Ours, "after the yes the folder is VGAC's");
            string why = null;
            try { new SetupRunner(s.L, new FlakyHost(), m, Ledger.Load(s.Root), new SetupLog(s.Root)).Run(steps, new Progress()); } catch (StarterError e) { why = e.Message; }
            Check(why == null, "and the same change now goes through: " + why);
        }
    }

    static void TheProjectFolderMovedByHandKeepsWorking()
    {
        string rootA = NewProject();
        var mA = MachineFile.Load(rootA); var lA = Layout.From(rootA, mA);
        var h = new FlakyHost();
        new SetupRunner(lA, h, mA, Ledger.Load(rootA), new SetupLog(rootA)).Run(PlanOf(lA, h.Inner), new Progress());
        string rootB = Path.Combine(Tmp("moved"), "VGAC copy");
        SafeFs.CopyTree(rootA, rootB, null, new FsReport());
        var mB = MachineFile.Load(rootB); var lB = Layout.From(rootB, mB);
        Check(lB.DataFolder.StartsWith(rootB), "(the default data folder is inside the project, so it moved with it)");
        Check(DataFolders.Check(lB.DataFolder, mB, rootB).State == DataState.Ours, "the moved data folder is still VGAC's: the id is in both the marker and the settings, no absolute path");
        var steps = PlanOf(lB, h.Inner);
        Check(steps.Contains("venv"), "(the Python environment has to be made again after a move)");
        string why = null;
        try { new SetupRunner(lB, h, mB, Ledger.Load(rootB), new SetupLog(rootB)).Run(steps, new Progress()); } catch (StarterError e) { why = e.Message; }
        Check(why == null, "setup in the moved project goes through without stopping: " + why);
        Check(MoveRules.NamesIn(lB.DataFolder).Contains("python") && MoveRules.NamesIn(lB.DataFolder).Contains("uv-cache"), "Move sees what is there");
        var plan = RemovalPlanner.Build(lB, MachineFile.Load(rootB), Ledger.Load(rootB), new StandInComfy());
        Check(plan.Get("tools").Items.Any(i => i.Path.StartsWith(lB.DataFolder)), "and Remove lists it");
        // the data folder renamed by hand, with the settings edited to match
        string renamed = lB.DataFolder + "-renamed";
        Directory.Move(lB.DataFolder, renamed);
        var m2 = MachineFile.Load(rootB); m2.Set("starter", "dataFolder", renamed); m2.Save();
        Check(DataFolders.Check(renamed, MachineFile.Load(rootB), rootB).State == DataState.Ours, "a data folder renamed by hand (settings edited) is still VGAC's");
    }

    // ---- adoption is strict ------------------------------------------------------------------------------------------------------

    static void AdoptionNeedsTheRightShapesNotJustNames()
    {
        string root = NewProject();
        string data = Path.Combine(Tmp("shapes"), "Dev");
        var m0 = MachineFile.Load(root); m0.Set("starter", "dataFolder", data); m0.Save();
        var m = MachineFile.Load(root); var l = Layout.From(root, m);
        // every fixed name, every one with the wrong contents
        Put(Path.Combine(data, "python", "cpython-3.13.1-windows-x86_64-none", "python.exe"), "not a program");   // venv does not point here
        Put(Path.Combine(data, "uv-cache", "stuff.bin"), "x");                                                      // no cache tag
        Put(Path.Combine(data, "downloads", "invoice.pdf"), "the person's");
        Put(Path.Combine(data, "tools", "uv", "readme.txt"), "no uv.exe");
        Put(Path.Combine(data, "webview2", "notes.txt"), "the person's");
        Put(Path.Combine(data, "comfyui", "main.py"), "their comfy");
        DataStatus st = DataFolders.Check(data, m, root);
        Check(st.State == DataState.NotOurs && !st.Adoptable && st.AdoptList.Count == 0, "names that are right with shapes that are wrong are not adoptable");
        foreach (string word in new[] { "python:", "uv-cache:", "downloads:", "tools:", "webview2:", "comfyui:" })
            Check(st.Why.Contains(word), "and the reason names " + word.TrimEnd(':'));
        NotClaimedError err = null;
        try { DataFolders.Adopt(data, m, root); } catch (NotClaimedError e) { err = e; }
        Check(err != null && !File.Exists(Path.Combine(data, DataFolders.MarkerName)), "Adopt refuses and writes nothing");
        err = null;
        try { new SetupRunner(l, new FlakyHost(), m, Ledger.Load(root), new SetupLog(root)).Run(new List<string> { "uv" }, new Progress()); } catch (NotClaimedError e) { err = e; }
        Check(err != null && !err.Status.Adoptable && err.Fix.Contains("another folder"), "setup stops and the screen offers another folder, not the adopt button");
        Check(File.ReadAllText(Path.Combine(data, "downloads", "invoice.pdf")) == "the person's" && File.Exists(Path.Combine(data, "webview2", "notes.txt")), "the person's files were not touched");
        // the Python folder is claimed only when VGAC's own Python environment points into it
        string root2 = NewProject(); string data2 = Path.Combine(Tmp("py"), "Dev");
        var q = MachineFile.Load(root2); q.Set("starter", "dataFolder", data2); q.Save();
        var l2 = Layout.From(root2, MachineFile.Load(root2));
        string home = Path.Combine(data2, "python", "cpython-3.13.1-windows-x86_64-none");
        Directory.CreateDirectory(home); File.WriteAllBytes(Path.Combine(home, "python.exe"), Mz());
        Check(!DataFolders.Check(data2, MachineFile.Load(root2), root2).Adoptable, "a Python folder with no VGAC environment using it is not adoptable");
        Directory.CreateDirectory(Path.Combine(l2.Venv, "Scripts")); File.WriteAllText(Path.Combine(l2.Venv, "pyvenv.cfg"), "home = C:\\\\somewhere else entirely\r\n");
        Check(!DataFolders.Check(data2, MachineFile.Load(root2), root2).Adoptable, "nor when the environment points somewhere else");
        File.WriteAllText(Path.Combine(l2.Venv, "pyvenv.cfg"), "home = " + home + "\r\n");
        Check(DataFolders.Check(data2, MachineFile.Load(root2), root2).Adoptable, "and it is, when VGAC's own environment uses it");
        Put(Path.Combine(data2, "uv-cache", "stuff.bin"), "x");   // a cache folder without uv's tag, next to the good Python
        Check(!DataFolders.Check(data2, MachineFile.Load(root2), root2).Adoptable, "one folder with the right shape next to one with the wrong shape is not adoptable either");
        // only hidden things or other things in the folder
        string data3 = Path.Combine(Tmp("hidden"), "Dev"); Put(Path.Combine(data3, ".git", "config"), "x");
        string root3 = NewProject(); var r3 = MachineFile.Load(root3); r3.Set("starter", "dataFolder", data3); r3.Save();
        DataStatus hid = DataFolders.Check(data3, MachineFile.Load(root3), root3);
        Check(hid.State == DataState.NotOurs && !hid.Adoptable && hid.Why.Contains("only hidden files"), "a folder holding only hidden files says so: " + hid.Why);
        string data4 = Path.Combine(Tmp("extra"), "Dev"); FakeHost.MakeUvCache(Path.Combine(data4, "uv-cache")); Put(Path.Combine(data4, "Saves", "a.sav"), "x");
        string root4 = NewProject(); var r4 = MachineFile.Load(root4); r4.Set("starter", "dataFolder", data4); r4.Save();
        DataStatus ex = DataFolders.Check(data4, MachineFile.Load(root4), root4);
        Check(!ex.Adoptable && ex.Why.Contains("Saves"), "a folder that also holds other things is not adoptable, and names them: " + ex.Why);
    }

    static void IdMismatchMissingAndDamagedMarkersAreNotOurs()
    {
        var s = Installed();
        string marker = Path.Combine(s.Data, DataFolders.MarkerName);
        Check(DataFolders.Check(s.Data, s.M, s.Root).State == DataState.Ours, "(the fixture's folder is ours)");
        // another id
        File.WriteAllText(marker, "{\"made_by\":\"VGAC\",\"id\":\"some-other-install\"}");
        DataStatus st = DataFolders.Check(s.Data, MachineFile.Load(s.Root), s.Root);
        Check(st.State == DataState.NotOurs && st.Why.Contains("does not match"), "a marker with another id is not ours: " + st.Why);
        var plan = RemovalPlanner.Build(s.L, MachineFile.Load(s.Root), s.Led, s.Comfy);
        Check(!plan.Groups.SelectMany(g => g.Items).Any(i => i.Kind == "data-folder") && plan.NotRemoved.Any(n => n.Contains("Nothing in it is removed")), "Remove lists nothing in it and says why");
        RemovalRunner.Run(plan, new List<string> { "tools" }, null, s.L, MachineFile.Load(s.Root), s.Led, s.Comfy, new FakeRecycler(), new SetupLog(s.Root), null, null);
        Check(Directory.Exists(s.L.PythonDir) && Directory.Exists(s.L.NodeDir), "so a removal run leaves it whole");
        var mv = Mover(s, null); string to = Path.Combine(Tmp("target"), "newbig");
        string said = mv.Start(to, new Progress());
        Check(said != null && said.Contains("does not match") && !Directory.Exists(to) && MoveRunner.Load(s.Root) == null, "Move refuses and starts nothing: " + said);
        // no marker at all
        File.Delete(marker);
        st = DataFolders.Check(s.Data, MachineFile.Load(s.Root), s.Root);
        Check(st.State == DataState.NotOurs && st.Why.Contains("no VGAC marker"), "a missing marker is not ours");
        Check(Mover(s, null).Start(to, new Progress()) != null && !Directory.Exists(to), "Move refuses");
        // a damaged marker
        File.WriteAllText(marker, "{ not json");
        Check(DataFolders.Check(s.Data, MachineFile.Load(s.Root), s.Root).State == DataState.NotOurs, "a damaged marker is not ours");
        // the settings lost the id
        DataFolders.Stamp(s.Data, MachineFile.Load(s.Root));
        var m = MachineFile.Load(s.Root); m.Set("starter", "dataFolderId", ""); m.Save();
        Check(DataFolders.Check(s.Data, MachineFile.Load(s.Root), s.Root).State == DataState.NotOurs, "a settings file without the id (or with another one) means not ours");
        // the repair check offers what can be done
        var lines = Repair.Check(s.L, new CaptureHost(), s.Led);
        Check(lines.First(c => c.Id == "data").Text.Contains("not shown to be VGAC's") || lines.First(c => c.Id == "data").Status == "fix", "the check tells the person");
    }

    static void ACopiedDataFolderCountsOnlyWhereTheSettingsPoint()
    {
        var s = Installed();
        string copy = Path.Combine(Tmp("copied"), "bigfiles-copy");
        SafeFs.CopyTree(s.Data, copy, null, new FsReport());
        Check(DataFolders.ReadMarkerId(copy) == DataFolders.SettingsId(s.M), "(the copy carries the same id)");
        var plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        Check(plan.Groups.SelectMany(g => g.Items).Where(i => i.Kind == "data-folder").All(i => i.Path.StartsWith(s.Data)), "Remove looks only at the folder the settings point at");
        RemovalRunner.Run(plan, new List<string> { "tools", "comfy" }, null, s.L, s.M, s.Led, s.Comfy, new FakeRecycler(), new SetupLog(s.Root), null, null);
        Check(!Directory.Exists(s.L.PythonDir) && Directory.Exists(Path.Combine(copy, "python")) && Directory.Exists(Path.Combine(copy, "tools", "uv")), "the copy is untouched");
        var s2 = Installed(); string copy2 = Path.Combine(Tmp("copied"), "copy2");
        SafeFs.CopyTree(s2.Data, copy2, null, new FsReport());
        string to = Path.Combine(Tmp("target"), "newbig");
        Check(Mover(s2, null).Start(to, new Progress()) == null && Directory.Exists(Path.Combine(copy2, "python")), "a move acts on the folder in the settings and leaves the copy alone");
    }

    static void APersonsFilesBesideTheFixedNamesSurviveRemoveAndMove()
    {
        var s = Movable();
        Put(Path.Combine(s.Data, "notes.txt"), "the person's notes"); Put(Path.Combine(s.Data, "MyStuff", "a.txt"), "the person's stuff");
        var plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        Check(plan.Notes.Any(n => n.Contains("Left for you") && n.Contains("notes.txt") && n.Contains("MyStuff")), "the summary names what is left for the person");
        string text = RemovalSummary.Text(plan, new List<string> { "tools" }, s.L);
        Check(text.Contains("removed whole") && text.Contains("Left for you"), "and says plainly that the fixed-name folders go whole");
        // Move first (a copy)
        string to = Path.Combine(Tmp("target"), "newbig");
        var mv = Mover(s, null);
        Check(mv.Start(to, new Progress()) == null, "the move runs");
        Check(!File.Exists(Path.Combine(to, "notes.txt")) && !Directory.Exists(Path.Combine(to, "MyStuff")), "the person's things are not copied");
        Check(File.Exists(Path.Combine(s.Data, "notes.txt")) && File.Exists(Path.Combine(s.Data, "MyStuff", "a.txt")) && Directory.Exists(s.Data), "the old folder, with the person's things, is left exactly as it was");
        // Remove in an untouched scene
        var s2 = Installed();
        Put(Path.Combine(s2.Data, "notes.txt"), "the person's notes"); Put(Path.Combine(s2.Data, "MyStuff", "a.txt"), "the person's stuff");
        var p2 = RemovalPlanner.Build(s2.L, s2.M, s2.Led, s2.Comfy);
        RemovalRunner.Run(p2, new List<string> { "tools", "comfy" }, null, s2.L, s2.M, s2.Led, s2.Comfy, new FakeRecycler(), new SetupLog(s2.Root), null, null);
        Check(!Directory.Exists(s2.L.PythonDir) && !Directory.Exists(s2.L.NodeDir), "Remove took the fixed-name folders");
        Check(File.Exists(Path.Combine(s2.Data, "notes.txt")) && File.Exists(Path.Combine(s2.Data, "MyStuff", "a.txt")), "and left the person's file and folder");
        Check(Directory.Exists(s2.Data), "and the folder for the big files stays while the person's things are in it");
    }

    // ---- N4 -------------------------------------------------------------------------------------------------------------------

    static void ALibraryIsTouchedOnlyWhenDedicated()
    {
        var s = Installed();
        string lib = Path.Combine(Tmp("libs"), "library");
        WriteSqlite(Path.Combine(lib, "vgac.sqlite")); Put(Path.Combine(lib, "projects", "mine", "a.txt"), "the person's own projects folder"); Put(Path.Combine(lib, "notes.txt"), "n");
        var m = MachineFile.Load(s.Root); m.Set("backend", "library_path", lib); m.Save();
        LibraryFolders.Stamp(lib, MachineFile.Load(s.Root));
        s.M = MachineFile.Load(s.Root);
        var plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        Check(plan.Get("work").Items.All(i => i.Kind != "work-library"), "a library folder holding a person's own 'projects' next to other files offers nothing");
        RemovalRunner.Run(plan, new List<string> { "work" }, "REMOVE", s.L, s.M, s.Led, s.Comfy, new FakeRecycler(), new SetupLog(s.Root), null, null);
        Check(File.Exists(Path.Combine(lib, "projects", "mine", "a.txt")) && File.Exists(Path.Combine(lib, "vgac.sqlite")), "so nothing in it is removed, even with the typed word, and not for good either");
        // a dedicated library: only the app's own names at its top
        string lib2 = Path.Combine(Tmp("libs"), "library2");
        WriteSqlite(Path.Combine(lib2, "vgac.sqlite")); Put(Path.Combine(lib2, "projects", "p1", "a.txt"), "p"); Put(Path.Combine(lib2, "prefs.json"), "{}");
        var m2 = MachineFile.Load(s.Root); m2.Set("backend", "library_path", lib2); m2.Save();
        LibraryFolders.Stamp(lib2, MachineFile.Load(s.Root));
        var plan2 = RemovalPlanner.Build(s.L, MachineFile.Load(s.Root), s.Led, s.Comfy);
        Check(plan2.Get("work").Items.Count(i => i.Kind == "work-library") == 3, "a dedicated library lists its own entries");
        RemovalRunner.Run(plan2, new List<string> { "work" }, "REMOVE", s.L, MachineFile.Load(s.Root), s.Led, s.Comfy, new FakeRecycler(), new SetupLog(s.Root), null, null);
        Check(!File.Exists(Path.Combine(lib2, "vgac.sqlite")) && !Directory.Exists(Path.Combine(lib2, "projects")) && Directory.Exists(lib2), "and removes them, but not the folder");
    }

    // ---- N3 and the minors ----------------------------------------------------------------------------------------------------------

    static void LeftoversAreRecoveredInsideTheClaimedFolderOnly()
    {
        string root = NewProject();
        var m = MachineFile.Load(root); var l = Layout.From(root, m);
        new SetupRunner(l, new FlakyHost(), m, Ledger.Load(root), new SetupLog(root)).Run(PlanOf(l, new FakeHost()), new Progress());
        // a cut-off tool swap: the old Node folder is aside and the new one is missing; no marker needs to say what it is
        Directory.Move(l.NodeDir, l.NodeDir + ".old");
        var notes = Recovery.AtStart(l, null);
        Check(File.Exists(Path.Combine(l.NodeDir, "node.exe")) && !Directory.Exists(l.NodeDir + ".old") && notes.Any(n => n.Contains("tool update")), "a cut-off tool swap is undone inside the claimed folder");
        // the same names in a folder that is not VGAC's are never touched
        string root2 = NewProject(); string other = Path.Combine(Tmp("notours"), "Dev");
        var q = MachineFile.Load(root2); q.Set("starter", "dataFolder", other); q.Save();
        var l2 = Layout.From(root2, MachineFile.Load(root2));
        Put(Path.Combine(other, "tools", "node.old", "x.txt"), "theirs"); Put(Path.Combine(other, "tools", "uv.new", "y.txt"), "theirs");
        Recovery.AtStart(l2, null);
        Check(File.Exists(Path.Combine(other, "tools", "node.old", "x.txt")) && File.Exists(Path.Combine(other, "tools", "uv.new", "y.txt")), "in an unclaimed folder they are left alone");
        Check(Repair.Leftovers(l2, false).Count == 0, "and not listed as leftovers");
    }

    // git stand-in whose checkout writes the file, as the real one does, and whose pull answers as told
    class WritingGit : IGit
    {
    public int RunToFile(string a, string c, string f) { return -1; }
        readonly string published; public int PullCode; public string[] Heads = { "aaa\n", "aaa\n" }; int n;
        public WritingGit(string publishedExe, int pullCode) { published = publishedExe; PullCode = pullCode; }
        public int Run(string a, string c, out string o)
        {
            o = "";
            if (a.StartsWith("rev-parse HEAD")) { o = Heads[Math.Min(n, Heads.Length - 1)]; n++; return 0; }
            if (a.StartsWith("checkout")) File.Copy(published, Path.Combine(c, "VGAC.exe"), true);
            if (a.StartsWith("pull")) { o = PullCode == -1 ? "git did not finish in time" : "error"; return PullCode; }
            return 0;
        }
    }

    static void TheRestoreNoteGoesWhenVgacExeIsThere()
    {
        string root = Tmp("note2"); Directory.CreateDirectory(Path.Combine(root, ".git"));
        string exe = Path.Combine(root, "VGAC.exe"); File.Copy(CmdExe, exe);
        var res = GitUpdate.Pull(root, exe, new WritingGit(PingExe, -1));
        Check(File.Exists(exe) && !File.Exists(ExeAside.RestoreNotePath(root)), "after a git timeout the note is gone because VGAC.exe is there: " + res.Message);
        string root2 = Tmp("note3"); Directory.CreateDirectory(Path.Combine(root2, ".git"));
        string exe2 = Path.Combine(root2, "VGAC.exe"); File.Copy(CmdExe, exe2);
        var part = new WritingGit(PingExe, 1); part.Heads = new[] { "aaa\n", "bbb\n" };
        GitUpdate.Pull(root2, exe2, part);
        Check(File.Exists(exe2) && !File.Exists(ExeAside.RestoreNotePath(root2)), "and after a pull that stopped partway");
        // but while VGAC.exe really is missing, the note stays
        string root4 = Tmp("note4"); string exe4 = Path.Combine(root4, "VGAC.exe"); File.Copy(PingExe, exe4);
        ExeAside.Aside(exe4, root4);
        Check(File.Exists(ExeAside.RestoreNotePath(root4)) && !File.Exists(exe4), "(while VGAC.exe is missing the note is there)");
        // a note left behind is cleaned at the next start
        string root3 = NewProject(); var m3 = MachineFile.Load(root3); var l3 = Layout.From(root3, m3);
        string e3 = Path.Combine(root3, "VGAC.exe"); File.Copy(CmdExe, e3);
        File.WriteAllText(ExeAside.RestoreNotePath(root3), "old note");
        Recovery.AtStart(l3, e3);
        Check(!File.Exists(ExeAside.RestoreNotePath(root3)), "a note left over is removed at the next start when nothing is missing");
    }
}
