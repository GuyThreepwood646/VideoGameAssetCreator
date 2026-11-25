// VGAC.exe: the launcher. Does only what must exist before the backend can answer (a private Python with the app's
// packages, the built screens, a settings file), starts the backend, opens the app in its own window, and then stays as
// the app's host: a status window, a tray icon, the taskbar badge, notifications, one copy at a time, and a careful quit.
// Everything else (ComfyUI, Blender, models) is the app's own setup card.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("VGAC")]
[assembly: System.Reflection.AssemblyDescription("Starter for the Video Game Asset Creator: sets up the app's Python and screens, starts it and opens it.")]
[assembly: System.Reflection.AssemblyProduct("Video Game Asset Creator")]
[assembly: System.Reflection.AssemblyCopyright("GPL-3.0-or-later")]
[assembly: System.Reflection.AssemblyVersion("0.2.0.0")]

namespace Vgac.Starter
{
    public static class Program
    {
        public const int DefaultPort = 8765;
        public static bool AutoStart;        // --start: go through the Welcome page without the button when only the screens' build is pending
        public static int OpenTab = -1;      // --open-maintenance <0..3>: for checking the window

        public static string FindProjectRoot(string start) { return ProjectFinder.Find(start); }

        [STAThread]
        public static int Main(string[] args)
        {
            string project = null;
            string planOut = null;
            string stateOut = null;
            bool keepStatus = false;
            int exitAfter = 0;
            int port = DefaultPort;
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--project" && i + 1 < args.Length) project = args[++i];
                else if (args[i] == "--plan" && i + 1 < args.Length) planOut = args[++i];
                else if (args[i] == "--port" && i + 1 < args.Length) int.TryParse(args[++i], out port);
                else if (args[i] == "--exit-after" && i + 1 < args.Length) int.TryParse(args[++i], out exitAfter);
                else if (args[i] == "--state-out" && i + 1 < args.Length) stateOut = args[++i];
                else if (args[i] == "--keep-status") keepStatus = true;
                else if (args[i] == "--start") AutoStart = true;
                else if (args[i] == "--open-maintenance" && i + 1 < args.Length) int.TryParse(args[++i], out OpenTab);
                else if (args[i] == "--after" && i + 1 < args.Length) { int pid; if (int.TryParse(args[++i], out pid)) WaitForExit(pid); }
            }
            for (int i = 0; i + 1 < args.Length; i++)
                if (args[i] == "--window") return WindowOnly(args[i + 1]);
            string root = FindProjectRoot(project ?? AppDomain.CurrentDomain.BaseDirectory);
            if (planOut != null) return PlanOnly(root, planOut);

            ErrorBoxes.Off();
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            if (root == null)
            {
                MessageBox.Show("VGAC.exe has to stay inside the VGAC project folder (the one with the backend and frontend folders in it).\r\n\r\nMove it back, or clone the project again.",
                    "VGAC", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return 2;
            }
            // One copy per project folder. A second start says nothing: it tells the first to come forward and leaves.
            Foreground.AllowAny();
            bool answered;
            using (InstanceGate gate = InstanceGate.TryOwn(InstanceGate.KeyFor(root), 3000, out answered))
            {
                if (gate == null)
                {
                    // The first copy did not answer: it is stuck, or it runs as another user.
                    if (!answered)
                        MessageBox.Show("VGAC did not answer. It may be starting or closing.\r\n\r\nTry again in a moment. If it keeps happening, close VGAC.exe in Task Manager and start again.", "VGAC", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    return 0;
                }
                var form = new MainForm(root, port, gate, exitAfter, stateOut, keepStatus);
                Application.Run(form);
            }
            return 0;
        }

        // After "Restart VGAC": the old copy is still closing; wait for it (up to a minute) before taking its place.
        static void WaitForExit(int pid)
        {
            try { using (var p = Process.GetProcessById(pid)) p.WaitForExit(60000); }
            catch (Exception) { /* already gone */ }
        }

        // --window <url>: opens only the in-app window on a page (for checking the window and the bridge without the
        // backend). The page's origin is the one the bridge answers to. Its browser data goes to a temporary folder.
        static int WindowOnly(string url)
        {
            Application.EnableVisualStyles();
            string bin = WebBin.Find(AppDomain.CurrentDomain.BaseDirectory);
            if (bin == null) return 3;
            WebBin.Prepare(bin);
            return RunWindow(url);
        }

        [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
        static int RunWindow(string url)
        {
            if (!AppWindow.RuntimeInstalled()) return 4;
            string data = Path.Combine(Path.GetTempPath(), "vgac-window-" + Guid.NewGuid().ToString("N").Substring(0, 8));
            var w = new AppWindow(url, data, new SetupLog(data));
            Application.Run(w);
            return w.Problem == null ? 0 : 5;
        }

        // --plan <file>: writes what a start would do, as JSON, and changes nothing else. For checks without a window.
        static int PlanOnly(string root, string outFile)
        {
            var d = new Dictionary<string, object>();
            if (root == null) { d["error"] = "not in a VGAC project folder"; File.WriteAllText(outFile, Json.Write(d)); return 2; }
            try
            {
                var machine = MachineFile.Load(root);
                var lay = Vgac.Starter.Layout.From(root, machine);
                var probe = ProbeReader.Read(lay, new RealHost());
                d["project"] = root;
                d["dataFolder"] = lay.DataFolder;
                d["steps"] = Planner.Plan(probe, root);
            }
            catch (StarterError e) { d["error"] = e.Message; }
            File.WriteAllText(outFile, Json.Write(d));
            return 0;
        }
    }

    // A small question with two buttons that say what they do ("Quit and stop them" / "Cancel").
    public static class Ask
    {
        public static bool Show(IWin32Window owner, string title, string detail, string okLabel, string cancelLabel, bool defaultCancel)
        {
            using (var f = new Form())
            {
                f.Text = "VGAC";
                f.FormBorderStyle = FormBorderStyle.FixedDialog;
                f.MaximizeBox = false; f.MinimizeBox = false; f.ShowInTaskbar = false;
                f.StartPosition = FormStartPosition.CenterScreen;
                f.ClientSize = new Size(520, 210);
                var head = new Label(); head.Text = title; head.Font = new Font(f.Font.FontFamily, 11f, FontStyle.Bold); head.SetBounds(16, 14, 488, 26);
                var body = new Label(); body.Text = detail; body.SetBounds(16, 48, 488, 100);
                var ok = new Button(); ok.Text = okLabel; ok.DialogResult = DialogResult.OK; ok.SetBounds(16, 162, 200, 32);
                var cancel = new Button(); cancel.Text = cancelLabel; cancel.DialogResult = DialogResult.Cancel; cancel.SetBounds(228, 162, 120, 32);
                f.Controls.AddRange(new Control[] { head, body, ok, cancel });
                f.AcceptButton = defaultCancel ? cancel : ok;
                f.CancelButton = cancel;
                f.TopMost = true;
                f.Shown += delegate { (defaultCancel ? cancel : ok).Focus(); };
                return (owner == null ? f.ShowDialog() : f.ShowDialog(owner)) == DialogResult.OK;
            }
        }
    }

    public class MainForm : Form, IHostActions, IMaintenanceHost
    {
        readonly string root;
        readonly int port;
        readonly InstanceGate gate;
        readonly int exitAfter;
        readonly string stateOut;
        readonly bool keepStatus;
        readonly RealHost host = new RealHost();
        readonly SetupLog log;
        MachineFile machine;
        Vgac.Starter.Layout lay;
        DesktopSettings desk = new DesktopSettings();
        List<string> steps;
        BackendRunner backend;
        Supervisor sup;
        AppWindow appWindow;
        NotifyIcon tray;
        volatile bool cancel;
        volatile bool setupRunning;
        volatile bool quitting;
        volatile bool engineBusy;
        bool statusMode;
        Action onPrimary = delegate { }, onSecondary = delegate { }, onChoose = delegate { };

        readonly Label title = new Label();
        readonly Label body = new Label();
        readonly ProgressBar bar = new ProgressBar();
        readonly Button primary = new Button();
        readonly Button choose = new Button();
        readonly Button secondary = new Button();
        readonly Button openLog = new Button();
        // the status view
        readonly Panel statusPanel = new Panel();
        readonly Label summary = new Label();
        readonly Panel rowsHost = new Panel();
        readonly Label hint = new Label();

        public MainForm(string projectRoot, int port, InstanceGate gate, int exitAfter, string stateOut, bool keepStatus)
        {
            comfyInstall = new BackendComfyInstall(delegate(string a, int ms, Func<bool> c) { return RunComfyCli(a, ms, c); });
            root = projectRoot; this.port = port; this.gate = gate; this.exitAfter = exitAfter; this.stateOut = stateOut; this.keepStatus = keepStatus;
            log = new SetupLog(root);
            Text = "VGAC status";
            Icon = AppIcon.Get(32);
            ClientSize = new Size(620, 300);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            title.SetBounds(16, 12, 588, 28); title.Font = new Font(Font.FontFamily, 14f, FontStyle.Bold);
            body.SetBounds(16, 48, 588, 150);
            bar.SetBounds(16, 206, 588, 18); bar.Visible = false;
            primary.SetBounds(16, 250, 130, 32);
            secondary.SetBounds(156, 250, 110, 32);
            choose.SetBounds(276, 250, 220, 32); choose.Text = "Choose another folder...";
            openLog.SetBounds(506, 250, 98, 32); openLog.Text = "Open log";
            choose.Visible = false; openLog.Visible = false; primary.Visible = false; secondary.Visible = false;
            statusPanel.Dock = DockStyle.Fill; statusPanel.Visible = false;
            Controls.AddRange(new Control[] { title, body, bar, primary, secondary, choose, openLog, statusPanel });
            BuildStatusView();
            primary.Click += delegate { onPrimary(); };
            secondary.Click += delegate { onSecondary(); };
            choose.Click += delegate { onChoose(); };
            openLog.Click += delegate { OpenPath(log.Path); };
            Shown += delegate { Begin(); };
            FormClosing += OnFormClosing;
            FormClosed += delegate { if (!quitting) Teardown(); };
            // A second start: when this window cannot show anything yet (still being made) or VGAC is closing, say so at once;
            // otherwise the window answers after it has come forward (a hung window never does).
            gate.Listen(delegate
            {
                if (quitting || IsDisposed || !IsHandleCreated) { gate.Ack(); return; }
                BeginInvokeSafe(ShowForeground);
            });
            MakeTray();
        }

        // ---- the status view -----------------------------------------------------------------------------------------

        void BuildStatusView()
        {
            summary.SetBounds(16, 10, 580, 24); summary.Font = new Font(Font.FontFamily, 11f, FontStyle.Bold);
            rowsHost.SetBounds(0, 40, 620, 300); rowsHost.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
            hint.SetBounds(16, 350, 580, 20); hint.ForeColor = Color.DimGray;
            var open = new Button(); open.Text = "Open app"; open.Name = "openApp"; open.SetBounds(16, 380, 120, 32);
            var logs = new Button(); logs.Text = "Open logs folder"; logs.SetBounds(146, 380, 130, 32);
            var exports = new Button(); exports.Text = "Open exports folder"; exports.SetBounds(286, 380, 140, 32);
            var setup = new Button(); setup.Text = "Open log"; setup.SetBounds(436, 380, 80, 32);
            var quit = new Button(); quit.Text = "Quit"; quit.SetBounds(526, 380, 76, 32);
            var maint = new Button(); maint.Text = "Maintenance..."; maint.SetBounds(16, 420, 140, 32);
            maint.Click += delegate { OpenMaintenance(0); };
            open.Click += delegate { OpenApp2(); };
            logs.Click += delegate { OpenFolder(Path.Combine(root, "runtime", "logs")); };
            exports.Click += delegate { OpenFolder(Path.Combine(root, "exports")); };
            setup.Click += delegate { OpenPath(log.Path); };
            quit.Click += delegate { RequestQuit(); };
            statusPanel.Controls.AddRange(new Control[] { summary, rowsHost, hint, open, logs, exports, setup, quit, maint });
        }

        void ShowStatusView()
        {
            if (!statusMode)
            {
                statusMode = true;
                foreach (Control c in new Control[] { title, body, bar, primary, secondary, choose, openLog }) c.Visible = false;
                statusPanel.Visible = true;
                FormBorderStyle = FormBorderStyle.Sizable;
                MaximizeBox = true;
                MinimumSize = new Size(500, 420);
                ClientSize = new Size(620, 470);
            }
            if (sup != null) RenderRows(sup.Latest);
        }

        static Color DotColor(string state)
        {
            switch (state)
            {
                case "up": return Color.FromArgb(0x2e, 0xa0, 0x5f);
                case "starting": case "building": return Color.FromArgb(0xd8, 0x9b, 0x1c);
                case "error": return Color.FromArgb(0xc0, 0x39, 0x2b);
                case "off": return Color.FromArgb(0x6a, 0x7a, 0x99);
            }
            return Color.Gray;
        }

        void RenderRows(StatusSnapshot snap)
        {
            if (snap == null || IsDisposed) return;
            summary.Text = snap.Summary;
            rowsHost.SuspendLayout();
            rowsHost.Controls.Clear();
            int y = 0;
            foreach (Row r in snap.Rows)
            {
                var row = new Panel(); row.SetBounds(0, y, rowsHost.Width, 72);
                row.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
                var dot = new Panel(); dot.SetBounds(16, 8, 12, 12); dot.BackColor = DotColor(r.State);
                var name = new Label(); name.Text = r.Name; name.Font = new Font(Font.FontFamily, 9.5f, FontStyle.Bold); name.SetBounds(36, 4, 380, 20);
                var msg = new Label(); msg.Text = r.Message + (r.Owned && r.Pid > 0 ? " (PID " + r.Pid + ")" : ""); msg.SetBounds(36, 24, 380, 44);
                msg.ForeColor = r.State == "error" ? Color.FromArgb(0xc0, 0x39, 0x2b) : (r.State == "off" ? Color.DimGray : ForeColor);
                row.Controls.AddRange(new Control[] { dot, name, msg });
                int bx = 430;
                foreach (ActionItem a in r.Actions)
                {
                    var b = new Button(); b.Text = a.Label; b.SetBounds(bx, 18, 150, 28); b.Tag = a.Id;
                    b.Click += delegate(object s, EventArgs e) { Act((string)((Button)s).Tag); };
                    row.Controls.Add(b);
                    bx -= 0; // one button per row is the common case; a second would not fit, so it is stacked below
                    break;
                }
                rowsHost.Controls.Add(row);
                y += 72;
            }
            rowsHost.ResumeLayout();
            var be = snap.Get("backend");
            bool backendUp = be != null && be.State == "up";
            foreach (Control c in statusPanel.Controls) if (c.Name == "openApp") c.Enabled = backendUp;
            hint.Text = snap.AllUp ? "" : (backendUp ? "Some services are still down" : "Waiting for the app's engine");
        }

        void OnState(StatusSnapshot snap)
        {
            BeginInvokeSafe(delegate
            {
                if (tray != null) tray.Text = snap.Tooltip;
                if (statusMode) RenderRows(snap);
            });
        }

        // ---- start -----------------------------------------------------------------------------------------------------

        void Begin()
        {
            try
            {
                machine = MachineFile.Load(root);
                desk = DesktopSettings.From(machine);
                lay = Vgac.Starter.Layout.From(root, machine);
                Probe probe = ProbeReader.Read(lay, host);
                steps = Planner.Plan(probe, root);
            }
            catch (StarterError e) { ShowError(e); return; }
            AtStartChecks();
            if (exitAfter > 0) ScheduleExit();
            string moveGate = MoveGate.Blocking(root);
            if (moveGate != null) { ShowMoveBlocked(moveGate); return; }
            if (steps.Count == 0) { StartBackend(); return; }
            ShowWelcome();
            if (Program.AutoStart)
            {
                if (UnattendedStart.Allowed(steps)) { log.Line("start: --start, only the screens are to be built; going on without the button"); OnStart(); }
                else log.Line("start: --start, but a step needs a person (" + string.Join(", ", steps.ToArray()) + "); waiting for the Start button");
            }
        }

        // What an earlier, interrupted job may have left, and what to tell the person about it.
        void AtStartChecks()
        {
            try
            {
                foreach (string n in Recovery.AtStart(lay, Application.ExecutablePath)) log.Line("start: " + n);
                // a restart request left from before this launcher started is never acted on
                try { if (File.Exists(RestartRequests.FilePath(root))) { File.Delete(RestartRequests.FilePath(root)); log.Line("start: an old restart request was deleted unread"); } }
                catch (Exception ex) { log.Line("start: could not delete an old restart request: " + ex.Message); }
                // a move that switched needed this restart: from here the new place is the one in use
                if (MoveRules.ClearRestartAtStart(root)) log.Line("start: restarted after a move of the big files");
                // the screens or the Python may have been put right by the recovery: look again
                steps = Planner.Plan(ProbeReader.Read(lay, host), root);
                // the folder for the big files is looked at again at every start, not only when it was chosen
                DataStatus dstat = DataFolders.Check(lay.DataFolder, machine, root);
                if (dstat.State == DataState.NotOurs)
                {
                    log.Line("start: the folder for the big files is not shown to be VGAC's: " + dstat.Why + (dstat.Adoptable ? " It can be adopted in Check and repair." : ""));
                    if (dstat.Problem)
                        MessageBox.Show("The folder for the big files (" + lay.DataFolder + ") is not one VGAC will set up in, move or remove from.\r\n\r\n" + dstat.Why + "\r\n\r\nChoose another folder when you have a moment. VGAC carries on with what is already there.", "VGAC", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
                // a library folder that is new or empty is claimed before the app puts anything in it; one that already holds things is not
                try { LibraryFolders.ClaimIfNew(lay, machine); } catch (Exception ex) { log.Line("start: the library folder was not claimed: " + ex.Message); }
                string note = GitUpdate.ExeDiffersNote(Application.ExecutablePath, Path.Combine(root, "starter", "CHECKSUMS.txt"));
                if (note != null) log.Line("start: " + note);
                if (GitUpdate.MidPull(root) != null)
                    MessageBox.Show(GitUpdate.MidPullText + "\r\n\r\n(Git left a pull open in the project folder. VGAC carries on, but the files may be a mix of two versions.)", "VGAC", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                var jm = MoveRunner.Load(root, lay.DataFolder);
                if (jm == null && MoveRunner.StaleFound)
                    log.Line("start: a record of an earlier move of the big files does not fit this install and is ignored");
                if (jm != null)
                {
                    if (MessageBox.Show("A move of the big files was interrupted (" + (jm.State == "switching" ? "while switching to the new folder" : "while copying") + "). Open Maintenance to continue it" + (jm.State == "copying" ? " or abandon it" : "") + "?", "VGAC", MessageBoxButtons.YesNo) == DialogResult.Yes) pendingTab = 2;
                }
                else if (File.Exists(RemovalRunner.JournalPath(root)) && RemovalRunner.InProgress(root).Count > 0)
                {
                    if (MessageBox.Show("A removal was interrupted. Open Maintenance to finish it?", "VGAC", MessageBoxButtons.YesNo) == DialogResult.Yes) pendingTab = 3;
                }
            }
            catch (Exception e) { log.Line("start checks failed: " + e.Message); }
        }

        int pendingTab = Program.OpenTab;

        void ScheduleExit()
        {
            var t = new System.Windows.Forms.Timer();
            t.Interval = exitAfter;
            t.Tick += delegate
            {
                t.Stop();
                WriteState();
                // A test switch, but it never stops a backend that has work in it.
                if (!QuitPolicy.AutoExitAllowed(backend != null && backend.Owned, sup != null ? sup.Jobs : 0)) { log.Line("--exit-after: jobs are running, not quitting"); return; }
                DoQuit();
            };
            t.Start();
        }

        // --exit-after: the rows as JSON, so a scripted check can read what the launcher saw.
        void WriteState()
        {
            if (stateOut == null) return;
            try
            {
                var d = new Dictionary<string, object>();
                var rows = new List<object>();
                if (sup != null && sup.Latest != null)
                    foreach (Row r in sup.Latest.Rows)
                    {
                        var o = new Dictionary<string, object>();
                        o["key"] = r.Key; o["state"] = r.State; o["message"] = r.Message;
                        rows.Add(o);
                    }
                d["rows"] = rows;
                d["appWindowOpen"] = appWindow != null;
                d["backendOwned"] = backend != null && backend.Owned;
                File.WriteAllText(stateOut, Json.Write(d));
            }
            catch (Exception) { }
        }

        void ShowWelcome()
        {
            DefaultButtons();
            title.Text = "Welcome to VGAC";
            var sb = new StringBuilder();
            sb.AppendLine("Before the app can open, VGAC will:");
            foreach (string s in steps) sb.AppendLine("   - " + Planner.Plain(s));
            sb.AppendLine();
            sb.AppendLine("It needs about " + (Pins.NeedBytes >> 30) + " GB in: " + lay.DataFolder);
            sb.AppendLine("It will contact: " + Pins.Hosts + ".");
            sb.AppendLine("It does not ask for administrator rights and does not change Windows settings.");
            body.Text = sb.ToString();
            primary.Text = "Start"; secondary.Text = "Not now";
            primary.Visible = secondary.Visible = choose.Visible = true; openLog.Visible = false;
            onPrimary = OnStart; onSecondary = OnNotNow; onChoose = OnChoose;
        }

        void OnNotNow() { RequestQuit(); }

        void OnChoose()
        {
            using (var d = new FolderBrowserDialog())
            {
                d.Description = "Choose a folder just for VGAC's big files (Python, tools; later the models). Pick a drive with room.";
                if (d.ShowDialog(this) != DialogResult.OK) return;
                string unfit = Proof.ChoiceProblem(d.SelectedPath, root);
                if (unfit != null) { MessageBox.Show(this, unfit, "VGAC"); return; }
                try
                {
                    DataFolders.Choose(machine, d.SelectedPath, root);          // keeps the claim only when the folder's marker is this install's
                    lay = Vgac.Starter.Layout.From(root, machine);
                    backend = null;
                    if (afterChooseBegins) { afterChooseBegins = false; Begin(); return; }
                    body.Text = "The big files will go to: " + lay.DataFolder + "\r\n\r\nPress Start.";
                }
                catch (Exception ex) { MessageBox.Show(ex.Message, "VGAC"); }
            }
        }

        void OnStart()
        {
            primary.Visible = secondary.Visible = choose.Visible = false;
            bar.Visible = true; title.Text = "Setting up VGAC";
            setupRunning = true;
            var th = new Thread(RunSetup);
            th.IsBackground = true;
            th.Start();
        }

        void BeginInvokeSafe(Action a)
        {
            if (IsDisposed || !IsHandleCreated) return;
            try { BeginInvoke(a); } catch (InvalidOperationException) { }
        }

        void Ui(Action a) { BeginInvokeSafe(a); }

        void RunSetup()
        {
            try
            {
                var runner = new SetupRunner(lay, host, machine, Ledger.Load(root), log);
                var prog = new Progress();
                prog.Say = delegate(string t) { Ui(delegate { body.Text = t; }); };
                prog.Fraction = delegate(double f) { Ui(delegate { bar.Value = (int)(f * 100); }); };
                prog.Cancelled = delegate { return cancel; };
                runner.Run(steps, prog);
                setupRunning = false;
                Ui(delegate { bar.Visible = false; StartBackend(); });
            }
            catch (StarterError ex) { setupRunning = false; if (!quitting) Ui(delegate { ShowError(ex); }); }
            catch (Exception ex)
            {
                setupRunning = false;
                log.Line("unexpected: " + ex);
                if (!quitting) Ui(delegate { ShowError(new StarterError("Something went wrong that VGAC did not expect: " + ex.Message, "Open the log, then start VGAC again.")); });
            }
        }

        // A move is unfinished: nothing runs. The window says so and opens Maintenance on the move; only Continue and Abandon are there.
        void ShowMoveBlocked(string why)
        {
            DefaultButtons();
            title.Text = "A move of the big files is not finished";
            body.Text = why;
            bar.Visible = false;
            primary.Text = "Open the move"; secondary.Text = "Quit";
            primary.Visible = secondary.Visible = true; choose.Visible = false; openLog.Visible = false;
            onPrimary = delegate { OpenMaintenance(2); }; onSecondary = OnNotNow;
            BeginInvokeSafe(delegate { OpenMaintenance(2); });
        }

        void StartBackend()
        {
            if (quitting) return;
            string moveGate = MoveGate.Blocking(root);
            if (moveGate != null) { ShowMoveBlocked(moveGate); return; }     // setup that finishes while the person quits must not start a backend
            title.Text = "Starting VGAC";
            body.Text = "Starting the app's engine...";
            bar.Visible = false;
            if (backend == null) backend = new BackendRunner(lay, port);
            BackendRunner b = backend;
            engineBusy = true;
            var th = new Thread(delegate()
            {
                try
                {
                    if (quitting) return;
                    b.StartAndWait(120, log);
                    Ui(delegate { if (!quitting) Running(); });
                }
                catch (StarterError ex) { if (!quitting) Ui(delegate { ShowError(ex); }); }
                finally { engineBusy = false; }
            });
            th.IsBackground = true;
            th.Start();
        }

        void Running()
        {
            sup = new Supervisor(backend, desk, log);
            sup.StartTried = true;
            sup.Changed += OnState;
            sup.RestartBusy = delegate { return engineBusy || quitting || setupRunning || backend == null; };
            sup.RestartAsked += RestartOnRequest;
            sup.BackendCameUp += delegate { BeginInvokeSafe(delegate { if (appWindow != null) appWindow.Reload(); }); };
            sup.Start();
            ShowStatusView();
            if (pendingTab >= 0) { int t = pendingTab; pendingTab = -1; BeginInvokeSafe(delegate { OpenMaintenance(t); }); }
            if (!OpenInWindow()) { OpenApp(); }
            else if (!keepStatus) Hide();
        }

        // The app in its own window (WebView2); false when the files or Microsoft's runtime are missing, and the caller
        // uses the browser instead. Reached only through this wrapper so a missing DLL cannot stop the starter.
        bool OpenInWindow()
        {
            try
            {
                string bin = WebBin.Find(AppDomain.CurrentDomain.BaseDirectory);
                if (bin == null) { log.Line("no WebView2 files in starter\\bin; using the browser"); return false; }
                WebBin.Prepare(bin);
                return OpenWindowCore();
            }
            catch (Exception ex) { log.Line("in-app window not available: " + ex.Message + "; using the browser"); return false; }
        }

        [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
        bool OpenWindowCore()
        {
            if (!AppWindow.RuntimeInstalled())
            {
                log.Line("Microsoft's WebView2 runtime is not installed; using the browser");
                hint.Text = "The app opened in your browser because Microsoft's WebView2 runtime is not installed (microsoft.com, search 'WebView2 Runtime').";
                return false;
            }
            // the window's data goes in the folder for the big files only when that folder is VGAC's; otherwise in the project's own local folder
            bool dataOurs = DataFolders.Check(lay.DataFolder, machine, root).State == DataState.Ours;
            string profile = dataOurs ? Path.Combine(lay.DataFolder, "webview2") : Path.Combine(root, "local", "webview2-profile");
            var w = new AppWindow(backend.Url, profile, log, this);
            w.CloseRequested = delegate { BeginInvokeSafe(RequestQuit); };
            w.QuitRequested = delegate { BeginInvokeSafe(RequestQuit); };
            w.StatusRequested = delegate { BeginInvokeSafe(ShowStatusWindow); };
            w.FormClosed += delegate
            {
                bool problem = w.Problem != null;
                appWindow = null;
                if (problem && !quitting) { ShowStatusWindow(); OpenApp(); }
            };
            appWindow = w;
            w.Show();
            return true;
        }

        // ---- opening things --------------------------------------------------------------------------------------------

        void OpenApp()
        {
            try { Process.Start(backend.Url); } catch (Exception ex) { log.Line("could not open the browser: " + ex.Message); }
        }

        void OpenApp2()
        {
            if (appWindow != null) appWindow.Bring();
            else if (backend != null) OpenApp();
        }

        void ShowStatusWindow()
        {
            if (IsDisposed) return;
            if (statusMode) ShowStatusView();
            if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
            Show();
            Activate();
            Foreground.Front(Handle);
        }

        // A second start came in: the app window if there is one, else the status window.
        void ShowForeground()
        {
            try
            {
                if (quitting) return;
                bool toApp = LauncherRules.Target(appWindow != null) == FrontWindow.App;
                log.Line("second start: bringing the " + (toApp ? "app" : "status") + " window forward");
                if (toApp) appWindow.Bring(); else ShowStatusWindow();
            }
            finally { gate.Ack(); }
        }

        static void OpenPath(string p)
        {
            try { if (File.Exists(p)) Process.Start("notepad.exe", "\"" + p + "\""); } catch (Exception) { }
        }

        void OpenFolder(string p)
        {
            try { Directory.CreateDirectory(p); Process.Start("explorer.exe", "\"" + p + "\""); }
            catch (Exception e) { log.Line("could not open " + p + ": " + e.Message); }
        }

        // ---- the tray --------------------------------------------------------------------------------------------------

        void MakeTray()
        {
            tray = new NotifyIcon();
            tray.Icon = AppIcon.Get(16);
            tray.Text = "Video Game Asset Creator";
            var menu = new ContextMenuStrip();
            menu.Items.Add("Open Video Game Asset Creator", null, delegate { OpenApp2(); });
            menu.Items.Add("Status window", null, delegate { ShowStatusWindow(); });
            var svc = new ToolStripMenuItem("Services");
            svc.DropDownItems.Add("Restart backend", null, delegate { Act("restartBackend"); });
            svc.DropDownItems.Add("-");
            svc.DropDownItems.Add("Open logs folder", null, delegate { OpenFolder(Path.Combine(root, "runtime", "logs")); });
            svc.DropDownItems.Add("Open exports folder", null, delegate { OpenFolder(Path.Combine(root, "exports")); });
            svc.DropDownItems.Add("Open settings file", null, delegate { OpenSettingsFile(); });
            svc.DropDownItems.Add("-");
            svc.DropDownItems.Add("Check and repair...", null, delegate { OpenMaintenance(0); });
            svc.DropDownItems.Add("Update VGAC...", null, delegate { OpenMaintenance(1); });
            svc.DropDownItems.Add("Move the big files...", null, delegate { OpenMaintenance(2); });
            svc.DropDownItems.Add("Remove VGAC's downloads...", null, delegate { OpenMaintenance(3); });
            menu.Items.Add(svc);
            menu.Items.Add("-");
            menu.Items.Add("Quit", null, delegate { RequestQuit(); });
            tray.ContextMenuStrip = menu;
            tray.MouseClick += delegate(object s, MouseEventArgs e) { if (e.Button == MouseButtons.Left) { if (LauncherRules.Target(appWindow != null) == FrontWindow.App) appWindow.Bring(); else ShowStatusWindow(); } };
            tray.DoubleClick += delegate { OpenApp2(); };
            tray.BalloonTipClicked += delegate { if (appWindow != null) appWindow.Bring(); };
            tray.Visible = true;
        }

        void OpenSettingsFile()
        {
            try
            {
                if (machine == null) machine = MachineFile.Load(root);
                if (!File.Exists(machine.Path)) machine.Save();
                OpenPath(machine.Path);
            }
            catch (Exception e) { log.Line("could not open the settings file: " + e.Message); }
        }

        // ---- actions from the status rows and the tray -----------------------------------------------------------------

        void Act(string id)
        {
            log.Line("action: " + id);
            switch (id)
            {
                case "startA": Background(delegate { sup.StartViaBackend("a"); sup.TickNow(); }); break;
                case "restartBackend": case "startBackend": RestartBackend(); break;
                case "rebuild": Rebuild(); break;
                case "startB": Background(delegate { sup.StartViaBackend("b"); sup.TickNow(); }); break;
                case "startAnywayB": Background(delegate { sup.SetCard("b", true); sup.AllowAutoStartB(); sup.StartViaBackend("b"); sup.TickNow(); }); break;
                case "turnOnB": Background(delegate { sup.SetCard("b", true); sup.AllowAutoStartB(); sup.TickNow(); }); break;
            }
        }

        static void Background(Action a)
        {
            var t = new Thread(delegate() { try { a(); } catch (Exception) { } });
            t.IsBackground = true; t.Start();
        }

        // Restart (or start again) the backend. Asked first when it would lose something: a backend this launcher did not
        // start (only that one process is stopped, after checking again that it is still the same process), or work in
        // progress. Cancel is the default in both. Not while the screens are being built.
        bool RestartBackend()
        {
            string gateWhy = MoveGate.Blocking(root);
            if (gateWhy != null) { MessageBox.Show(gateWhy, "VGAC"); return false; }
            RestartStart gate0 = LauncherRules.Restart(engineBusy, backend != null, quitting, setupRunning);
            if (gate0 == RestartStart.BuildRunning)
            {
                MessageBox.Show("The screens are being built. Try again when that has finished.", "VGAC");
                return false;
            }
            if (gate0 != RestartStart.Go) return false;
            engineBusy = true;
            Background(delegate
            {
                try
                {
                    List<ProcInfo> foreign = backend.Foreign();
                    bool owned = backend.Owned;
                    QuitPlan plan = RestartPolicy.Decide(owned, owned && sup != null ? sup.Jobs : 0, foreign);
                    if (plan.Ask)
                    {
                        bool yes = false;
                        try { Invoke((Action)delegate { if (!quitting) yes = Ask.Show(this, plan.Message, plan.Detail, plan.OkLabel, plan.CancelLabel, plan.DefaultCancel); }); }
                        catch (Exception) { return; }
                        if (!yes) return;
                    }
                    if (quitting) return;
                    // only processes that are still, unchanged, what the question was about
                    List<ProcInfo> targets = foreign.Count > 0 ? Adoption.StillSame(foreign, backend.Procs.List()) : foreign;
                    log.Line("restart backend: own " + (owned ? backend.Pid.ToString() : "-") + ", other [" + string.Join(", ", targets.ConvertAll<string>(delegate(ProcInfo p) { return p.Pid.ToString(); }).ToArray()) + "]");
                    backend.StopIfOurs(log);
                    if (backend.Owned) throw new StarterError("The app's engine could not be stopped.", "Close it from Task Manager, then try again.");
                    foreach (ProcInfo p in targets) backend.Killer.Kill(p.Pid, p.CreatedTicks);
                    for (int i = 0; i < 30 && BackendRunner.HealthOk(port, 500); i++) Thread.Sleep(500);
                    backend.StartAndWait(120, log);
                    if (sup != null) sup.TickNow();
                }
                catch (StarterError e)
                {
                    log.Line("restart failed: " + e.Message);
                    if (!quitting) Ui(delegate { MessageBox.Show(e.Message + (e.Fix != null ? "\r\n\r\n" + e.Fix : ""), "VGAC"); });
                }
                finally { engineBusy = false; }
            });
            return true;
        }

        // The backend asked to be restarted (POST /api/restart). The launcher does it, so the new backend is the launcher's.
        // Work still running stops the restart unless the request says to go on. "keep" leaves the engines the app started
        // running (the new backend takes them over); "restart" stops everything below the backend and the new backend
        // starts the listed engines again. The request file is already deleted. A failed start is logged and shown in the
        // status rows; the tray's Restart backend still works.
        void RestartOnRequest(RestartRequest r)
        {
            if (quitting) return;
            if (r.Quit)
            {
                if (!r.Force && sup != null && sup.Jobs > 0) { log.Line("quit request " + r.Id + ": " + sup.Jobs + " job(s) running or waiting; not acting"); return; }
                log.Line("quit request " + r.Id + ": closing, as Quit does");
                BeginInvokeSafe(DoQuit);
                return;
            }
            if (backend == null) return;
            if (!backend.Owned) { log.Line("restart request " + r.Id + ": the running backend is not this launcher's; not acting"); return; }
            if (!r.Force && sup != null && sup.Jobs > 0) { log.Line("restart request " + r.Id + ": " + sup.Jobs + " job(s) running or waiting; not acting"); return; }
            engineBusy = true;
            Background(delegate
            {
                try
                {
                    log.Line("restart request " + r.Id + ": engines " + (r.KeepEngines ? "keep" : "restart") + (r.Force ? ", forced" : ""));
                    List<int> stopped = r.KeepEngines ? backend.StopKeepingEngines(r.KeepPids, log) : backend.StopIfOurs(log);
                    if (backend.Owned) throw new StarterError("The app's engine could not be stopped.", "Close it from Task Manager, then try again.");
                    log.Line("restart request " + r.Id + ": stopped [" + string.Join(", ", stopped.ConvertAll<string>(delegate(int p) { return p.ToString(); }).ToArray()) + "]");
                    for (int i = 0; i < 30 && BackendRunner.HealthOk(port, 500); i++) Thread.Sleep(500);
                    backend.NextEnv["VGAC_RESTART_ID"] = r.Id;
                    if (!r.KeepEngines) backend.NextEnv["VGAC_RESTART_ENGINES"] = string.Join(",", r.RestartEngines.ToArray());
                    backend.StartAndWait(120, log);
                    if (sup != null) sup.TickNow();
                }
                catch (StarterError e)
                {
                    log.Line("restart request " + r.Id + " failed: " + e.Message);
                    if (!quitting) Ui(delegate { MessageBox.Show(e.Message + (e.Fix != null ? "\r\n\r\n" + e.Fix : ""), "VGAC"); });
                }
                finally { backend.NextEnv = new Dictionary<string, string>(); engineBusy = false; }
            });
        }

        // "Build again": run the screens' build, then restart the backend so it serves the new build.
        void Rebuild()
        {
            if (setupRunning || sup == null || engineBusy || quitting || MoveGate.Blocking(root) != null) return;
            setupRunning = true;
            sup.Building = true; sup.BuildError = null;
            Background(delegate
            {
                try
                {
                    Probe probe = ProbeReader.Read(lay, host);
                    List<string> s = new List<string>();
                    if (probe.NodeFound == null) s.Add("node");
                    s.Add("screens");
                    var runner = new SetupRunner(lay, host, machine, Ledger.Load(root), log);
                    var prog = new Progress();
                    prog.Cancelled = delegate { return cancel; };
                    runner.Run(s, prog);
                    sup.Building = false;
                    setupRunning = false;
                    Ui(delegate { RestartBackend(); });
                }
                catch (StarterError e) { sup.Building = false; sup.BuildError = e.Message; setupRunning = false; log.Line("build failed: " + e.Message); }
                catch (Exception e) { sup.Building = false; sup.BuildError = "The build failed: " + e.Message; setupRunning = false; log.Line("build failed: " + e); }
                sup.TickNow();
            });
        }

        // ---- the page's bridge (IHostActions) ----------------------------------------------------------------------------

        public void SetWork(int count, byte[] png)
        {
            if (appWindow == null) return;
            Taskbar.SetOverlay(appWindow.Handle, count > 0 ? png : null, count + " running");
        }

        public bool Notify(string titleText, string bodyText)
        {
            if (tray == null || appWindow == null) { log.Line(LauncherRules.NotifyLine(titleText, false, "no tray icon or app window")); return false; }
            if (appWindow.InFront) { log.Line(LauncherRules.NotifyLine(titleText, false, "the app window is in front (the page shows its own message)")); return false; }
            tray.ShowBalloonTip(5000, string.IsNullOrEmpty(titleText) ? "VGAC" : titleText, string.IsNullOrEmpty(bodyText) ? " " : bodyText, ToolTipIcon.None);
            log.Line(LauncherRules.NotifyLine(titleText, true, ""));
            return true;
        }

        public bool StartEngine()
        {
            if (engineBusy || backend == null) return false;
            log.Line("action: startEngine (from the app window)");
            return RestartBackend();
        }

        // ---- maintenance (IMaintenanceHost) ------------------------------------------------------------------------------

        MaintenanceForm maintenance;
        readonly IComfyInstall comfyInstall;

        // Built from the settings file at each use: a layout loaded earlier would still name the place before a move.
        public Layout Lay { get { return Vgac.Starter.Layout.From(root, MachineFile.Load(root)); } }
        public MachineFile Machine { get { return machine; } }
        public IHost Host { get { return host; } }
        public SetupLog Log { get { return log; } }
        public string ExePath { get { return Application.ExecutablePath; } }
        public IComfyInstall Comfy { get { return comfyInstall; } }
        public IProcessSource Procs { get { return backend != null ? backend.Procs : new WmiProcesses(); } }

        void OpenMaintenance(int tab)
        {
            if (lay == null || quitting) return;
            if (maintenance == null || maintenance.IsDisposed)
            {
                maintenance = new MaintenanceForm(this);
                maintenance.FormClosed += delegate { maintenance = null; };
            }
            maintenance.ShowTab(tab);
            maintenance.Show();
            if (maintenance.WindowState == FormWindowState.Minimized) maintenance.WindowState = FormWindowState.Normal;
            maintenance.Activate();
        }

        // Before maintenance that needs the engine stopped: asked like Quit (with the job count), refused when the engine
        // is not ours, and the engine this launcher started is stopped. No restart while maintenance runs.
        public bool Quiesce(IWin32Window owner, string what)
        {
            if (backend == null) return true;
            bool owns = backend.Owned;
            bool foreign = !owns && BackendRunner.HealthOk(port, 800);
            QuitPlan plan = UpdatePolicy.Decide(owns, owns && sup != null ? sup.Jobs : 0, foreign);
            if (plan.Block) { MessageBox.Show(owner, plan.Message + "\r\n\r\n" + plan.Detail, "VGAC"); return false; }
            if (plan.Ask && !Ask.Show(owner, "Stop the app's engine for this " + what + "?", plan.Detail, "Stop it and go on", "Cancel", plan.DefaultCancel)) return false;
            engineBusy = true;
            if (owns)
            {
                backend.StopIfOurs(log);
                if (backend.Owned) { engineBusy = false; MessageBox.Show(owner, "The app's engine could not be stopped. Close it from Task Manager, then try again.", "VGAC"); return false; }
            }
            return true;
        }

        public void Resume()
        {
            engineBusy = false;
            if (quitting || backend == null || MoveGate.Blocking(root) != null) return;
            BeginInvokeSafe(delegate { RestartBackend(); });
        }

        public void RestartVgac()
        {
            try { Process.Start(Application.ExecutablePath, "--project \"" + root + "\" --port " + port + " --after " + Process.GetCurrentProcess().Id); }
            catch (Exception e) { log.Line("could not restart VGAC: " + e.Message); MessageBox.Show("VGAC could not start itself again (" + e.Message + "). Open VGAC.exe yourself.", "VGAC"); }
            DoQuit();
        }

        // The app's own ComfyUI is moved and rebuilt by the backend's own code (python -m vgac.comfy_install), run with the app's Python.
        // Runs one of the backend's ComfyUI commands (the backend itself need not be running) and returns its one json line, or null.
        string RunComfyCli(string args, int timeoutMs, Func<bool> cancelled)
        {
            return ComfyCli.Run(host, lay, root, args, timeoutMs, cancelled);
        }

        // ---- quit ------------------------------------------------------------------------------------------------------

        void OnFormClosing(object sender, FormClosingEventArgs e)
        {
            bool ending = e.CloseReason == CloseReason.WindowsShutDown || e.CloseReason == CloseReason.TaskManagerClosing;
            CloseOutcome o = LauncherRules.StatusWindowClosing(quitting, ending, appWindow != null);
            if (o == CloseOutcome.Allow) return;
            e.Cancel = true;
            if (o == CloseOutcome.AskQuit) BeginInvokeSafe(RequestQuit); else Hide();
        }

        // Asks when something would be lost, then stops what this launcher started and exits.
        void RequestQuit()
        {
            if (quitting) return;
            var q = new QuitInputs();
            q.OwnsBackend = backend != null && backend.Owned;
            q.SetupRunning = setupRunning;
            q.Jobs = sup != null ? sup.Jobs : 0;
            QuitPlan plan = QuitPolicy.Decide(q);
            if (plan.Ask && !Ask.Show(Visible ? (IWin32Window)this : null, plan.Message, plan.Detail, plan.OkLabel, plan.CancelLabel, plan.DefaultCancel)) return;
            DoQuit();
        }

        void DoQuit()
        {
            if (quitting) return;
            quitting = true;
            cancel = true;
            Teardown();
            if (appWindow != null) { appWindow.Quitting = true; try { appWindow.Close(); } catch (Exception) { } }
            // Posted, never called from inside a window's own close event.
            BeginInvokeSafe(delegate { Application.Exit(); });
        }

        bool tornDown;

        // Stops polling, stops the setup program if one runs, stops the backend this launcher owns (and what it started),
        // removes the tray icon. A ComfyUI that someone else started is not touched.
        void Teardown()
        {
            if (tornDown) return;
            tornDown = true;
            quitting = true;
            if (sup != null) sup.Stop();
            host.AbortCurrent();
            if (backend != null)
            {
                // On its own thread, with a limit: the process list (WMI) must not be able to hang the window at quit.
                bool had = backend.Owned;
                int pid = backend.Pid;
                List<int> stopped = new List<int>();
                var th = new Thread(delegate() { try { stopped = backend.Shutdown(log); } catch (Exception e) { log.Line("stopping the backend failed: " + e.Message); } });
                th.IsBackground = true; th.Start();
                if (!th.Join(15000)) log.Line("stopping the backend took too long; quitting anyway");
                log.Line(LauncherRules.QuitLine(had, pid, stopped));
            }
            if (tray != null) { tray.Visible = false; tray.Dispose(); tray = null; }
            log.Line("quit");
        }

        // ---- keys ------------------------------------------------------------------------------------------------------

        protected override bool ProcessCmdKey(ref Message msg, Keys keyData)
        {
            if (Shortcuts.Handle(keyData, "status window", log, RequestQuit, ShowStatusWindow, null)) return true;
            return base.ProcessCmdKey(ref msg, keyData);
        }

        bool afterChooseBegins;

        void DefaultButtons()
        {
            primary.SetBounds(16, 250, 130, 32); secondary.SetBounds(156, 250, 110, 32); choose.SetBounds(276, 250, 220, 32); openLog.SetBounds(506, 250, 98, 32);
        }

        // The folder for the big files is not shown to be VGAC's: offer what the person can do from here. Nothing is claimed silently.
        void ShowNotClaimed(NotClaimedError e)
        {
            log.Line("error: " + e.Message + (e.Fix != null ? " | " + e.Fix : ""));
            DefaultButtons();
            title.Text = "This folder is not VGAC's yet";
            var sb = new StringBuilder();
            sb.AppendLine(e.Message);
            bool adopt = e.Status.Adoptable && !e.Status.Problem;
            if (adopt)
            {
                sb.AppendLine();
                sb.AppendLine("If you let VGAC use this folder, VGAC will treat ONLY these as its own, and Remove and Move will act on them:");
                foreach (string x in e.Status.AdoptList) sb.AppendLine("   - " + x);
            }
            body.Text = sb.ToString();
            bar.Visible = false;
            primary.Text = "Use this folder for VGAC only"; primary.SetBounds(16, 250, 210, 32); primary.Visible = adopt;
            onPrimary = delegate
            {
                try { DataFolders.Adopt(lay.DataFolder, machine, root); log.Line("the folder for the big files was adopted as VGAC's: " + lay.DataFolder); machine = MachineFile.Load(root); Begin(); }
                catch (StarterError ex) { ShowError(ex); }
            };
            choose.Text = "Choose another folder..."; choose.SetBounds(adopt ? 236 : 16, 250, 190, 32); choose.Visible = true;
            onChoose = delegate { afterChooseBegins = true; OnChoose(); };
            string folder = e.Folder;
            secondary.Text = "Open the folder"; secondary.SetBounds(adopt ? 436 : 216, 250, 130, 32); secondary.Visible = Directory.Exists(folder);
            onSecondary = delegate { try { Process.Start("explorer.exe", "\"" + folder + "\""); } catch (Exception) { } };
            openLog.Visible = false;
        }

        void ShowError(StarterError e)
        {
            var nc = e as NotClaimedError;
            if (nc != null) { ShowNotClaimed(nc); return; }
            DefaultButtons();
            log.Line("error: " + e.Message + (e.Fix != null ? " | " + e.Fix : ""));
            title.Text = "VGAC could not start";
            body.Text = e.Message + (e.Fix != null ? "\r\n\r\nWhat to do: " + e.Fix : "");
            bar.Visible = false;
            primary.Visible = false; choose.Visible = LauncherRules.ErrorOffersChoose(e);
            if (choose.Visible) { choose.Text = "Choose another folder"; onChoose = delegate { afterChooseBegins = true; OnChoose(); }; }
            secondary.Text = "Close"; secondary.Visible = true; openLog.Visible = true;
            onSecondary = RequestQuit;
        }
    }
}
