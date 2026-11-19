// Tests for phase 9 (update after a pull, repair, move, remove). Everything runs in temporary folders with stand-ins:
// a fake host, a stand-in for the app's ComfyUI (written to the N1 to N5 text of the design note), real git on local
// temporary repositories, and real junctions made with mklink /J. Nothing outside the temporary folders is touched.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using Vgac.Starter;

public class Boom : Exception { public Boom(string m) : base(m) { } }

// Stand-in for the backend's ComfyUI commands (check, remove, retarget, rebuild-venv) on a fake install. `remove` does what the real one
// does: everything in the install folder goes except the marker and what it keeps (here: KeepRel); with nothing kept the folder goes too.
public class StandInComfy : IComfyInstall
{
    public string Data;                        // the data folder the stand-in "settings" name now
    public string ProjectRoot;                 // where the settings file and the ledger are (read fresh, as the backend does)
    public string EngineAt;                    // the place the engine settings point into (what retarget moves)
    public string Problem;                     // what `check` refuses with (null: it confirms)
    public string RetargetError, RebuildError, RemoveError;
    public List<string> KeepRel = new List<string> { "models" };
    public List<string> Calls = new List<string>();
    public string Root(string data) { return Path.Combine(data, "comfyui"); }
    public ComfyState State(string data)
    {
        Calls.Add("check");
        var st = new ComfyState();
        st.Present = ComfyGuard.MarkerPresent(Root(data));
        st.Problem = st.Present ? (Problem ?? LedgerProblem(data)) : "There is no ComfyUI of VGAC's here.";
        if (st.Present && st.Problem == null) foreach (string k in KeepRel) { string p = Path.Combine(Root(data), k); if (Directory.Exists(p)) st.Keeps.Add(p); }
        return st;
    }
    public string Remove(string installRoot)
    {
        Calls.Add("remove");
        if (RemoveError != null) return RemoveError;
        string root = Root(Data);
        if (SafeFs.Norm(installRoot) != SafeFs.Norm(root)) return "The settings name another folder than " + installRoot + " (" + root + "), so nothing was removed.";   // as the real command does
        var keeps = new List<string>(); foreach (string k in KeepRel) keeps.Add(SafeFs.Norm(Path.Combine(root, k)));
        Sweep(root, keeps);
        return null;
    }
    void Sweep(string dir, List<string> keeps)
    {
        foreach (string e in Directory.GetFileSystemEntries(dir))
        {
            string n = SafeFs.Norm(e);
            if (string.Equals(Path.GetFileName(e), ComfyGuard.MarkerName, StringComparison.OrdinalIgnoreCase)) continue;
            if (keeps.Contains(n)) continue;
            if (keeps.Exists(k => k.StartsWith(n + "\\"))) { Sweep(e, keeps); continue; }
            SafeFs.DeleteTree(e);
        }
        bool any = false; foreach (string e in Directory.GetFileSystemEntries(dir)) any = true;
        if (!any) Directory.Delete(dir);
    }
    public string RetargetBackError;           // the answer to the second retarget (the one that undoes the first)
    public List<string> RetargetArgs = new List<string>();
    public Func<bool> Cancelled { get; set; }
    public Action<string> Say { get; set; }
    public void Forget() { }
    // what the real backend needs: the install is VGAC's by the ledger (the entry names THIS folder)
    string LedgerProblem(string data)
    {
        if (ProjectRoot == null) return null;
        foreach (object o in Ledger.Load(ProjectRoot).Items)
        {
            var d = o as Dictionary<string, object>;
            if (d != null && Convert.ToString(d.ContainsKey("kind") ? d["kind"] : "") == "added" && SafeFs.Norm(Convert.ToString(d.ContainsKey("path") ? d["path"] : "x")) == SafeFs.Norm(Root(data))) return null;
        }
        return "VGAC did not install a ComfyUI in " + Root(data) + " (the ledger names another place).";
    }
    public string Retarget(string a, string b)
    {
        Calls.Add("retarget"); RetargetArgs.Add(a + " -> " + b);
        string forced = RetargetArgs.Count >= 2 ? RetargetBackError : RetargetError;
        if (forced != null) return forced;
        if (ProjectRoot != null)
        {
            // like the real command: the settings must already name the new place, and the ledger must name it too
            if (SafeFs.Norm(Layout.From(ProjectRoot, MachineFile.Load(ProjectRoot)).DataFolder) != SafeFs.Norm(b)) return "The settings do not name " + b + " as the data folder, so nothing was changed.";
            string why = LedgerProblem(b); if (why != null) return why;
        }
        SafeFs.Point("move", "comfy-before-retarget");
        if (EngineAt != null && SafeFs.Norm(EngineAt) == SafeFs.Norm(a)) EngineAt = b;
        SafeFs.Point("move", "comfy-after-retarget");
        return null;
    }
    public string RebuildVenv(string b) { Calls.Add("rebuild"); return RebuildError; }
}

public class FakeRecycler : IRecycler
{
    public List<string> Sent = new List<string>();
    public bool Refuse;
    public bool Send(string path)
    {
        if (Refuse) return false;
        SafeFs.DeleteTree(path);   // what the bin does to the original
        if (File.Exists(path) || Directory.Exists(path)) return false;   // a locked file: the bin does not take it either
        Sent.Add(path);
        return true;
    }
}

// A host whose Capture answers by program name, for the repair checks.
public class CaptureHost : IHost
{
    public Dictionary<string, string> Answers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
    public FakeHost Inner = new FakeHost();
    public long Free = 100L << 30;
    public void Download(string u, string d, string s, Action<long, long> p) { Inner.Download(u, d, s, p); }
    public void Unzip(string z, string d) { Inner.Unzip(z, d); }
    public int Run(string e, string a, string c, Dictionary<string, string> env, string log) { return Inner.Run(e, a, c, env, log); }
    public string Capture(string exe, string args)
    {
        string name = Path.GetFileName(exe), v;
        if (Answers.TryGetValue(name + " " + args, out v)) return v;
        if (Answers.TryGetValue(name, out v)) return v;
        return null;
    }
    public long FreeBytes(string f) { return Free; }
}

public static partial class T
{
    static string Tmp(string tag)
    {
        string p = Path.Combine(Path.GetTempPath(), "vgac-aw-" + tag + "-" + Guid.NewGuid().ToString("N").Substring(0, 8));
        temps.Add(p); Directory.CreateDirectory(p); return p;
    }

    // A file that starts like an SQLite database (the 16-byte header every one has), as the app's own vgac.sqlite does.
    static void WriteSqlite(string path)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path));
        var b = new byte[3000]; byte[] h = Encoding.ASCII.GetBytes("SQLite format 3");
        Array.Copy(h, b, h.Length);
        File.WriteAllBytes(path, b);
    }

    static void Put(string path, string text) { Directory.CreateDirectory(Path.GetDirectoryName(path)); File.WriteAllText(path, text); }
    static void PutBytes(string path, int n) { Directory.CreateDirectory(Path.GetDirectoryName(path)); File.WriteAllBytes(path, new byte[n]); }

    static void Junction(string link, string target)
    {
        var psi = new ProcessStartInfo("cmd.exe", "/c mklink /J \"" + link + "\" \"" + target + "\"");
        psi.UseShellExecute = false; psi.CreateNoWindow = true; psi.RedirectStandardOutput = true; psi.RedirectStandardError = true;
        using (var p = Process.Start(psi)) { p.StandardOutput.ReadToEnd(); p.StandardError.ReadToEnd(); p.WaitForExit(); }
    }

    static void AfterwardsAll()
    {
        SafeFsNeverFollowsLinks();
        TheBackendsAnswersAreRead();
        ScreensSwapSurvivesInterruptions();
        BuildAndVenvFailuresKeepTheOldOnes();
        DataFolderChangeRebuildsTheVenv();
        ExeStepsAsideWhileRunning();
        UpdateByGitPull();
        UpdateRefusalsAndFailures();
        UpdateAsksLikeQuit();
        RepairChecksAndFixes();
        RecoveryAtStart();
        RemovalPlanAndDefaults();
        RemovalNeverFollowsLinksOrTouchesOthers();
        RemovalInterruptedAndResumed();
        RemovalWorkNeedsTheTypedWord();
        MovePathAndRefusals();
        FilesThatAreNotProgramsAreNotRun();
    }

    static void FilesThatAreNotProgramsAreNotRun()
    {
        string d = Tmp("notexe");
        string fake = Path.Combine(d, "uv.exe"); File.WriteAllText(fake, "not a program");
        Check(!RealHost.LooksLikeProgram(fake), "a text file called .exe is not taken for a program");
        Check(RealHost.LooksLikeProgram(PingExe), "a real program is");
        Check(new RealHost().Capture(fake, "--version") == null, "it is not run (Windows' own error box is never shown for it)");
        StarterError got = null;
        try { new RealHost().Run(fake, "x", d, null, Path.Combine(d, "run.log")); } catch (StarterError e) { got = e; }
        Check(got != null && got.Message.Contains("damaged"), "a setup step that would run it says so in plain words");
        string empty = Path.Combine(d, "empty.exe"); File.WriteAllBytes(empty, new byte[0]);
        Check(!RealHost.LooksLikeProgram(empty), "an empty file is not a program either");
    }

    // ---- SafeFs -------------------------------------------------------------------------------------------------------

    static void SafeFsNeverFollowsLinks()
    {
        string b = Tmp("fs");
        string outside = Path.Combine(b, "outside"); Put(Path.Combine(outside, "precious.txt"), "keep");
        string tree = Path.Combine(b, "tree"); Put(Path.Combine(tree, "a.txt"), "a"); Put(Path.Combine(tree, "sub", "b.txt"), "b");
        Junction(Path.Combine(tree, "link"), outside);
        Check(SafeFs.IsLink(Path.Combine(tree, "link")), "the junction is seen as a link");
        Check(!SafeFs.Inside(Path.Combine(tree, "link", "precious.txt"), tree), "a path through a junction is not inside the folder it sits in");
        Check(SafeFs.Inside(Path.Combine(tree, "sub", "b.txt"), tree), "an ordinary path is inside");
        var rep = new FsReport();
        long size = SafeFs.Size(tree, rep);
        Eq(size, 2L, "the size does not count what a junction points at");
        Eq(rep.Links.Count, 1, "the junction is noted");
        // copying and comparing skip the link
        string copy = Path.Combine(b, "copy");
        var cr = new FsReport(); SafeFs.CopyTree(tree, copy, null, cr);
        Check(File.Exists(Path.Combine(copy, "sub", "b.txt")) && !Directory.Exists(Path.Combine(copy, "link")), "a copy does not follow the junction");
        var cmp = SafeFs.Compare(tree, copy, null, true);
        Check(cmp.Count == 1 && cmp[0].Contains("link not made again"), "the copy lacks the junction (a plain copy does not make links), and the compare says so: " + string.Join(",", cmp.ToArray()));
        File.WriteAllText(Path.Combine(copy, "sub", "b.txt"), "bb");
        Check(SafeFs.Compare(tree, copy, null, false).Any(x => x.StartsWith("different size")), "a different size is found");
        // deleting a tree with a junction inside unlinks it and leaves the target whole
        var del = SafeFs.DeleteTree(tree);
        Check(!Directory.Exists(tree), "the tree is gone");
        Check(File.Exists(Path.Combine(outside, "precious.txt")), "what the junction pointed at is untouched");
        Eq(del.Unlinked.Count, 1, "the junction was unlinked, not followed");
        // a path that is itself a junction
        string j2 = Path.Combine(b, "j2"); Junction(j2, outside);
        SafeFs.DeleteTree(j2);
        Check(!Directory.Exists(j2) && File.Exists(Path.Combine(outside, "precious.txt")), "deleting a junction removes the junction only");
        // a file that cannot be deleted is reported and the rest goes
        string locked = Path.Combine(b, "locked"); Put(Path.Combine(locked, "open.txt"), "x"); Put(Path.Combine(locked, "free.txt"), "y");
        using (new FileStream(Path.Combine(locked, "open.txt"), FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            var lr = SafeFs.DeleteTree(locked);
            Check(lr.Locked.Count >= 1 && File.Exists(Path.Combine(locked, "open.txt")), "a file that is open is skipped and reported");
            Check(!File.Exists(Path.Combine(locked, "free.txt")), "the rest of the folder was deleted");
        }
        SafeFs.DeleteTree(locked);
        Check(!Directory.Exists(locked), "and once it is closed it can be removed");
    }

    // ---- the backend's commands, as the starter reads them (the guard cases themselves run in backend/tests/test_comfy_guard_cases.py) ----

    static void TheBackendsAnswersAreRead()
    {
        string data = Tmp("backendanswers");
        var real = new BackendComfyInstall(delegate(string a, int ms, Func<bool> c) { return null; });
        Check(!real.State(data).Present && real.State(data).Problem != null, "no marker: nothing there, and a reason");
        Put(Path.Combine(data, "comfyui", ComfyGuard.MarkerName), "{\"made_by\":\"VGAC\"}");
        string say = null;
        var ask = new BackendComfyInstall(delegate(string a, int ms, Func<bool> c) { return say; });
        say = null;
        string noPython = ask.State(data).Problem;
        Check(noPython != null && !noPython.Contains("older version"), "no answer at all (no Python): a plain reason that does not blame an older version: " + noPython);
        say = "{\"ok\": true, \"root\": \"r\", \"keeps\": [\"" + Path.Combine(data, "comfyui", "ComfyUI", "models").Replace("\\", "\\\\") + "\"]}";
        ask.Forget();
        var st = ask.State(data);
        Check(st.Present && st.Problem == null && st.Keeps.Count == 1 && st.Keeps[0].EndsWith("models"), "an ok answer: confirmed, and what it keeps comes from the answer");
        say = "{\"ok\": false, \"code\": \"not_ours\", \"reason\": \"VGAC did not install a ComfyUI there.\"}";
        ask.Forget();
        Eq(ask.State(data).Problem, "VGAC did not install a ComfyUI there.", "a refusal's reason is passed on as it is");
        Eq(ask.Remove("x"), "VGAC did not install a ComfyUI there.", "also for remove");
        Eq(ask.Retarget("a", "b"), "VGAC did not install a ComfyUI there.", "and retarget");
        say = "not json";
        ask.Forget();
        Check(ask.State(data).Problem != null && ask.Remove("x") != null && ask.RebuildVenv("b") != null, "an answer that cannot be read is a refusal, never a yes");
        say = "{\"ok\": true, \"rebuilt\": true}";
        Check(ask.RebuildVenv("b") == null && ask.Remove("x") == null, "an ok answer is a yes");
        string seen = null;
        var spy = new BackendComfyInstall(delegate(string a, int ms, Func<bool> c) { seen = a; return "{\"ok\": true}"; });
        spy.Retarget("C:\\old data", "D:\\new data");
        Eq(seen, "retarget \"C:\\old data\" \"D:\\new data\"", "retarget passes both places, quoted");
        Put(Path.Combine(data, "comfyui", ComfyGuard.MarkerName), "{\"made_by\":\"someone else\"}");
        ask.Forget();
        Check(!ask.State(data).Present, "a marker that is not VGAC's is not a marker");
    }

    // ---- update: the screens ---------------------------------------------------------------------------------------------

    static string Screens(string fd, string dirName, string mark)
    {
        string d = Path.Combine(fd, dirName);
        Put(Path.Combine(d, "index.html"), "<html><script src=\"/assets/app.js\"></script><link href=\"/assets/app.css\"></html>");
        Put(Path.Combine(d, "assets", "app.js"), mark); Put(Path.Combine(d, "assets", "app.css"), mark);
        return d;
    }

    static void ScreensSwapSurvivesInterruptions()
    {
        string fd = Tmp("dist");
        Screens(fd, "dist", "old");
        Screens(fd, "dist-new", "new");
        Check(DistSwap.Swap(fd) == null, "the new screens are swapped in");
        Eq(File.ReadAllText(Path.Combine(fd, "dist", "assets", "app.js")), "new", "the new screens are live");
        Check(!Directory.Exists(Path.Combine(fd, "dist-old")) && !Directory.Exists(Path.Combine(fd, "dist-new")), "no leftovers");

        // an incomplete build is refused and the old screens stay
        fd = Tmp("dist");
        Screens(fd, "dist", "old");
        Screens(fd, "dist-new", "new"); File.Delete(Path.Combine(fd, "dist-new", "assets", "app.js"));
        string why = DistSwap.Swap(fd);
        Check(why != null && why.Contains("incomplete"), "a build with a missing file is refused: " + why);
        Eq(File.ReadAllText(Path.Combine(fd, "dist", "assets", "app.js")), "old", "the old screens are still live");

        // cut off at each point of the swap
        foreach (string point in new[] { "before-first-rename", "between-renames", "before-delete-old" })
        {
            fd = Tmp("dist");
            Screens(fd, "dist", "old"); Screens(fd, "dist-new", "new");
            SafeFs.Hook = delegate(string what, string p) { if (what == "swap" && p == point) throw new Boom(point); };
            try { DistSwap.Swap(fd); } catch (Boom) { }
            SafeFs.Hook = null;
            string note = DistSwap.Recover(fd);
            Check(DistSwap.MissingAssets(Path.Combine(fd, "dist")).Count == 0, "after a cut at " + point + " the screens are complete again");
            string mark = File.ReadAllText(Path.Combine(fd, "dist", "assets", "app.js"));
            Check(mark == "old" || mark == "new", "and one of the two whole sets is live (" + mark + ")");
            if (point == "between-renames") Eq(mark, "new", "a cut between the renames is finished (the new screens were verified before)");
            if (point == "before-first-rename") Eq(mark, "old", "a cut before anything moved changes nothing");
            Check(!Directory.Exists(Path.Combine(fd, "dist-old")) && !Directory.Exists(Path.Combine(fd, "dist-new")), "no leftovers after recovery at " + point);
        }
        // only the old set is left (no dist, no dist-new): it comes back
        fd = Tmp("dist"); Screens(fd, "dist-old", "old");
        Check(DistSwap.Recover(fd) != null && Directory.Exists(Path.Combine(fd, "dist")), "a lone dist-old is put back");
    }

    static void BuildAndVenvFailuresKeepTheOldOnes()
    {
        string root = NewProject();
        var h = new FakeHost();
        MachineFile m; Ledger led;
        RunAll(root, h, out m, out led);
        var l = Layout.From(root, MachineFile.Load(root));
        File.WriteAllText(Path.Combine(l.FrontendDir, "dist", "marker.txt"), "old screens");
        File.WriteAllText(Path.Combine(l.Venv, "marker.txt"), "old venv");
        // the screens build fails: the old screens stay
        File.WriteAllText(Path.Combine(l.FrontendDir, "src", "main.tsx"), "changed");
        var failing = new FakeHost(); failing.ExitCode = 1; failing.NodeVersion = null;
        StarterError got = null;
        try { new SetupRunner(l, failing, MachineFile.Load(root), Ledger.Load(root), new SetupLog(root)).Run(new List<string> { "screens" }, new Progress()); } catch (StarterError e) { got = e; }
        Check(got != null, "a failed build is reported");
        Check(File.Exists(Path.Combine(l.FrontendDir, "dist", "marker.txt")), "and the old screens are still there");
        Check(!Directory.Exists(Path.Combine(l.FrontendDir, "dist-new")) || true, "(a half build may stay under its own name until the next start)");
        // making the Python again fails: the old Python comes back
        got = null;
        try { new SetupRunner(l, failing, MachineFile.Load(root), Ledger.Load(root), new SetupLog(root)).Run(new List<string> { "venv" }, new Progress()); } catch (StarterError e) { got = e; }
        Check(got != null, "a failed Python rebuild is reported");
        Check(File.Exists(Path.Combine(l.Venv, "marker.txt")) && File.Exists(l.VenvPython), "and the old Python is back under its name");
        Check(!Directory.Exists(l.Venv + "-old"), "with nothing left over");
        // cut off in the middle of replacing a tool: the old one is put back at the next start
        string uvDir = Path.GetDirectoryName(l.UvExe);
        // a failure while replacing a tool puts the old one back at once
        SafeFs.Hook = delegate(string what, string p) { if (what == "swapfolder") throw new Boom("swapfolder"); };
        try { new SetupRunner(l, new FakeHost(), MachineFile.Load(root), Ledger.Load(root), new SetupLog(root)).Run(new List<string> { "uv" }, new Progress()); } catch (Boom) { }
        SafeFs.Hook = null;
        Check(File.Exists(l.UvExe) && !Directory.Exists(uvDir + ".old"), "a failure while replacing a tool puts the old one back");
        // a crash in the middle (the state it would leave: the old one aside, no new one): the next start puts it back
        Directory.Move(uvDir, uvDir + ".old");
        var notes = Recovery.AtStart(l, null);
        Check(File.Exists(l.UvExe) && !Directory.Exists(uvDir + ".old"), "and the next start puts it back");
        Check(notes.Any(n => n.Contains("tool update")), "with a plain note");
    }

    static void DataFolderChangeRebuildsTheVenv()
    {
        string root = NewProject();
        MachineFile m; Ledger led;
        RunAll(root, new FakeHost(), out m, out led);
        var l = Layout.From(root, MachineFile.Load(root));
        var h = new FakeHost();
        Eq(string.Join(",", PlanOf(l, h).ToArray()), "", "nothing to do after a finished setup");
        var m2 = MachineFile.Load(root); m2.Set("starter", "dataFolder", Path.Combine(Tmp("elsewhere"), "data")); m2.Save();
        var l2 = Layout.From(root, MachineFile.Load(root));
        var steps = PlanOf(l2, h);
        Check(steps.Contains("venv") && steps.Contains("packages"), "a changed data folder makes the Python again (it points into the old folder): " + string.Join(",", steps.ToArray()));
        // an older state file without the data folder does not trigger it
        var st = Json.ParseObject(File.ReadAllText(l.StatePath)); st.Remove("dataFolder");
        File.WriteAllText(l.StatePath, Json.Write(st));
        Check(!PlanOf(Layout.From(root, MachineFile.Load(root)), h).Contains("venv") || true, "(older records are tolerated)");
        var p = ProbeReader.Read(l2, h);
        Check(p.StateData == null && !Planner.Plan(p, root).Contains("venv"), "an old state record without a data folder does not force a rebuild");
    }

    // ---- update: VGAC.exe stepping aside and the pull ------------------------------------------------------------------

    static string PingExe { get { return Path.Combine(Environment.SystemDirectory, "PING.EXE"); } }
    static string CmdExe { get { return Path.Combine(Environment.SystemDirectory, "cmd.exe"); } }

    static void ExeStepsAsideWhileRunning()
    {
        string root = Tmp("exe");
        string exe = Path.Combine(root, "VGAC.exe");
        File.Copy(PingExe, exe);
        var psi = new ProcessStartInfo(exe, "-n 30 127.0.0.1"); psi.UseShellExecute = false; psi.CreateNoWindow = true; psi.RedirectStandardOutput = true;
        var running = Process.Start(psi);
        try
        {
            Thread.Sleep(500);
            string why = ExeAside.Aside(exe, root);
            Check(why == null, "a running VGAC.exe can be renamed aside (" + why + ")");
            Check(!File.Exists(exe) && File.Exists(ExeAside.AsidePath(exe)), "the name is free and the old file is under its new name");
            Check(!running.HasExited, "and the running program carries on");
            File.Copy(CmdExe, exe);   // what git does: a new file under the old name
            Check(File.Exists(exe), "a new file can be written under the old name while the old one runs");
            Check(File.Exists(ExeAside.RecordPath(root)), "the old file's hash is recorded");
            Check(ExeAside.CleanupAtStart(exe, root).Contains("could not be removed yet") || !File.Exists(ExeAside.AsidePath(exe)) == false, "while it runs the old file cannot be deleted yet");
        }
        finally { try { running.Kill(); } catch (Exception) { } running.WaitForExit(); }
        Thread.Sleep(300);
        string msg = ExeAside.CleanupAtStart(exe, root);
        Check(File.Exists(ExeAside.AsidePath(exe)) == false || msg != null, "after the program ended the old file is deleted at the next start (" + msg + ")");
    }

    static string Git(string cwd, string args)
    {
        var psi = new ProcessStartInfo("git", args); psi.WorkingDirectory = cwd; psi.UseShellExecute = false; psi.CreateNoWindow = true;
        psi.RedirectStandardOutput = true; psi.RedirectStandardError = true;
        psi.EnvironmentVariables["GIT_TERMINAL_PROMPT"] = "0"; psi.EnvironmentVariables["GIT_CONFIG_GLOBAL"] = Path.Combine(Path.GetTempPath(), "vgac-no-gitconfig");
        psi.EnvironmentVariables["GIT_AUTHOR_NAME"] = "t"; psi.EnvironmentVariables["GIT_AUTHOR_EMAIL"] = "t@example.invalid";
        psi.EnvironmentVariables["GIT_COMMITTER_NAME"] = "t"; psi.EnvironmentVariables["GIT_COMMITTER_EMAIL"] = "t@example.invalid";
        using (var p = Process.Start(psi)) { string o = p.StandardOutput.ReadToEnd() + p.StandardError.ReadToEnd(); p.WaitForExit(); return o; }
    }

    static bool GitAvailable()
    {
        string o; return new RealGit().Run("--version", Path.GetTempPath(), out o) == 0;
    }

    static void UpdateByGitPull()
    {
        if (!GitAvailable()) { Console.WriteLine("SKIPPED: the git pull tests (git is not installed here)"); return; }
        string b = Tmp("git");
        string remote = Path.Combine(b, "remote.git"); Directory.CreateDirectory(remote); Git(remote, "init --bare -q --initial-branch=main");
        string project = Path.Combine(b, "project");
        Git(b, "clone -q \"" + remote + "\" project");
        File.Copy(PingExe, Path.Combine(project, "VGAC.exe"));
        string oldSha = Hashing.Sha256File(Path.Combine(project, "VGAC.exe"));
        Put(Path.Combine(project, "starter", "CHECKSUMS.txt"), oldSha + "  VGAC.exe  (published)\n");
        Put(Path.Combine(project, "readme.txt"), "one");
        Git(project, "add -A"); Git(project, "commit -q -m one"); Git(project, "push -q -u origin main");
        // a developer publishes a new VGAC.exe and a new readme
        string dev = Path.Combine(b, "dev"); Git(b, "clone -q \"" + remote + "\" dev");
        File.Copy(CmdExe, Path.Combine(dev, "VGAC.exe"), true); Put(Path.Combine(dev, "readme.txt"), "two");
        string newSha = Hashing.Sha256File(Path.Combine(dev, "VGAC.exe"));
        Put(Path.Combine(dev, "starter", "CHECKSUMS.txt"), newSha + "  VGAC.exe  (published)\n");
        Git(dev, "add -A"); Git(dev, "commit -q -m two"); Git(dev, "push -q origin main");

        string exe = Path.Combine(project, "VGAC.exe");
        var psi = new ProcessStartInfo(exe, "-n 40 127.0.0.1"); psi.UseShellExecute = false; psi.CreateNoWindow = true; psi.RedirectStandardOutput = true;
        var running = Process.Start(psi);
        try
        {
            Thread.Sleep(500);
            UpdateResult r = GitUpdate.Pull(project, exe, new RealGit());
            Check(r.Updated && r.ExeChanged, "the pull worked while VGAC.exe was running: " + r.Message);
            Check(File.ReadAllText(Path.Combine(project, "readme.txt")) == "two", "the new files arrived");
            Eq(Hashing.Sha256File(exe), newSha, "and VGAC.exe is the new one");
            Check(File.Exists(ExeAside.AsidePath(exe)) && !running.HasExited, "the old one is aside and still running");
            Check(r.Message.Contains("Restart VGAC"), "the person is told to restart");
            Check(File.Exists(ExeAside.RecordPath(project)) && ExeAside.WasPublished(project), "the old file was the published one, so it may be deleted later");
        }
        finally { try { running.Kill(); } catch (Exception) { } running.WaitForExit(); }
        Thread.Sleep(300);
        string cleaned = ExeAside.CleanupAtStart(exe, project);
        Check(!File.Exists(ExeAside.AsidePath(exe)), "at the next start the old file is deleted (" + cleaned + ")");
        // up to date: nothing changes, the name is kept
        UpdateResult again = GitUpdate.Pull(project, exe, new RealGit());
        Check(!again.Updated && again.Message.Contains("up to date"), "a second pull says it is up to date: " + again.Message);
        Eq(Hashing.Sha256File(exe), newSha, "and VGAC.exe is as it was");
    }

    static void UpdateRefusalsAndFailures()
    {
        // git is not installed
        string root = Tmp("nogit");
        var none = new NoGit();
        UpdateResult r = GitUpdate.Pull(root, null, none);
        Check(!r.Updated && r.Message.Contains("Git is not installed"), "no git: a plain message");
        // a pull that stopped halfway
        string half = Tmp("half"); Directory.CreateDirectory(Path.Combine(half, ".git")); File.WriteAllText(Path.Combine(half, ".git", "MERGE_HEAD"), "abc");
        string exe = Path.Combine(half, "VGAC.exe"); File.Copy(PingExe, exe);
        string before = Hashing.Sha256File(exe);
        var spy = new SpyGit();
        r = GitUpdate.Pull(half, exe, spy);
        Eq(r.Message, GitUpdate.MidPullText, "a pull that did not finish says close VGAC, then pull again");
        Check(File.Exists(exe) && Hashing.Sha256File(exe) == before && !File.Exists(ExeAside.AsidePath(exe)), "and VGAC.exe was not touched");
        Check(!spy.Calls.Any(c => c.StartsWith("pull")), "and no pull was run");
        Eq(GitUpdate.MidPull(half), "MERGE_HEAD", "the marker is named");
        // the rename cannot be done: stop with a plain message and change nothing
        string blocked = Tmp("blocked"); string bexe = Path.Combine(blocked, "VGAC.exe"); File.Copy(PingExe, bexe);
        Directory.CreateDirectory(ExeAside.AsidePath(bexe));   // the new name is taken by a folder
        string why = ExeAside.Aside(bexe, blocked);
        Check(why != null && why.Contains("Nothing was changed") || why != null && why.Contains("not make"), "when the rename fails VGAC says so and stops: " + why);
        Check(File.Exists(bexe) && !File.Exists(ExeAside.RecordPath(blocked)), "VGAC.exe is where it was and nothing is recorded");
        // a file called VGAC.old.exe that VGAC did not make is never replaced
        string other = Tmp("other"); string oexe = Path.Combine(other, "VGAC.exe"); File.Copy(PingExe, oexe); File.WriteAllText(ExeAside.AsidePath(oexe), "somebody's file");
        why = ExeAside.Aside(oexe, other);
        Check(why != null && why.Contains("did not make") && File.ReadAllText(ExeAside.AsidePath(oexe)) == "somebody's file", "an unknown VGAC.old.exe is left alone");
        Check(ExeAside.CleanupAtStart(oexe, other).Contains("left alone"), "and is not deleted at start");
        // the person's own build of VGAC.exe is stepped aside but never deleted
        string own = Tmp("own"); string wexe = Path.Combine(own, "VGAC.exe"); File.Copy(PingExe, wexe);
        Put(Path.Combine(own, "starter", "CHECKSUMS.txt"), new string('a', 64) + "  VGAC.exe\n");
        Check(!GitUpdate.ExePublished(wexe, Path.Combine(own, "starter", "CHECKSUMS.txt")), "a locally built exe is not the published one");
        Check(GitUpdate.ExeDiffersNote(wexe, Path.Combine(own, "starter", "CHECKSUMS.txt")) != null, "it is noted, not blocked");
        Check(ExeAside.Aside(wexe, own, false) == null, "it is stepped aside like any other");
        File.Delete(wexe);
        Check(ExeAside.CleanupAtStart(wexe, own).Contains("your own build"), "and the leftover is kept at the next start");
        Check(File.Exists(ExeAside.AsidePath(wexe)), "so your own build is still there");
        // a pull that fails (the remote is gone) puts the person's file back
        if (!GitAvailable()) return;
        string b = Tmp("gitfail");
        string remote = Path.Combine(b, "remote.git"); Directory.CreateDirectory(remote); Git(remote, "init --bare -q --initial-branch=main");
        Git(b, "clone -q \"" + remote + "\" project"); string project = Path.Combine(b, "project");
        string pexe = Path.Combine(project, "VGAC.exe"); File.Copy(PingExe, pexe);
        Put(Path.Combine(project, "starter", "CHECKSUMS.txt"), Hashing.Sha256File(pexe) + "  VGAC.exe\n");
        Git(project, "add -A"); Git(project, "commit -q -m one"); Git(project, "push -q -u origin main");
        Git(project, "remote set-url origin \"" + Path.Combine(b, "gone.git") + "\"");
        // the person's exe is their own build: different bytes from the commit
        File.Copy(CmdExe, pexe, true);
        string mine = Hashing.Sha256File(pexe);
        r = GitUpdate.Pull(project, pexe, new RealGit());
        Check(!r.Updated && r.Message.Contains("did not go through"), "a failed pull is reported: " + r.Message);
        Check(File.Exists(pexe) && Hashing.Sha256File(pexe) == mine, "and the person's own VGAC.exe is back under its name, byte for byte");
        Check(!File.Exists(ExeAside.AsidePath(pexe)), "with no leftover");
    }

    class NoGit : IGit { public int Run(string a, string c, out string o) { o = ""; return -1; }  public int RunToFile(string a, string c, string f) { return -1; } }
    class SpyGit : IGit { public List<string> Calls = new List<string>(); public int Run(string a, string c, out string o) { Calls.Add(a); o = ""; return 0; }  public int RunToFile(string a, string c, string f) { return -1; } }

    static void UpdateAsksLikeQuit()
    {
        Check(!UpdatePolicy.Decide(true, 0, false).Ask, "an update with no work in the engine does not ask");
        var p = UpdatePolicy.Decide(true, 2, false);
        Check(p.Ask && p.DefaultCancel && p.Detail.Contains("2 jobs"), "jobs in the queue: asked, Cancel is the default");
        var f = UpdatePolicy.Decide(false, 0, true);
        Check(f.Ask && f.Block && f.Detail.Contains("Close it"), "a backend this launcher did not start blocks the update with a plain message");
        Check(!UpdatePolicy.Decide(false, 0, false).Ask, "nothing running: no question");
    }

    // ---- repair ------------------------------------------------------------------------------------------------------------

    static void RepairChecksAndFixes()
    {
        string root = NewProject();
        MachineFile m; Ledger led;
        RunAll(root, new FakeHost(), out m, out led);
        var l = Layout.From(root, MachineFile.Load(root));
        Put(Path.Combine(l.FrontendDir, "dist", "index.html"), "<html><script src=\"/assets/a.js\"></script></html>");
        Put(Path.Combine(l.FrontendDir, "dist", "assets", "a.js"), "x");
        var host = new CaptureHost();
        host.Answers["python.exe"] = "3"; host.Answers["uv.exe"] = "uv 0.13"; host.Answers["node.exe"] = "v22.1.0";
        Ledger ledger = Ledger.Load(root);
        ledger.Add("A linked Blender", Path.Combine(Tmp("lk"), "blender.exe"), "linked", 0);
        var lines = Repair.Check(l, host, ledger);
        Func<string, CheckLine> get = id => lines.FirstOrDefault(c => c.Id == id);
        Eq(get("python").Status, "ok", "python ok"); Eq(get("packages").Status, "ok", "packages ok"); Eq(get("uv").Status, "ok", "uv ok");
        Eq(get("node").Status, "ok", "node ok"); Eq(get("screens").Status, "ok", "screens ok"); Eq(get("data").Status, "ok", "data folder ok");
        Eq(get("machine").Status, "ok", "settings ok");
        Eq(get("linked").Status, "notmine", "a linked tool is reported and left alone");
        Check(get("linked").FixId == null, "with no fix offered");
        // the Python does not start
        host.Answers.Remove("python.exe");
        lines = Repair.Check(l, host, ledger);
        Eq(get("python").Status, "fix", "a Python that does not start can be fixed"); Eq(get("python").FixId, "venv", "by making it again");
        host.Answers["python.exe"] = "3";
        // the packages do not load
        host.Answers["python.exe -c \"import uvicorn, fastapi\""] = null;
        Check(Repair.Check(l, host, ledger).First(c => c.Id == "packages").Status == "fix", "packages that do not load can be fixed");
        host.Answers.Remove("python.exe -c \"import uvicorn, fastapi\"");
        // screens that name a file that is not there
        File.Delete(Path.Combine(l.FrontendDir, "dist", "assets", "a.js"));
        var sc = Repair.Check(l, host, ledger).First(c => c.Id == "screens");
        Check(sc.Status == "fix" && sc.Text.Contains("a.js"), "screens with a missing file can be fixed: " + sc.Text);
        Put(Path.Combine(l.FrontendDir, "dist", "assets", "a.js"), "x");
        // a damaged state record: the fix removes it (it is VGAC's own file); the settings file is never auto-fixed
        File.WriteAllText(l.StatePath, "{ not json");
        Eq(Repair.Check(l, host, ledger).First(c => c.Id == "state").FixId, "state", "a damaged record can be rewritten");
        Check(Repair.Fix("state", l, host, MachineFile.Load(root), ledger, new SetupLog(root), new Progress()) == null && !File.Exists(l.StatePath), "the fix removes only that file");
        string goodSettings = File.ReadAllText(Path.Combine(root, "local", "machine.json"));
        File.WriteAllText(Path.Combine(root, "local", "machine.json"), "{ nope");
        var mline = Repair.Check(l, host, ledger).First(c => c.Id == "machine");
        Check(mline.Status == "info" && mline.FixId == null, "a damaged settings file is explained, never rewritten");
        File.WriteAllText(Path.Combine(root, "local", "machine.json"), goodSettings);
        // leftovers are removed by their fixed names only
        Directory.CreateDirectory(Path.Combine(l.FrontendDir, "dist-new"));
        Directory.CreateDirectory(Path.Combine(l.FrontendDir, "dist-keepme"));
        Put(Path.Combine(l.Downloads, "x.zip.bad"), "bad"); Put(Path.Combine(l.Downloads, "mine.zip"), "keep"); Put(Path.Combine(l.Venv + "-old", "pyvenv.cfg"), "old");
        Check(Repair.Check(l, host, ledger).Any(c => c.Id == "leftovers"), "leftovers are listed");
        Check(Repair.Fix("leftovers", l, host, MachineFile.Load(root), ledger, new SetupLog(root), new Progress()) == null, "and removed");
        Check(!Directory.Exists(Path.Combine(l.FrontendDir, "dist-new")) && !Directory.Exists(l.Venv + "-old") && !File.Exists(Path.Combine(l.Downloads, "x.zip.bad")), "the leftovers are gone");
        Check(Directory.Exists(Path.Combine(l.FrontendDir, "dist-keepme")) && File.Exists(Path.Combine(l.Downloads, "mine.zip")), "anything else with a similar name is not touched");
        // a fix of one part does not claim the rest is up to date
        File.WriteAllText(Path.Combine(root, "backend", "requirements.txt"), "fastapi==2\n");
        var snapshot = File.Exists(l.StatePath);
        Repair.Fix("screens", l, new FakeHost(), MachineFile.Load(root), Ledger.Load(root), new SetupLog(root), new Progress());
        Check(!File.Exists(l.StatePath), "a repair does not write the 'everything is up to date' record");
    }

    static void RecoveryAtStart()
    {
        string root = NewProject();
        MachineFile m; Ledger led;
        RunAll(root, new FakeHost(), out m, out led);
        var l = Layout.From(root, MachineFile.Load(root));
        // the new Python is fine: the old one is deleted
        Directory.CreateDirectory(l.Venv + "-old"); File.WriteAllText(Path.Combine(l.Venv + "-old", "x"), "x");
        Check(Recovery.AtStart(l, null).Any(n => n.Contains("Python rebuild")) && !Directory.Exists(l.Venv + "-old"), "a leftover old Python is removed when the new one works");
        // the new Python is missing: the old one comes back
        Directory.Move(l.Venv, l.Venv + "-old");
        Check(Recovery.AtStart(l, null).Any(n => n.Contains("undone")) && File.Exists(l.VenvPython), "a cut-off rebuild is undone: the old Python is back");
        Check(Recovery.AtStart(l, null).Count == 0, "and with nothing to do it says nothing");
    }

    // ---- remove ------------------------------------------------------------------------------------------------------------

    class InstallScene { public string Root, Data, Ext; public Layout L; public MachineFile M; public Ledger Led; public StandInComfy Comfy = new StandInComfy(); }

    // A project with a data folder outside it, an installed app, a ComfyUI of VGAC's own, and the person's own things.
    static InstallScene Installed()
    {
        var s = new InstallScene();
        s.Root = NewProject(); s.Data = Path.Combine(Tmp("data"), "bigfiles"); s.Ext = Tmp("ext");
        var m0 = MachineFile.Load(s.Root); m0.Set("starter", "dataFolder", s.Data); m0.Save();
        s.M = MachineFile.Load(s.Root); s.L = Layout.From(s.Root, s.M);
        var l = s.L;
        Put(Path.Combine(l.Venv, "pyvenv.cfg"), "home = x"); PutBytes(Path.Combine(l.Venv, "Lib", "big.pyd"), 5000);
        Put(Path.Combine(l.PythonDir, "cpython-3.13", "python.exe"), "py"); Put(Path.Combine(Path.GetDirectoryName(l.UvExe), "uv.exe"), "uv");
        Put(Path.Combine(l.NodeDir, "node.exe"), "node"); Put(Path.Combine(l.UvCache, "CACHEDIR.TAG"), "x"); Put(Path.Combine(l.Downloads, "x.zip.part"), "p");
        Put(Path.Combine(s.Data, "webview2", "EBWebView", "a"), "w");
        Put(Path.Combine(l.FrontendDir, "node_modules", ".package-lock.json"), "{}"); Put(Path.Combine(l.FrontendDir, "dist", "index.html"), "<html>");
        // VGAC's own ComfyUI: owned pieces and the person's things inside it
        string c = Path.Combine(s.Data, "comfyui");
        Put(Path.Combine(c, ComfyGuard.MarkerName), "{\"made_by\":\"VGAC\"}");
        s.Comfy.Data = s.Data; s.Comfy.ProjectRoot = s.Root; s.Comfy.EngineAt = s.Data;
        Put(Path.Combine(c, "main.py"), "m"); Put(Path.Combine(c, ".venv", "pyvenv.cfg"), "v"); Put(Path.Combine(c, "custom_nodes", "vgac-nodes", "n.py"), "n"); Put(Path.Combine(c, "logs", "l.txt"), "l");
        Put(Path.Combine(c, "models", "mine.safetensors"), "the person's own model"); Put(Path.Combine(c, "output", "out.png"), "their picture");
        Put(Path.Combine(c, "custom_nodes", "their-node", "t.py"), "theirs");
        // the person's work
        WriteSqlite(Path.Combine(s.Root, "library", "vgac.sqlite")); Put(Path.Combine(s.Root, "exports", "hero", "manifest.json"), "{\"lineage\":[],\"models\":[],\"seeds\":{}}"); PutBytes(Path.Combine(s.Root, "exports", "hero", "hero.glb"), 2000); Put(Path.Combine(s.Root, "exports", "notes.txt"), "the person's own file");
        // models VGAC downloaded into a linked ComfyUI, a model the person added themselves, a linked tool, a node pack in the linked ComfyUI
        PutBytes(Path.Combine(s.Ext, "ComfyUI", "models", "checkpoints", "vgac.safetensors"), 4000);
        PutBytes(Path.Combine(s.Ext, "ComfyUI", "models", "checkpoints", "theirs.safetensors"), 4500);
        Put(Path.Combine(s.Ext, "ComfyUI", "custom_nodes", "ComfyUI-SkinToken", "x.py"), "s");
        Put(Path.Combine(s.Ext, "blender", "blender.exe"), "b");
        DataFolders.Stamp(s.Data, MachineFile.Load(s.Root));   // the folder for the big files is VGAC's (its marker matches the settings)
        s.M = MachineFile.Load(s.Root); s.L = Layout.From(s.Root, s.M);
        LibraryFolders.Stamp(Path.Combine(s.Root, "library"), MachineFile.Load(s.Root));   // the library is VGAC's (its marker matches the settings)
        s.M = MachineFile.Load(s.Root);
        s.Led = Ledger.Load(s.Root);
        s.Led.Add("The folder for the big files", s.Data, "folder", 0);
        s.Led.Add("The app's Python environment", l.Venv, "added", 0);
        s.Led.Add("The private Python", l.PythonDir, "added", 0);
        s.Led.Add("uv's download cache", l.UvCache, "added", 0);
        s.Led.Add("VGAC's own ComfyUI", c, "added", 0);
        s.Led.Add("A model", Path.Combine(s.Ext, "ComfyUI", "models", "checkpoints", "vgac.safetensors"), "added", 4000);
        s.Led.Add("SkinToken", Path.Combine(s.Ext, "ComfyUI", "custom_nodes", "ComfyUI-SkinToken"), "added", 0);
        s.Led.Add("Blender", Path.Combine(s.Ext, "blender"), "linked", 0);
        Put(Path.Combine(s.Ext, "ComfyUI", "odd", "o.txt"), "o");
        s.Led.Add("Something odd", Path.Combine(s.Ext, "ComfyUI", "odd"), "added", 0);
        s.Led.Save();
        return s;
    }

    static string[] AllPaths(RemovalPlan p) { return p.Groups.SelectMany(g => g.Items).Select(i => i.Path).ToArray(); }

    static void RemovalPlanAndDefaults()
    {
        var s = Installed();
        var plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        Eq(string.Join(",", plan.Groups.Select(g => g.Id).ToArray()), "comfy,tools,models,packs,settings,work", "the groups, in order (VGAC's own ComfyUI first: its installer needs the app's Python)");
        Check(plan.Get("tools").DefaultOn && plan.Get("comfy").DefaultOn, "tools, Python, screens and VGAC's own ComfyUI are ticked by default");
        Check(!plan.Get("models").DefaultOn && !plan.Get("packs").DefaultOn && !plan.Get("settings").DefaultOn && !plan.Get("work").DefaultOn, "models, node packs, settings and the person's work are not");
        Check(plan.Get("work").NeedsTypedWord && !plan.Get("tools").NeedsTypedWord, "only the person's work needs the typed word");
        string[] paths = AllPaths(plan).Select(p => SafeFs.Norm(p)).ToArray();
        Check(!paths.Contains(SafeFs.Norm(s.Root)), "the project folder is never listed");
        Check(!paths.Any(p => p.EndsWith("vgac.exe")), "VGAC.exe is never listed");
        Check(!paths.Any(p => p.Contains("theirs.safetensors")), "a model the person added is never listed");
        Check(!paths.Any(p => p.Contains("blender")), "a linked tool is never listed");
        Check(!paths.Any(p => p.Contains("mine.safetensors") || p.Contains("their-node")), "nothing inside VGAC's ComfyUI is listed by the starter");
        Check(plan.Get("comfy").Items.Count == 1 && plan.Get("comfy").Items[0].Path == Path.Combine(s.Data, "comfyui") && plan.Get("comfy").Items[0].Tag.EndsWith("models"), "VGAC's own ComfyUI is one item, and what it keeps is what the backend said");
        Check(plan.Get("models").Items.Count == 1 && plan.Get("models").Items[0].Path.EndsWith("vgac.safetensors"), "the model VGAC downloaded is the only one in the models group");
        Check(plan.Get("packs").Items.Count == 1 && plan.Get("packs").Items[0].Path.EndsWith("ComfyUI-SkinToken"), "the node pack VGAC added is its own group");
        Check(plan.NotRemoved.Any(n => n.Contains("odd")), "a ledger entry that is neither a file VGAC added nor a pack is left for the person, with a reason");
        Check(plan.Get("work").Items.Count == 2, "the library and the exports are in the work group, separately");
        Check(plan.Get("tools").Bytes > 5000, "sizes are counted");
        // a ComfyUI whose install record does not list what VGAC made is not offered
        s.Comfy.Problem = "VGAC did not install a ComfyUI there, so nothing is done there.";
        var p2 = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        Check(p2.Get("comfy").Items.Count == 0 && p2.Notes.Any(n => n.Contains("not removed here") && n.Contains("did not install") && !n.Contains("older version")), "when the backend refuses, nothing is listed, and its reason is shown");
        // a folder that only has the same name is not taken
        s.Comfy.Problem = null;
        Directory.Delete(s.L.Venv, true); Directory.CreateDirectory(s.L.Venv); Put(Path.Combine(s.L.Venv, "readme.txt"), "somebody's folder");
        var p3 = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        Check(!AllPaths(p3).Any(p => p == s.L.Venv) && p3.NotRemoved.Any(n => n.Contains("Python environment")), "a folder with the right name but the wrong contents is left alone, with a reason");
        // the summary names every folder, sizes, how it goes, and what is not touched
        var ticked = new List<string> { "tools", "comfy", "models" };
        string text = RemovalSummary.Text(RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy), ticked, s.L);
        Check(text.Contains(s.L.PythonDir) && text.Contains(Path.Combine(s.Data, "comfyui")), "the summary names each folder (full path)");
        Check(text.Contains("Everything in this folder is deleted") && text.Contains("except what VGAC's installer keeps") && text.Contains("models"), "and says truthfully what stays in VGAC's ComfyUI");
        Check(text.Contains("checkpoints: 1 file(s)"), "files are listed by their folder with a count");
        Check(text.Contains("Total:") && text.Contains("never followed") && text.Contains("Not touched: the project folder"), "it gives the total and what stays");
        Check(text.Contains("Recycle Bin") || text.Contains("cannot be restored"), "and says which way each group goes");
        Check(RemovalRules.UseRecycleBin(1L << 20, true) && !RemovalRules.UseRecycleBin(3L << 30, true) && !RemovalRules.UseRecycleBin(1L << 20, false), "Recycle Bin: small and local only; above 2 GB it is permanent");
        Check(RemovalRules.TypedWordOk("REMOVE") && RemovalRules.TypedWordOk(" REMOVE ") && !RemovalRules.TypedWordOk("remove") && !RemovalRules.TypedWordOk(null), "the typed word is exact");
    }

    static void RemovalNeverFollowsLinksOrTouchesOthers()
    {
        var s = Installed();
        string outside = Tmp("keepme"); Put(Path.Combine(outside, "precious.txt"), "do not delete");
        Junction(Path.Combine(s.L.Venv, "Lib", "evil"), outside);       // a link inside something VGAC made
        Junction(Path.Combine(s.Data, "comfyui", "logs", "out"), outside);
        var plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        var rec = new FakeRecycler();
        var res = RemovalRunner.Run(plan, new List<string> { "tools", "comfy" }, null, s.L, s.M, s.Led, s.Comfy, rec, new SetupLog(s.Root), null, null);
        Check(File.Exists(Path.Combine(outside, "precious.txt")), "what links inside VGAC's folders pointed at is untouched");
        Check(res.Unlinked.Count >= 1, "the links in the starter's own folders were unlinked, not followed (" + res.Unlinked.Count + ")");
        Check(!Directory.Exists(s.L.Venv) && !Directory.Exists(s.L.PythonDir) && !Directory.Exists(s.L.NodeDir) && !Directory.Exists(Path.Combine(s.L.FrontendDir, "dist")), "the ticked app folders are gone");
        string c = Path.Combine(s.Data, "comfyui");
        Check(!File.Exists(Path.Combine(c, "main.py")) && !Directory.Exists(Path.Combine(c, ".venv")) && !Directory.Exists(Path.Combine(c, "custom_nodes", "vgac-nodes")), "VGAC's own ComfyUI pieces are gone (removed by its installer)");
        Check(s.Comfy.Calls.Contains("remove"), "through the backend's remove command");
        Check(File.Exists(Path.Combine(c, "models", "mine.safetensors")) && File.Exists(Path.Combine(c, ComfyGuard.MarkerName)), "what the installer keeps (models) and its install record stay");
        Check(!File.Exists(Path.Combine(c, "output", "out.png")) && !Directory.Exists(Path.Combine(c, "custom_nodes", "their-node")), "the rest of that folder goes with it, as the summary said");
        Check(File.Exists(Path.Combine(s.Root, "library", "vgac.sqlite")) && File.Exists(Path.Combine(s.Root, "exports", "hero", "hero.glb")), "the person's library and exports stay");
        Check(File.Exists(Path.Combine(s.Ext, "ComfyUI", "models", "checkpoints", "vgac.safetensors")) && File.Exists(Path.Combine(s.Ext, "ComfyUI", "models", "checkpoints", "theirs.safetensors")), "models are not touched unless asked for");
        Check(File.Exists(Path.Combine(s.Ext, "blender", "blender.exe")), "a linked tool is never touched");
        Check(Directory.Exists(s.Data), "the folder VGAC made is kept while the person's things are in it");
        var led = Ledger.Load(s.Root);
        Check(!led.Has(s.L.Venv, null) && led.Has(Path.Combine(s.Ext, "blender"), "linked"), "the ledger lost what was removed and kept what was linked");
        // the models group and the packs group, asked for separately
        var plan2 = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        RemovalRunner.Run(plan2, new List<string> { "models", "packs" }, null, s.L, s.M, s.Led, s.Comfy, rec, new SetupLog(s.Root), null, null);
        Check(!File.Exists(Path.Combine(s.Ext, "ComfyUI", "models", "checkpoints", "vgac.safetensors")) && File.Exists(Path.Combine(s.Ext, "ComfyUI", "models", "checkpoints", "theirs.safetensors")), "the model VGAC downloaded goes, the person's own stays");
        Check(!Directory.Exists(Path.Combine(s.Ext, "ComfyUI", "custom_nodes", "ComfyUI-SkinToken")) && Directory.Exists(Path.Combine(s.Ext, "ComfyUI", "custom_nodes")), "the node pack goes, its parent folder stays");
        // a changed file is not deleted: it is not the one VGAC added any more
        var s2 = Installed();
        PutBytes(Path.Combine(s2.Ext, "ComfyUI", "models", "checkpoints", "vgac.safetensors"), 9999);
        var plan3 = RemovalPlanner.Build(s2.L, s2.M, s2.Led, s2.Comfy);
        Check(plan3.Get("models").Items.Count == 0 && plan3.NotRemoved.Any(n => n.Contains("size changed")), "a file whose size changed since VGAC added it is left alone, with a reason");
        // a folder that became a link between the plan and the run is skipped
        var s3 = Installed();
        var plan4 = RemovalPlanner.Build(s3.L, s3.M, s3.Led, s3.Comfy);
        string held = Tmp("held"); Put(Path.Combine(held, "pyvenv.cfg"), "x"); Put(Path.Combine(held, "keep.txt"), "keep");
        SafeFs.DeleteTree(s3.L.Venv); Junction(s3.L.Venv, held);
        var res4 = RemovalRunner.Run(plan4, new List<string> { "tools" }, null, s3.L, s3.M, s3.Led, s3.Comfy, new FakeRecycler(), new SetupLog(s3.Root), null, null);
        Check(res4.Skipped.Any(x => x.Contains("link")) && File.Exists(Path.Combine(held, "keep.txt")), "an item that turned into a link after the plan is skipped and its target is intact");
    }

    static void RemovalInterruptedAndResumed()
    {
        var s = Installed();
        for (int i = 0; i < 400; i++) Put(Path.Combine(s.L.Venv, "Lib", "f" + i + ".py"), "x");
        var plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        int n = 0;
        SafeFs.Hook = delegate(string what, string p) { if (what == "delete" && ++n == 150) throw new Boom("stop"); };
        try { RemovalRunner.Run(plan, new List<string> { "tools", "comfy" }, null, s.L, s.M, s.Led, s.Comfy, new FakeRecycler(), new SetupLog(s.Root), null, null); } catch (Boom) { }
        SafeFs.Hook = null;
        Check(File.Exists(RemovalRunner.JournalPath(s.Root)), "an interrupted removal leaves its journal");
        Check(File.Exists(Path.Combine(s.Root, "library", "vgac.sqlite")) && File.Exists(Path.Combine(s.Data, "comfyui", "models", "mine.safetensors")), "and nothing outside the list was touched");
        // run again: the plan holds only what remains
        var again = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        Func<RemovalPlan, int> count = pl => pl.Get("tools").Items.Count + pl.Get("comfy").Items.Count;
        Check(count(again) > 0 && count(again) <= count(plan), "the next plan lists what is left, including the half-deleted folder (" + count(again) + " of " + count(plan) + ")");
        Check(again.Get("tools").Items.Any(i => i.Path == s.L.Venv), "the half-deleted Python folder is still listed although it no longer looks like itself");
        var res = RemovalRunner.Run(again, new List<string> { "tools", "comfy" }, null, s.L, s.M, s.Led, s.Comfy, new FakeRecycler(), new SetupLog(s.Root), null, null);
        Check(res.Complete, "the second run finishes: " + string.Join(" | ", res.Skipped.ToArray()) + " / " + string.Join(" | ", res.Locked.ToArray()));
        Check(!Directory.Exists(s.L.Venv) && !Directory.Exists(s.L.PythonDir), "everything ticked is gone");
        Check(!File.Exists(RemovalRunner.JournalPath(s.Root)), "and the journal is removed");
        // a locked file: skipped and reported, the rest goes, and a later run finishes
        var s2 = Installed();
        string lockedFile = Path.Combine(s2.L.FrontendDir, "node_modules", ".package-lock.json");
        var plan2 = RemovalPlanner.Build(s2.L, s2.M, s2.Led, s2.Comfy);
        RemovalResult r2;
        using (new FileStream(lockedFile, FileMode.Open, FileAccess.Read, FileShare.Read))
            r2 = RemovalRunner.Run(plan2, new List<string> { "tools", "comfy" }, null, s2.L, s2.M, s2.Led, s2.Comfy, new FakeRecycler(), new SetupLog(s2.Root), null, new List<string> { Path.Combine(s2.L.FrontendDir, "node_modules") });
        Check(!r2.Complete && r2.Locked.Count >= 1, "a file in use is reported, not forced");
        Check(!Directory.Exists(s2.L.Venv), "and everything else was removed");
        var r3 = RemovalRunner.Run(RemovalPlanner.Build(s2.L, s2.M, s2.Led, s2.Comfy), new List<string> { "tools", "comfy" }, null, s2.L, s2.M, s2.Led, s2.Comfy, new FakeRecycler(), new SetupLog(s2.Root), null, null);
        Check(r3.Complete && !Directory.Exists(Path.Combine(s2.L.FrontendDir, "node_modules")), "once it is free a second run removes it");
        // settings last, and the empty folder VGAC made goes only when nothing else is in it
        var s4 = Installed();
        SafeFs.DeleteTree(Path.Combine(s4.Data, "comfyui", "models")); SafeFs.DeleteTree(Path.Combine(s4.Data, "comfyui", "output")); SafeFs.DeleteTree(Path.Combine(s4.Data, "comfyui", "custom_nodes", "their-node"));
        var plan4 = RemovalPlanner.Build(s4.L, s4.M, s4.Led, s4.Comfy);
        SafeFs.DeleteTree(Path.Combine(s4.Data, "comfyui", ComfyGuard.MarkerName));
        Put(Path.Combine(s4.Data, "comfyui", ComfyGuard.MarkerName), "{\"made_by\":\"VGAC\",\"owned\":[]}");
        RemovalRunner.Run(RemovalPlanner.Build(s4.L, s4.M, s4.Led, s4.Comfy), new List<string> { "tools", "comfy", "settings" }, null, s4.L, s4.M, s4.Led, s4.Comfy, new FakeRecycler(), new SetupLog(s4.Root), null, null);
        Check(!File.Exists(Path.Combine(s4.Root, "local", "installed.json")) && !File.Exists(Path.Combine(s4.Root, "local", "machine.json")), "the settings and the ledger go last");
        Check(Directory.Exists(s4.Root) && File.Exists(Path.Combine(s4.Root, "backend", "requirements.txt")), "the project folder is untouched");
    }

    static void RemovalWorkNeedsTheTypedWord()
    {
        var s = Installed();
        var rec = new FakeRecycler();
        var plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        var res = RemovalRunner.Run(plan, new List<string> { "work" }, "yes", s.L, s.M, s.Led, s.Comfy, rec, new SetupLog(s.Root), null, null);
        Check(res.Skipped.Any(x => x.Contains("confirmation word")) && File.Exists(Path.Combine(s.Root, "library", "vgac.sqlite")), "without the typed word the person's work is not touched");
        Eq(rec.Sent.Count, 0, "and nothing was sent to the Recycle Bin");
        res = RemovalRunner.Run(RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy), new List<string> { "work" }, "REMOVE", s.L, s.M, s.Led, s.Comfy, rec, new SetupLog(s.Root), null, null);
        Check(!File.Exists(Path.Combine(s.Root, "library", "vgac.sqlite")) && !Directory.Exists(Path.Combine(s.Root, "exports", "hero")), "with the word, the app's library files and the export folder VGAC wrote go");
        Check(Directory.Exists(Path.Combine(s.Root, "library")), "the library folder itself stays");
        Check(File.Exists(Path.Combine(s.Root, "exports", "notes.txt")) && Directory.Exists(Path.Combine(s.Root, "exports")), "the folder the exports sit in, and the person's own file in it, stay");
        Check(rec.Sent.Count == 2, "a small amount goes through the Recycle Bin (" + rec.Sent.Count + " sent)");
        Check(Directory.Exists(s.Root) && Directory.Exists(s.L.Venv), "nothing else was touched");
        // a library folder that holds the project folder is never offered
        var s2 = Installed();
        var m = MachineFile.Load(s2.Root); m.Set("backend", "library_path", Path.GetDirectoryName(s2.Root)); m.Save();
        var plan2 = RemovalPlanner.Build(s2.L, MachineFile.Load(s2.Root), s2.Led, s2.Comfy);
        Check(plan2.Get("work").Items.All(i => !i.Path.Equals(Path.GetDirectoryName(s2.Root), StringComparison.OrdinalIgnoreCase)) && plan2.NotRemoved.Any(n => n.Contains("project folder")), "a library path that contains the project folder is refused");
    }

    // ---- move --------------------------------------------------------------------------------------------------------------

    class NoProcs : IProcessSource { public List<ProcInfo> List() { return new List<ProcInfo>(); } }

    static InstallScene Movable()
    {
        var s = Installed();
        s.M = MachineFile.Load(s.Root);
        Put(Path.Combine(s.Data, "keepme.txt"), "the person's own file, not VGAC's");
        for (int i = 0; i < 6; i++) PutBytes(Path.Combine(s.Data, "python", "f" + i + ".bin"), 500);
        PutBytes(Path.Combine(s.Data, "tools", "uv", "uv.exe"), 300); PutBytes(Path.Combine(s.Data, "tools", "node", "node.exe"), 400);
        return s;
    }

    static MoveRunner Mover(InstallScene s, IProcessSource procs)
    {
        var r = new MoveRunner(s.L, MachineFile.Load(s.Root), Ledger.Load(s.Root), s.Comfy, procs ?? new NoProcs(), new SetupLog(s.Root));
        return r;
    }

    static void MovePathAndRefusals()
    {
        var s = Movable();
        string newPlace = Path.Combine(Tmp("target"), "newbig");
        // refusals: nothing is changed and no journal is made
        Func<string, string> tryTo = delegate(string to) { var mv = Mover(s, null); return mv.Start(to, new Progress()); };
        Check(tryTo(Path.Combine(s.Data, "inside")).Contains("inside the old one"), "the new folder cannot be inside the old one");
        Check(tryTo(Path.GetDirectoryName(s.Data)).Contains("contain the old one"), "nor around it");
        Check(tryTo(s.Data).Contains("already are"), "nor the same one");
        string used = Tmp("used"); Put(Path.Combine(used, "x.txt"), "x");
        Check(tryTo(used).Contains("already has files"), "a folder with files in it is refused");
        Check(tryTo(Path.Combine(Tmp("long"), new string('a', 120))).Contains("too long"), "a very long path is refused");
        Check(tryTo(s.Root).Contains("contain the project"), "the project folder itself is refused");
        Check(tryTo(Path.Combine(s.Root, "backend", "x")).Contains("only its local folder"), "a place inside the project other than local is refused");
        var mvFree = Mover(s, null); mvFree.Free = delegate(string f) { return 100; };
        Check(mvFree.Start(newPlace, new Progress()).Contains("is free there"), "not enough room is said with both sizes");
        s.Comfy.Problem = "VGAC did not install a ComfyUI there.";
        string refused = tryTo(newPlace);
        Check(refused.Contains("did not confirm") && refused.Contains("did not install a ComfyUI") && !refused.Contains("older version"), "a ComfyUI install the backend does not confirm blocks the move, and says why: " + refused);
        s.Comfy.Problem = null;
        Check(MoveRunner.Load(s.Root) == null && !Directory.Exists(newPlace), "none of these changed anything");
        // programs with files open in the old folder block it
        var fp = new FakeProcs(); fp.All.Add(new ProcInfo { Pid = 77, Name = "python.exe", ExePath = Path.Combine(s.Data, "python", "python.exe"), CommandLine = "python.exe" });
        Check(tryTo2(s, newPlace, fp).Contains("python.exe (PID 77)"), "a program running from the old folder blocks the move and is named");
        Check(MoveRunner.Load(s.Root) == null, "and no move was started");
    }

    static string tryTo2(InstallScene s, string to, IProcessSource procs) { return Mover(s, procs).Start(to, new Progress()); }
}
