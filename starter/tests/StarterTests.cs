// Tests for the starter's decisions and file handling. Everything runs in temporary folders with a fake host: no
// network, no real downloads, nothing written in the project. Run with: starter\build.cmd test
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Text;
using Vgac.Starter;

public class FakeHost : IHost
{
    public List<string> Calls = new List<string>();
    public bool CorruptDownload;
    public long Free = 100L << 30;
    public int ExitCode;
    public string NodeVersion = null;

    public void Download(string url, string dest, string sha256, Action<long, long> progress)
    {
        Calls.Add("download " + Path.GetFileName(url));
        if (CorruptDownload) throw new StarterError("The downloaded file from x is not the published one.", "Try again.");
        Directory.CreateDirectory(Path.GetDirectoryName(dest));
        File.WriteAllText(dest, "zip");
        progress(1, 1);
    }

    public void Unzip(string zip, string destDir)
    {
        Calls.Add("unzip " + Path.GetFileName(zip));
        Directory.CreateDirectory(destDir);
        if (zip.Contains("uv")) File.WriteAllText(Path.Combine(destDir, "uv.exe"), "uv");
        else
        {
            string inner = Path.Combine(destDir, "node-v22-win-x64");
            Directory.CreateDirectory(inner);
            File.WriteAllText(Path.Combine(inner, "node.exe"), "node");
        }
    }

    public int Run(string exe, string args, string cwd, Dictionary<string, string> env, string logFile)
    {
        Calls.Add("run " + Path.GetFileName(exe) + " " + args.Split(' ')[0] + (args.StartsWith("/d") ? " " + args.Split(' ')[3] : ""));
        if (ExitCode == 0 && args.StartsWith("venv"))
        {
            // what uv does: a venv with a python in it
            string venv = args.Split('"')[1];
            Directory.CreateDirectory(Path.Combine(venv, "Scripts"));
            File.WriteAllText(Path.Combine(venv, "Scripts", "python.exe"), "py");
        }
        if (ExitCode == 0 && args.Contains("run build"))
        {
            Directory.CreateDirectory(Path.Combine(cwd, "dist"));
            File.WriteAllText(Path.Combine(cwd, "dist", "index.html"), "<html>");
        }
        File.WriteAllText(logFile, "log");
        return ExitCode;
    }

    public string Capture(string exe, string args) { return NodeVersion; }
    public long FreeBytes(string folder) { return Free; }
}

public static class T
{
    static int failed, passed;
    static readonly List<string> temps = new List<string>();

    static void Check(bool ok, string what)
    {
        if (ok) { passed++; return; }
        failed++;
        Console.WriteLine("FAIL: " + what);
    }

    static void Eq(object a, object b, string what) { Check(object.Equals(a, b), what + " (got '" + a + "', wanted '" + b + "')"); }

    // A fake project folder: backend\requirements.txt, backend\vgac\config.py and a small frontend.
    static string NewProject()
    {
        string root = Path.Combine(Path.GetTempPath(), "vgac-starter-test-" + Guid.NewGuid().ToString("N").Substring(0, 8));
        temps.Add(root);
        Directory.CreateDirectory(Path.Combine(root, "backend", "vgac"));
        Directory.CreateDirectory(Path.Combine(root, "frontend", "src"));
        File.WriteAllText(Path.Combine(root, "backend", "vgac", "config.py"), "# x");
        File.WriteAllText(Path.Combine(root, "backend", "requirements.txt"), "fastapi==1\n");
        File.WriteAllText(Path.Combine(root, "frontend", "package-lock.json"), "{}");
        File.WriteAllText(Path.Combine(root, "frontend", "package.json"), "{}");
        File.WriteAllText(Path.Combine(root, "frontend", "src", "main.tsx"), "a");
        return root;
    }

    static List<string> PlanOf(Layout l, FakeHost h) { return Planner.Plan(ProbeReader.Read(l, h), l.ProjectRoot); }

    static void RunAll(string root, FakeHost h, out MachineFile m, out Ledger led)
    {
        m = MachineFile.Load(root);
        var l = Layout.From(root, m);
        led = Ledger.Load(root);
        var steps = PlanOf(l, h);
        new SetupRunner(l, h, m, led, new SetupLog(root)).Run(steps, new Progress());
    }

    public static int Main()
    {
        try
        {
            FreshMachineNeedsEverything();
            SecondStartNeedsNothing();
            ChangedRequirementsOnlyRepackages();
            ChangedFrontendRebuildsScreens();
            MovedProjectRebuildsVenv();
            RunsInOrderAndWritesRecords();
            ChecksumFailureStopsAndRecordsNothing();
            CommandFailureIsPlain();
            NotEnoughSpaceIsRefused();
            OwnNodeIsUsedWhenNewEnough();
            MachineFileKeepsOtherSectionsAndBackup();
            BadMachineFileIsPlainAndUntouched();
            DataFolderChoiceIsRespected();
            DefaultDataFolderIsNotRecorded();
            NotAProjectFolder();
            BackendWaitAndReuse();
            ProjectRootFound();
            VersionParsing();
            ZipSlipRefused();
            DownloadChecksumAndResume();
        }
        finally
        {
            foreach (string t in temps) { try { if (Directory.Exists(t)) Directory.Delete(t, true); } catch (Exception) { } }
        }
        Console.WriteLine(passed + " checks passed, " + failed + " failed");
        return failed == 0 ? 0 : 1;
    }

    static void FreshMachineNeedsEverything()
    {
        string root = NewProject();
        var h = new FakeHost();
        var l = Layout.From(root, MachineFile.Load(root));
        Eq(string.Join(",", PlanOf(l, h).ToArray()), "uv,venv,packages,node,screens", "fresh machine plan");
        Check(l.DataFolder.EndsWith("local\\data"), "default data folder is local\\data in the project");
    }

    static void SecondStartNeedsNothing()
    {
        string root = NewProject();
        var h = new FakeHost();
        MachineFile m; Ledger led;
        RunAll(root, h, out m, out led);
        var l = Layout.From(root, MachineFile.Load(root));
        Eq(PlanOf(l, h).Count, 0, "after a full run nothing is needed");
        Check(File.Exists(l.StatePath), "state.json written");
    }

    static void ChangedRequirementsOnlyRepackages()
    {
        string root = NewProject();
        var h = new FakeHost();
        MachineFile m; Ledger led;
        RunAll(root, h, out m, out led);
        File.WriteAllText(Path.Combine(root, "backend", "requirements.txt"), "fastapi==2\n");
        var l = Layout.From(root, MachineFile.Load(root));
        Eq(string.Join(",", PlanOf(l, h).ToArray()), "packages", "changed requirements");
    }

    static void ChangedFrontendRebuildsScreens()
    {
        string root = NewProject();
        var h = new FakeHost();
        MachineFile m; Ledger led;
        RunAll(root, h, out m, out led);
        File.WriteAllText(Path.Combine(root, "frontend", "src", "main.tsx"), "changed");
        var l = Layout.From(root, MachineFile.Load(root));
        Eq(string.Join(",", PlanOf(l, h).ToArray()), "screens", "changed screens' source (own node is already there)");
        File.Delete(l.DistIndex);
        Eq(string.Join(",", PlanOf(l, h).ToArray()), "screens", "missing dist");
    }

    static void MovedProjectRebuildsVenv()
    {
        string root = NewProject();
        var h = new FakeHost();
        MachineFile m; Ledger led;
        RunAll(root, h, out m, out led);
        // Pretend the install was made in another folder.
        var l = Layout.From(root, MachineFile.Load(root));
        string st = File.ReadAllText(l.StatePath).Replace(root.Replace("\\", "\\\\"), "D:\\\\elsewhere");
        File.WriteAllText(l.StatePath, st);
        Eq(string.Join(",", PlanOf(l, h).ToArray()), "venv,packages", "moved project rebuilds the venv and packages");
    }

    static void RunsInOrderAndWritesRecords()
    {
        string root = NewProject();
        var h = new FakeHost();
        MachineFile m; Ledger led;
        RunAll(root, h, out m, out led);
        string calls = string.Join(" | ", h.Calls.ToArray());
        int iUv = calls.IndexOf("download uv"), iVenv = calls.IndexOf("run uv.exe venv"), iPk = calls.IndexOf("run uv.exe pip"),
            iNode = calls.IndexOf("download node"), iCi = calls.IndexOf("cmd.exe /d ci"), iBuild = calls.IndexOf("cmd.exe /d run");
        Check(iUv >= 0 && iUv < iVenv && iVenv < iPk && iPk < iNode && iNode < iCi && iCi < iBuild, "step order: " + calls);
        var l = Layout.From(root, MachineFile.Load(root));
        Check(File.Exists(l.UvExe), "uv.exe in the data folder");
        Check(File.Exists(Path.Combine(l.NodeDir, "node.exe")), "node.exe in the data folder (inner folder flattened)");
        Check(File.Exists(Path.Combine(root, "local", "installed.json")), "ledger written");
        string ledger = File.ReadAllText(Path.Combine(root, "local", "installed.json"));
        Check(ledger.Contains("\"kind\": \"added\"") && ledger.Contains("Node.js") && ledger.Contains(".venv"), "ledger lists what was added");
        Check(File.Exists(new SetupLog(root).Path) && File.ReadAllText(new SetupLog(root).Path).Contains("step screens done"), "setup.log has the steps");
        Check(!Directory.Exists(l.Downloads) || Directory.GetFiles(l.Downloads).Length == 0, "downloaded zips removed after unpacking");
    }

    static void ChecksumFailureStopsAndRecordsNothing()
    {
        string root = NewProject();
        var h = new FakeHost { CorruptDownload = true };
        var m = MachineFile.Load(root);
        var l = Layout.From(root, m);
        bool threw = false;
        try { new SetupRunner(l, h, m, Ledger.Load(root), new SetupLog(root)).Run(PlanOf(l, h), new Progress()); }
        catch (StarterError e) { threw = true; Check(e.Message.Contains("not the published one"), "plain checksum message"); }
        Check(threw, "checksum failure stops setup");
        Check(!File.Exists(l.StatePath), "no state stamp after a failed run");
        Check(!h.Calls.Any(c => c.StartsWith("run ")), "nothing ran after the failed download");
    }

    static void CommandFailureIsPlain()
    {
        string root = NewProject();
        var h = new FakeHost { ExitCode = 3 };
        var m = MachineFile.Load(root);
        var l = Layout.From(root, m);
        Directory.CreateDirectory(Path.GetDirectoryName(l.UvExe));
        File.WriteAllText(l.UvExe, "uv");
        StarterError got = null;
        try { new SetupRunner(l, h, m, Ledger.Load(root), new SetupLog(root)).Run(PlanOf(l, h), new Progress()); }
        catch (StarterError e) { got = e; }
        Check(got != null && got.Message.Contains("Could not make the app's Python") && got.Fix != null && got.Fix.Contains("online"), "failed venv step says what and what to do");
        Check(!File.Exists(l.StatePath), "no stamp after a failed step");
    }

    static void NotEnoughSpaceIsRefused()
    {
        string root = NewProject();
        var h = new FakeHost { Free = 1L << 30 };
        var m = MachineFile.Load(root);
        var l = Layout.From(root, m);
        StarterError got = null;
        try { new SetupRunner(l, h, m, Ledger.Load(root), new SetupLog(root)).Run(PlanOf(l, h), new Progress()); }
        catch (StarterError e) { got = e; }
        Check(got != null && got.Message.Contains("has 1024 MB free") && got.Fix.Contains("another folder"), "not enough space is refused with the numbers");
        Eq(h.Calls.Count, 0, "nothing downloaded when there is no room");
    }

    static void OwnNodeIsUsedWhenNewEnough()
    {
        string root = NewProject();
        var h = new FakeHost();
        var l = Layout.From(root, MachineFile.Load(root));
        Directory.CreateDirectory(l.NodeDir);
        File.WriteAllText(Path.Combine(l.NodeDir, "node.exe"), "n");
        Check(!PlanOf(l, h).Contains("node"), "a node already in the data folder is not fetched again");
    }

    static void MachineFileKeepsOtherSectionsAndBackup()
    {
        string root = NewProject();
        Directory.CreateDirectory(Path.Combine(root, "local"));
        string path = Path.Combine(root, "local", "machine.json");
        File.WriteAllText(path, "{\"backend\": {\"blender_exe\": \"C:\\\\B\\\\blender.exe\", \"instances\": {\"a\": {\"url\": \"http://127.0.0.1:8188\"}}}, \"desktop\": {\"x\": 1}}");
        var m = MachineFile.Load(root);
        m.Set("starter", "dataFolder", "D:\\Big");
        m.Save();
        var again = MachineFile.Load(root);
        Eq(again.GetString("starter", "dataFolder"), "D:\\Big", "starter section saved");
        Eq(again.GetString("backend", "blender_exe"), "C:\\B\\blender.exe", "backend section kept");
        Check(File.ReadAllText(path).Contains("\"x\": 1"), "desktop section kept");
        Check(File.Exists(path + ".bak") && !File.ReadAllText(path + ".bak").Contains("starter"), "previous file kept as .bak");
        Check(!File.Exists(path + ".tmp"), "no temporary file left");
    }

    static void BadMachineFileIsPlainAndUntouched()
    {
        string root = NewProject();
        Directory.CreateDirectory(Path.Combine(root, "local"));
        string path = Path.Combine(root, "local", "machine.json");
        File.WriteAllText(path, "{ not json");
        StarterError got = null;
        try { MachineFile.Load(root); } catch (StarterError e) { got = e; }
        Check(got != null && got.Message.Contains("could not be read") && got.Fix.Contains("machine.json.bak"), "bad settings file: plain error");
        Eq(File.ReadAllText(path), "{ not json", "bad settings file left as it was");
    }

    static void DataFolderChoiceIsRespected()
    {
        string root = NewProject();
        string big = Path.Combine(Path.GetTempPath(), "vgac-big-" + Guid.NewGuid().ToString("N").Substring(0, 6));
        temps.Add(big);
        var m = MachineFile.Load(root);
        m.Set("starter", "dataFolder", big);
        m.Save();
        var h = new FakeHost();
        MachineFile m2; Ledger led;
        RunAll(root, h, out m2, out led);
        Check(File.Exists(Path.Combine(big, "tools", "uv", "uv.exe")), "tools went to the chosen folder");
        Check(!Directory.Exists(Path.Combine(root, "local", "data")), "nothing in the default folder");
        Eq(MachineFile.Load(root).GetString("starter", "dataFolder"), big, "choice still recorded");
    }

    static void DefaultDataFolderIsNotRecorded()
    {
        string root = NewProject();
        MachineFile m; Ledger led;
        RunAll(root, new FakeHost(), out m, out led);
        Eq(MachineFile.Load(root).GetString("starter", "dataFolder"), null, "default data folder stays implicit (the project can move)");
    }

    static void NotAProjectFolder()
    {
        string empty = Path.Combine(Path.GetTempPath(), "vgac-empty-" + Guid.NewGuid().ToString("N").Substring(0, 6));
        temps.Add(empty);
        Directory.CreateDirectory(empty);
        var l = new Layout(empty, Path.Combine(empty, "d"));
        var m = MachineFile.Load(empty);
        StarterError got = null;
        try { new SetupRunner(l, new FakeHost(), m, Ledger.Load(empty), new SetupLog(empty)).Run(new List<string> { "uv" }, new Progress()); }
        catch (StarterError e) { got = e; }
        Check(got != null && got.Message.Contains("does not look like the VGAC project"), "wrong folder is named plainly");
    }

    static void BackendWaitAndReuse()
    {
        // A fake backend answering /api/health on an unused port: the starter reuses it and does not start another.
        int port = FreePort();
        var listener = new HttpListener();
        listener.Prefixes.Add("http://127.0.0.1:" + port + "/");
        listener.Start();
        var t = new System.Threading.Thread(delegate()
        {
            try
            {
                for (;;)
                {
                    var ctx = listener.GetContext();
                    var body = Encoding.UTF8.GetBytes("{\"ok\":true}");
                    ctx.Response.OutputStream.Write(body, 0, body.Length);
                    ctx.Response.Close();
                }
            }
            catch (Exception) { }
        });
        t.IsBackground = true;
        t.Start();
        string root = NewProject();
        var b = new BackendRunner(Layout.From(root, MachineFile.Load(root)), port);
        b.StartAndWait(5, new SetupLog(root));
        Check(b.Started == null, "an answering backend is reused, not started again");
        b.StopIfOurs();
        listener.Stop();

        // A port held by something that is not the backend: a plain refusal, no process started.
        var tcp = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0);
        tcp.Start();
        int busy = ((IPEndPoint)tcp.LocalEndpoint).Port;
        var b2 = new BackendRunner(Layout.From(root, MachineFile.Load(root)), busy);
        StarterError got = null;
        try { b2.StartAndWait(2, new SetupLog(root)); } catch (StarterError e) { got = e; }
        Check(got != null && got.Message.Contains("in use by something that is not VGAC"), "busy port refused plainly");
        Check(b2.Started == null, "nothing started on a busy port");
        tcp.Stop();

        // No Python yet: say so.
        int free = FreePort();
        var b3 = new BackendRunner(Layout.From(root, MachineFile.Load(root)), free);
        got = null;
        try { b3.StartAndWait(2, new SetupLog(root)); } catch (StarterError e) { got = e; }
        Check(got != null && got.Message.Contains("Python is not there yet"), "no python yet is said plainly");
    }

    static int FreePort()
    {
        var l = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0);
        l.Start();
        int p = ((IPEndPoint)l.LocalEndpoint).Port;
        l.Stop();
        return p;
    }

    static void ProjectRootFound()
    {
        string root = NewProject();
        string deep = Path.Combine(root, "starter", "x");
        Directory.CreateDirectory(deep);
        Eq(ProjectFinder.Find(deep), root, "project root found upward");
        Check(ProjectFinder.Find(Path.GetTempPath()) == null, "no project root outside a project");
    }

    static void VersionParsing()
    {
        Eq(ProbeReader.MajorVersion("v22.23.5\n"), 22, "node 22");
        Eq(ProbeReader.MajorVersion("v18.0.0"), 18, "node 18");
        Eq(ProbeReader.MajorVersion("junk"), 0, "junk");
        Eq(ProbeReader.MajorVersion(null), 0, "null");
    }

    static void ZipSlipRefused()
    {
        string dir = Path.Combine(Path.GetTempPath(), "vgac-zip-" + Guid.NewGuid().ToString("N").Substring(0, 6));
        temps.Add(dir);
        Directory.CreateDirectory(dir);
        string zip = Path.Combine(dir, "evil.zip");
        using (var za = System.IO.Compression.ZipFile.Open(zip, System.IO.Compression.ZipArchiveMode.Create))
        {
            var e = za.CreateEntry("..\\outside.txt");
            using (var w = new StreamWriter(e.Open())) w.Write("x");
        }
        StarterError got = null;
        try { new RealHost().Unzip(zip, Path.Combine(dir, "out")); } catch (StarterError e) { got = e; }
        Check(got != null && got.Message.Contains("outside its folder"), "zip entry outside the folder is refused");
        Check(!File.Exists(Path.Combine(dir, "outside.txt")), "nothing written outside");
    }

    static void DownloadChecksumAndResume()
    {
        byte[] data = Encoding.UTF8.GetBytes(new string('x', 5000));
        string sha;
        using (var h = System.Security.Cryptography.SHA256.Create())
            sha = string.Join("", h.ComputeHash(data).Select(b => b.ToString("x2")).ToArray());
        int port = FreePort();
        var listener = new HttpListener();
        listener.Prefixes.Add("http://127.0.0.1:" + port + "/");
        listener.Start();
        var th = new System.Threading.Thread(delegate()
        {
            try
            {
                for (;;)
                {
                    var ctx = listener.GetContext();
                    string range = ctx.Request.Headers["Range"];
                    int from = 0;
                    if (range != null && range.StartsWith("bytes=")) { from = int.Parse(range.Substring(6).TrimEnd('-')); ctx.Response.StatusCode = 206; }
                    ctx.Response.ContentLength64 = data.Length - from;
                    ctx.Response.OutputStream.Write(data, from, data.Length - from);
                    ctx.Response.Close();
                }
            }
            catch (Exception) { }
        });
        th.IsBackground = true;
        th.Start();
        string dir = Path.Combine(Path.GetTempPath(), "vgac-dl-" + Guid.NewGuid().ToString("N").Substring(0, 6));
        temps.Add(dir);
        string url = "http://127.0.0.1:" + port + "/f.zip";
        string dest = Path.Combine(dir, "f.zip");
        Directory.CreateDirectory(dir);
        // A half-finished earlier download is continued, then checked.
        File.WriteAllBytes(dest + ".part", data.Take(2000).ToArray());
        long last = 0;
        new RealHost().Download(url, dest, sha, delegate(long got, long total) { last = got; });
        Check(File.Exists(dest) && !File.Exists(dest + ".part") && new FileInfo(dest).Length == 5000, "resumed download completes and the .part is gone");
        Eq(last, 5000L, "progress ends at the full size");
        // A wrong pin: set aside, never used.
        string dest2 = Path.Combine(dir, "g.zip");
        StarterError got2 = null;
        try { new RealHost().Download(url, dest2, new string('0', 64), delegate { }); } catch (StarterError e) { got2 = e; }
        Check(got2 != null && got2.Message.Contains("not the published one"), "wrong checksum refused");
        Check(!File.Exists(dest2) && File.Exists(dest2 + ".bad"), "wrong file set aside as .bad, not left in place");
        listener.Stop();
    }
}
