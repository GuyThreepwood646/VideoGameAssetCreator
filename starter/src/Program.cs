// VGAC.exe: the first window. Does only what must exist before the backend can answer (a private Python with the
// app's packages, the built screens, a settings file), starts the backend and opens the app. Everything else
// (ComfyUI, Blender, models) is the app's own setup card.
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
[assembly: System.Reflection.AssemblyVersion("0.1.0.0")]

namespace Vgac.Starter
{
    public static class Program
    {
        public const int DefaultPort = 8765;

        public static string FindProjectRoot(string start) { return ProjectFinder.Find(start); }

        [STAThread]
        public static int Main(string[] args)
        {
            string project = null;
            string planOut = null;
            int port = DefaultPort;
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--project" && i + 1 < args.Length) project = args[++i];
                else if (args[i] == "--plan" && i + 1 < args.Length) planOut = args[++i];
                else if (args[i] == "--port" && i + 1 < args.Length) int.TryParse(args[++i], out port);
            }
            string root = FindProjectRoot(project ?? AppDomain.CurrentDomain.BaseDirectory);
            if (planOut != null) return PlanOnly(root, planOut);

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            if (root == null)
            {
                MessageBox.Show("VGAC.exe has to stay inside the VGAC project folder (the one with the backend and frontend folders in it).\r\n\r\nMove it back, or clone the project again.",
                    "VGAC", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return 2;
            }
            bool created;
            using (var mutex = new Mutex(true, "Local\\VGAC-starter-" + Hashing.Sha256Text(root.ToLowerInvariant()).Substring(0, 16), out created))
            {
                if (!created)
                {
                    MessageBox.Show("VGAC is already starting or running.", "VGAC", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    return 0;
                }
                Application.Run(new MainForm(root, port));
            }
            return 0;
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

    public class MainForm : Form
    {
        readonly string root;
        readonly int port;
        readonly RealHost host = new RealHost();
        readonly SetupLog log;
        MachineFile machine;
        Vgac.Starter.Layout lay;
        List<string> steps;
        BackendRunner backend;
        volatile bool cancel;
        Action onPrimary = delegate { }, onSecondary = delegate { }, onChoose = delegate { };

        readonly Label title = new Label();
        readonly Label body = new Label();
        readonly ProgressBar bar = new ProgressBar();
        readonly Button primary = new Button();
        readonly Button choose = new Button();
        readonly Button secondary = new Button();
        readonly Button openLog = new Button();

        public MainForm(string projectRoot, int port)
        {
            root = projectRoot; this.port = port;
            log = new SetupLog(root);
            Text = "VGAC";
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
            Controls.AddRange(new Control[] { title, body, bar, primary, secondary, choose, openLog });
            primary.Click += delegate { onPrimary(); };
            secondary.Click += delegate { onSecondary(); };
            choose.Click += delegate { onChoose(); };
            openLog.Click += delegate { OpenPath(log.Path); };
            Shown += delegate { Begin(); };
            FormClosing += delegate { cancel = true; if (backend != null) backend.StopIfOurs(); };
        }

        static void OpenPath(string p)
        {
            try { if (File.Exists(p)) Process.Start("notepad.exe", "\"" + p + "\""); } catch (Exception) { }
        }

        void Begin()
        {
            try
            {
                machine = MachineFile.Load(root);
                lay = Vgac.Starter.Layout.From(root, machine);
                Probe probe = ProbeReader.Read(lay, host);
                steps = Planner.Plan(probe, root);
            }
            catch (StarterError e) { ShowError(e); return; }
            if (steps.Count == 0) { StartBackend(); return; }
            ShowWelcome();
        }

        void ShowWelcome()
        {
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

        void OnNotNow() { Close(); }

        void OnChoose()
        {
            using (var d = new FolderBrowserDialog())
            {
                d.Description = "Choose a folder for the big files (Python, tools; later the models). Pick a drive with room.";
                if (d.ShowDialog(this) != DialogResult.OK) return;
                try
                {
                    machine.Set("starter", "dataFolder", d.SelectedPath);
                    machine.Save();
                    lay = Vgac.Starter.Layout.From(root, machine);
                    body.Text = "The big files will go to: " + lay.DataFolder + "\r\n\r\nPress Start.";
                }
                catch (Exception ex) { MessageBox.Show(ex.Message, "VGAC"); }
            }
        }

        void OnStart()
        {
            primary.Visible = secondary.Visible = choose.Visible = false;
            bar.Visible = true; title.Text = "Setting up VGAC";
            var th = new Thread(RunSetup);
            th.IsBackground = true;
            th.Start();
        }

        void Ui(Action a)
        {
            if (IsDisposed) return;
            try { BeginInvoke(a); } catch (InvalidOperationException) { }
        }

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
                Ui(delegate { bar.Visible = false; StartBackend(); });
            }
            catch (StarterError ex) { Ui(delegate { ShowError(ex); }); }
            catch (Exception ex)
            {
                log.Line("unexpected: " + ex);
                Ui(delegate { ShowError(new StarterError("Something went wrong that VGAC did not expect: " + ex.Message, "Open the log, then start VGAC again.")); });
            }
        }

        void StartBackend()
        {
            title.Text = "Starting VGAC";
            body.Text = "Starting the app's engine...";
            bar.Visible = false;
            var th = new Thread(delegate()
            {
                try
                {
                    backend = new BackendRunner(lay, port);
                    backend.StartAndWait(120, log);
                    Ui(delegate { Running(); });
                }
                catch (StarterError ex) { Ui(delegate { ShowError(ex); }); }
            });
            th.IsBackground = true;
            th.Start();
        }

        void Running()
        {
            title.Text = "VGAC is running";
            body.Text = "The app is open in your web browser at " + backend.Url + ".\r\n\r\nKeep this window open while you work. Closing it stops the app's engine if this window started it.";
            primary.Text = "Open the app"; secondary.Text = "Quit";
            primary.Visible = secondary.Visible = openLog.Visible = true; choose.Visible = false;
            onPrimary = OpenApp; onSecondary = Close;
            OpenApp();
        }

        void OpenApp()
        {
            try { Process.Start(backend.Url); } catch (Exception ex) { log.Line("could not open the browser: " + ex.Message); }
        }

        void ShowError(StarterError e)
        {
            log.Line("error: " + e.Message + (e.Fix != null ? " | " + e.Fix : ""));
            title.Text = "VGAC could not start";
            body.Text = e.Message + (e.Fix != null ? "\r\n\r\nWhat to do: " + e.Fix : "");
            bar.Visible = false;
            primary.Visible = false; choose.Visible = false;
            secondary.Text = "Close"; secondary.Visible = true; openLog.Visible = true;
            onSecondary = Close;
        }
    }
}
