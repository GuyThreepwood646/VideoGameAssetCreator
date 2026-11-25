// The restart the backend asks for (local/restart-request.json): reading the request, ignoring old or repeated ones, and
// stopping the backend while the engines it named keep running.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using Vgac.Starter;

public static partial class T
{
    static void RestartAll()
    {
        RestartRequestIsRead();
        RestartRequestIsJudged();
        RestartKeepingLeavesTheNamedEnginesAlone();
        RestartKeepingStopsAnythingElseTheBackendStarted();
        RestartKeepingNeverTouchesAnIdThatIsNotBelowTheBackend();
        RestartKeepingWithRealProcesses();
        StartSwitchStopsAtWhatNeedsAPerson();
    }

    static void StartSwitchStopsAtWhatNeedsAPerson()
    {
        Check(UnattendedStart.Allowed(new List<string> { "screens" }), "--start goes through the screens' build");
        Check(UnattendedStart.Allowed(new List<string>()), "and through nothing to do");
        foreach (string s in new[] { "uv", "venv", "packages", "node" })
            Check(!UnattendedStart.Allowed(new List<string> { s, "screens" }), "--start stops at " + s + ", which downloads and needs the person's yes");
    }

    static string RequestText(string id, long time, string engines)
    {
        return "{\"id\": \"" + id + "\", \"kind\": \"restart\", \"time\": " + time + ", \"engines\": \"" + engines + "\", \"force\": true, \"keep_pids\": [101], \"restart_engines\": [\"a\", \"b\"]}";
    }

    static void RestartRequestIsRead()
    {
        RestartRequest r = RestartRequests.Parse(RequestText("abc", 1000, "keep"));
        Check(r != null && r.Id == "abc" && r.Time == 1000 && r.KeepEngines && r.Force, "a request is read");
        Check(r != null && r.KeepPids.Count == 1 && r.KeepPids[0] == 101 && r.RestartEngines.Count == 2, "its engine lists are read");
        Check(RestartRequests.Parse(RequestText("abc", 1000, "restart")).KeepEngines == false, "engines restart is read");
        Check(RestartRequests.Parse(RequestText("abc", 1000, "maybe")) == null, "an unknown engines word is refused");
        Check(RestartRequests.Parse("{\"time\": 5}") == null, "a request with no id is refused");
        RestartRequest q = RestartRequests.Parse("{\"id\": \"q1\", \"kind\": \"quit\", \"time\": 1000, \"force\": false}");
        Check(q != null && q.Quit && !q.Force, "a quit request is read");
        Check(!RestartRequests.Parse(RequestText("abc", 1000, "keep")).Quit, "a restart request is not a quit");
        Check(RestartRequests.Parse("{\"id\": \"x\", \"kind\": \"reboot\", \"time\": 1000}") == null, "an unknown kind is refused");
        Check(RestartRequests.Parse("{\"id\": \"x\", \"time\": 1000}") == null, "a request with no kind is refused");
        Check(RestartRequests.Parse("not json") == null, "text that is not a request is refused");
    }

    static void RestartRequestIsJudged()
    {
        var handled = new HashSet<string>();
        RestartRequest r = RestartRequests.Parse(RequestText("one", 1000, "keep"));
        Check(RestartRequests.Judge(r, 1010, handled, false) == RequestVerdict.Act, "a fresh request is acted on");
        Check(RestartRequests.Judge(r, 1010, handled, true) == RequestVerdict.Wait, "a busy launcher waits and keeps it");
        Check(RestartRequests.Judge(r, 1000 + RestartRequests.MaxAgeSeconds + 1, handled, true) == RequestVerdict.Drop, "an old request is dropped even while busy");
        Check(RestartRequests.Judge(r, 1000 + RestartRequests.MaxAgeSeconds + 1, handled, false) == RequestVerdict.Drop, "an old request is dropped");
        Check(RestartRequests.Judge(RestartRequests.Parse(RequestText("future", 5000, "keep")), 1000, handled, false) == RequestVerdict.Drop, "a request dated in the future is dropped");
        handled.Add("one");
        Check(RestartRequests.Judge(r, 1010, handled, false) == RequestVerdict.Drop, "an id acted on once is never acted on again");
        Check(RestartRequests.Judge(null, 1010, handled, false) == RequestVerdict.Drop, "an unreadable request is dropped");
    }

    static void RestartKeepingLeavesTheNamedEnginesAlone()
    {
        FakeProcs f = Scene();   // backend 100 -> 110 (script) -> 101 (engine b); 100 has another child 400 older than it
        List<ProcInfo> kept;
        List<ProcInfo> stop = RestartRequests.StopKeeping(f.List(), 100, new List<int> { 110 }, out kept);
        Eq(string.Join(",", stop.Select(x => x.Pid.ToString()).ToArray()), "100", "only the backend is stopped when the engine's script is kept");
        Check(kept.Count == 1 && kept[0].Pid == 110, "the kept engine root is remembered with its start time");
    }

    static void RestartKeepingStopsAnythingElseTheBackendStarted()
    {
        FakeProcs f = Scene();
        f.All.Add(P(120, 100, "C:\\Blender\\blender.exe", "blender.exe --background", 1000 * Sec + 40 * Sec));
        List<ProcInfo> kept;
        List<ProcInfo> stop = RestartRequests.StopKeeping(f.List(), 100, new List<int> { 110 }, out kept);
        Eq(string.Join(",", stop.Select(x => x.Pid.ToString()).ToArray()), "120,100", "a step process the backend started is stopped, then the backend");
        stop = RestartRequests.StopKeeping(f.List(), 100, new List<int>(), out kept);
        Eq(string.Join(",", stop.Select(x => x.Pid.ToString()).ToArray()), "101,110,120,100", "with nothing to keep the whole tree is stopped, as the tray does");
    }

    static void RestartKeepingNeverTouchesAnIdThatIsNotBelowTheBackend()
    {
        FakeProcs f = Scene();
        List<ProcInfo> kept;
        // 200 is an engine the person started: it is not in the tree, so naming it keeps nothing and stops nothing extra
        List<ProcInfo> stop = RestartRequests.StopKeeping(f.List(), 100, new List<int> { 200, 100 }, out kept);
        Eq(string.Join(",", stop.Select(x => x.Pid.ToString()).ToArray()), "101,110,100", "an id outside the tree, or the backend's own, protects nothing");
        Check(kept.Count == 0, "and nothing is remembered as kept");
    }

    // A real parent with a real child standing in for the engine: the restart ends the parent only, and the kept child is
    // stopped when the launcher later stops the backend (as at Quit).
    static void RestartKeepingWithRealProcesses()
    {
        string root; var log = new SetupLog(Path.GetTempPath());
        string dir = Path.Combine(Path.GetTempPath(), "vgac-keep-" + Guid.NewGuid().ToString("N").Substring(0, 8));
        temps.Add(dir); Directory.CreateDirectory(dir);
        var parent = NativeProcess.Start(Path.Combine(Environment.SystemDirectory, "cmd.exe"), "/c ping -n 60 127.0.0.1 >nul", dir, Path.Combine(dir, "p.log"), null);
        try
        {
            int childPid = 0;
            for (int i = 0; i < 30 && childPid == 0; i++)
            {
                Thread.Sleep(200);
                foreach (ProcInfo p in new WmiProcesses().List()) if (p.ParentPid == parent.Pid && p.Name.StartsWith("PING", StringComparison.OrdinalIgnoreCase)) childPid = p.Pid;
            }
            Check(childPid != 0 && IsAlive(parent.Pid), "the stand-in backend and its engine are running");
            var runner = RunnerWithSleepingPython(out root);
            Check(runner.Own(parent.Pid, parent.CreatedTicks, false), "the stand-in backend is owned");
            var stopped = runner.StopKeepingEngines(new List<int> { childPid }, log);
            Thread.Sleep(400);
            Check(stopped.Contains(parent.Pid) && !runner.Owned, "the backend was stopped and is no longer owned");
            Check(IsAlive(childPid) && !stopped.Contains(childPid), "the named engine was left running");
            runner.Shutdown(log);
            Thread.Sleep(400);
            Check(!IsAlive(childPid), "stopping the app later stops the engine a restart had left");
        }
        finally { ProcessIdentity.Close(parent.Handle); }
    }
}
