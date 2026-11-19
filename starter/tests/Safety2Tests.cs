// Tests added after the second independent safety review of phase 9 (B1, S1 to S7, M1 to M3 and the wording). Each fails without the
// fix it belongs to. Temporary folders and stand-ins only.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using Vgac.Starter;

public class ScriptedGit : IGit
{
    public int RunToFile(string a, string c, string f) { Calls.Add(a); File.WriteAllText(f, "MZcommitted"); return 0; }
    public List<string> Calls = new List<string>();
    public int PullCode = 0; public string PullOutput = "";
    public string[] Heads = { "aaa\n", "aaa\n" };
    int headCalls;
    public int Run(string a, string c, out string o)
    {
        Calls.Add(a); o = "";
        if (a.StartsWith("rev-parse HEAD:") || a.StartsWith("hash-object")) { o = "same"; return 0; }
        if (a.StartsWith("rev-parse HEAD")) { o = Heads[Math.Min(headCalls, Heads.Length - 1)]; headCalls++; return 0; }
        if (a.StartsWith("pull")) { o = PullOutput; return PullCode; }
        return 0;
    }
}

public static partial class T
{
    static void Safety2All()
    {
        SetupChecksTheDataFolderAgain();
        SummarySaysWhatReallyHappensPerItem();
        PermanentDeletionOnlyForTheItemsNamed();
        LibraryIsOnlyWhatTheAppMade();
        HalfRemovedFolderIsNotWaivedForAReplacement();
        UpdateWordingAndOwnBuildAfterUpToDate();
        DataFolderIsJudgedByRealPath();
        MoveCopiesLinksInsideAFolderAndRefusesOutsideOnes();
    }

    // ---- B1 -----------------------------------------------------------------------------------------------------------------

    static InstallScene FreshInShared(out string shared)
    {
        var s = new InstallScene();
        s.Root = NewProject(); shared = Path.Combine(Tmp("dev"), "Dev");
        Directory.CreateDirectory(shared);
        var m = MachineFile.Load(s.Root); m.Set("starter", "dataFolder", shared); m.Save();
        s.M = MachineFile.Load(s.Root); s.L = Layout.From(s.Root, s.M); s.Led = Ledger.Load(s.Root);
        return s;
    }

    static string RunSetup(InstallScene s, params string[] steps)
    {
        try { new SetupRunner(s.L, new FakeHost(), s.M, s.Led, new SetupLog(s.Root)).Run(new List<string>(steps), new Progress()); return null; }
        catch (StarterError e) { return e.Message; }
    }

    static void SetupChecksTheDataFolderAgain()
    {
        var s = new InstallScene();
        s.Root = NewProject();
        var m = MachineFile.Load(s.Root); m.Set("starter", "dataFolder", Environment.GetFolderPath(Environment.SpecialFolder.UserProfile)); m.Save();
        s.M = MachineFile.Load(s.Root); s.L = Layout.From(s.Root, s.M); s.Led = Ledger.Load(s.Root);
        string why = RunSetup(s, "uv");
        Check(why != null && why.Contains("not one VGAC will set up in") && !Directory.Exists(Path.Combine(s.L.DataFolder, "tools", "uv")) && !File.Exists(Path.Combine(s.L.DataFolder, DataFolders.MarkerName)), "a data folder setting that names the profile folder stops setup before anything is made, and nothing is claimed: " + why);
        var lines = Repair.Check(s.L, new CaptureHost(), s.Led);
        Check(lines.First(c => c.Id == "data").Text.Contains("not one VGAC will set up"), "and the check says so at every start");
    }

    static void SummarySaysWhatReallyHappensPerItem()
    {
        var s = Installed();
        string outside = Tmp("outside-lib"); Put(Path.Combine(outside, "precious.txt"), "keep");
        Put(Path.Combine(s.Root, "library", "projects", "p1", "a.txt"), "p");
        Junction(Path.Combine(s.Root, "library", "projects", "lnk"), outside);
        var plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        var g = plan.Get("work");
        var proj = g.Items.First(i => i.Path.EndsWith("projects")); var db = g.Items.First(i => i.Path.EndsWith("vgac.sqlite"));
        Check(!RemovalRules.ItemUsesBin(g, proj) && RemovalRules.ItemUsesBin(g, db), "a folder with a link inside is not for the bin, a plain file is");
        string text = RemovalSummary.Text(plan, new List<string> { "work" }, s.L);
        Check(text.Contains("[deleted for good]") && text.Contains("[Recycle Bin]") && text.Contains("partly"), "the summary says which item goes which way");
        var rec = new FakeRecycler();
        RemovalRunner.Run(plan, new List<string> { "work" }, "REMOVE", s.L, s.M, s.Led, s.Comfy, rec, new SetupLog(s.Root), null, null);
        Check(!rec.Sent.Contains(proj.Path) && rec.Sent.Contains(db.Path), "and that is exactly what was done");
        Check(File.Exists(Path.Combine(outside, "precious.txt")) && !Directory.Exists(proj.Path), "the link was unlinked, its target kept");
    }

    static void PermanentDeletionOnlyForTheItemsNamed()
    {
        var s = Installed();
        var rec = new FakeRecycler(); rec.Refuse = true;
        var plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        var first = RemovalRunner.Run(plan, new List<string> { "tools" }, null, s.L, s.M, s.Led, s.Comfy, rec, new SetupLog(s.Root), null, null);
        Check(first.RecycleFailed.Count >= 3, "several items were refused by the bin (" + first.RecycleFailed.Count + ")");
        string one = first.RecycleFailed.First(p => p.EndsWith("downloads"));
        var second = RemovalRunner.Run(RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy), new List<string> { "tools" }, null, s.L, s.M, s.Led, s.Comfy, rec, new SetupLog(s.Root), null, new List<string> { one });
        Check(!Directory.Exists(one), "the item the person agreed to is deleted for good");
        Check(Directory.Exists(s.L.PythonDir) && Directory.Exists(s.L.NodeDir) && second.RecycleFailed.Count == first.RecycleFailed.Count - 1, "every other item is still there and still waiting for its own answer");
        // the permission is for the exact path only
        var third = RemovalRunner.Run(RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy), new List<string> { "tools" }, null, s.L, s.M, s.Led, s.Comfy, rec, new SetupLog(s.Root), null, new List<string> { Path.Combine(s.L.PythonDir, "..", "nothing") });
        Check(Directory.Exists(s.L.PythonDir), "a path that is not an item's own path permits nothing");
    }

    static void LibraryIsOnlyWhatTheAppMade()
    {
        var s = Installed();
        // a vgac.sqlite that is not a database does not make a library
        string notDb = Path.Combine(Tmp("fakelib"), "Games");
        Put(Path.Combine(notDb, "vgac.sqlite"), "just some text, not a database"); Put(Path.Combine(notDb, "Saves", "slot1.sav"), "the person's saves");
        var m = MachineFile.Load(s.Root); m.Set("backend", "library_path", notDb); m.Save();
        var plan = RemovalPlanner.Build(s.L, MachineFile.Load(s.Root), s.Led, s.Comfy);
        Check(plan.NotRemoved.Any(n => n.Contains("not an SQLite database")) && plan.Get("work").Items.All(i => i.Kind != "work-library"), "a file named vgac.sqlite that is not an SQLite database does not make a VGAC library");
        // a broad folder that holds the app's database: only what the app made is removed
        string broad = Path.Combine(Tmp("broad"), "My Games");
        WriteSqlite(Path.Combine(broad, "vgac.sqlite")); Put(Path.Combine(broad, "projects", "p1", "a.txt"), "p"); Put(Path.Combine(broad, "prefs.json"), "{}");
        Put(Path.Combine(broad, "Saves", "slot1.sav"), "the person's saves"); Put(Path.Combine(broad, "photos", "me.jpg"), "photo"); Put(Path.Combine(broad, "notes.txt"), "notes");
        m = MachineFile.Load(s.Root); m.Set("backend", "library_path", broad); m.Save();
        LibraryFolders.Stamp(broad, MachineFile.Load(s.Root));      // claimed, and the person's own things are in it as well
        s.M = MachineFile.Load(s.Root);
        plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
Check(plan.Get("work").Items.All(i => i.Kind != "work-library") && plan.NotRemoved.Any(n => n.Contains("My Games") && n.Contains("Saves") && n.Contains("nothing in it is removed")), "a library folder that also holds the person's own things is not touched at all, and the summary says so");
        RemovalRunner.Run(plan, new List<string> { "work" }, "REMOVE", s.L, s.M, s.Led, s.Comfy, new FakeRecycler(), new SetupLog(s.Root), null, null);
        Check(File.Exists(Path.Combine(broad, "vgac.sqlite")) && Directory.Exists(Path.Combine(broad, "projects")) && File.Exists(Path.Combine(broad, "Saves", "slot1.sav")) && File.Exists(Path.Combine(broad, "notes.txt")), "so with the typed word nothing in it goes (not even the app's own files)");
        // a name the app does not make is refused at the moment of deleting too
        var item = new RemovalItem(); item.Kind = "work-library"; item.Path = Path.Combine(broad, "notes.txt"); item.GroupId = "work";
        Check(RemovalPlanner.Revalidate(item, s.L, s.Comfy, s.Led, s.M) != null, "an entry the app does not make is refused again just before deleting");
    }

    static void HalfRemovedFolderIsNotWaivedForAReplacement()
    {
        // the app's Python environment in the project (the one kind of folder that is judged by its shape, not by the data folder's marker)
        var s = Installed();
        for (int i = 0; i < 80; i++) Put(Path.Combine(s.L.Venv, "Lib", "f" + i + ".py"), "x");
        var plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        int n = 0;
        SafeFs.Hook = delegate(string what, string p) { if (what == "delete" && p.Contains(".venv") && ++n == 30) throw new Boom("cut"); };
        try { RemovalRunner.Run(plan, new List<string> { "tools" }, null, s.L, s.M, s.Led, s.Comfy, new FakeRecycler(), new SetupLog(s.Root), null, null); } catch (Boom) { }
        SafeFs.Hook = null;
        Check(RemovalRunner.InProgress(s.Root).Any(p => p.EndsWith(".venv")), "the cut-off folder is remembered together with which folder it was");
        var resumed = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        Check(resumed.Get("tools").Items.Any(i => i.Path == s.L.Venv), "the half-deleted folder is still the same folder and is listed to finish");
        // the person deletes what is left and makes their own environment with the same name
        SafeFs.DeleteTree(s.L.Venv); Thread.Sleep(30);
        Put(Path.Combine(s.L.Venv, "pyvenv.cfg"), "home = their own python");
        var after = RemovalPlanner.Build(s.L, s.M, Ledger.Load(s.Root), s.Comfy);
        Check(!after.Get("tools").Items.Any(i => i.Path == s.L.Venv), "a new folder of that name is not waived by the old journal");
    }

    // ---- S4 -------------------------------------------------------------------------------------------------------------------

    static void UpdateWordingAndOwnBuildAfterUpToDate()
    {
        // the note that says what to do if the computer dies mid-update
        string root = Tmp("note"); string exe = Path.Combine(root, "VGAC.exe"); File.Copy(PingExe, exe);
        ExeAside.Aside(exe, root);
        Check(File.Exists(ExeAside.RestoreNotePath(root)) && File.ReadAllText(ExeAside.RestoreNotePath(root)).Contains("Rename VGAC.old.exe to VGAC.exe"), "while VGAC.exe is aside, a note beside the project says plainly how to put it back");
        ExeAside.Restore(exe, root);
        Check(!File.Exists(ExeAside.RestoreNotePath(root)) && File.Exists(exe), "it is gone once VGAC.exe is back");

        // killed git: not 'nothing changed'
        string r2 = Tmp("killed"); Directory.CreateDirectory(Path.Combine(r2, ".git"));
        string e2 = Path.Combine(r2, "VGAC.exe"); File.Copy(CmdExe, e2);
        var killed = new ScriptedGit(); killed.PullCode = -1; killed.PullOutput = "git did not finish in time";
        var res = GitUpdate.Pull(r2, e2, killed);
        Check(res.Message.Contains("took too long") && res.Message.Contains("git status") && !res.Message.Contains("Nothing of yours was changed"), "a git that was stopped is never reported as 'nothing changed': " + res.Message);
        // stopped partway
        string r3 = Tmp("partway"); Directory.CreateDirectory(Path.Combine(r3, ".git"));
        string e3 = Path.Combine(r3, "VGAC.exe"); File.Copy(CmdExe, e3);
        var part = new ScriptedGit(); part.PullCode = 1; part.PullOutput = "error: could not write file"; part.Heads = new[] { "aaa\n", "bbb\n" };
        res = GitUpdate.Pull(r3, e3, part);
        Check(res.Message.Contains("stopped partway") && !res.Message.Contains("Nothing of yours was changed"), "a pull that moved the branch before failing says so: " + res.Message);
        // an own build after 'already up to date': it is back, byte for byte, with nothing aside
        string r4 = Tmp("uptodate"); Directory.CreateDirectory(Path.Combine(r4, ".git"));
        string e4 = Path.Combine(r4, "VGAC.exe"); File.Copy(CmdExe, e4); string own = Hashing.Sha256File(e4);
        Put(Path.Combine(r4, "starter", "CHECKSUMS.txt"), new string('c', 64) + "  VGAC.exe\n");
        var same = new ScriptedGit(); same.Heads = new[] { "aaa\n", "aaa\n" };
        // git "checkout" in this stand-in does not write a file, so make the working copy what a checkout would: the published one
        var g = new CheckoutWritesGit(PingExe);
        res = GitUpdate.Pull(r4, e4, g);
        Check(!res.Updated && res.Message.Contains("up to date"), "(up to date)");
        Check(File.Exists(e4) && Hashing.Sha256File(e4) == own && !File.Exists(ExeAside.AsidePath(e4)) && !File.Exists(ExeAside.RestoreNotePath(r4)), "an own build is back under VGAC.exe after 'already up to date', with no leftover and no note");
    }

    // A stand-in for git whose "checkout -- VGAC.exe" writes the published file, as the real one does.
    class CheckoutWritesGit : IGit
    {
    public int RunToFile(string a, string c, string f) { File.Copy(published, f, true); return 0; }
        readonly string published;
        public CheckoutWritesGit(string publishedExe) { published = publishedExe; }
        public int Run(string a, string c, out string o)
        {
            if (a.StartsWith("rev-parse HEAD:") || a.StartsWith("hash-object")) { o = "same"; return 0; }
            o = a.StartsWith("rev-parse HEAD") ? "aaa\n" : "";
            if (a.StartsWith("checkout")) File.Copy(published, Path.Combine(c, "VGAC.exe"), true);
            return 0;
        }
    }

    // ---- S5, S6, S7, M3 ------------------------------------------------------------------------------------------------------

    static void DataFolderIsJudgedByRealPath()
    {
        string root = NewProject();
        string temp = Path.GetTempPath();
        string profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        string j = Tmp("junctions");
        Junction(Path.Combine(j, "to-drive"), Path.GetPathRoot(temp));
        Check(Proof.DataFolderProblem(Path.Combine(j, "to-drive"), root) != null, "a junction to a drive root is refused like the drive");
        Junction(Path.Combine(j, "to-profile"), profile);
        Check(Proof.DataFolderProblem(Path.Combine(j, "to-profile"), root) != null, "a junction to the profile folder is refused");
        string pf = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        Check(Proof.DataFolderProblem(Path.Combine(pf, "VGAC"), root) != null, "a folder inside Program Files is refused");
        string shortPf = ShortPath(pf);
        if (shortPf != null && !string.Equals(shortPf, pf, StringComparison.OrdinalIgnoreCase)) Check(Proof.DataFolderProblem(shortPf, root) != null, "Program Files by its short (8.3) name is refused (" + shortPf + ")");
        string pub = Environment.GetEnvironmentVariable("PUBLIC");
        if (!string.IsNullOrEmpty(pub) && Directory.Exists(pub)) Check(Proof.DataFolderProblem(pub, root) != null, "the Public folder is refused");
        string users = Path.GetDirectoryName(profile);
        Check(Proof.DataFolderProblem(users, root) != null, "the folder that holds every profile is refused");
        foreach (string other in Directory.GetDirectories(users))
            if (!string.Equals(other, profile, StringComparison.OrdinalIgnoreCase)) Check(Proof.DataFolderProblem(other, root) != null, "another user's profile folder is refused (" + Path.GetFileName(other) + ")");
        Check(Proof.DataFolderProblem(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), root) != null, "Documents as the system knows it (also when moved to OneDrive) is refused");
        Check(Proof.DataFolderProblem(Environment.GetFolderPath(Environment.SpecialFolder.Windows), root) != null, "Windows is refused");
        string gone = null; foreach (char ch in "QRSTUVWXYZ") if (!Directory.Exists(ch + ":\\")) { gone = ch + ":\\x"; break; }
        if (gone != null) Check(Proof.DataFolderProblem(gone, root) != null, "a place that cannot be resolved is refused, not guessed");
        Check(Proof.DataFolderProblem(Path.Combine(Tmp("ok"), "VGAC data"), root) == null, "an ordinary new folder is still fine");
    }

    [System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    static extern uint GetShortPathNameW(string longPath, StringBuilder shortPath, uint len);

    static string ShortPath(string p)
    {
        var sb = new StringBuilder(260);
        return GetShortPathNameW(p, sb, 260) > 0 ? sb.ToString() : null;
    }

    static void MoveCopiesLinksInsideAFolderAndRefusesOutsideOnes()
    {
        // uv keeps links between its Python versions inside the python folder: a move must not refuse every time
        var s = Movable(); string to = Path.Combine(Tmp("target"), "newbig");
        string py = Path.Combine(s.Data, "python");
        Put(Path.Combine(py, "cpython-3.13.1-windows", "python.exe"), "py");
        Junction(Path.Combine(py, "cpython-3.13-windows"), Path.Combine(py, "cpython-3.13.1-windows"));
        Check(SafeFs.FirstExternalLink(py, null) == null, "a link to another place inside the same folder is not an outside link");
        string went = Mover(s, null).Start(to, new Progress());
        Check(went == null, "so the move goes through: " + went);
        string newLink = Path.Combine(to, "python", "cpython-3.13-windows");
        Check(SafeFs.IsLink(newLink) && SafeFs.Real(newLink) == SafeFs.Real(Path.Combine(to, "python", "cpython-3.13.1-windows")), "the link is made again in the copy and leads to the copy's own folder, not the old place");
        Check(File.Exists(Path.Combine(py, "cpython-3.13.1-windows", "python.exe")), "the old place is untouched");
        // an outside link in the same folder still refuses the whole move
        var s2 = Movable(); string to2 = Path.Combine(Tmp("target"), "newbig");
        string outside = Tmp("elsewhere"); Put(Path.Combine(outside, "big.txt"), "x");
        Junction(Path.Combine(s2.Data, "python", "outside-link"), outside);
        string why = Mover(s2, null).Start(to2, new Progress());
        Check(why != null && why.Contains("leads outside") && !Directory.Exists(to2), "a link that leads outside the folder still stops the move: " + why);
    }
}
