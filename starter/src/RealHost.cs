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
                p.WaitForExit();
                return p.ExitCode;
            }
        }

        public string Capture(string exe, string args)
        {
            try
            {
                var psi = new ProcessStartInfo(exe, args);
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                using (var p = Process.Start(psi))
                {
                    string o = p.StandardOutput.ReadToEnd();
                    if (!p.WaitForExit(10000)) { try { p.Kill(); } catch (Exception) { } return null; }
                    return p.ExitCode == 0 ? o : null;
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

    // Starting the backend, or finding the one already running. Never stops a backend it did not start.
    public class BackendRunner
    {
        public readonly Layout L;
        public readonly int Port;
        public Process Started;   // set only when this starter started it

        public BackendRunner(Layout layout, int port) { L = layout; Port = port; }

        public string Url { get { return "http://127.0.0.1:" + Port; } }

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

        // Returns when /api/health answers. Throws a plain error with the last lines of the backend log otherwise.
        public void StartAndWait(int timeoutSeconds, SetupLog log)
        {
            if (HealthOk(Port, 1500)) { log.Line("backend already answering on port " + Port + "; using it"); return; }
            if (PortOpen(Port))
                throw new StarterError("Port " + Port + " is in use by something that is not VGAC's backend.",
                    "Close the program using that port (or a second copy of VGAC that is stuck), then start again.");
            if (!File.Exists(L.VenvPython))
                throw new StarterError("The app's Python is not there yet.", "Start VGAC again to set it up.");
            string logFile = Path.Combine(L.ProjectRoot, "runtime", "logs", "backend.log");
            Directory.CreateDirectory(Path.GetDirectoryName(logFile));
            var psi = new ProcessStartInfo(L.VenvPython, "-m uvicorn vgac.main:app --host 127.0.0.1 --port " + Port);
            psi.WorkingDirectory = Path.Combine(L.ProjectRoot, "backend");
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            var w = new StreamWriter(logFile, true, new UTF8Encoding(false));
            var gate = new object();
            var p = new Process();
            p.StartInfo = psi;
            DataReceivedEventHandler h = delegate(object s, DataReceivedEventArgs a)
            {
                if (a.Data != null) lock (gate) { try { w.WriteLine(a.Data); w.Flush(); } catch (ObjectDisposedException) { } }
            };
            p.OutputDataReceived += h;
            p.ErrorDataReceived += h;
            p.Start();
            p.BeginOutputReadLine();
            p.BeginErrorReadLine();
            Started = p;
            log.Line("backend started, pid " + p.Id + ", log " + logFile);
            DateTime end = DateTime.UtcNow.AddSeconds(timeoutSeconds);
            while (DateTime.UtcNow < end)
            {
                if (p.HasExited)
                    throw new StarterError("The app's engine stopped while starting (exit code " + p.ExitCode + ").\r\n" + Tail(logFile, 8), "Open the log. Starting VGAC again re-checks the setup.");
                if (HealthOk(Port, 1500)) return;
                Thread.Sleep(500);
            }
            throw new StarterError("The app's engine did not answer within " + timeoutSeconds + " seconds.\r\n" + Tail(logFile, 8), "Open the log, or start VGAC again.");
        }

        public void StopIfOurs()
        {
            if (Started != null && !Started.HasExited)
            {
                try { Started.Kill(); } catch (Exception) { }
            }
        }

        public static string Tail(string file, int lines)
        {
            try
            {
                string[] all = File.ReadAllLines(file);
                int from = Math.Max(0, all.Length - lines);
                return string.Join("\r\n", all, from, all.Length - from);
            }
            catch (Exception) { return ""; }
        }
    }
}
