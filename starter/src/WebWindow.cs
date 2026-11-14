// The window that shows the app itself (Microsoft WebView2) and the small bridge to the page. The WebView2 files live
// in starter\bin (see starter\bin\PINS.txt); when they, or Microsoft's WebView2 runtime, are missing, the caller opens
// the default browser instead. Only this file names WebView2 types, and callers reach it through AppWindow.Available,
// so a missing file never stops the starter from running.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace Vgac.Starter
{
    // The page may call only these (and only from the backend's own origin): hello, pickFolder, pickFile.
    // The chosen path is sent back to the page, which sends it to the backend; the backend checks it.
    public static class BridgeOps
    {
        public const string Script =
            "(function(){if(location.origin!==\"__ORIGIN__\"||!window.chrome||!window.chrome.webview)return;" +
            "var pend={},n=0;window.chrome.webview.addEventListener('message',function(e){var m=e.data;if(m&&pend[m.id]){pend[m.id](m.result);delete pend[m.id];}});" +
            "function call(op,a){return new Promise(function(r){var id=++n;pend[id]=r;window.chrome.webview.postMessage({id:id,op:op,args:a||{}});});}" +
            "window.vgacHost={version:1,hello:function(){return call('hello');}," +
            "pickFolder:function(t){return call('pickFolder',{title:t});}," +
            "pickFile:function(t,f){return call('pickFile',{title:t,filter:f});}};})();";

        // Pure: runs one request (parsed JSON) and returns the result value, or null for "cancelled" and for anything
        // not in the list. `ui` does the dialogs, so this is testable without a window.
        public static object Handle(Dictionary<string, object> msg, IPickers ui)
        {
            object opObj;
            if (!msg.TryGetValue("op", out opObj)) return null;
            var args = msg.ContainsKey("args") ? msg["args"] as Dictionary<string, object> : null;
            string title = Arg(args, "title");
            switch (opObj as string)
            {
                case "hello": return "VGAC starter 0.1";
                case "pickFolder": return ui.PickFolder(title ?? "Choose a folder");
                case "pickFile": return ui.PickFile(title ?? "Choose a file", Arg(args, "filter"));
            }
            return null;
        }

        static string Arg(Dictionary<string, object> a, string k)
        {
            object v;
            return a != null && a.TryGetValue(k, out v) && v != null ? v.ToString() : null;
        }

        // The page's origin must be exactly the backend's.
        public static bool FromOurOrigin(string source, string origin)
        {
            if (string.IsNullOrEmpty(source)) return false;
            return source.Equals(origin, StringComparison.OrdinalIgnoreCase) || source.StartsWith(origin + "/", StringComparison.OrdinalIgnoreCase);
        }
    }

    public interface IPickers
    {
        string PickFolder(string title);
        string PickFile(string title, string filter);
    }

    public class DialogPickers : IPickers
    {
        readonly IWin32Window owner;
        public DialogPickers(IWin32Window owner) { this.owner = owner; }

        public string PickFolder(string title)
        {
            using (var d = new FolderBrowserDialog())
            {
                d.Description = title;
                return d.ShowDialog(owner) == DialogResult.OK ? d.SelectedPath : null;
            }
        }

        // filter like "Blender|blender.exe" or "Programs|*.exe"; empty means every file.
        public string PickFile(string title, string filter)
        {
            using (var d = new OpenFileDialog())
            {
                d.Title = title;
                if (!string.IsNullOrEmpty(filter))
                {
                    string[] parts = filter.Split('|');
                    if (parts.Length == 2) d.Filter = parts[0] + "|" + parts[1] + "|All files|*.*";
                }
                return d.ShowDialog(owner) == DialogResult.OK ? d.FileName : null;
            }
        }
    }

    public class AppWindow : Form
    {
        readonly string url;
        readonly string origin;
        readonly string userData;
        readonly WebView2 view = new WebView2();
        readonly IPickers pickers;
        readonly SetupLog log;
        public string Problem;   // why the window could not open, if it could not

        // True when Microsoft's WebView2 runtime is installed. False (never an exception) when it is not.
        [MethodImpl(MethodImplOptions.NoInlining)]
        public static bool RuntimeInstalled()
        {
            try { return !string.IsNullOrEmpty(CoreWebView2Environment.GetAvailableBrowserVersionString()); }
            catch (Exception) { return false; }
        }

        public AppWindow(string appUrl, string userDataFolder, SetupLog log)
        {
            url = appUrl;
            origin = new Uri(appUrl).GetLeftPart(UriPartial.Authority);
            userData = userDataFolder;
            this.log = log;
            pickers = new DialogPickers(this);
            Text = "VGAC";
            ClientSize = new System.Drawing.Size(1440, 900);
            MinimumSize = new System.Drawing.Size(1000, 700);
            StartPosition = FormStartPosition.CenterScreen;
            view.Dock = DockStyle.Fill;
            Controls.Add(view);
            Shown += async delegate { await Init(); };
        }

        async System.Threading.Tasks.Task Init()
        {
            try
            {
                Directory.CreateDirectory(userData);
                var env = await CoreWebView2Environment.CreateAsync(null, userData);
                await view.EnsureCoreWebView2Async(env);
                var core = view.CoreWebView2;
                core.Settings.AreDefaultContextMenusEnabled = true;
                core.Settings.IsStatusBarEnabled = false;
                await core.AddScriptToExecuteOnDocumentCreatedAsync(BridgeOps.Script.Replace("__ORIGIN__", origin));
                core.WebMessageReceived += OnMessage;
                core.NavigationStarting += OnNavigating;
                core.NewWindowRequested += delegate(object s, CoreWebView2NewWindowRequestedEventArgs e) { e.Handled = true; OpenOutside(e.Uri); };
                core.DocumentTitleChanged += delegate { if (!string.IsNullOrEmpty(core.DocumentTitle)) Text = core.DocumentTitle; };
                core.Navigate(url);
                log.Line("app window open at " + url);
            }
            catch (Exception e)
            {
                Problem = e.Message;
                log.Line("app window failed: " + e.Message);
                Close();
            }
        }

        void OnNavigating(object sender, CoreWebView2NavigationStartingEventArgs e)
        {
            // Pages of other sites open in the person's own browser, never inside this window.
            if (!BridgeOps.FromOurOrigin(e.Uri, origin) && !e.Uri.StartsWith("about:", StringComparison.OrdinalIgnoreCase))
            {
                e.Cancel = true;
                OpenOutside(e.Uri);
            }
        }

        static void OpenOutside(string uri)
        {
            if (uri != null && (uri.StartsWith("http://", StringComparison.OrdinalIgnoreCase) || uri.StartsWith("https://", StringComparison.OrdinalIgnoreCase)))
            {
                try { Process.Start(uri); } catch (Exception) { }
            }
        }

        void OnMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            if (!BridgeOps.FromOurOrigin(e.Source, origin)) return;
            try
            {
                var ser = new JavaScriptSerializer();
                var msg = ser.DeserializeObject(e.WebMessageAsJson) as Dictionary<string, object>;
                if (msg == null || !msg.ContainsKey("id")) return;
                object result = BridgeOps.Handle(msg, pickers);
                var reply = new Dictionary<string, object>();
                reply["id"] = msg["id"];
                reply["result"] = result;
                view.CoreWebView2.PostWebMessageAsJson(ser.Serialize(reply));
            }
            catch (Exception ex) { log.Line("bridge error: " + ex.Message); }
        }
    }
}
