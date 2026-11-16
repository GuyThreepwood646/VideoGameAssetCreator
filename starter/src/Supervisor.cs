// Watches the services every two seconds and turns what it sees into the status rows (StatusModel). It starts nothing
// itself except, once, a request to the backend to bring up ComfyUI B; the backend starts and owns every ComfyUI it runs.
// This program starts, restarts and stops only the backend (through BackendRunner).
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;

namespace Vgac.Starter
{
    public class HttpAnswer
    {
        public bool Ok;
        public int Status;
        public string ContentType = "";
        public string Text = "";
    }

    public static class Http
    {
        public static HttpAnswer Get(string url, int timeoutMs) { return Send("GET", url, null, timeoutMs); }

        public static HttpAnswer Send(string method, string url, string body, int timeoutMs)
        {
            var a = new HttpAnswer();
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = method;
                req.Timeout = timeoutMs;
                req.ReadWriteTimeout = timeoutMs;
                req.AllowAutoRedirect = false;
                if (body != null)
                {
                    req.ContentType = "application/json";
                    byte[] b = Encoding.UTF8.GetBytes(body);
                    req.ContentLength = b.Length;
                    using (var s = req.GetRequestStream()) s.Write(b, 0, b.Length);
                }
                else if (method == "POST" || method == "PUT") req.ContentLength = 0;
                using (var r = (HttpWebResponse)req.GetResponse()) Fill(a, r);
            }
            catch (WebException e)
            {
                var r = e.Response as HttpWebResponse;
                if (r != null) { using (r) Fill(a, r); a.Ok = false; }
            }
            catch (Exception) { }
            return a;
        }

        static void Fill(HttpAnswer a, HttpWebResponse r)
        {
            a.Status = (int)r.StatusCode;
            a.Ok = a.Status >= 200 && a.Status < 300;
            a.ContentType = r.ContentType ?? "";
            using (var s = r.GetResponseStream())
            using (var sr = new StreamReader(s, Encoding.UTF8)) a.Text = sr.ReadToEnd();
        }
    }

    public class Supervisor
    {
        readonly BackendRunner backend;
        readonly DesktopSettings settings;
        readonly string prefsFile;
        readonly string distIndex;
        readonly SetupLog log;
        Timer timer;
        int ticking;
        bool autoStartedB;
        bool backendWasUp = true;   // the supervisor starts after the backend answered; a later up-after-down reloads the page
        string lastLog = "";

        // Set by the window while it runs the screens' build.
        public volatile bool Building;
        public volatile string BuildError;
        public volatile bool StartTried;

        public volatile StatusSnapshot Latest;
        public volatile int Jobs;
        Dictionary<string, GpuInfo> gpus;
        bool gpusFromBackend;

        public event Action<StatusSnapshot> Changed;
        public event Action BackendCameUp;

        public Supervisor(BackendRunner backend, DesktopSettings settings, SetupLog log)
        {
            this.backend = backend; this.settings = settings; this.log = log;
            prefsFile = Path.Combine(backend.L.ProjectRoot, "library", "prefs.json");
            distIndex = backend.L.DistIndex;
            if (settings.StartScriptSet)
                log.Line("the start script for ComfyUI B in the settings (desktop.comfyB.startScript) is no longer used; the app starts this engine itself");
            Latest = StatusModel.Build(Inputs(false, null, false, 0));
        }

        public void Start() { timer = new Timer(delegate { Tick(); }, null, 0, 2000); }

        public void Stop()
        {
            Timer t = timer; timer = null;
            if (t != null) t.Dispose();
        }

        public void TickNow() { ThreadPool.QueueUserWorkItem(delegate { Tick(); }); }

        public GpuInfo Gpu(string key) { var g = gpus; GpuInfo r; return g != null && g.TryGetValue(key, out r) ? r : null; }

        StatusInputs Inputs(bool backendUp, string ui, bool comfyA, int foreignPid)
        {
            var i = new StatusInputs();
            i.BackendUp = backendUp;
            i.UiHtml = ui != null && ui.IndexOf("text/html", StringComparison.OrdinalIgnoreCase) >= 0;
            i.ComfyAUp = comfyA;
            i.ComfyAUrl = settings.ComfyAUrl; i.ComfyBUrl = settings.ComfyBUrl; i.BackendUrl = backend.Url;
            i.StartScriptIgnored = settings.StartScriptSet;
            i.DesktopExeExists = !string.IsNullOrEmpty(settings.ComfyDesktopExe) && File.Exists(settings.ComfyDesktopExe);
            i.BackendOwned = backend.Owned;
            i.BackendPid = backend.Pid;
            i.BackendAgeSeconds = backend.Owned ? (DateTime.UtcNow - backend.StartedUtc).TotalSeconds : 0;
            i.BackendExitCode = backend.Started && !backend.Owned ? backend.ExitCode : -1;
            i.ForeignBackendPid = foreignPid;
            i.BackendStartTried = StartTried;
            i.Building = Building; i.BuildError = BuildError;
            i.DistExists = File.Exists(distIndex);
            var g = gpus;
            i.GpusFromBackend = gpusFromBackend;
            if (g != null)
            {
                GpuInfo a, b;
                if (g.TryGetValue("a", out a)) i.A = a;
                if (g.TryGetValue("b", out b)) i.B = b;
            }
            return i;
        }

        void Tick()
        {
            if (Interlocked.Exchange(ref ticking, 1) == 1) return;
            try
            {
                HttpAnswer a = Http.Get(settings.ComfyAUrl + "/system_stats", 3000);
                HttpAnswer b = Http.Get(settings.ComfyBUrl + "/system_stats", 3000);
                HttpAnswer h = Http.Get(backend.Url + "/api/health", 3000);
                HttpAnswer u = Http.Get(backend.Url + "/", 3000);
                bool backendUp = h.Ok && HealthReader.Ok(h.Text);
                if (backendUp)
                {
                    Jobs = HealthReader.Jobs(h.Text);
                    HttpAnswer g = Http.Get(backend.Url + "/api/gpus", 3000);
                    if (g.Ok)
                    {
                        var parsed = HealthReader.Gpus(g.Text);
                        if (parsed != null) { gpus = parsed; gpusFromBackend = true; }
                    }
                }
                else if (!gpusFromBackend)
                {
                    try { var parsed = HealthReader.GpusFromPrefs(File.ReadAllText(prefsFile, Encoding.UTF8)); if (parsed != null) gpus = parsed; }
                    catch (Exception) { }
                }
                int foreignPid = 0;
                if (!backendUp && !backend.Owned && StartTried)
                {
                    List<ProcInfo> f = backend.Foreign();
                    if (f.Count > 0) foreignPid = f[0].Pid;
                }
                StatusInputs i = Inputs(backendUp, u.ContentType, a.Ok, foreignPid);
                i.ComfyBUp = b.Ok;
                StatusSnapshot snap = StatusModel.Build(i);
                Latest = snap;
                foreach (Row r in snap.Rows)
                {
                    string line = r.Key + ": " + r.State + " - " + r.Message;
                    // one log line per change, not one per tick
                    if (!lastLog.Contains("|" + line + "|")) { log.Line(line); }
                }
                var sb = new StringBuilder("|");
                foreach (Row r in snap.Rows) sb.Append(r.Key + ": " + r.State + " - " + r.Message + "|");
                lastLog = sb.ToString();
                if (backendUp && !backendWasUp) { var cb = BackendCameUp; if (cb != null) cb(); }
                backendWasUp = backendUp;
                MaybeStartB(i, backendUp);
                var ch = Changed;
                if (ch != null) ch(snap);
            }
            catch (Exception e) { log.Line("check failed: " + e.Message); }
            finally { Interlocked.Exchange(ref ticking, 0); }
        }

        // Once per run, when the backend is up and card b is on but ComfyUI B is not there: ask the backend to start it.
        // The backend starts, owns and records the engine; this program never runs a ComfyUI itself.
        void MaybeStartB(StatusInputs i, bool backendUp)
        {
            if (autoStartedB || !backendUp || i.ComfyBUp || !gpusFromBackend || !i.B.Enabled || !i.B.CanStart) return;
            if (i.B.State == "starting" || i.B.State == "running") return;
            autoStartedB = true;
            StartViaBackend("b");
        }

        public bool StartViaBackend(string instance)
        {
            HttpAnswer r = Http.Send("POST", backend.Url + "/api/gpus/" + Uri.EscapeDataString(instance) + "/start", null, 5000);
            log.Line("card " + instance + ": asked the app to start its ComfyUI -> HTTP " + r.Status);
            return r.Ok;
        }

        public bool SetCard(string instance, bool enabled)
        {
            HttpAnswer r = Http.Send("PUT", backend.Url + "/api/gpus/" + Uri.EscapeDataString(instance), "{\"enabled\":" + (enabled ? "true" : "false") + "}", 5000);
            log.Line("card " + instance + ": set " + (enabled ? "on" : "off") + " in the app -> HTTP " + r.Status);
            return r.Ok;
        }

        // A graphics card turned back on may be started again once.
        public void AllowAutoStartB() { autoStartedB = false; }
    }
}
