// The app window and the web view's cache: after the screens are rebuilt, and after a load that drew nothing, the
// window shows the current build by itself.
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using Vgac.Starter;

public static partial class T
{
    // Serves the screens as the backend does: no Cache-Control, a Last-Modified date, and index.html (with 200) for any
    // address that is not a file of the current build.
    class ScreensServer : IDisposable
    {
        readonly HttpListener listener = new HttpListener();
        public readonly string Url;
        public volatile string Build = "old";
        public volatile int BrokenScripts;      // this many script requests get index.html instead of the script
        readonly List<string> drawn = new List<string>();

        public ScreensServer()
        {
            var l = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0); l.Start();
            int port = ((IPEndPoint)l.LocalEndpoint).Port; l.Stop();
            Url = "http://127.0.0.1:" + port;
            listener.Prefixes.Add(Url + "/");
            listener.Start();
            var th = new Thread(Serve); th.IsBackground = true; th.Start();
        }

        public bool Drew(string build) { lock (drawn) return drawn.Contains(build); }
        public void Forget() { lock (drawn) drawn.Clear(); }

        void Serve()
        {
            while (listener.IsListening)
            {
                HttpListenerContext c;
                try { c = listener.GetContext(); } catch (Exception) { return; }
                try { Answer(c); } catch (Exception) { }
            }
        }

        void Answer(HttpListenerContext c)
        {
            string path = c.Request.Url.AbsolutePath;
            string build = Build, type = "text/html; charset=utf-8", body;
            DateTime changed = DateTime.UtcNow.AddDays(-10);
            if (path == "/drawn") { lock (drawn) drawn.Add(c.Request.QueryString["b"]); body = "ok"; type = "text/plain"; }
            else if (path == "/assets/index-" + build + ".js" && BrokenScripts <= 0)
            {
                type = "application/javascript";
                changed = DateTime.UtcNow;     // asked for again at every load
                body = "document.getElementById('root').innerHTML='<p>app</p>';fetch('/drawn?b=" + build + "',{cache:'no-store'});";
            }
            else
            {
                if (path.EndsWith(".js")) BrokenScripts--;
                body = "<!doctype html><html><body><div id=\"root\"></div><script type=\"module\" crossorigin src=\"/assets/index-" + build + ".js\"></script></body></html>";
            }
            byte[] b = Encoding.UTF8.GetBytes(body);
            c.Response.ContentType = type;
            c.Response.Headers["Last-Modified"] = changed.ToString("R");
            c.Response.ContentLength64 = b.Length;
            c.Response.OutputStream.Write(b, 0, b.Length);
            c.Response.Close();
        }

        public void Dispose() { try { listener.Stop(); listener.Close(); } catch (Exception) { } }
    }

    // Shows the app window and handles its messages until `done`, or the time is up.
    static bool ShowUntil(AppWindow w, Func<bool> done, int seconds)
    {
        DateTime until = DateTime.UtcNow.AddSeconds(seconds);
        w.Show();
        while (!done() && !w.IsDisposed && DateTime.UtcNow < until) { System.Windows.Forms.Application.DoEvents(); Thread.Sleep(20); }
        if (!done()) Console.WriteLine("window not done" + (w.Problem != null ? ": " + w.Problem : ""));
        return done();
    }

    static void CloseWindow(AppWindow w)
    {
        w.Quitting = true; w.Close();
        for (int i = 0; i < 20; i++) { System.Windows.Forms.Application.DoEvents(); Thread.Sleep(100); }
    }

    // The web view needs a single-threaded apartment, which the test program's own thread is not.
    static void WindowCacheAll()
    {
        Exception failure = null;
        var th = new Thread(delegate() { try { WindowCacheChecks(); } catch (Exception e) { failure = e; } });
        th.SetApartmentState(ApartmentState.STA);
        th.Start(); th.Join();
        Check(failure == null, "the window cache checks ran" + (failure != null ? ": " + failure.Message : ""));
    }

    static void WindowCacheChecks()
    {
        Check(LauncherRules.PageCheck(true, "true", 0) == PageAction.Done, "a page that drew is left alone");
        Check(LauncherRules.PageCheck(true, "null", 0) == PageAction.Done, "a page without the app's root element is left alone");
        Check(LauncherRules.PageCheck(true, "false", 0) == PageAction.Reload, "an empty page is loaded again");
        Check(LauncherRules.PageCheck(false, null, 2) == PageAction.Reload, "a page that did not load is loaded again");
        Check(LauncherRules.PageCheck(true, "false", 3) == PageAction.GiveUp, "but not for ever");
        if (!AppWindow.RuntimeInstalled()) { Console.WriteLine("no WebView2 runtime: the window cache checks are skipped"); return; }
        string dir = Path.Combine(Path.GetTempPath(), "vgac-cache-" + Guid.NewGuid().ToString("N").Substring(0, 8));
        temps.Add(dir); Directory.CreateDirectory(dir);
        var log = new SetupLog(dir);
        using (var server = new ScreensServer())
        {
            string data = Path.Combine(dir, "data");
            var w = new AppWindow(server.Url, data, log);
            Check(ShowUntil(w, delegate { return server.Drew("old"); }, 30), "the window draws the screens");
            CloseWindow(w);

            server.Build = "new"; server.Forget();
            w = new AppWindow(server.Url, data, log);
            Check(ShowUntil(w, delegate { return server.Drew("new"); }, 30), "after a rebuild, a new window on the same data folder draws the new screens");
            Check(!File.ReadAllText(log.Path).Contains("loading it again"), "at the first load, without a second try");

            server.Build = "newer"; server.Forget();
            w.BeginInvoke(new Action(w.Reload));     // on the window's own message loop, as the app does
            Check(ShowUntil(w, delegate { return server.Drew("newer"); }, 30), "after a rebuild, a reload of the open window draws the new screens");
            CloseWindow(w);

            server.Forget(); server.BrokenScripts = 1;
            w = new AppWindow(server.Url, data, log);
            Check(ShowUntil(w, delegate { return server.Drew("newer"); }, 30), "a load that drew nothing is loaded again without being asked");
            CloseWindow(w);
        }
    }
}
