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
            "pickFile:function(t,f){return call('pickFile',{title:t,filter:f});}};" +
            // The same three calls the Electron launcher gave the page (window.vgacDesktop), so the screens work with either.
            "window.vgacDesktop={setWork:function(c,i){return call('setWork',{count:c,icon:typeof i==='string'?i:''});}," +
            "notify:function(t,b){return call('notify',{title:String(t||''),body:String(b||'')});}," +
            "startEngine:function(){return call('startEngine');}};})();";

        // Pure: runs one request (parsed JSON) and returns the result value, or null for "cancelled" and for anything
        // not in the list. `ui` does the dialogs, `actions` the badge, the notification and the engine restart, so this is
        // testable without a window.
        public static object Handle(Dictionary<string, object> msg, IPickers ui) { return Handle(msg, ui, null); }

        public static object Handle(Dictionary<string, object> msg, IPickers ui, IHostActions actions)
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
                case "setWork":
                    if (actions == null) return false;
                    int n = BridgeRules.CleanCount(args != null && args.ContainsKey("count") ? args["count"] : null);
                    actions.SetWork(n, n > 0 ? BridgeRules.CleanIcon(Arg(args, "icon")) : null);
                    return true;
                case "notify":
                    if (actions == null) return false;
                    return actions.Notify(BridgeRules.CleanText(title, 80), BridgeRules.CleanText(Arg(args, "body"), 240));
                case "startEngine":
                    return actions != null && actions.StartEngine();
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

    // What the page may ask the launcher to do besides choosing a path (the Electron launcher's three calls).
    public interface IHostActions
    {
        void SetWork(int count, byte[] png);          // taskbar badge; 0 clears it
        bool Notify(string title, string body);       // false when not shown (window in front, or not possible)
        bool StartEngine();                           // restart the backend
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
        readonly IHostActions actions;
        public string Problem;   // why the window could not open, if it could not
        // When set, the X on the window asks this instead of closing (it means "quit").
        public Action CloseRequested;
        public Action QuitRequested, StatusRequested;   // Ctrl+Q and Ctrl+Shift+S pressed while this window has focus
        public bool Quitting;
        FormBorderStyle borderBefore; FormWindowState stateBefore; bool full;

        // F11: the page fills the screen and back.
        public void ToggleFullscreen()
        {
            if (!full)
            {
                borderBefore = FormBorderStyle; stateBefore = WindowState;
                if (WindowState == FormWindowState.Maximized) WindowState = FormWindowState.Normal;
                FormBorderStyle = FormBorderStyle.None; WindowState = FormWindowState.Maximized; full = true;
            }
            else
            {
                FormBorderStyle = borderBefore; WindowState = stateBefore; full = false;
            }
        }

        protected override bool ProcessCmdKey(ref Message msg, Keys keyData)
        {
            if (QuitRequested != null && Shortcuts.Handle(keyData, "app window", log, QuitRequested, StatusRequested, ToggleFullscreen)) return true;
            return base.ProcessCmdKey(ref msg, keyData);
        }

        // True when Microsoft's WebView2 runtime is installed. False (never an exception) when it is not.
        [MethodImpl(MethodImplOptions.NoInlining)]
        public static bool RuntimeInstalled()
        {
            try { return !string.IsNullOrEmpty(CoreWebView2Environment.GetAvailableBrowserVersionString()); }
            catch (Exception) { return false; }
        }

        public AppWindow(string appUrl, string userDataFolder, SetupLog log) : this(appUrl, userDataFolder, log, null) { }

        public AppWindow(string appUrl, string userDataFolder, SetupLog log, IHostActions actions)
        {
            this.actions = actions;
            url = appUrl;
            origin = new Uri(appUrl).GetLeftPart(UriPartial.Authority);
            userData = userDataFolder;
            this.log = log;
            pickers = new DialogPickers(this);
            Text = "Video Game Asset Creator";
            Icon = AppIcon.Get(32);
            ClientSize = new System.Drawing.Size(1440, 900);
            MinimumSize = new System.Drawing.Size(360, 480);
            StartPosition = FormStartPosition.CenterScreen;
            view.Dock = DockStyle.Fill;
            Controls.Add(view);
            Shown += async delegate { await Init(); };
            FormClosing += delegate(object s, FormClosingEventArgs e)
            {
                if (LauncherRules.AppWindowClosing(Quitting, CloseRequested != null) == CloseOutcome.Allow) return;
                e.Cancel = true;
                CloseRequested();
            };
        }

        public void Reload()
        {
            try { if (view.CoreWebView2 != null) view.CoreWebView2.Reload(); } catch (Exception) { }
        }

        // Shows the window and brings it to the front, also when it is minimised.
        public void Bring()
        {
            if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
            Show();
            Activate();
            Foreground.Front(Handle);
        }

        public bool InFront { get { return Form.ActiveForm == this; } }

        // The web view's data folder can still be held for a moment by a copy that just closed (HRESULT 0x800700AA, "resource
        // in use"). That one failure gets a second try after a short wait; any other failure, or a second one, closes the window.
        public int RetryDelayMs = 2000;
        public Action<int> StartProbe;   // called with the try number (1, 2) before each start; may throw (tests)

        async System.Threading.Tasks.Task Init()
        {
            for (int attempt = 1; ; attempt++)
            {
                Exception failure = null;
                try
                {
                    if (StartProbe != null) StartProbe(attempt);
                    await StartView();
                    return;
                }
                catch (Exception e) { failure = e; }
                if (LauncherRules.WebViewStartRetry(failure.HResult, attempt))
                {
                    log.Line("app window: the web view's data folder is in use (" + failure.Message + "); trying again in " + (RetryDelayMs / 1000.0).ToString("0.#") + " s");
                    await System.Threading.Tasks.Task.Delay(RetryDelayMs);
                    if (IsDisposed) return;
                    continue;
                }
                Problem = failure.Message;
                log.Line("app window failed: " + failure.Message);
                // This close is the window giving up, not the person's X: let it through instead of asking to quit,
                // which would leave an empty window behind a question nobody asked.
                Quitting = true;
                Close();
                return;
            }
        }

        async System.Threading.Tasks.Task StartView()
        {
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
                // Every permission the page could ask for (camera, location, ...) is refused; the title stays fixed.
                // Keys pressed while the page itself has focus never reach the form, so ask the web view to tell us.
                CoreWebView2Controller ctl = null;
                foreach (FieldInfo f in typeof(WebView2).GetFields(BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public))
                    if (f.FieldType == typeof(CoreWebView2Controller)) ctl = f.GetValue(view) as CoreWebView2Controller;
                if (ctl == null) log.Line("page shortcuts (Ctrl+Q, F11) are not available in this WebView2 version; they work while a window outside the page has focus");
                else ctl.AcceleratorKeyPressed += delegate(object s, CoreWebView2AcceleratorKeyPressedEventArgs e)
                {
                    if (e.KeyEventKind != CoreWebView2KeyEventKind.KeyDown && e.KeyEventKind != CoreWebView2KeyEventKind.SystemKeyDown) return;
                    Keys k = (Keys)e.VirtualKey;
                    if ((Control.ModifierKeys & Keys.Control) != 0) k |= Keys.Control;
                    if ((Control.ModifierKeys & Keys.Shift) != 0) k |= Keys.Shift;
                    if ((Control.ModifierKeys & Keys.Alt) != 0) k |= Keys.Alt;
                    if (QuitRequested != null && Shortcuts.Handle(k, "app page", log, QuitRequested, StatusRequested, ToggleFullscreen)) e.Handled = true;
                };
                core.PermissionRequested += delegate(object s, CoreWebView2PermissionRequestedEventArgs e) { e.State = CoreWebView2PermissionState.Deny; };
                core.Navigate(url);
                log.Line("app window open at " + url);
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
                object result = BridgeOps.Handle(msg, pickers, actions);
                var reply = new Dictionary<string, object>();
                reply["id"] = msg["id"];
                reply["result"] = result;
                view.CoreWebView2.PostWebMessageAsJson(ser.Serialize(reply));
            }
            catch (Exception ex) { log.Line("bridge error: " + ex.Message); }
        }
    }

    // Keys the launcher answers itself, with a log line for each so a hand test can tell "no key arrived" from "arrived,
    // nothing happened". Used by both windows.
    public static class Shortcuts
    {
        public static bool Handle(Keys keyData, string where, SetupLog log, Action quit, Action status, Action fullscreen)
        {
            Keys key = keyData & Keys.KeyCode;
            string name = key == Keys.F11 ? "F11" : key.ToString();
            ShortcutAction a = LauncherRules.Shortcut((keyData & Keys.Control) != 0, (keyData & Keys.Shift) != 0, (keyData & Keys.Alt) != 0, name);
            if (a == ShortcutAction.None) return false;
            if (a == ShortcutAction.Fullscreen && fullscreen == null) return false;
            log.Line("shortcut received in the " + where + ": " + a);
            if (a == ShortcutAction.Quit) quit();
            else if (a == ShortcutAction.StatusWindow) status();
            else fullscreen();
            return true;
        }
    }
}
