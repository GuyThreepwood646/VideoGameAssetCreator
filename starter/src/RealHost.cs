// The real effects: downloads (resumable, checked against a pinned SHA-256), unzipping, running programs, free space,
// and starting and finding the backend. Nothing here asks for administrator rights or changes Windows settings.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Text;
using System.Threading;

namespace Vgac.Starter
{
    public class RealHost : IHost
    {
        volatile Process current;     // the setup program running now (uv, npm), so quitting can stop it

        // Stops the setup program that is running, with everything it started. A download in progress is just dropped;
        // its .part file resumes next time.
        public void AbortCurrent()
        {
            Process p = current;
            if (p == null) return;
            try { if (!p.HasExited) ProcessTree.Stop(new WmiProcesses(), new ProcessKiller(), p.Id, p.StartTime.ToUniversalTime().Ticks); } catch (Exception) { }
        }

        public void Download(string url, string dest, string sha256, Action<long, long> progress)
        {
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
            Directory.CreateDirectory(Path.GetDirectoryName(dest));
            string part = dest + ".part";
            // An earlier whole file that still matches is reused.
            if (File.Exists(dest) && string.Equals(Hashing.Sha256File(dest), sha256, StringComparison.OrdinalIgnoreCase)) return;
            long have = File.Exists(part) ? new FileInfo(part).Length : 0;
            string host = new Uri(url).Host;
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.UserAgent = "VGAC-starter";
                req.Timeout = 30000;
                req.ReadWriteTimeout = 60000;
                if (have > 0) req.AddRange(have);
                using (var resp = (HttpWebResponse)req.GetResponse())
                {
                    bool resumed = resp.StatusCode == HttpStatusCode.PartialContent;
                    if (!resumed) have = 0;
                    long total = resp.ContentLength >= 0 ? resp.ContentLength + have : -1;
                    using (var src = resp.GetResponseStream())
                    using (var dst = new FileStream(part, resumed ? FileMode.Append : FileMode.Create, FileAccess.Write))
                    {
                        var buf = new byte[81920];
                        int n;
                        long got = have;
                        while ((n = src.Read(buf, 0, buf.Length)) > 0)
                        {
                            dst.Write(buf, 0, n);
                            got += n;
                            progress(got, total);
                        }
                    }
                }
            }
            catch (WebException e)
            {
                var r = e.Response as HttpWebResponse;
                if (r != null && r.StatusCode == HttpStatusCode.RequestedRangeNotSatisfiable) { File.Delete(part); }
                else throw new StarterError("Cannot reach " + host + " (" + e.Message + ").",
                    "Check that this computer is online, then start VGAC again. The download continues where it stopped.");
            }
            catch (IOException e)
            {
                throw new StarterError("Windows did not allow writing " + part + " (" + e.Message + ").",
                    "Choose a folder in your own user area for the big files, or free some space, then try again.");
            }
            string actual = Hashing.Sha256File(part);
            if (!string.Equals(actual, sha256, StringComparison.OrdinalIgnoreCase))
            {
                string bad = dest + ".bad";
                if (File.Exists(bad)) File.Delete(bad);
                File.Move(part, bad);
                throw new StarterError("The downloaded file from " + host + " is not the published one (it may be damaged or changed). It has been set aside as " + Path.GetFileName(bad) + ".",
                    "Try again. If it happens again, open the log and tell the project.");
            }
            if (File.Exists(dest)) File.Delete(dest);
            File.Move(part, dest);
        }

        public void Unzip(string zip, string destDir)
        {
            string root = Path.GetFullPath(destDir).TrimEnd('\\') + "\\";
            Directory.CreateDirectory(destDir);
            using (var za = ZipFile.OpenRead(zip))
            {
                foreach (var e in za.Entries)
                {
                    string target = Path.GetFullPath(Path.Combine(destDir, e.FullName));
                    if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                        throw new StarterError("The downloaded archive holds a file outside its folder, so it was not unpacked.", "Try again.");
                    if (e.FullName.EndsWith("/") || e.FullName.EndsWith("\\")) { Directory.CreateDirectory(target); continue; }
                    Directory.CreateDirectory(Path.GetDirectoryName(target));
                    e.ExtractToFile(target, true);
                }
            }
        }

        public int Run(string exe, string args, string cwd, Dictionary<string, string> env, string logFile)
        {
            var psi = new ProcessStartInfo(exe, args);
            psi.WorkingDirectory = cwd;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            if (env != null) foreach (var kv in env) psi.EnvironmentVariables[kv.Key] = kv.Value;
            if (!LooksLikeProgram(exe)) throw new StarterError(Path.GetFileName(exe) + " is damaged or is not a program.", "Try again; VGAC fetches it again if it can.");
            using (var w = new StreamWriter(logFile, true, new UTF8Encoding(false)))
            using (var p = new Process())
            {
                p.StartInfo = psi;
                var gate = new object();
                DataReceivedEventHandler h = delegate(object s, DataReceivedEventArgs a)
                {
                    if (a.Data != null) lock (gate) { w.WriteLine(a.Data); w.Flush(); }
                };
                p.OutputDataReceived += h;
                p.ErrorDataReceived += h;
                try { p.Start(); }
                catch (System.ComponentModel.Win32Exception e)
                {
                    throw new StarterError("Could not start " + Path.GetFileName(exe) + " (" + e.Message + ").", "Try again; if it keeps failing, an antivirus program may have removed it. Open the log.");
                }
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                current = p;
                p.WaitForExit();
                current = null;
                return p.ExitCode;
            }
        }

        // A file that is not a program (damaged, empty, or something else with the name) must not reach CreateProcess:
        // Windows would show its own "unsupported application" box and wait for a click.
        public static bool LooksLikeProgram(string exe)
        {
            try
            {
                if (!exe.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) || !File.Exists(exe)) return true;   // not ours to judge
                using (var f = File.OpenRead(exe)) return f.ReadByte() == 'M' && f.ReadByte() == 'Z';
            }
            catch (IOException) { return false; }
        }

        public string Capture(string exe, string args)
        {
            if (!LooksLikeProgram(exe)) return null;
            try
            {
                var psi = new ProcessStartInfo(exe, args);
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                using (var p = Process.Start(psi))
                {
                    // read on another thread: a program that never ends must not hold this one past the timeout
                    var reader = System.Threading.Tasks.Task.Factory.StartNew<string>(delegate { try { return p.StandardOutput.ReadToEnd(); } catch (Exception) { return ""; } });
                    if (!p.WaitForExit(10000)) { try { p.Kill(); } catch (Exception) { } return null; }
                    reader.Wait(2000);
                    return p.ExitCode == 0 ? (reader.IsCompleted ? reader.Result : "") : null;
                }
            }
            catch (Exception) { return null; }
        }

        public long FreeBytes(string folder)
        {
            try { return new DriveInfo(Path.GetPathRoot(Path.GetFullPath(folder))).AvailableFreeSpace; }
            catch (Exception) { return -1; }
        }
    }

    // Starting the backend, or finding the one already running. Never stops a backend it did not start (a backend left
    // by an earlier run of this program is taken over only when it is provably the same process: see Adoption.Matches).
    // "Owned" means this object holds an open handle to that exact process, taken when it was started or taken over, so
    // its id cannot be given to another program while it is owned. Starting, taking over and stopping share one lock, and
    // Shutdown() makes any start that comes later refuse, so quitting cannot be followed by a new backend.
    public class BackendRunner
    {
        public readonly Layout L;
        public readonly int Port;
        public int Pid;
        public long CreatedTicks;
        public bool Adopted;
        public DateTime StartedUtc;
        public IProcessSource Procs = new WmiProcesses();
        public IKiller Killer = new ProcessKiller();
        readonly object gate = new object();
        IntPtr handle = IntPtr.Zero;
        volatile bool closing;

        public BackendRunner(Layout layout, int port) { L = layout; Port = port; }

        public string Url { get { return "http://127.0.0.1:" + Port; } }

        // True while the process this launcher owns is still running.
        public bool Owned { get { return ProcessIdentity.IsRunning(handle); } }

        // True when a process was started or taken over in this run (it may have exited since).
        public bool Started { get { return handle != IntPtr.Zero; } }

        public bool Closing { get { return closing; } }

        public int ExitCode { get { return handle != IntPtr.Zero ? ProcessIdentity.ExitCode(handle) : -1; } }

        public string LogFile { get { return Path.Combine(L.ProjectRoot, "runtime", "logs", "backend.log"); } }

        public static bool HealthOk(int port, int timeoutMs)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + port + "/api/health");
                req.Timeout = timeoutMs;
                using (var r = (HttpWebResponse)req.GetResponse()) return r.StatusCode == HttpStatusCode.OK;
            }
            catch (WebException) { return false; }
        }

        public static bool PortOpen(int port)
        {
            try
            {
                using (var c = new System.Net.Sockets.TcpClient())
                {
                    var ar = c.BeginConnect("127.0.0.1", port, null, null);
                    bool ok = ar.AsyncWaitHandle.WaitOne(500) && c.Connected;
                    return ok;
                }
            }
            catch (Exception) { return false; }
        }

        // Backend processes on this port that this launcher does not own (with their start times, for the later check).
        public List<ProcInfo> Foreign()
        {
            var all = Procs.List();
            var mine = new HashSet<int>();
            if (Owned) foreach (int pid in ProcessTree.Targets(all, Pid)) mine.Add(pid);
            var res = new List<ProcInfo>();
            foreach (ProcInfo p in all) if (!mine.Contains(p.Pid) && Adoption.LooksLikeBackend(p, Port)) res.Add(p);
            return res;
        }

        // Holds the process for as long as it is owned. Called with the lock held.
        void Attach(IntPtr h, int pid, long created, bool adopted)
        {
            ProcessIdentity.Close(handle);
            handle = h; Pid = pid; CreatedTicks = created; Adopted = adopted; StartedUtc = DateTime.UtcNow;
        }

        // For tests and for taking over: own the process with this id, only if it started at `created`.
        public bool Own(int pid, long created, bool adopted)
        {
            lock (gate)
            {
                if (closing) return false;
                IntPtr h = ProcessIdentity.Open(pid, created);
                if (h == IntPtr.Zero) return false;
                Attach(h, pid, created, adopted);
                return true;
            }
        }

        // After a crash or a forced close of the starter the backend is still running. Take it over only when the process
        // recorded last time is still there, the starter that wrote the record is gone, and every detail matches (id,
        // creation time, the app's own Python, command line). Anything else stays foreign. A record that does not match
        // is deleted; one whose writer is still running is left for it.
        public bool TryAdopt(SetupLog log)
        {
            var rec = Adoption.Load(L.ProjectRoot);
            if (rec == null) return false;
            var me = Process.GetCurrentProcess();
            long myStart = 0;
            try { myStart = me.StartTime.ToUniversalTime().Ticks; } catch (Exception) { }
            if (Adoption.WriterAlive(rec, me.Id, myStart, ProcessIdentity.StartTicksOf(rec.StarterPid)))
            {
                log.Line("the backend (pid " + rec.Pid + ") belongs to another VGAC that is still running; leaving it alone");
                return false;
            }
            ProcInfo live = null;
            foreach (ProcInfo p in Procs.List()) if (p.Pid == rec.Pid) live = p;
            if (Adoption.Matches(rec, live, L.VenvPython, Port) && Own(rec.Pid, rec.CreatedTicks, true))
            {
                log.Line("backend (pid " + Pid + ") started by an earlier run of VGAC and still the same process; taking it over");
                SaveRecord(rec.Exe, rec.CommandLine);
                return true;
            }
            // The record is dropped only when its process is really gone or is a different process (another start time).
            // A process list that came back empty (a timeout) proves nothing, so the record stays for the next start.
            long actual = ProcessIdentity.StartTicksOf(rec.Pid);
            bool stale = actual <= 0 || Math.Abs(actual - rec.CreatedTicks) > Adoption.TickTolerance;
            log.Line("an earlier record of a backend (pid " + rec.Pid + ") " + (stale ? "no longer matches a running process; leaving any backend alone" : "could not be confirmed now; leaving any backend alone and keeping the record"));
            if (stale) Adoption.Delete(L.ProjectRoot);
            return false;
        }

        void SaveRecord(string exe, string cmd)
        {
            var me = Process.GetCurrentProcess();
            var rec = new OwnedRecord();
            rec.Pid = Pid; rec.CreatedTicks = CreatedTicks; rec.Exe = exe; rec.CommandLine = cmd;
            rec.StarterPid = me.Id;
            try { rec.StarterCreatedTicks = me.StartTime.ToUniversalTime().Ticks; } catch (Exception) { }
            try { Adoption.Save(L.ProjectRoot, rec); } catch (Exception) { }
        }

        // Returns when /api/health answers. Throws a plain error with the last lines of the backend log otherwise.
        public void StartAndWait(int timeoutSeconds, SetupLog log)
        {
            if (HealthOk(Port, 1500))
            {
                log.Line("backend already answering on port " + Port + "; using it");
                TryAdopt(log);
                return;
            }
            // Something that looks like the backend is starting but does not answer yet: wait for it, never start a second.
            List<ProcInfo> other = Foreign();
            if (other.Count > 0)
            {
                log.Line("a backend process not started by this launcher (pid " + other[0].Pid + ") is starting; waiting for it");
                DateTime until = DateTime.UtcNow.AddSeconds(timeoutSeconds);
                while (DateTime.UtcNow < until) { if (HealthOk(Port, 1500)) return; Thread.Sleep(500); }
                throw new StarterError("A copy of the app's engine that VGAC did not start is on port " + Port + " and does not answer.", "Close it (or restart the computer), then start VGAC again.");
            }
            if (PortOpen(Port))
                throw new StarterError("Port " + Port + " is in use by something that is not VGAC's backend.",
                    "Close the program using that port (or a second copy of VGAC that is stuck), then start again.");
            if (!File.Exists(L.VenvPython))
                throw new StarterError("The app's Python is not there yet.", "Start VGAC again to set it up.");
            LaunchProcess(log);
            DateTime end = DateTime.UtcNow.AddSeconds(timeoutSeconds);
            while (DateTime.UtcNow < end)
            {
                if (closing) throw new StarterError("VGAC is closing.");
                if (!Owned)
                    throw new StarterError("The app's engine stopped while starting (exit code " + ExitCode + ").\r\n" + Tail(LogFile, 8), "Open the log. Starting VGAC again re-checks the setup.");
                if (HealthOk(Port, 1500)) return;
                Thread.Sleep(500);
            }
            throw new StarterError("The app's engine did not answer within " + timeoutSeconds + " seconds.\r\n" + Tail(LogFile, 8), "Open the log, or start VGAC again.");
        }

        // Starts the backend with its output going straight to backend.log, keeps its handle and records what was started.
        // Refuses once Shutdown() has been called.
        public void LaunchProcess(SetupLog log)
        {
            lock (gate)
            {
                if (closing) throw new StarterError("VGAC is closing.");
                if (Owned) throw new StarterError("The app's engine is already running.");
                string args = "-m uvicorn vgac.main:app --host 127.0.0.1 --port " + Port;
                var env = new Dictionary<string, string>();
                env["PYTHONUNBUFFERED"] = "1";
                NativeProcess.Started s;
                try { s = NativeProcess.Start(L.VenvPython, args, Path.Combine(L.ProjectRoot, "backend"), LogFile, env); }
                catch (Exception e)
                {
                    throw new StarterError("Could not start the app's engine (" + e.Message + ").", "Try again; if it keeps failing, an antivirus program may have removed the app's Python. Open the log.");
                }
                if (s.CreatedTicks <= 0)
                {
                    // Not able to tell this process from another later: do not keep a process this program could not stop safely.
                    ProcessIdentity.Kill(s.Handle); ProcessIdentity.Close(s.Handle);
                    throw new StarterError("Windows would not say when the app's engine started, so it was stopped again.", "Try again.");
                }
                Attach(s.Handle, s.Pid, s.CreatedTicks, false);
                SaveRecord(L.VenvPython, "\"" + L.VenvPython + "\" " + args);
                log.Line("backend started, pid " + Pid + ", log " + LogFile);
            }
        }

        // Restart: stops the backend this launcher owns and everything it started (its process tree), nothing else.
        // Returns the ids stopped. The record is kept when the process was owned and could not be stopped, so a later
        // start can still deal with it.
        public List<int> StopIfOurs(SetupLog log)
        {
            lock (gate) { return StopLocked(log); }
        }

        // Quit: the same, and no backend can be started afterwards.
        public List<int> Shutdown(SetupLog log)
        {
            lock (gate) { closing = true; return StopLocked(log); }
        }

        List<int> StopLocked(SetupLog log)
        {
            var done = new List<int>();
            if (handle == IntPtr.Zero) return done;           // nothing owned: another starter's record is not ours to delete
            if (!ProcessIdentity.IsRunning(handle))
            {
                // The backend ended by itself. What it started may still run; our open handle keeps its id from being
                // reused, so anything still pointing at it as its parent is its own child.
                done = ProcessTree.Stop(Procs, Killer, Pid, CreatedTicks);
                ProcessIdentity.Close(handle); handle = IntPtr.Zero;
                Adoption.Delete(L.ProjectRoot);
                return done;
            }
            done = ProcessTree.Stop(Procs, Killer, Pid, CreatedTicks);
            if (done.Contains(Pid))
            {
                ProcessIdentity.Close(handle); handle = IntPtr.Zero;
                Adoption.Delete(L.ProjectRoot);
            }
            else if (log != null) log.Line("could not stop the backend (pid " + Pid + "); it is still running and is still recorded as owned");
            return done;
        }

        public static string Tail(string file, int lines)
        {
            try
            {
                string[] all;
                using (var fs = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                using (var sr = new StreamReader(fs, Encoding.UTF8))
                    all = sr.ReadToEnd().Split(new[] { "\r\n", "\n" }, StringSplitOptions.None);
                int from = Math.Max(0, all.Length - lines);
                return string.Join("\r\n", all, from, all.Length - from);
            }
            catch (Exception) { return ""; }
        }
    }
}
