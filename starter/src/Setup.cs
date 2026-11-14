// What the starter sets up before the backend can answer, and how it decides what is still needed.
// Pure decisions (Planner) are separate from effects (IHost), so the decisions are tested without a network.
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace Vgac.Starter
{
    // Pinned downloads. A file whose SHA-256 differs is set aside and never used.
    public static class Pins
    {
        public const string UvUrl = "https://github.com/astral-sh/uv/releases/download/0.13.8/uv-x86_64-pc-windows-msvc.zip";
        public const string UvSha256 = "529e78a9cec6cd7e3e014f7ecd8bda0c113969d4ed0f2efeedb85c30943325b8";
        public const string NodeUrl = "https://nodejs.org/dist/v22.23.5/node-v22.23.5-win-x64.zip";
        public const string NodeSha256 = "4a191082e9706866830379d577d2cc8a50c776fcd9436a8d061b063ed0d9a0e3";
        public const string PythonVersion = "3.13";
        public const int MinNodeMajor = 20;
        // Rough room the first run needs on the data folder's drive: Python and packages, Node, and the screens' packages.
        public const long NeedBytes = 3L * 1024 * 1024 * 1024;
        public const string Hosts = "github.com (the package tool), pypi.org and files.pythonhosted.org (the app's Python packages), nodejs.org and registry.npmjs.org (to build the screens)";
    }

    public class Layout
    {
        public string ProjectRoot;
        public string DataFolder;

        public Layout(string projectRoot, string dataFolder)
        {
            ProjectRoot = Path.GetFullPath(projectRoot);
            DataFolder = Path.GetFullPath(dataFolder);
        }

        public string UvExe { get { return Path.Combine(DataFolder, "tools", "uv", "uv.exe"); } }
        public string NodeDir { get { return Path.Combine(DataFolder, "tools", "node"); } }
        public string PythonDir { get { return Path.Combine(DataFolder, "python"); } }
        public string UvCache { get { return Path.Combine(DataFolder, "uv-cache"); } }
        public string Venv { get { return Path.Combine(ProjectRoot, "backend", ".venv"); } }
        public string VenvPython { get { return Path.Combine(Venv, "Scripts", "python.exe"); } }
        public string Requirements { get { return Path.Combine(ProjectRoot, "backend", "requirements.txt"); } }
        public string FrontendDir { get { return Path.Combine(ProjectRoot, "frontend"); } }
        public string Lock { get { return Path.Combine(FrontendDir, "package-lock.json"); } }
        public string DistIndex { get { return Path.Combine(FrontendDir, "dist", "index.html"); } }
        public string StatePath { get { return Path.Combine(ProjectRoot, "local", "state.json"); } }
        public string Downloads { get { return Path.Combine(DataFolder, "downloads"); } }

        public static string DefaultDataFolder(string projectRoot)
        {
            return Path.Combine(projectRoot, "local", "data");
        }

        // The data folder: the person's choice in machine.json, else local/data in the project. Relative values are
        // read against the project folder.
        public static Layout From(string projectRoot, MachineFile machine)
        {
            string chosen = machine.GetString("starter", "dataFolder");
            string data = string.IsNullOrEmpty(chosen) ? DefaultDataFolder(projectRoot)
                : (Path.IsPathRooted(chosen) ? chosen : Path.Combine(projectRoot, chosen));
            return new Layout(projectRoot, data);
        }
    }

    // What the folder looks like now (reads only).
    public class Probe
    {
        public bool UvPresent;
        public bool VenvPythonPresent;
        public bool DistPresent;
        public string NodeFound;       // path of a usable node.exe (Node 20 or newer), or null
        public string RequirementsHash;
        public string LockHash;
        public string SourceHash;
        public string StateProject;    // the project folder the last install was made in
        public string StateRequirements;
        public string StateLock;
        public string StateSource;
    }

    public static class Planner
    {
        // The steps still needed, in the order they run: uv, venv, packages, node, screens.
        public static List<string> Plan(Probe p, string projectRoot)
        {
            var steps = new List<string>();
            bool moved = p.StateProject != null && !SamePath(p.StateProject, projectRoot);
            bool venv = !p.VenvPythonPresent || moved;
            bool packages = venv || p.StateRequirements != p.RequirementsHash;
            bool screens = !p.DistPresent || p.StateLock != p.LockHash || p.StateSource != p.SourceHash;
            if ((venv || packages) && !p.UvPresent) steps.Add("uv");
            if (venv) steps.Add("venv");
            if (packages) steps.Add("packages");
            if (screens && p.NodeFound == null) steps.Add("node");
            if (screens) steps.Add("screens");
            return steps;
        }

        public static bool SamePath(string a, string b)
        {
            return string.Equals(Path.GetFullPath(a).TrimEnd('\\'), Path.GetFullPath(b).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);
        }

        public static string Plain(string step)
        {
            switch (step)
            {
                case "uv": return "Get the small package tool (uv, about 20 MB)";
                case "venv": return "Make the app's private Python " + Pins.PythonVersion + " (about 100 MB, not added to Windows)";
                case "packages": return "Install the app's Python packages (about 1 GB)";
                case "node": return "Get Node.js to build the screens (about 35 MB, used only for the build)";
                case "screens": return "Build the screens (about a minute)";
            }
            return step;
        }
    }

    public static class ProbeReader
    {
        public static Probe Read(Layout l, IHost host)
        {
            var p = new Probe();
            p.UvPresent = File.Exists(l.UvExe);
            p.VenvPythonPresent = File.Exists(l.VenvPython);
            p.DistPresent = File.Exists(l.DistIndex);
            p.RequirementsHash = File.Exists(l.Requirements) ? Hashing.Sha256File(l.Requirements) : "";
            p.LockHash = File.Exists(l.Lock) ? Hashing.Sha256File(l.Lock) : "";
            p.SourceHash = SourceHash(l.FrontendDir);
            p.NodeFound = FindNode(l, host);
            if (File.Exists(l.StatePath))
            {
                try
                {
                    var d = Json.ParseObject(File.ReadAllText(l.StatePath, Encoding.UTF8));
                    p.StateProject = Str(d, "project");
                    p.StateRequirements = Str(d, "requirements");
                    p.StateLock = Str(d, "lock");
                    p.StateSource = Str(d, "source");
                }
                catch (Exception) { /* unreadable state means "not installed yet": the steps run again */ }
            }
            return p;
        }

        static string Str(Dictionary<string, object> d, string k)
        {
            object v;
            return d.TryGetValue(k, out v) && v != null ? v.ToString() : null;
        }

        // Hash of the files the screens are built from (frontend/src, index.html, package.json, vite config).
        public static string SourceHash(string frontendDir)
        {
            var sb = new StringBuilder();
            var files = new List<string>();
            string src = Path.Combine(frontendDir, "src");
            if (Directory.Exists(src)) files.AddRange(Directory.GetFiles(src, "*", SearchOption.AllDirectories));
            string pub = Path.Combine(frontendDir, "public");
            if (Directory.Exists(pub)) files.AddRange(Directory.GetFiles(pub, "*", SearchOption.AllDirectories));
            foreach (string n in new[] { "index.html", "package.json", "vite.config.ts", "tsconfig.json", "tsconfig.app.json" })
            {
                string f = Path.Combine(frontendDir, n);
                if (File.Exists(f)) files.Add(f);
            }
            files.Sort(StringComparer.OrdinalIgnoreCase);
            foreach (string f in files)
                sb.Append(f.Substring(frontendDir.Length)).Append('=').Append(Hashing.Sha256File(f)).Append('\n');
            return Hashing.Sha256Text(sb.ToString());
        }

        // The private Node first (a person's own Node is not relied on to stay), then any Node 20 or newer on the PATH.
        public static string FindNode(Layout l, IHost host)
        {
            string mine = Path.Combine(l.NodeDir, "node.exe");
            if (File.Exists(mine)) return mine;
            string path = Environment.GetEnvironmentVariable("PATH") ?? "";
            foreach (string dir in path.Split(';'))
            {
                if (dir.Length == 0) continue;
                string exe;
                try { exe = Path.Combine(dir.Trim('"'), "node.exe"); } catch (ArgumentException) { continue; }
                if (!File.Exists(exe)) continue;
                int major = MajorVersion(host.Capture(exe, "--version"));
                if (major >= Pins.MinNodeMajor) return exe;
            }
            return null;
        }

        // "v22.23.5" -> 22; anything else -> 0.
        public static int MajorVersion(string text)
        {
            if (text == null) return 0;
            text = text.Trim();
            if (text.StartsWith("v")) text = text.Substring(1);
            int dot = text.IndexOf('.');
            int n;
            return int.TryParse(dot > 0 ? text.Substring(0, dot) : text, out n) ? n : 0;
        }
    }

    // The effects. The real one is RealHost; tests use a fake.
    public interface IHost
    {
        void Download(string url, string dest, string sha256, Action<long, long> progress);
        void Unzip(string zip, string destDir);
        int Run(string exe, string args, string cwd, Dictionary<string, string> env, string logFile);
        string Capture(string exe, string args);
        long FreeBytes(string folder);
    }

    public class Progress
    {
        public Action<string> Say = delegate { };            // what is happening now
        public Action<double> Fraction = delegate { };       // 0..1 for the whole run
        public Func<bool> Cancelled = delegate { return false; };
    }

    public class SetupRunner
    {
        readonly Layout l;
        readonly IHost host;
        readonly MachineFile machine;
        readonly Ledger ledger;
        readonly SetupLog log;

        public SetupRunner(Layout layout, IHost host, MachineFile machine, Ledger ledger, SetupLog log)
        {
            this.l = layout; this.host = host; this.machine = machine; this.ledger = ledger; this.log = log;
        }

        Dictionary<string, string> UvEnv()
        {
            var env = new Dictionary<string, string>();
            env["UV_PYTHON_INSTALL_DIR"] = l.PythonDir;      // the private Python lives in the data folder
            env["UV_CACHE_DIR"] = l.UvCache;
            env["UV_PYTHON_PREFERENCE"] = "only-managed";     // never pick up a Python the person installed
            env["UV_PYTHON_INSTALL_REGISTRY"] = "0";          // nothing written to the Windows registry
            env["UV_NO_PROGRESS"] = "1";
            return env;
        }

        string StepLog(string name) { return Path.Combine(l.ProjectRoot, "runtime", "logs", "setup-" + name + ".log"); }

        public void Run(List<string> steps, Progress prog)
        {
            if (steps.Count == 0) { return; }
            if (!File.Exists(l.Requirements) || !Directory.Exists(l.FrontendDir))
                throw new StarterError("This folder does not look like the VGAC project (backend\\requirements.txt or frontend is missing).",
                    "Clone the project again, or put VGAC.exe back in the project folder.");
            Directory.CreateDirectory(l.DataFolder);
            long free = host.FreeBytes(l.DataFolder);
            if (free >= 0 && free < Pins.NeedBytes)
                throw new StarterError("Setup needs about " + (Pins.NeedBytes >> 30) + " GB; the drive of " + l.DataFolder + " has " + (free >> 20) + " MB free.",
                    "Choose another folder for the big files (on a drive with more room), or free some space, then start again.");
            log.Line("setup start: " + string.Join(",", steps.ToArray()) + "; data folder " + l.DataFolder);
            for (int i = 0; i < steps.Count; i++)
            {
                if (prog.Cancelled()) throw new StarterError("Setup was stopped. Nothing is broken; starting VGAC again continues where it stopped.");
                prog.Say(Planner.Plain(steps[i]) + "...");
                prog.Fraction((double)i / steps.Count);
                log.Line("step " + steps[i] + " begins");
                RunStep(steps[i], prog);
                log.Line("step " + steps[i] + " done");
            }
            prog.Fraction(1.0);
            WriteStamps();
        }

        void RunStep(string step, Progress prog)
        {
            switch (step)
            {
                case "uv": FetchZip("uv", Pins.UvUrl, Pins.UvSha256, Path.GetDirectoryName(l.UvExe), l.UvExe, prog); break;
                case "node": FetchNode(prog); break;
                case "venv":
                    // A venv made in another folder holds that folder's path; make it again here.
                    if (Directory.Exists(l.Venv)) Directory.Delete(l.Venv, true);
                    Exec(l.UvExe, "venv \"" + l.Venv + "\" --python " + Pins.PythonVersion, l.ProjectRoot, UvEnv(), "venv",
                        "Could not make the app's Python", "Check that this computer is online (github.com and the Python download host), then try again.");
                    ledger.Add("The app's Python environment", l.Venv, "added", 0);
                    ledger.Add("The app's private Python " + Pins.PythonVersion, l.PythonDir, "added", 0);
                    break;
                case "packages":
                    Exec(l.UvExe, "pip install --python \"" + l.VenvPython + "\" -r \"" + l.Requirements + "\"", l.ProjectRoot, UvEnv(), "packages",
                        "Could not install the app's Python packages", "Check that this computer is online (pypi.org), then try again. The log has the package that failed.");
                    break;
                case "screens":
                    string node = ProbeReader.FindNode(l, host);
                    if (node == null) throw new StarterError("Node.js is not available to build the screens.", "Try again; if it keeps failing, install Node.js 20 or newer from nodejs.org.");
                    var env = new Dictionary<string, string>();
                    env["PATH"] = Path.GetDirectoryName(node) + ";" + Environment.GetEnvironmentVariable("PATH");
                    Exec("cmd.exe", "/d /c npm ci --no-audit --no-fund", l.FrontendDir, env, "screens-install",
                        "Could not fetch the screens' packages", "Check that this computer is online (registry.npmjs.org), then try again.");
                    Exec("cmd.exe", "/d /c npm run build", l.FrontendDir, env, "screens-build",
                        "Could not build the screens", "Open the log to see the error; the app itself still works from its last build, if there is one.");
                    break;
                default: throw new StarterError("Unknown setup step: " + step);
            }
        }

        void Exec(string exe, string args, string cwd, Dictionary<string, string> env, string logName, string failure, string fix)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(StepLog(logName)));
            log.Line("run " + exe + " " + args);
            int code = host.Run(exe, args, cwd, env, StepLog(logName));
            if (code != 0)
            {
                log.Line("exit " + code + " (see " + StepLog(logName) + ")");
                throw new StarterError(failure + " (exit code " + code + ").", fix);
            }
        }

        // Download, check against the pin, unpack into a temporary folder, then swap into place.
        void FetchZip(string what, string url, string sha, string targetDir, string mustExist, Progress prog)
        {
            Directory.CreateDirectory(l.Downloads);
            string zip = Path.Combine(l.Downloads, Path.GetFileName(new Uri(url).LocalPath));
            log.Line("download " + url + " -> " + zip);
            host.Download(url, zip, sha, delegate(long got, long total) { prog.Say(Planner.Plain(what == "uv" ? "uv" : "node") + " (" + (got >> 20) + " of " + (total >> 20) + " MB)..."); });
            string tmp = targetDir + ".new";
            if (Directory.Exists(tmp)) Directory.Delete(tmp, true);
            host.Unzip(zip, tmp);
            // uv's zip holds the files at its top level.
            if (Directory.Exists(targetDir)) Directory.Delete(targetDir, true);
            Directory.CreateDirectory(Path.GetDirectoryName(targetDir));
            Directory.Move(tmp, targetDir);
            if (!File.Exists(mustExist)) throw new StarterError("The downloaded " + what + " did not hold the file it should (" + Path.GetFileName(mustExist) + ").", "Try again.");
            ledger.Add(what, targetDir, "added", new FileInfo(zip).Length);
            File.Delete(zip);
        }

        void FetchNode(Progress prog)
        {
            Directory.CreateDirectory(l.Downloads);
            string zip = Path.Combine(l.Downloads, Path.GetFileName(new Uri(Pins.NodeUrl).LocalPath));
            log.Line("download " + Pins.NodeUrl + " -> " + zip);
            host.Download(Pins.NodeUrl, zip, Pins.NodeSha256, delegate(long got, long total) { prog.Say(Planner.Plain("node") + " (" + (got >> 20) + " of " + (total >> 20) + " MB)..."); });
            string tmp = l.NodeDir + ".new";
            if (Directory.Exists(tmp)) Directory.Delete(tmp, true);
            host.Unzip(zip, tmp);
            // Node's zip holds one folder (node-v22...-win-x64): its contents become the node folder.
            string inner = tmp;
            string[] subs = Directory.GetDirectories(tmp);
            if (subs.Length == 1 && !File.Exists(Path.Combine(tmp, "node.exe"))) inner = subs[0];
            if (Directory.Exists(l.NodeDir)) Directory.Delete(l.NodeDir, true);
            Directory.CreateDirectory(Path.GetDirectoryName(l.NodeDir));
            Directory.Move(inner, l.NodeDir);
            if (inner != tmp && Directory.Exists(tmp)) Directory.Delete(tmp, true);
            if (!File.Exists(Path.Combine(l.NodeDir, "node.exe"))) throw new StarterError("The downloaded Node.js did not hold node.exe.", "Try again.");
            ledger.Add("Node.js (used to build the screens)", l.NodeDir, "added", new FileInfo(zip).Length);
            File.Delete(zip);
        }

        // What a later start compares against (B7 of the plan): when nothing changed, setup is skipped.
        void WriteStamps()
        {
            var p = ProbeReader.Read(l, host);
            var d = new Dictionary<string, object>();
            d["project"] = l.ProjectRoot;
            d["requirements"] = p.RequirementsHash;
            d["lock"] = p.LockHash;
            d["source"] = p.SourceHash;
            Directory.CreateDirectory(Path.GetDirectoryName(l.StatePath));
            string tmp = l.StatePath + ".tmp";
            File.WriteAllText(tmp, Json.Write(d), new UTF8Encoding(false));
            if (File.Exists(l.StatePath)) File.Delete(l.StatePath);
            File.Move(tmp, l.StatePath);
            // Only a folder the person chose is recorded; the default stays implicit so the project can be moved.
            if (!Planner.SamePath(l.DataFolder, Layout.DefaultDataFolder(l.ProjectRoot)) && machine.GetString("starter", "dataFolder") == null)
            {
                machine.Set("starter", "dataFolder", l.DataFolder);
                machine.Save();
            }
            ledger.Save();
        }
    }
}
