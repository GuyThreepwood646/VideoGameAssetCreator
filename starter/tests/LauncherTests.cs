// Tests for the launcher's decisions and process handling (phase 8): the bridge's rules, the status rows, the quit
// question, which processes belong to the starter, taking over a backend after a crash, one copy per project, and
// that the backend's output goes to a file so a starter that dies leaves it running and logging.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using Vgac.Starter;

public class FakeProcs : IProcessSource
{
    public List<ProcInfo> All = new List<ProcInfo>();
    public List<ProcInfo> List() { return new List<ProcInfo>(All); }
}

public class FakeKiller : IKiller
{
    public List<int> Killed = new List<int>();
    public Dictionary<int, long> Identity = new Dictionary<int, long>();   // what each id really is; others match anything
    public bool Refuse;
    public bool Kill(int pid, long created)
    {
        if (Refuse || created <= 0) return false;
        long real;
        if (Identity.TryGetValue(pid, out real) && Math.Abs(real - created) > Adoption.TickTolerance) return false;
        Killed.Add(pid);
        return true;
    }
}

public class FakeActions : IHostActions
{
    public int Count = -1; public byte[] Png; public string Title, Body; public bool Shown = true; public int Restarts;
    public void SetWork(int count, byte[] png) { Count = count; Png = png; }
    public bool Notify(string title, string body) { Title = title; Body = body; return Shown; }
    public bool StartEngine() { Restarts++; return true; }
}

public static partial class T
{
    const long Sec = 10000000;

    static ProcInfo P(int pid, int ppid, string exe, string cmd, long created)
    {
        var p = new ProcInfo(); p.Pid = pid; p.ParentPid = ppid; p.Name = Path.GetFileName(exe); p.ExePath = exe; p.CommandLine = cmd; p.CreatedTicks = created;
        return p;
    }

    static string PngDataUrl(int extra)
    {
        var bytes = new List<byte> { 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A };
        for (int i = 0; i < extra; i++) bytes.Add(1);
        return "data:image/png;base64," + Convert.ToBase64String(bytes.ToArray());
    }

    static void LauncherAll(string[] args)
    {
        BridgeRulesMatchTheElectronOnes();
        BridgeCallsGoToTheLauncher();
        StatusRowsBackend();
        StatusRowsComfy();
        StatusSummaryAndTooltip();
        QuitAsksOnlyWhenSomethingWouldBeLost();
        QuitTargetsOnlyTheOwnedTree();
        AdoptionNeedsEveryDetailToMatch();
        AdoptionAgainstTheRecordFile();
        SecondStartReachesTheFirst();
        HealthAnswersAreRead();
        RestartIsAskedWhenSomethingWouldBeLost();
        KillerWorksOnARealTree();
        SmallQuitAndStartRules();
        ARunningBackendIsNotStartedTwiceAndItsEnginesAreSwept();
        QuitCannotBeFollowedByAStart();
        RealProcessListIsReadable();
        BackendOutputGoesToAFileAndSurvivesTheStarter();
        MachineSettingsForTheLauncher();
    }

    static void BridgeRulesMatchTheElectronOnes()
    {
        Eq(BridgeRules.CleanCount(3), 3, "count 3");
        Eq(BridgeRules.CleanCount(3.9), 3, "count is a whole number");
        Eq(BridgeRules.CleanCount(250), 99, "count is at most 99");
        Eq(BridgeRules.CleanCount(-4), 0, "negative count clears");
        Eq(BridgeRules.CleanCount("x"), 0, "text count clears");
        Eq(BridgeRules.CleanCount(null), 0, "no count clears");
        Check(BridgeRules.CleanIcon(PngDataUrl(10)) != null, "a small PNG data URL is accepted");
        Check(BridgeRules.CleanIcon("data:image/jpeg;base64,AAAA") == null, "not a PNG data URL");
        Check(BridgeRules.CleanIcon("C:\\x.png") == null, "a path is refused");
        Check(BridgeRules.CleanIcon("http://example.com/a.png") == null, "a URL is refused");
        Check(BridgeRules.CleanIcon(PngDataUrl(BridgeRules.MaxIconBytes + 100)) == null, "an icon larger than a badge is refused");
        Check(BridgeRules.CleanIcon("data:image/png;base64,bm90IGEgcG5n") == null, "bytes that are not a PNG are refused");
        Check(BridgeRules.CleanIcon("data:image/png;base64,@@@@") == null, "bad characters are refused");
        Eq(BridgeRules.CleanText("a\u0001b\nc", 80), "a b c", "control characters become spaces");
        Eq(BridgeRules.CleanText(new string('x', 100), 80).Length, 80, "text is cut to the limit");
        Eq(BridgeRules.CleanText(null, 80), "", "no text is empty");
    }

    static void BridgeCallsGoToTheLauncher()
    {
        var ui = new FakePickers(); var act = new FakeActions();
        Func<string, object> run = delegate(string json) { return BridgeOps.Handle(Json.ParseObject(json), ui, act); };
        Eq(run("{\"id\":1,\"op\":\"setWork\",\"args\":{\"count\":4,\"icon\":\"" + PngDataUrl(5) + "\"}}"), true, "setWork answers true");
        Eq(act.Count, 4, "badge count passed on"); Check(act.Png != null, "badge picture passed on");
        run("{\"id\":2,\"op\":\"setWork\",\"args\":{\"count\":0,\"icon\":\"" + PngDataUrl(5) + "\"}}");
        Eq(act.Count, 0, "zero clears the badge"); Check(act.Png == null, "no picture when clearing");
        run("{\"id\":3,\"op\":\"setWork\",\"args\":{\"count\":2,\"icon\":\"C:\\\\evil.png\"}}");
        Check(act.Png == null, "a path is never passed as the badge picture");
        act.Shown = true;
        Eq(run("{\"id\":4,\"op\":\"notify\",\"args\":{\"title\":\"Done\",\"body\":\"Your character is ready\"}}"), true, "notify answers what the launcher said");
        Eq(act.Title, "Done", "title passed on"); Eq(act.Body, "Your character is ready", "body passed on");
        act.Shown = false;
        Eq(run("{\"id\":5,\"op\":\"notify\",\"args\":{\"title\":\"x\",\"body\":\"y\"}}"), false, "not shown when the window is in front");
        Eq(run("{\"id\":6,\"op\":\"startEngine\"}"), true, "startEngine answers true");
        Eq(act.Restarts, 1, "the engine restart was asked for");
        Eq(BridgeOps.Handle(Json.ParseObject("{\"id\":7,\"op\":\"setWork\",\"args\":{\"count\":1}}"), ui), false, "without a launcher the calls do nothing");
        Eq(run("{\"id\":8,\"op\":\"openFile\"}"), null, "an operation not in the list does nothing");
        Check(BridgeOps.Script.Contains("window.vgacDesktop") && BridgeOps.Script.Contains("setWork") && BridgeOps.Script.Contains("startEngine"), "the page gets window.vgacDesktop");
    }

    static StatusInputs Backend(bool up)
    {
        var i = new StatusInputs(); i.BackendUp = up; i.UiHtml = up; i.ComfyAUp = true; i.ComfyBUp = true; i.GpusFromBackend = true;
        return i;
    }

    static void StatusRowsBackend()
    {
        var i = Backend(true); i.BackendOwned = true; i.BackendPid = 123;
        var s = StatusModel.Build(i);
        Eq(s.Get("backend").State, "up", "backend up"); Check(s.Get("backend").Message.Contains("started by this launcher"), "owned backend says so");
        Eq(s.Get("backend").Pid, 123, "owned backend shows its PID");
        i.BackendOwned = false;
        Check(StatusModel.Build(i).Get("backend").Message.Contains("not started by this launcher, left alone on quit"), "foreign backend is left alone");
        i = Backend(false); i.BackendOwned = true; i.BackendAgeSeconds = 12;
        Eq(StatusModel.Build(i).Get("backend").State, "starting", "owned and not answering yet is starting");
        i.BackendAgeSeconds = 200;
        Eq(StatusModel.Build(i).Get("backend").State, "error", "still not answering after the limit is an error");
        i = Backend(false); i.BackendExitCode = 3; i.BackendStartTried = true;
        var r = StatusModel.Build(i).Get("backend");
        Eq(r.State, "error", "an exited backend is an error"); Check(r.Message.Contains("code 3"), "exit code shown"); Eq(r.Actions[0].Id, "startBackend", "Start again offered");
        i = Backend(false); i.ForeignBackendPid = 77; i.BackendStartTried = true;
        Check(StatusModel.Build(i).Get("backend").Message.Contains("PID 77"), "a backend starting outside is waited for");
        i = Backend(false); i.BackendStartTried = true;
        Eq(StatusModel.Build(i).Get("backend").State, "down", "not running and nothing else going on is down");
        i = Backend(true); i.UiHtml = false;
        Check(StatusModel.Build(i).Get("ui").Message.Contains("restart it"), "backend up but not serving the screens says restart");
        i = Backend(true); i.UiHtml = false; i.Building = true;
        Eq(StatusModel.Build(i).Get("ui").State, "building", "building");
        i = Backend(true); i.UiHtml = false; i.BuildError = "The build failed";
        Eq(StatusModel.Build(i).Get("ui").Actions[0].Id, "rebuild", "a failed build offers Build again");
    }

    static void StatusRowsComfy()
    {
        var i = Backend(true); i.B.Owned = true; i.A.Owned = false;
        var s = StatusModel.Build(i);
        Check(s.Get("comfyB").Message.Contains("started by the app"), "an engine the app started says so");
        Eq(s.Get("comfyB").Actions.Count, 0, "no Restart on a running ComfyUI: this program never stops one");
        i.B.Owned = false;
        Check(StatusModel.Build(i).Get("comfyB").Message.Contains("not started by this launcher"), "an engine nobody here started is left alone");
        Eq(StatusModel.Build(i).Get("comfyB").Actions.Count, 0, "no way to stop it from here either");
        i = Backend(true); i.ComfyBUp = false; i.B.CanStart = true;
        var row = StatusModel.Build(i).Get("comfyB");
        Eq(row.State, "down", "B not running"); Eq(row.Actions[0].Id, "startB", "Start asks the app");
        i.B.State = "starting";
        Eq(StatusModel.Build(i).Get("comfyB").State, "starting", "the app is starting it");
        i = Backend(true); i.ComfyBUp = false; i.B.Enabled = false;
        row = StatusModel.Build(i).Get("comfyB");
        Eq(row.State, "off", "card off"); Eq(row.Actions[0].Id, "startAnywayB", "Start anyway");
        i.ComfyBUp = true;
        Eq(StatusModel.Build(i).Get("comfyB").Actions[0].Id, "turnOnB", "running while off: only turn on, never stop");
        i = Backend(true); i.ComfyBUp = false; i.StartScriptIgnored = true;
        Check(StatusModel.Build(i).Get("comfyB").Message.Contains("The start script in the settings is no longer used; the app starts this engine itself."), "an ignored start script is said in the row");
        i = Backend(true); i.ComfyAUp = false; i.DesktopExeExists = true;
        Eq(StatusModel.Build(i).Get("comfyA").Actions[0].Id, "openComfyDesktop", "Open ComfyUI Desktop offered when it exists");
        i.DesktopExeExists = false;
        Eq(StatusModel.Build(i).Get("comfyA").Actions.Count, 0, "not offered when the exe is missing");
    }

    static void StatusSummaryAndTooltip()
    {
        var i = Backend(true); i.BackendOwned = true;
        var s = StatusModel.Build(i);
        Check(s.AllUp, "all up"); Eq(s.Summary, "All services up", "summary all up");
        i.B.Enabled = false; i.ComfyBUp = false;
        s = StatusModel.Build(i);
        Check(s.AllUp, "a card turned off in the app counts as fine"); Check(s.Summary.Contains("1 graphics card off in the app"), "off card mentioned");
        i = Backend(false);
        s = StatusModel.Build(i);
        Check(!s.AllUp, "not all up"); Check(s.Summary.StartsWith("3 of 4") || s.Summary.StartsWith("2 of 4"), "n of 4 services up (" + s.Summary + ")");
        Check(s.Tooltip.Length <= 63, "tray tooltip fits");
        var many = StatusModel.Build(Backend(true));
        Check(many.Tooltip.Length <= 63 && many.Tooltip.Contains("4/4"), "tooltip says 4/4");
    }

    static void QuitAsksOnlyWhenSomethingWouldBeLost()
    {
        var q = new QuitInputs();
        Check(!QuitPolicy.Decide(q).Ask, "nothing owned and no setup: quit without a question");
        q.OwnsBackend = true;
        var p = QuitPolicy.Decide(q);
        Check(p.Ask, "owning the backend asks"); Check(!p.DefaultCancel, "no jobs: the default is to quit");
        Check(p.Detail.Contains("ComfyUI Desktop") && p.Detail.Contains("left running"), "says what is left running");
        q.Jobs = 2;
        p = QuitPolicy.Decide(q);
        Check(p.DefaultCancel, "jobs running: the default is Cancel"); Check(p.Detail.Contains("2 jobs") && p.Detail.Contains("lost"), "says how many jobs are lost");
        q.Jobs = 1; Check(QuitPolicy.Decide(q).Detail.Contains("1 job is running"), "one job");
        q = new QuitInputs(); q.Jobs = 3;
        Check(!QuitPolicy.Decide(q).Ask, "jobs on a backend this launcher does not own: nothing of ours is stopped, no question");
        q = new QuitInputs(); q.SetupRunning = true;
        p = QuitPolicy.Decide(q);
        Check(p.Ask && p.Detail.Contains("carries on where it stopped"), "quitting during setup asks and says it continues later");
    }

    // The backend (pid 100) started the ComfyUI B (pid 101, through PowerShell 110). A ComfyUI the person started (200)
    // and ComfyUI Desktop (300, with its own ComfyUI 301) run beside it. Only the backend and what it started are in the tree.
    static FakeProcs Scene()
    {
        var f = new FakeProcs();
        long t = 1000 * Sec;
        f.All.Add(P(1, 0, "C:\\Windows\\explorer.exe", "explorer.exe", t));
        f.All.Add(P(50, 1, "C:\\p\\VGAC.exe", "VGAC.exe", t + 10 * Sec));
        f.All.Add(P(100, 50, "C:\\p\\backend\\.venv\\Scripts\\python.exe", "python.exe -m uvicorn vgac.main:app --host 127.0.0.1 --port 8765", t + 20 * Sec));
        f.All.Add(P(110, 100, "C:\\Windows\\powershell.exe", "powershell.exe -File start-b.ps1", t + 30 * Sec));
        f.All.Add(P(101, 110, "C:\\comfy\\python.exe", "python.exe main.py --port 8189", t + 31 * Sec));
        f.All.Add(P(200, 1, "C:\\comfy\\python.exe", "python.exe main.py --port 8188", t + 5 * Sec));
        f.All.Add(P(300, 1, "C:\\Comfy\\ComfyUI.exe", "ComfyUI.exe", t + 6 * Sec));
        f.All.Add(P(301, 300, "C:\\comfy\\python.exe", "python.exe main.py --port 8190", t + 7 * Sec));
        // a process whose recorded parent id was reused: it is older than the "parent", so it is not a child of it
        f.All.Add(P(400, 100, "C:\\other\\old.exe", "old.exe", t + 1 * Sec));
        return f;
    }

    static void QuitTargetsOnlyTheOwnedTree()
    {
        var f = Scene();
        var order = ProcessTree.Targets(f.List(), 100);
        Eq(string.Join(",", order.Select(x => x.ToString()).ToArray()), "101,110,100", "the backend tree, children before parents");
        Check(!order.Contains(200) && !order.Contains(300) && !order.Contains(301), "an engine the person started and ComfyUI Desktop are not in the tree");
        Check(!order.Contains(400), "an older process with a reused parent id is not in the tree");
        Check(!order.Contains(50), "the launcher itself is not in the tree");
        var k = new FakeKiller();
        var done = ProcessTree.Stop(f, k, 100, 1020 * Sec);
        Eq(string.Join(",", k.Killed.Select(x => x.ToString()).ToArray()), "101,110,100", "only those were killed");
        Eq(done.Count, 3, "three stopped");
        // the id of the root now belongs to another process: the killer refuses it, and the children of the real one still go
        k = new FakeKiller(); k.Identity[100] = 5000 * Sec;
        Check(!ProcessTree.Stop(f, k, 100, 1020 * Sec).Contains(100), "a reused root id is not stopped");
        // a child whose id was reused between the list and the kill is refused as well
        k = new FakeKiller(); k.Identity[101] = 9000 * Sec;
        var done2 = ProcessTree.Stop(f, k, 100, 1020 * Sec);
        Check(!done2.Contains(101) && done2.Contains(110) && done2.Contains(100), "a child that changed is left alone, the rest is stopped");
        // unknown start time of the root: nothing is stopped
        k = new FakeKiller();
        Eq(ProcessTree.Stop(f, k, 100, 0).Count, 0, "an unknown start time stops nothing");
        Eq(k.Killed.Count, 0, "and nothing was killed");
        // a child with an unknown start time is left alone
        var f2 = Scene(); f2.All.First(p => p.Pid == 101).CreatedTicks = 0;
        k = new FakeKiller();
        Check(!ProcessTree.Stop(f2, k, 100, 1020 * Sec).Contains(101), "a child with no known start time is not stopped");
        // the process list is empty or fails: the root, whose identity is known, is still stopped
        k = new FakeKiller();
        Eq(string.Join(",", ProcessTree.Stop(new FakeProcs(), k, 100, 1020 * Sec).Select(x => x.ToString()).ToArray()), "100", "an empty list still stops the owned root");
        // already gone
        k = new FakeKiller(); k.Refuse = true;
        Eq(ProcessTree.Stop(f, k, 999, 1 * Sec).Count, 0, "a process that is gone is not an error");
    }

    static void AdoptionNeedsEveryDetailToMatch()
    {
        string venv = "C:\\p\\backend\\.venv\\Scripts\\python.exe";
        string cmd = "\"" + venv + "\" -m uvicorn vgac.main:app --host 127.0.0.1 --port 8765";
        var rec = new OwnedRecord(); rec.Pid = 100; rec.CreatedTicks = 1020 * Sec; rec.Exe = venv; rec.CommandLine = cmd;
        Check(Adoption.Matches(rec, P(100, 1, venv, cmd, 1021 * Sec), venv, 8765), "same id, time, program and command line: match");
        Check(!Adoption.Matches(rec, P(100, 1, venv, cmd, 3000 * Sec), venv, 8765), "same id, other creation time (the id was reused): no match");
        Check(!Adoption.Matches(rec, P(100, 1, "C:\\Windows\\notepad.exe", "notepad.exe", 1020 * Sec), venv, 8765), "the id now belongs to another program: no match");
        Check(!Adoption.Matches(rec, P(100, 1, venv, cmd + " --reload", 1020 * Sec), venv, 8765), "a different command line: no match");
        Check(!Adoption.Matches(rec, P(101, 1, venv, cmd, 1020 * Sec), venv, 8765), "another id: no match");
        Check(!Adoption.Matches(rec, null, venv, 8765), "the process is gone: no match");
        Check(!Adoption.Matches(rec, P(100, 1, venv, cmd, 1020 * Sec), "C:\\other\\backend\\.venv\\Scripts\\python.exe", 8765), "not this project's Python: no match");
        Check(!Adoption.Matches(rec, P(100, 1, venv, cmd, 1020 * Sec), venv, 9000), "another port: no match");
        // an engine is never taken over: its program and command line are not the backend's
        string comfy = "C:\\comfy\\python.exe"; string ccmd = "python.exe main.py --port 8189";
        var er = new OwnedRecord(); er.Pid = 101; er.CreatedTicks = 1031 * Sec; er.Exe = comfy; er.CommandLine = ccmd;
        Check(!Adoption.Matches(er, P(101, 110, comfy, ccmd, 1031 * Sec), venv, 8765), "a ComfyUI is never adopted");
        Check(Adoption.LooksLikeBackend(P(1, 0, venv, "python.exe -m uvicorn vgac.main:app --host 127.0.0.1 --port 8765", 0), 8765), "looks like the backend");
        Check(!Adoption.LooksLikeBackend(P(1, 0, comfy, ccmd, 0), 8765), "ComfyUI does not look like the backend");
        Check(!Adoption.LooksLikeBackend(P(1, 0, "C:\\Windows\\System32\\cmd.exe", "cmd.exe /c python.exe -m uvicorn vgac.main:app --host 127.0.0.1 --port 8765", 0), 8765), "a shell that only mentions it is not the backend");
        Check(!Adoption.LooksLikeBackend(P(1, 0, venv, "python.exe -m uvicorn vgac.main:app --host 127.0.0.1 --port 87650", 0), 8765), "--port 87650 is not port 8765");
        Check(Adoption.LooksLikeBackend(P(1, 0, venv, "python.exe -m uvicorn vgac.main:app --host 127.0.0.1 --port 8765", 0), 8765), "the exact port matches");
        Check(!Adoption.Matches(rec, P(100, 1, venv, cmd.Replace("8765", "87650"), 1020 * Sec), venv, 8765), "adoption does not match a longer port either");
    }

    static void AdoptionAgainstTheRecordFile()
    {
        string root = NewProject();
        var lay = Layout.From(root, MachineFile.Load(root));
        string venv = lay.VenvPython;
        string cmd = "\"" + venv + "\" -m uvicorn vgac.main:app --host 127.0.0.1 --port 8765";
        var log = new SetupLog(root);
        int me = Process.GetCurrentProcess().Id;
        long mine = ProcessIdentity.StartTicksOf(me);
        Check(mine > 0, "this program's own start time can be read");
        var rec = new OwnedRecord(); rec.Pid = me; rec.CreatedTicks = mine; rec.Exe = venv; rec.CommandLine = cmd;

        // match: the record and the running process agree; the backend is taken over, so Quit would stop it
        Adoption.Save(root, rec);
        var fp = new FakeProcs(); fp.All.Add(P(me, 1, venv, cmd, mine));
        var b = new BackendRunner(lay, 8765); b.Procs = fp;
        Check(b.TryAdopt(log), "a record that matches is taken over");
        Check(b.Adopted && b.Pid == me && b.Owned && b.Started, "the taken-over backend is owned");
        Eq(Adoption.Load(root).StarterPid, Process.GetCurrentProcess().Id, "the record now names this starter");

        // the id was reused by another program
        Adoption.Save(root, rec);
        fp = new FakeProcs(); fp.All.Add(P(me, 1, "C:\\Windows\\notepad.exe", "notepad.exe", mine));
        b = new BackendRunner(lay, 8765); b.Procs = fp;
        Check(!b.TryAdopt(log), "a process that is not the recorded program is left alone");
        Check(!b.Owned && !b.Started, "and is not owned");
        Check(Adoption.Load(root) != null, "(the start time is the same, so the record is kept: only a different or no start time deletes it)");
        Adoption.Delete(root);

        // the record's start time is not the running process's (the id was reused by a program of the same kind)
        rec.CreatedTicks = mine - 600 * Sec; Adoption.Save(root, rec);
        fp = new FakeProcs(); fp.All.Add(P(me, 1, venv, cmd, mine - 600 * Sec));
        b = new BackendRunner(lay, 8765); b.Procs = fp;
        Check(!b.TryAdopt(log), "a start time that the handle does not confirm is not taken over");
        Check(Adoption.Load(root) == null, "and that record, whose start time differs from the running process, is deleted");
        rec.CreatedTicks = mine;

        // the process list came back empty (a timeout) while the recorded process is running: the record is kept
        Adoption.Save(root, rec);
        b = new BackendRunner(lay, 8765); b.Procs = new FakeProcs();
        Check(!b.TryAdopt(log), "an empty process list adopts nothing");
        Check(Adoption.Load(root) != null, "but keeps the record for the next start");

        // the record is there but the process is gone
        var gone = new OwnedRecord(); gone.Pid = 2000000; gone.CreatedTicks = mine; gone.Exe = venv; gone.CommandLine = cmd;
        Adoption.Save(root, gone);
        b = new BackendRunner(lay, 8765); b.Procs = new FakeProcs();
        Check(!b.TryAdopt(log), "a record whose process is gone adopts nothing");
        Check(Adoption.Load(root) == null, "and the record is deleted");
        Adoption.Save(root, rec); Adoption.Delete(root);

        // no record: a backend found running is foreign
        b = new BackendRunner(lay, 8765); b.Procs = fp;
        Check(!b.TryAdopt(log), "no record, nothing adopted");

        // a corrupt record file
        File.WriteAllText(Adoption.FilePath(root), "{ this is not json");
        Check(Adoption.Load(root) == null, "a corrupt record reads as no record");
        b = new BackendRunner(lay, 8765); b.Procs = fp;
        Check(!b.TryAdopt(log), "a corrupt record adopts nothing and does not throw");
        File.Delete(Adoption.FilePath(root));

        // the starter that wrote the record is still running: its backend is not taken over, and the record stays for it
        var other = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, "PING.EXE"), "-n 30 127.0.0.1");
        other.UseShellExecute = false; other.CreateNoWindow = true; other.RedirectStandardOutput = true;
        var writer = Process.Start(other);
        try
        {
            rec.StarterPid = writer.Id; rec.StarterCreatedTicks = ProcessIdentity.StartTicksOf(writer.Id);
            Adoption.Save(root, rec);
            fp = new FakeProcs(); fp.All.Add(P(me, 1, venv, cmd, mine));
            b = new BackendRunner(lay, 8765); b.Procs = fp;
            Check(!b.TryAdopt(log), "a backend whose starter is still running is not taken over");
            Check(Adoption.Load(root) != null, "and its record is left for that starter");
        }
        finally { try { writer.Kill(); } catch (Exception) { } }
        // once that starter is gone the same record can be used
        writer.WaitForExit();
        Thread.Sleep(300);
        b = new BackendRunner(lay, 8765); b.Procs = fp;
        Check(b.TryAdopt(log), "after that starter has ended its backend can be taken over");
        Check(Adoption.WriterAlive(rec, 1, 1, 0) == false, "no running writer: not alive");
        Check(!Adoption.WriterAlive(rec, rec.StarterPid, rec.StarterCreatedTicks, rec.StarterCreatedTicks), "the writer is this very starter: not 'another one'");

        // a stop that does not happen keeps the record and the ownership
        rec.StarterPid = 0; Adoption.Save(root, rec);
        fp = new FakeProcs(); fp.All.Add(P(me, 1, venv, cmd, mine));
        b = new BackendRunner(lay, 8765); b.Procs = fp; var refusing = new FakeKiller(); refusing.Refuse = true; b.Killer = refusing;
        Check(b.TryAdopt(log), "adopted again for the failed-stop check");
        var stopped = b.StopIfOurs(log);
        Eq(stopped.Count, 0, "nothing was stopped");
        Check(Adoption.Load(root) != null, "the record is kept when nothing was stopped");
        Check(b.Started, "and the process is still held as owned");
        // nothing owned: a record that belongs to someone else is not deleted
        var nobody = new BackendRunner(lay, 8765); nobody.Procs = new FakeProcs();
        nobody.StopIfOurs(log);
        Check(Adoption.Load(root) != null, "a starter that owns nothing leaves the record alone");
        Adoption.Delete(root);
    }

    static void SecondStartReachesTheFirst()
    {
        string key = "test" + Guid.NewGuid().ToString("N").Substring(0, 8);
        int told = 0;
        var evt = new ManualResetEvent(false);
        bool answered;
        using (var first = InstanceGate.TryOwn(key, 100, out answered))
        {
            Check(first != null, "the first start owns the project");
            first.Listen(delegate { Interlocked.Increment(ref told); evt.Set(); first.Ack(); });
            var second = InstanceGate.TryOwn(key, 3000, out answered);
            Check(second == null, "the second start is not given the project");
            Check(evt.WaitOne(3000), "the first copy is told to come forward");
            Check(answered, "and it answers, so the second copy can leave without a message");
            Eq(told, 1, "told once");
        }
        using (var again = InstanceGate.TryOwn(key, 100, out answered)) Check(again != null, "after the first one is gone a new start owns it");
        Check(InstanceGate.KeyFor("C:\\A\\VGAC") != InstanceGate.KeyFor("C:\\B\\VGAC"), "another project folder has its own lock");
        Eq(InstanceGate.KeyFor("C:\\A\\VGAC"), InstanceGate.KeyFor("c:\\a\\vgac"), "the lock ignores letter case");

        // a first copy that is hung: it hears nothing and never answers; the second copy finds that out
        string key2 = "test" + Guid.NewGuid().ToString("N").Substring(0, 8);
        using (var hung = InstanceGate.TryOwn(key2, 100, out answered))
        {
            hung.Listen(delegate { Thread.Sleep(5000); });   // a stuck window: never acknowledges in time
            var sw = Stopwatch.StartNew();
            var second = InstanceGate.TryOwn(key2, 600, out answered);
            Check(second == null && !answered, "a hung first copy does not answer");
            Check(sw.ElapsedMilliseconds < 3000, "and the second copy does not wait for long");
        }
        string key3 = "test" + Guid.NewGuid().ToString("N").Substring(0, 8);
        using (var deaf = InstanceGate.TryOwn(key3, 100, out answered))
        {
            var second = InstanceGate.TryOwn(key3, 400, out answered);
            Check(second == null && !answered, "a first copy that is not listening does not answer");
        }
    }

    static void HealthAnswersAreRead()
    {
        string h = "{\"ok\":true,\"queue\":{\"a\":{\"current_job_id\":\"j1\",\"queued\":2},\"b\":{\"current_job_id\":null,\"queued\":0}}}";
        Check(HealthReader.Ok(h), "health ok"); Eq(HealthReader.Jobs(h), 3, "one running and two waiting");
        Eq(HealthReader.Jobs("{\"ok\":true,\"queue\":{}}"), 0, "no jobs");
        Eq(HealthReader.Jobs("{\"queue\":{\"a\":{\"current_job_id\":\"j1\",\"queued\":0,\"lent_to\":null},\"b\":{\"current_job_id\":null,\"queued\":0,\"lent_to\":\"j1\"}}}"), 1, "a card lent to a job that another card shows is one job");
        Eq(HealthReader.Jobs("{\"queue\":{\"a\":{\"current_job_id\":null,\"queued\":0,\"lent_to\":\"j9\"}}}"), 1, "a card lent to a job counts");
        Eq(HealthReader.Jobs("{\"queue\":{\"host\":{\"quick_job_id\":\"q1\",\"helper_job_ids\":[]}}}"), 1, "a quick export running alone counts");
        Eq(HealthReader.Jobs("{\"queue\":{\"host\":{\"helper_job_ids\":[\"h1\",\"h2\"],\"quick_job_id\":null}}}"), 2, "helper jobs on the host count");
        Eq(HealthReader.Jobs("not json"), 0, "unreadable answer counts as no jobs");
        Check(!HealthReader.Ok("{\"ok\":false}"), "not ok");
        var g = HealthReader.Gpus("[{\"instance\":\"a\",\"enabled\":true,\"state\":\"running\",\"owned\":false,\"can_start\":true},{\"instance\":\"b\",\"enabled\":false,\"state\":\"off\",\"owned\":true,\"can_start\":false}]");
        Check(g["a"].Enabled && g["a"].CanStart && !g["a"].Owned, "card a read"); Check(!g["b"].Enabled && g["b"].Owned, "card b read");
        var pr = HealthReader.GpusFromPrefs("{\"disabled_instances\":[\"b\"]}");
        Check(pr["a"].Enabled && !pr["b"].Enabled, "prefs file read before the backend answers");
    }

    static void RestartIsAskedWhenSomethingWouldBeLost()
    {
        var none = new List<ProcInfo>();
        Check(!RestartPolicy.Decide(true, 0, none).Ask, "an owned backend with no work restarts without a question");
        Check(!RestartPolicy.Decide(false, 0, none).Ask, "starting again when nothing runs is not asked");
        var p = RestartPolicy.Decide(true, 3, none);
        Check(p.Ask && p.DefaultCancel && p.Detail.Contains("3 jobs"), "work in progress: asked, Cancel is the default");
        var foreign = new List<ProcInfo> { P(77, 1, "C:\\x\\python.exe", "python.exe -m uvicorn vgac.main:app --port 8765", 5 * Sec) };
        p = RestartPolicy.Decide(false, 0, foreign);
        Check(p.Ask && p.DefaultCancel && p.Detail.Contains("PID 77"), "a backend this launcher did not start: asked, Cancel is the default");
        Check(p.Detail.Contains("keep running"), "and says the graphics engines keep running");
        // only entries that are still the same process are acted on after the question
        var after = new List<ProcInfo> { P(77, 1, "C:\\x\\python.exe", "python.exe", 5 * Sec) };
        Eq(Adoption.StillSame(foreign, after).Count, 1, "the same process is still a target");
        after = new List<ProcInfo> { P(77, 1, "C:\\other\\app.exe", "app.exe", 900 * Sec) };
        Eq(Adoption.StillSame(foreign, after).Count, 0, "an id reused while the question was open is not a target");
        Eq(Adoption.StillSame(foreign, new List<ProcInfo>()).Count, 0, "a process that ended is not a target");
    }

    // A real small process tree made by this test: cmd.exe running ping (parent and child), and an unrelated ping beside it.
    static void KillerWorksOnARealTree()
    {
        string dir = Path.Combine(Path.GetTempPath(), "vgac-tree-" + Guid.NewGuid().ToString("N").Substring(0, 8));
        temps.Add(dir); Directory.CreateDirectory(dir);
        string cmd = Path.Combine(Environment.SystemDirectory, "cmd.exe");
        var started = NativeProcess.Start(cmd, "/c ping -n 40 127.0.0.1", dir, Path.Combine(dir, "t.log"), null);
        int unrelated = NativeProcess.StartLogged(Path.Combine(Environment.SystemDirectory, "PING.EXE"), "-n 40 127.0.0.1", dir, Path.Combine(dir, "u.log"), null);
        try
        {
            Check(started.CreatedTicks > 0, "the start time comes from the handle");
            Thread.Sleep(1500);
            var src = new WmiProcesses();
            Check(ProcessTree.Targets(src.List(), started.Pid).Count >= 2, "the tree has the shell and its ping");
            long unrelatedStart = ProcessIdentity.StartTicksOf(unrelated);
            // the wrong start time never stops a process
            Check(!new ProcessKiller().Kill(unrelated, unrelatedStart + 3000 * Sec), "a process is not stopped when its start time differs");
            Check(!new ProcessKiller().Kill(unrelated, 0), "a process is not stopped when the start time is unknown");
            Check(IsAlive(unrelated), "the unrelated process is untouched");
            var done = ProcessTree.Stop(src, new ProcessKiller(), started.Pid, started.CreatedTicks);
            Check(done.Count >= 2 && done.Contains(started.Pid), "the tree was stopped (" + done.Count + " processes)");
            Thread.Sleep(500);
            Check(!ProcessIdentity.IsRunning(started.Handle), "the held handle sees the root is gone");
            Check(IsAlive(unrelated), "a process beside the tree is still running");
        }
        finally
        {
            ProcessIdentity.Close(started.Handle);
            try { Process.GetProcessById(unrelated).Kill(); } catch (Exception) { }
        }
    }

    // Quit and restart against the start of a backend. The backend's Python is a copy of this test program that sleeps.
    static BackendRunner RunnerWithSleepingPython(out string root)
    {
        root = NewProject();
        var lay = Layout.From(root, MachineFile.Load(root));
        Directory.CreateDirectory(Path.GetDirectoryName(lay.VenvPython));
        File.Copy(System.Reflection.Assembly.GetExecutingAssembly().Location, lay.VenvPython, true);
        return new BackendRunner(lay, FreePortLocal());
    }

    static int FreePortLocal()
    {
        var l = new System.Net.Sockets.TcpListener(System.Net.IPAddress.Loopback, 0); l.Start();
        int p = ((System.Net.IPEndPoint)l.LocalEndpoint).Port; l.Stop(); return p;
    }

    static void SmallQuitAndStartRules()
    {
        Check(!QuitPolicy.AutoExitAllowed(true, 2), "--exit-after does not quit an owned backend that has jobs");
        Check(QuitPolicy.AutoExitAllowed(true, 0), "--exit-after quits when no jobs are running");
        Check(QuitPolicy.AutoExitAllowed(false, 5), "--exit-after quits when nothing is owned");
        Check(!QuitPolicy.MayStartBackend(true), "no backend is started once quitting has begun");
        Check(QuitPolicy.MayStartBackend(false), "a backend may start otherwise");
    }

    static void ARunningBackendIsNotStartedTwiceAndItsEnginesAreSwept()
    {
        string root; var log = new SetupLog(Path.GetTempPath());
        var b = RunnerWithSleepingPython(out root);
        b.LaunchProcess(log);
        StarterError got = null;
        try { b.LaunchProcess(log); } catch (StarterError e) { got = e; }
        Check(got != null && got.Message.Contains("already running"), "a second start while one is owned is refused");
        b.Shutdown(log);

        // the backend ended by itself but left a child (an engine): the stop still sweeps the child
        string dir = Path.Combine(Path.GetTempPath(), "vgac-orphan-" + Guid.NewGuid().ToString("N").Substring(0, 8));
        temps.Add(dir); Directory.CreateDirectory(dir);
        var parent = NativeProcess.Start(Path.Combine(Environment.SystemDirectory, "cmd.exe"), "/c start /b ping -n 60 127.0.0.1 >nul & exit", dir, Path.Combine(dir, "p.log"), null);
        try
        {
            int childPid = 0;
            for (int i = 0; i < 30 && childPid == 0; i++)
            {
                Thread.Sleep(200);
                foreach (ProcInfo p in new WmiProcesses().List()) if (p.ParentPid == parent.Pid && p.Name.StartsWith("PING", StringComparison.OrdinalIgnoreCase)) childPid = p.Pid;
            }
            Check(childPid != 0, "the test's child process is running");
            for (int i = 0; i < 30 && ProcessIdentity.IsRunning(parent.Handle); i++) Thread.Sleep(100);
            Check(!ProcessIdentity.IsRunning(parent.Handle), "the test's parent has ended by itself");
            Check(childPid != 0 && IsAlive(childPid), "while its child still runs");
            var runner = RunnerWithSleepingPython(out root);
            Check(runner.Own(parent.Pid, parent.CreatedTicks, false), "the ended backend is still held (its handle is open)");
            var swept = runner.StopIfOurs(log);
            Thread.Sleep(400);
            Check(childPid != 0 && !IsAlive(childPid), "the child of a backend that ended on its own is stopped");
            Check(swept.Contains(childPid), "and reported");
        }
        finally { ProcessIdentity.Close(parent.Handle); }
    }

    static void QuitCannotBeFollowedByAStart()
    {
        string root; var log = new SetupLog(Path.GetTempPath());
        var b = RunnerWithSleepingPython(out root);
        // quit first, then a start that was already on its way: it refuses and nothing runs
        b.Shutdown(log);
        StarterError got = null;
        try { b.LaunchProcess(log); } catch (StarterError e) { got = e; }
        Check(got != null && !b.Started, "a start after Shutdown is refused");
        Check(!b.Own(Process.GetCurrentProcess().Id, ProcessIdentity.StartTicksOf(Process.GetCurrentProcess().Id), true), "and so is taking a process over");

        // a start that finished, then quit: the new backend is stopped and the record deleted
        b = RunnerWithSleepingPython(out root);
        b.LaunchProcess(log);
        int pid = b.Pid;
        Check(b.Owned && IsAlive(pid), "the sleeping backend is running and owned");
        Check(Adoption.Load(root) != null && Adoption.Load(root).StarterPid == Process.GetCurrentProcess().Id, "its record names this starter");
        var done = b.Shutdown(log);
        Thread.Sleep(500);
        Check(done.Contains(pid) && !IsAlive(pid), "Shutdown stops what was started");
        Check(Adoption.Load(root) == null, "and deletes the record after a successful stop");

        // quit racing a restart's start: whichever order, no backend is left running
        for (int round = 0; round < 3; round++)
        {
            b = RunnerWithSleepingPython(out root);
            int launched = 0;
            var t = new Thread(delegate() { try { b.LaunchProcess(log); launched = b.Pid; } catch (StarterError) { } });
            t.Start();
            if (round == 1) Thread.Sleep(30);
            if (round == 2) Thread.Sleep(120);
            b.Shutdown(log);
            t.Join();
            Thread.Sleep(300);
            Check(launched == 0 || !IsAlive(launched), "after quit racing a start (round " + round + ") no backend is left running");
            Check(!b.Owned, "and nothing is owned (round " + round + ")");
        }
    }

    static void RealProcessListIsReadable()
    {
        var all = new WmiProcesses().List();
        int me = Process.GetCurrentProcess().Id;
        ProcInfo mine = all.FirstOrDefault(p => p.Pid == me);
        Check(mine != null, "the process list holds this program");
        if (mine != null)
        {
            Check(mine.CommandLine.Length > 0, "with its command line");
            Check(mine.CreatedTicks > 0, "and its creation time");
            Check(Math.Abs(mine.CreatedTicks - Process.GetCurrentProcess().StartTime.ToUniversalTime().Ticks) < 5 * Sec, "which is the real start time");
        }
    }

    // The backend's output goes to a file the child holds itself. If the starter is killed, the child keeps running
    // and keeps writing: run a helper copy of this test program that starts a slow command and is then killed.
    static void BackendOutputGoesToAFileAndSurvivesTheStarter()
    {
        string dir = Path.Combine(Path.GetTempPath(), "vgac-launch-" + Guid.NewGuid().ToString("N").Substring(0, 8));
        temps.Add(dir);
        Directory.CreateDirectory(dir);
        string logFile = Path.Combine(dir, "out.log");
        string ping = Path.Combine(Environment.SystemDirectory, "PING.EXE");
        var env = new Dictionary<string, string>(); env["VGAC_TEST_FLAG"] = "yes";
        int pid = NativeProcess.StartLogged(Path.Combine(Environment.SystemDirectory, "cmd.exe"), "/c echo flag=%VGAC_TEST_FLAG%& echo to-stderr 1>&2", dir, logFile, env);
        for (int i = 0; i < 30 && IsAlive(pid); i++) Thread.Sleep(100);
        string text = File.ReadAllText(logFile);
        Check(text.Contains("flag=yes"), "the child got the extra environment");
        Check(text.Contains("to-stderr"), "stderr goes to the same file");

        // the starter dies; the child must keep going
        string log2 = Path.Combine(dir, "slow.log");
        var psi = new ProcessStartInfo(System.Reflection.Assembly.GetExecutingAssembly().Location, "--spawn-and-wait \"" + log2 + "\"");
        psi.UseShellExecute = false; psi.RedirectStandardOutput = true; psi.CreateNoWindow = true;
        var helper = Process.Start(psi);
        string line = helper.StandardOutput.ReadLine();
        int child;
        Check(int.TryParse(line, out child), "the helper starter reported the child's id");
        Thread.Sleep(1500);
        long before = new FileInfo(log2).Length;
        helper.Kill();
        helper.WaitForExit();
        Thread.Sleep(2500);
        Check(IsAlive(child), "after the starter was killed the child is still running");
        long after = new FileInfo(log2).Length;
        Check(after > before, "and it still writes its log (" + before + " -> " + after + " bytes)");
        try { Process.GetProcessById(child).Kill(); } catch (Exception) { }
    }

    static bool IsAlive(int pid)
    {
        try { using (var p = Process.GetProcessById(pid)) return !p.HasExited; } catch (Exception) { return false; }
    }

    // Helper mode of this program: start a command that prints a line a second, tell the parent its id, wait to be killed.
    static int SpawnAndWait(string log)
    {
        int pid = NativeProcess.StartLogged(Path.Combine(Environment.SystemDirectory, "PING.EXE"), "-n 12 127.0.0.1", Path.GetDirectoryName(log), log, null);
        Console.WriteLine(pid);
        Console.Out.Flush();
        Thread.Sleep(60000);
        return 0;
    }

    // For a live check of VGAC.exe without the real backend: a project folder whose "Python" is the given program
    // (a stand-in that answers /api/health), with everything already set up so the starter goes straight to starting it.
    static int MakeFakeProject(string root, string fakePython)
    {
        Directory.CreateDirectory(Path.Combine(root, "backend", "vgac"));
        Directory.CreateDirectory(Path.Combine(root, "frontend", "src"));
        File.WriteAllText(Path.Combine(root, "backend", "vgac", "config.py"), "# x");
        File.WriteAllText(Path.Combine(root, "backend", "requirements.txt"), "fastapi==1\n");
        File.WriteAllText(Path.Combine(root, "frontend", "package-lock.json"), "{}");
        File.WriteAllText(Path.Combine(root, "frontend", "package.json"), "{}");
        File.WriteAllText(Path.Combine(root, "frontend", "src", "main.tsx"), "a");
        MachineFile m; Ledger led;
        RunAll(root, new FakeHost(), out m, out led);
        File.Copy(fakePython, Path.Combine(root, "backend", ".venv", "Scripts", "python.exe"), true);
        return 0;
    }

    static void MachineSettingsForTheLauncher()
    {
        string root = NewProject();
        var m = MachineFile.Load(root);
        var d = DesktopSettings.From(m);
        Check(!d.StartScriptSet && d.ComfyDesktopExe == null && d.ComfyAUrl.EndsWith(":8188") && d.ComfyBUrl.EndsWith(":8189"), "defaults without settings");
        Directory.CreateDirectory(Path.Combine(root, "local"));
        File.WriteAllText(Path.Combine(root, "local", "machine.json"),
            "{\"desktop\":{\"comfyA\":{\"desktopExe\":\"C:\\\\Comfy\\\\ComfyUI.exe\",\"url\":\"http://127.0.0.1:9000/\"},\"comfyB\":{\"startScript\":\"x.ps1\"}}}");
        d = DesktopSettings.From(MachineFile.Load(root));
        Check(d.StartScriptSet, "a start script that is set is noticed (it is ignored and said so)");
        Eq(d.ComfyDesktopExe, "C:\\Comfy\\ComfyUI.exe", "the ComfyUI Desktop exe is read from the same key as before");
        Eq(d.ComfyAUrl, "http://127.0.0.1:9000", "the card's address is read");
    }
}
