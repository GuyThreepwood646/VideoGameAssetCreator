// The Maintenance window: Check and repair, Update, Move the big files, Remove. The decisions and the file work are in
// Repair.cs, Update.cs, Move.cs and Remove.cs (all tested); this file only shows them and asks. C# 5 (see Core.cs).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace Vgac.Starter
{
    // What the window needs from the launcher.
    // Every question that could lead to deleting, moving or claiming something answers No when the person just presses Enter.
    public static class MaintenanceDefaults
    {
        public const MessageBoxDefaultButton Adopt = MessageBoxDefaultButton.Button2;
        public const MessageBoxDefaultButton FinishAnyway = MessageBoxDefaultButton.Button2;
        public const MessageBoxDefaultButton AbandonMove = MessageBoxDefaultButton.Button2;
        public const MessageBoxDefaultButton RestartAfterMove = MessageBoxDefaultButton.Button2;
        public const MessageBoxDefaultButton PermanentDelete = MessageBoxDefaultButton.Button2;
        public const MessageBoxDefaultButton RunSteps = MessageBoxDefaultButton.Button2;
    }

    public interface IMaintenanceHost
    {
        Layout Lay { get; }
        MachineFile Machine { get; }
        IHost Host { get; }
        SetupLog Log { get; }
        string ExePath { get; }
        IComfyInstall Comfy { get; }
        IProcessSource Procs { get; }
        // Asks (like Quit does) and stops the backend this launcher started. False: the person said no, or it is not ours to stop.
        bool Quiesce(IWin32Window owner, string what);
        void Resume();          // starts the backend again
        void RestartVgac();     // starts a fresh VGAC.exe and quits this one
    }

    public class MaintenanceForm : Form
    {
        readonly IMaintenanceHost host;
        readonly TabControl tabs = new TabControl();
        readonly Label status = new Label();
        readonly Button stopBtn = new Button();
        volatile bool busy;
        volatile bool stopRequested;

        public MaintenanceForm(IMaintenanceHost host)
        {
            this.host = host;
            Text = "VGAC maintenance";
            Icon = AppIcon.Get(32);
            ClientSize = new Size(760, 560);
            StartPosition = FormStartPosition.CenterScreen;
            tabs.SetBounds(8, 8, 744, 500); tabs.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
            status.SetBounds(12, 516, 640, 36); status.Anchor = AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
            stopBtn.Text = "Stop"; stopBtn.SetBounds(662, 514, 90, 30); stopBtn.Anchor = AnchorStyles.Bottom | AnchorStyles.Right; stopBtn.Visible = false;
            stopBtn.Click += delegate { stopRequested = true; Say("Stopping..."); };
            Controls.Add(tabs); Controls.Add(status); Controls.Add(stopBtn);
            tabs.TabPages.Add(RepairTab());
            tabs.TabPages.Add(UpdateTab());
            tabs.TabPages.Add(MoveTab());
            tabs.TabPages.Add(RemoveTab());
            Shown += delegate { if (moveDetails != null) RefreshMove(moveDetails); };
            Activated += delegate { if (!busy && moveDetails != null) ApplyMoveLock(); };
            ApplyMoveLock();
            FormClosing += delegate(object s, FormClosingEventArgs e) { if (busy) { e.Cancel = true; MessageBox.Show(this, "Something is still running. Wait for it to finish.", "VGAC"); } };
        }

        public void ShowTab(int i) { if (i >= 0 && i < tabs.TabPages.Count) tabs.SelectedIndex = i; }

        void Say(string s) { if (IsHandleCreated && !IsDisposed) try { BeginInvoke((Action)delegate { status.Text = s; }); } catch (Exception) { } }

        // Runs `work` on a thread with the window locked, then `done` on the window's thread.
        void Run(Action work, Action done)
        {
            if (busy) { Say("Something is still running. Wait for it, or press Stop."); return; }
            busy = true; UseWaitCursor = true; stopRequested = false; stopBtn.Visible = true;
            host.Comfy.Cancelled = delegate { return stopRequested; };     // the Stop button also stops VGAC's own ComfyUI installer
            host.Comfy.Say = delegate(string s) { Say(s); };
            var t = new Thread(delegate()
            {
                try { work(); }
                catch (StarterError e) { Say(e.Message + (e.Fix != null ? " " + e.Fix : "")); host.Log.Line("maintenance: " + e.Message); }
                catch (Exception e) { Say("Something went wrong: " + e.Message); host.Log.Line("maintenance: " + e); }
                finally
                {
                    busy = false;
                    try { BeginInvoke((Action)delegate { UseWaitCursor = false; stopBtn.Visible = false; if (done != null) done(); }); } catch (Exception) { }
                }
            });
            t.IsBackground = true; t.Start();
        }

        static Button Btn(string text, int x, int y, int w) { var b = new Button(); b.Text = text; b.SetBounds(x, y, w, 30); return b; }
        static Label Info(string text, int x, int y, int w, int h) { var l = new Label(); l.Text = text; l.SetBounds(x, y, w, h); return l; }

        Progress ProgressToStatus()
        {
            var p = new Progress();
            p.Say = delegate(string t) { Say(t); };
            p.Cancelled = delegate { return stopRequested; };
            return p;
        }

        // ---- check and repair ------------------------------------------------------------------------------------------

        ListView repairList;
        List<CheckLine> lines = new List<CheckLine>();

        TabPage RepairTab()
        {
            var tab = new TabPage("Check and repair");
            tab.Controls.Add(Info("Checks what VGAC itself installed and fixes only that. Tools you linked (Blender, ComfyUI, Unity) are never changed here.", 12, 10, 700, 36));
            repairList = new ListView(); repairList.SetBounds(12, 52, 710, 340); repairList.View = View.Details; repairList.FullRowSelect = true; repairList.MultiSelect = false;
            repairList.Columns.Add("", 90); repairList.Columns.Add("What was found", 600);
            var check = Btn("Check again", 12, 402, 120); var fix = Btn("Fix the selected", 142, 402, 140); var fixAll = Btn("Fix everything", 292, 402, 140);
            var logb = Btn("Open the log", 442, 402, 120);
            check.Click += delegate { DoCheck(); };
            fix.Click += delegate { if (repairList.SelectedIndices.Count == 1) DoFix(new List<CheckLine> { lines[repairList.SelectedIndices[0]] }); };
            fixAll.Click += delegate { DoFix(lines.Where(c => c.Status == "fix").ToList()); };
            logb.Click += delegate { try { Process.Start("notepad.exe", "\"" + host.Log.Path + "\""); } catch (Exception) { } };
            tab.Controls.AddRange(new Control[] { repairList, check, fix, fixAll, logb });
            tab.Enter += delegate { if (lines.Count == 0) DoCheck(); };
            return tab;
        }

        void DoCheck()
        {
            Run(delegate
            {
                Say("Checking...");
                var found = Repair.Check(host.Lay, host.Host, Ledger.Load(host.Lay.ProjectRoot));
                if (ComfyGuard.MarkerPresent(host.Comfy.Root(host.Lay.DataFolder)))
                {
                    ComfyState cs = host.Comfy.State(host.Lay.DataFolder);
                    if (cs.Problem != null) found.Add(new CheckLine("comfy", "info", "VGAC's own ComfyUI: its installer did not confirm it (" + cs.Problem + "). VGAC leaves it alone.", null));
                    else if (cs.VenvStale) found.Add(new CheckLine("comfy", "fix", "VGAC's own ComfyUI's Python still points at a place that is not there any more (the folder was renamed or its drive letter changed), so it will not start. It can be made again: this needs the internet and takes several minutes.", "comfyvenv"));
                    else found.Add(new CheckLine("comfy", "ok", "VGAC's own ComfyUI is confirmed by its installer.", null));
                }
                BeginInvoke((Action)delegate
                {
                    lines = found; repairList.Items.Clear();
                    foreach (CheckLine c in found)
                    {
                        string word = c.Status == "ok" ? "OK" : (c.Status == "fix" ? "Can fix" : (c.Status == "notmine" ? "Not mine" : "Note"));
                        var it = new ListViewItem(word); it.SubItems.Add(c.Text); repairList.Items.Add(it);
                    }
                    int n = found.Count(c => c.Status == "fix");
                    status.Text = n == 0 ? "Nothing needs fixing." : n + " thing(s) can be fixed.";
                });
            }, null);
        }

        void DoFix(List<CheckLine> todo)
        {
            if (todo.Count == 0) { status.Text = "Nothing to fix there."; return; }
            // Adopting a folder is never done silently, not even by "Fix everything": the person sees exactly what will be treated as VGAC's.
            if (todo.Any(c => c.FixId == "adopt"))
            {
                DataStatus ds = DataFolders.Check(host.Lay.DataFolder, MachineFile.Load(host.Lay.ProjectRoot), host.Lay.ProjectRoot);
                string list = ds.Adoptable ? string.Join("\r\n   - ", ds.AdoptList.ToArray()) : "(nothing: it can no longer be adopted)";
                bool yes = ds.Adoptable && MessageBox.Show(this, "Use " + host.Lay.DataFolder + " for VGAC only?\r\n\r\nVGAC will treat ONLY these as its own, and Remove and Move will act on them (they are removed or moved whole):\r\n   - " + list + "\r\n\r\nNothing else in the folder is touched.", "VGAC", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MaintenanceDefaults.Adopt) == DialogResult.Yes;
                if (!yes) { todo = todo.Where(c => c.FixId != "adopt").ToList(); if (todo.Count == 0) { status.Text = "The folder was not adopted."; return; } }
            }
            if (todo.Any(c => c.FixId == "adoptlib"))
            {
                string libp = RemovalPlanner.LibraryFolder(host.Lay, MachineFile.Load(host.Lay.ProjectRoot));
                bool yes = MessageBox.Show(this, "Mark " + libp + " as VGAC's library?\r\n\r\nIt holds only what the app makes. After this, Remove can delete its contents (only after you type the word REMOVE there). Nothing is deleted now.", "VGAC", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MaintenanceDefaults.Adopt) == DialogResult.Yes;
                if (!yes) { todo = todo.Where(c => c.FixId != "adoptlib").ToList(); if (todo.Count == 0) { status.Text = "The library was not marked."; return; } }
            }
            bool needsStop = todo.Any(c => c.FixId == "comfyvenv" || c.FixId == "venv" || c.FixId == "packages" || c.FixId == "uv" || c.FixId == "screens");
            if (needsStop && busy) { Say("Something is still running. Wait for it, or press Stop."); return; }
            if (needsStop && !host.Quiesce(this, "fix")) return;
            Run(delegate
            {
                foreach (CheckLine c in todo)
                {
                    Say("Fixing: " + c.Text);
                    string why = c.FixId == "comfyvenv" ? host.Comfy.RebuildVenv(host.Lay.DataFolder) : Repair.Fix(c.FixId, host.Lay, host.Host, MachineFile.Load(host.Lay.ProjectRoot), Ledger.Load(host.Lay.ProjectRoot), host.Log, ProgressToStatus());
                    if (why != null) { Say("Could not fix it: " + why); return; }
                }
                Say("Done. Starting the app's engine again...");
            }, delegate { host.Resume(); DoCheck(); });
        }

        // ---- update ---------------------------------------------------------------------------------------------------------

        TabPage UpdateTab()
        {
            var tab = new TabPage("Update");
            tab.Controls.Add(Info("Update VGAC gets the latest version with git (a fast-forward only: nothing of yours is merged or overwritten). " +
                "VGAC.exe steps aside while it runs so git can put the new one in place; restart VGAC afterwards to use it.\r\n\r\n" +
                "If you update by hand in a terminal, close VGAC first. A pull that stopped halfway is noticed at the next start.", 12, 10, 700, 110));
            var upd = Btn("Update VGAC now", 12, 130, 180);
            var apply = Btn("Apply changes already on disk", 202, 130, 220);
            var info = Info("", 12, 175, 700, 250);
            upd.Click += delegate { DoUpdate(info); };
            apply.Click += delegate { DoApply(info); };
            tab.Controls.AddRange(new Control[] { upd, apply, info });
            tab.Enter += delegate
            {
                var steps = Planner.Plan(ProbeReader.Read(host.Lay, host.Host), host.Lay.ProjectRoot);
                info.Text = steps.Count == 0 ? "The app's Python packages and screens match the files on disk." :
                    "Changes on disk are waiting to be applied:\r\n   - " + string.Join("\r\n   - ", steps.Select(s => Planner.Plain(s)).ToArray());
            };
            return tab;
        }

        void DoApply(Label info)
        {
            var steps = Planner.Plan(ProbeReader.Read(host.Lay, host.Host), host.Lay.ProjectRoot);
            if (steps.Count == 0) { info.Text = "Nothing to apply."; return; }
            if (busy) { Say("Something is still running. Wait for it, or press Stop."); return; }
            if (!host.Quiesce(this, "update")) return;
            Run(delegate
            {
                var runner = new SetupRunner(host.Lay, host.Host, MachineFile.Load(host.Lay.ProjectRoot), Ledger.Load(host.Lay.ProjectRoot), host.Log);
                runner.Run(steps, ProgressToStatus());
                Say("Updated. Starting the app's engine again...");
            }, delegate { host.Resume(); });
        }

        void DoUpdate(Label info)
        {
            string root = host.Lay.ProjectRoot;
            if (GitUpdate.MidPull(root) != null) { info.Text = GitUpdate.MidPullText; return; }
            if (busy) { Say("Something is still running. Wait for it, or press Stop."); return; }
            if (!host.Quiesce(this, "update")) return;
            UpdateResult result = null;
            List<string> pending = new List<string>();
            Run(delegate
            {
                Say("Updating...");
                result = GitUpdate.Pull(root, host.ExePath, new RealGit());
                // New files can mean new Python packages or screens. They are not installed in the same click: the person is
                // shown what would run (pip and npm scripts come from the files just pulled) and decides.
                if (result.Updated) pending = Planner.Plan(ProbeReader.Read(host.Lay, host.Host), root);
                Say(result.Message);
            }, delegate
            {
                info.Text = result == null ? "" : result.Message;
                if (result != null && pending.Count > 0)
                {
                    string list = string.Join("\r\n   - ", pending.Select(s => Planner.Plain(s)).ToArray());
                    if (MessageBox.Show(this, "The update needs these steps, which install packages and build the screens from the files just pulled:\r\n   - " + list + "\r\n\r\nRun them now?", "VGAC", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MaintenanceDefaults.RunSteps) == DialogResult.Yes)
                    {
                        List<string> steps = pending;
                        Run(delegate
                        {
                            var runner = new SetupRunner(host.Lay, host.Host, MachineFile.Load(root), Ledger.Load(root), host.Log);
                            runner.Run(steps, ProgressToStatus());
                            Say("Updated.");
                        }, delegate { AfterUpdate(result); });
                        return;
                    }
                    info.Text += "\r\n\r\nThe steps above were not run. Use 'Apply changes already on disk' when you are ready.";
                }
                AfterUpdate(result);
            });
        }

        void AfterUpdate(UpdateResult result)
        {
            if (result != null && result.ExeChanged && MessageBox.Show(this, result.Message + "\r\n\r\nRestart VGAC now?", "VGAC", MessageBoxButtons.YesNo) == DialogResult.Yes) { host.RestartVgac(); return; }
            host.Resume();
        }

        // ---- move ------------------------------------------------------------------------------------------------------------
        // Copy, check, switch, restart. Once the settings are switched the window offers nothing but Restart: everything here reads the
        // settings file afresh (host.Lay is built from it at each use), and nothing is built from a layout loaded before the move.

        TextBox moveTo;
        Label moveInfo;
        Button moveGo, moveCont, moveAbandon, moveRestart, moveChoose, moveForce;
        Label moveDetails;

        TabPage MoveTab()
        {
            var tab = new TabPage("Move the big files");
            moveInfo = Info("", 12, 10, 700, 110);
            moveTo = new TextBox(); moveTo.SetBounds(12, 130, 560, 24);
            moveChoose = Btn("Choose...", 582, 127, 100);
            moveGo = Btn("Copy, check, then switch", 12, 170, 220);
            moveCont = Btn("Continue an interrupted move", 242, 170, 220);
            moveAbandon = Btn("Abandon it", 472, 170, 100);
            moveForce = Btn("Finish anyway (without ComfyUI)", 242, 210, 260); moveForce.Visible = false;
            moveRestart = Btn("Restart VGAC now", 12, 170, 220); moveRestart.Visible = false;
            var details = Info("", 12, 255, 700, 220); moveDetails = details;
            moveChoose.Click += delegate { using (var d = new FolderBrowserDialog()) { d.Description = "Choose an empty folder for the big files."; if (d.ShowDialog(this) == DialogResult.OK) moveTo.Text = d.SelectedPath; } };
            moveGo.Click += delegate { DoMoveStart(details); };
            moveCont.Click += delegate { DoMoveContinue(details); };
            moveAbandon.Click += delegate { DoMoveAbandon(details); };
            moveRestart.Click += delegate { host.RestartVgac(); };
            moveForce.Click += delegate { DoMoveForce(details); };
            tab.Controls.AddRange(new Control[] { moveInfo, moveTo, moveChoose, moveGo, moveCont, moveAbandon, moveForce, moveRestart, details });
            tab.Enter += delegate { RefreshMove(details); };
            return tab;
        }

        MoveRunner Mover()
        {
            return new MoveRunner(host.Lay, MachineFile.Load(host.Lay.ProjectRoot), Ledger.Load(host.Lay.ProjectRoot), host.Comfy, host.Procs, host.Log);
        }

        // The same rule as in the code that changes things (MoveGate): while a move is unfinished, or has switched and VGAC is not restarted,
        // only the Move tab works, and on it only what fits (Continue, Finish anyway, Abandon, or Restart). Applied when the window is made,
        // every time it is shown again, and after every step: reopening Maintenance from the tray never gives working tabs.
        public bool Locked { get { return MoveGate.Blocking(host.Lay.ProjectRoot) != null; } }

        void ApplyMoveLock()
        {
            string root = host.Lay.ProjectRoot;
            bool pending = MoveRules.RestartPending(root);
            MoveJournal j = pending ? null : MoveRunner.Load(root, host.Lay.DataFolder);
            bool locked = pending || j != null;
            for (int i = 0; i < tabs.TabPages.Count; i++) tabs.TabPages[i].Enabled = !locked || tabs.TabPages[i].Text == "Move the big files";
            if (locked) tabs.SelectedIndex = 2;
            moveGo.Visible = moveTo.Visible = moveChoose.Visible = !locked;
            moveCont.Visible = j != null;
            moveAbandon.Visible = j != null && j.State == "copying";
            moveForce.Visible = j != null && j.State == "switching";
            moveRestart.Visible = pending;
        }

        void RefreshMove(Label details)
        {
            string root = host.Lay.ProjectRoot;
            ApplyMoveLock();
            if (MoveRules.RestartPending(root))
            {
                string to = "the new folder", old = null;
                try { var d = Json.ParseObject(File.ReadAllText(MoveRules.RestartPath(root), Encoding.UTF8)); to = Convert.ToString(d["moved_to"]); old = Convert.ToString(d["old_folder"]); } catch (Exception) { }
                moveInfo.Text = "The big files are now in " + to + ". VGAC has to be restarted to use them: close this window and press the button below.";
                details.Text = old == null ? "" : MoveView.OldFolderSentence(old);
                return;
            }
            var j = MoveRunner.Load(root, host.Lay.DataFolder);
            if (j == null)
                moveInfo.Text = "The big files (Python, tools, VGAC's own ComfyUI) are in:\r\n   " + host.Lay.DataFolder + "\r\n\r\nThey are copied to the new folder and checked; only then does VGAC switch to it, and it has to be restarted. The old folder is left as it is. Until the switch the old place is untouched and in use.";
            else if (j.State == "switching")
                moveInfo.Text = "A move to " + j.To + " is part way through switching. Nothing else runs until it is finished. Press Continue to finish it (everything is checked again first). If VGAC's own ComfyUI keeps refusing, 'Finish anyway' ends the move WITHOUT moving ComfyUI: it then keeps running from the old folder, which you must keep.";
            else
                moveInfo.Text = "A move to " + j.To + " was interrupted while copying. Nothing else runs until it is continued or abandoned. Continue it, or abandon it (abandoning forgets the move only; what was copied stays in " + j.To + ").";
            details.Text = MoveRunner.StaleFound ? "An old record of a move did not fit this install and was ignored." : "";

        }

        void DoMoveStart(Label details)
        {
            string to = moveTo.Text.Trim();
            if (busy) { Say("Something is still running. Wait for it, or press Stop."); return; }
            if (!host.Quiesce(this, "move")) return;
            string result = null;
            Run(delegate { result = Mover().Start(to, ProgressToStatus()); Say(result ?? "Copied, checked and switched."); },
                delegate { MoveDone(details, result); });
        }

        void DoMoveContinue(Label details)
        {
            if (busy) { Say("Something is still running. Wait for it, or press Stop."); return; }
            if (!host.Quiesce(this, "move")) return;
            string result = null;
            Run(delegate { result = Mover().Continue(ProgressToStatus()); Say(result ?? "Copied, checked and switched."); },
                delegate { MoveDone(details, result); });
        }

        void MoveDone(Label details, string result)
        {
            RefreshMove(details);
            if (result == null)
            {
                // switched: the app is NOT started again (it would run on the old place); only a restart uses the new one
                if (MessageBox.Show(this, "The big files are now in the new folder. VGAC has to restart to use them.\r\n\r\nRestart VGAC now?", "VGAC", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MaintenanceDefaults.RestartAfterMove) == DialogResult.Yes) host.RestartVgac();
            }
            else details.Text = result;       // nothing is resumed: the app does not run while a move is unfinished
        }

        void DoMoveForce(Label details)
        {
            if (busy) { Say("Something is still running. Wait for it, or press Stop."); return; }
            if (MessageBox.Show(this, "Finish the move without moving VGAC's own ComfyUI?\r\n\r\nIt keeps running from the old folder, which you must then keep. Everything else is already in the new folder.", "VGAC", MessageBoxButtons.YesNo, MessageBoxIcon.Warning, MaintenanceDefaults.FinishAnyway) != DialogResult.Yes) return;
            string result = null;
            Run(delegate { result = Mover().ContinueAnyway(ProgressToStatus()); Say(result ?? "The move is finished."); }, delegate { MoveDone(details, result); });
        }

        void DoMoveAbandon(Label details)
        {
            if (busy) { Say("Something is still running. Wait for it, or press Stop."); return; }
            if (MessageBox.Show(this, "Abandon the move? Nothing is deleted: what was copied stays in the new folder for you to delete by hand, and the old place is not changed.", "VGAC", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MaintenanceDefaults.AbandonMove) != DialogResult.Yes) return;
            details.Text = Mover().Abandon();
            RefreshMove(details);
            if (MoveGate.Blocking(host.Lay.ProjectRoot) == null && MessageBox.Show(this, "The move was abandoned. VGAC has to be started again to run on the old place.\r\n\r\nRestart VGAC now?", "VGAC", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MaintenanceDefaults.RestartAfterMove) == DialogResult.Yes) host.RestartVgac();
        }

        // ---- remove -----------------------------------------------------------------------------------------------------------

        CheckedListBox removeList;
        RemovalPlan removePlan;
        TextBox typed;

        TabPage RemoveTab()
        {
            var tab = new TabPage("Remove");
            tab.Controls.Add(Info("Remove what VGAC added to this computer. Nothing is removed until you have seen the full list and said yes. Your projects and exported characters are never removed unless you tick them and type the word.", 12, 10, 710, 50));
            removeList = new CheckedListBox(); removeList.SetBounds(12, 66, 710, 190); removeList.CheckOnClick = true;
            var review = Btn("Review what will be deleted...", 12, 266, 240);
            var word = Info("To remove your own work, type REMOVE:", 262, 272, 230, 20);
            typed = new TextBox(); typed.SetBounds(496, 268, 120, 24);
            var summaryBox = new TextBox(); summaryBox.SetBounds(12, 306, 710, 180); summaryBox.Multiline = true; summaryBox.ReadOnly = true; summaryBox.ScrollBars = ScrollBars.Vertical;
            review.Click += delegate { DoReview(summaryBox); };
            tab.Controls.AddRange(new Control[] { removeList, review, word, typed, summaryBox });
            tab.Enter += delegate { BuildPlan(summaryBox); };
            return tab;
        }

        // Entering the tab: the plan is built inside Run (on its own thread, with the window busy), so the window never freezes, the Stop
        // button reaches whatever is running, and no second command of ComfyUI's installer can start beside a running one.
        void BuildPlan(TextBox summaryBox)
        {
            if (busy) return;
            summaryBox.Text = "Looking at what VGAC added...";
            RemovalPlan plan = null;
            Run(delegate { plan = RemovalPlanner.Build(host.Lay, MachineFile.Load(host.Lay.ProjectRoot), Ledger.Load(host.Lay.ProjectRoot), host.Comfy); },
                delegate { if (plan != null) { removePlan = plan; ShowPlan(summaryBox); } });
        }

        // The plan again, keeping what the person ticked; `then` runs when it is there.
        void RefreshPlan(Action then)
        {
            var keep = Ticked();
            RemovalPlan plan = null;
            Run(delegate { plan = RemovalPlanner.Build(host.Lay, MachineFile.Load(host.Lay.ProjectRoot), Ledger.Load(host.Lay.ProjectRoot), host.Comfy); },
                delegate
                {
                    if (plan != null)
                    {
                        removePlan = plan; ShowPlan(new TextBox());
                        for (int i = 0; i < removeList.Items.Count; i++) removeList.SetItemChecked(i, keep.Contains(((string)removeList.Items[i]).Split(':')[0]));
                    }
                    if (then != null) then();
                });
        }

        void ShowPlan(TextBox summaryBox)
        {
            removeList.Items.Clear();
            foreach (RemovalGroup g in removePlan.Groups)
            {
                if (g.Items.Count == 0) continue;
                removeList.Items.Add(g.Id + ": " + g.Title + "  (" + RemovalSummary.Size(g.Bytes) + ")", g.DefaultOn);
            }
            summaryBox.Text = string.Join("\r\n", removePlan.Groups.Where(g => g.Items.Count > 0).Select(g => g.Title + ": " + g.Sentence).ToArray())
                + (removePlan.Notes.Count > 0 ? "\r\n\r\n" + string.Join("\r\n", removePlan.Notes.ToArray()) : "");
        }

        List<string> Ticked()
        {
            var res = new List<string>();
            for (int i = 0; i < removeList.Items.Count; i++)
                if (removeList.GetItemChecked(i)) res.Add(((string)removeList.Items[i]).Split(':')[0]);
            return res;
        }

        void DoReview(TextBox summaryBox)
        {
            if (busy) return;
            RefreshPlan(delegate { ReviewDialog(summaryBox); });
        }

        void ReviewDialog(TextBox summaryBox)
        {
            List<string> ticked = Ticked();
            if (ticked.Count == 0) { summaryBox.Text = "Nothing is ticked."; return; }
            string text = RemovalSummary.Text(removePlan, ticked, host.Lay);
            summaryBox.Text = text;
            if (ticked.Contains("work") && !RemovalRules.TypedWordOk(typed.Text)) { MessageBox.Show(this, "To remove your own projects and exported characters, type REMOVE in the box first.", "VGAC"); return; }
            var dlg = new Form();
            using (dlg)
            {
                dlg.Text = "VGAC"; dlg.ClientSize = new Size(700, 480); dlg.StartPosition = FormStartPosition.CenterParent; dlg.FormBorderStyle = FormBorderStyle.FixedDialog; dlg.MaximizeBox = false; dlg.MinimizeBox = false;
                var box = new TextBox(); box.SetBounds(12, 12, 676, 410); box.Multiline = true; box.ReadOnly = true; box.ScrollBars = ScrollBars.Vertical; box.Text = text;
                var ok = Btn("Delete these", 12, 434, 180); ok.DialogResult = DialogResult.OK;
                var no = Btn("Cancel", 202, 434, 100); no.DialogResult = DialogResult.Cancel;
                dlg.Controls.AddRange(new Control[] { box, ok, no }); dlg.AcceptButton = no; dlg.CancelButton = no;
                if (dlg.ShowDialog(this) != DialogResult.OK) return;
            }
            if (busy) { Say("Something is still running. Wait for it, or press Stop."); return; }
            if (!host.Quiesce(this, "removal")) return;
            DoRemoval(summaryBox, ticked, typed.Text, null);
        }

        // `permanentFor`: the items the person has agreed may be deleted for good after the Recycle Bin did not take them. Only those.
        void DoRemoval(TextBox summaryBox, List<string> ticked, string typedWord, ICollection<string> permanentFor)
        {
            RemovalResult res = null;
            Run(delegate
            {
                res = RemovalRunner.Run(removePlan, ticked, typedWord, host.Lay, MachineFile.Load(host.Lay.ProjectRoot), Ledger.Load(host.Lay.ProjectRoot), host.Comfy, new WindowsRecycler(), host.Log, delegate(string s) { Say(s); }, permanentFor);
            }, delegate
            {
                if (res.RecycleFailed.Count > 0)
                {
                    string list = string.Join("\r\n   ", res.RecycleFailed.ToArray());
                    // No is the default: the box names exactly the items it is about, and only those may be deleted for good
                    if (MessageBox.Show(this, "The Recycle Bin could not be used for:\r\n   " + list + "\r\n\r\nNothing was deleted for these. Delete exactly these for good instead? That cannot be undone.", "VGAC", MessageBoxButtons.YesNo, MessageBoxIcon.Warning, MaintenanceDefaults.PermanentDelete) == DialogResult.Yes)
                    {
                        var agreed = new List<string>(res.RecycleFailed);
                        RefreshPlan(delegate { DoRemoval(summaryBox, ticked, typedWord, agreed); });
                        return;
                    }
                }
                var sb = new StringBuilder();
                sb.AppendLine(res.Removed.Count + " item(s) removed, " + RemovalSummary.Size(res.Bytes) + " freed.");
                if (res.Unlinked.Count > 0) sb.AppendLine(res.Unlinked.Count + " link(s) were unlinked; what they pointed at was not touched.");
                foreach (string s in res.Skipped) sb.AppendLine("Not removed: " + s);
                foreach (string s in res.Locked) sb.AppendLine("In use, not removed: " + s);
                if (!res.Complete) sb.AppendLine("Close whatever is using them and press Review again to finish.");
                summaryBox.Text = sb.ToString();
                status.Text = "Removal finished.";
                RefreshPlan(null);
            });
        }
    }
}
