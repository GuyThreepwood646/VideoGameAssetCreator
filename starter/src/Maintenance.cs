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
        volatile bool busy;

        public MaintenanceForm(IMaintenanceHost host)
        {
            this.host = host;
            Text = "VGAC maintenance";
            Icon = AppIcon.Get(32);
            ClientSize = new Size(760, 560);
            StartPosition = FormStartPosition.CenterScreen;
            tabs.SetBounds(8, 8, 744, 500); tabs.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
            status.SetBounds(12, 516, 736, 36); status.Anchor = AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
            Controls.Add(tabs); Controls.Add(status);
            tabs.TabPages.Add(RepairTab());
            tabs.TabPages.Add(UpdateTab());
            tabs.TabPages.Add(MoveTab());
            tabs.TabPages.Add(RemoveTab());
            FormClosing += delegate(object s, FormClosingEventArgs e) { if (busy) { e.Cancel = true; MessageBox.Show(this, "Something is still running. Wait for it to finish.", "VGAC"); } };
        }

        public void ShowTab(int i) { if (i >= 0 && i < tabs.TabPages.Count) tabs.SelectedIndex = i; }

        void Say(string s) { if (IsHandleCreated && !IsDisposed) try { BeginInvoke((Action)delegate { status.Text = s; }); } catch (Exception) { } }

        // Runs `work` on a thread with the window locked, then `done` on the window's thread.
        void Run(Action work, Action done)
        {
            if (busy) return;
            busy = true; UseWaitCursor = true;
            var t = new Thread(delegate()
            {
                try { work(); }
                catch (StarterError e) { Say(e.Message + (e.Fix != null ? " " + e.Fix : "")); host.Log.Line("maintenance: " + e.Message); }
                catch (Exception e) { Say("Something went wrong: " + e.Message); host.Log.Line("maintenance: " + e); }
                finally
                {
                    busy = false;
                    try { BeginInvoke((Action)delegate { UseWaitCursor = false; if (done != null) done(); }); } catch (Exception) { }
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
            bool needsStop = todo.Any(c => c.FixId == "venv" || c.FixId == "packages" || c.FixId == "uv" || c.FixId == "screens");
            if (needsStop && !host.Quiesce(this, "fix")) return;
            Run(delegate
            {
                foreach (CheckLine c in todo)
                {
                    Say("Fixing: " + c.Text);
                    string why = Repair.Fix(c.FixId, host.Lay, host.Host, MachineFile.Load(host.Lay.ProjectRoot), Ledger.Load(host.Lay.ProjectRoot), host.Log, ProgressToStatus());
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
                    if (MessageBox.Show(this, "The update needs these steps, which install packages and build the screens from the files just pulled:\r\n   - " + list + "\r\n\r\nRun them now?", "VGAC", MessageBoxButtons.YesNo) == DialogResult.Yes)
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

        TextBox moveTo;
        Label moveInfo;

        TabPage MoveTab()
        {
            var tab = new TabPage("Move the big files");
            moveInfo = Info("", 12, 10, 700, 110);
            moveTo = new TextBox(); moveTo.SetBounds(12, 130, 560, 24);
            var choose = Btn("Choose...", 582, 127, 100);
            var go = Btn("Copy, check, then switch", 12, 170, 220);
            var cont = Btn("Continue an interrupted move", 242, 170, 220);
            var cancel = Btn("Cancel it", 472, 170, 100);
            var del = Btn("Delete the old copy", 12, 210, 220);
            var keep = Btn("Keep both copies", 242, 210, 160);
            var details = Info("", 12, 255, 700, 220);
            choose.Click += delegate { using (var d = new FolderBrowserDialog()) { d.Description = "Choose an empty folder for the big files."; if (d.ShowDialog(this) == DialogResult.OK) moveTo.Text = d.SelectedPath; } };
            go.Click += delegate { DoMoveStart(details); };
            cont.Click += delegate { DoMoveContinue(details); };
            cancel.Click += delegate { DoMoveCancel(details); };
            del.Click += delegate { DoMoveDeleteOld(details); };
            keep.Click += delegate { string r = Mover().Forget(); details.Text = r ?? "Done. Both copies are kept."; RefreshMove(details); };
            tab.Controls.AddRange(new Control[] { moveInfo, moveTo, choose, go, cont, cancel, del, keep, details });
            tab.Enter += delegate { RefreshMove(details); };
            return tab;
        }

        MoveRunner Mover()
        {
            return new MoveRunner(host.Lay, MachineFile.Load(host.Lay.ProjectRoot), Ledger.Load(host.Lay.ProjectRoot), host.Comfy, host.Procs, host.Log);
        }

        void RefreshMove(Label details)
        {
            var j = MoveRunner.Load(host.Lay.ProjectRoot, host.Lay.DataFolder);
            long need = Mover().NeedBytes();
            if (j == null)
                moveInfo.Text = "The big files (Python, tools, VGAC's own ComfyUI) are in:\r\n   " + host.Lay.DataFolder + "   (" + RemovalSummary.Size(need) + ")\r\n\r\nThey are copied to the new folder and checked; only then does VGAC switch to it. Until then the old place is untouched and in use.";
            else
                moveInfo.Text = "A move to " + j.To + " is at the step '" + j.State + "'. Continue it, or cancel it (cancelling removes only what the move copied).";
            details.Text = j != null && j.State == "rebuilt" ? "The move is finished. The old copy (" + string.Join(", ", MoveRunner.OldCopy(j).ToArray()) + ") can be deleted to free room." : "";
        }

        void DoMoveStart(Label details)
        {
            string to = moveTo.Text.Trim();
            if (!host.Quiesce(this, "move")) return;
            string result = null;
            Run(delegate { result = Mover().Start(to, ProgressToStatus()); Say(result ?? "Copied, checked and switched."); },
                delegate { details.Text = result ?? "The move is done. Restart VGAC so the app's Python is made again in the new place."; RefreshMove(details); if (result == null) FinishMove(); else host.Resume(); });
        }

        void DoMoveContinue(Label details)
        {
            if (!host.Quiesce(this, "move")) return;
            string result = null;
            Run(delegate { result = Mover().Continue(ProgressToStatus()); Say(result ?? "Copied, checked and switched."); },
                delegate { details.Text = result ?? "The move is done."; RefreshMove(details); if (result == null) FinishMove(); else host.Resume(); });
        }

        void FinishMove()
        {
            if (MessageBox.Show(this, "The big files are now in the new folder. VGAC has to restart to make the app's Python again in the new place (about a minute).\r\n\r\nRestart VGAC now?", "VGAC", MessageBoxButtons.YesNo) == DialogResult.Yes) host.RestartVgac();
        }

        void DoMoveCancel(Label details)
        {
            var jc = MoveRunner.Load(host.Lay.ProjectRoot, host.Lay.DataFolder);
            if (jc == null) { details.Text = "There is no move to cancel."; return; }
            bool inUse = jc.State == "switching" || jc.State == "switched" || jc.State == "rebuilt";
            string q = inUse ? "Undo the move? VGAC points at the old place again. The copy in the new folder is NOT deleted (the app may have used it); you delete it yourself."
                : "Cancel the move? What it copied into the new folder is removed. The old place is not changed.";
            if (MessageBox.Show(this, q, "VGAC", MessageBoxButtons.YesNo) != DialogResult.Yes) return;
            if (!host.Quiesce(this, "move")) return;
            string said = null;
            Run(delegate { said = Mover().Cancel(); Say(said ?? "The move was cancelled."); }, delegate { details.Text = said ?? "The move was cancelled."; RefreshMove(details); host.Resume(); });
        }

        void DoMoveDeleteOld(Label details)
        {
            var j = MoveRunner.Load(host.Lay.ProjectRoot, host.Lay.DataFolder);
            if (j == null || j.State != "rebuilt") { details.Text = "There is no finished move whose old copy can be deleted."; return; }
            long bytes = 0; foreach (string p in MoveRunner.OldCopy(j)) bytes += SafeFs.Size(p, null);
            string list = string.Join("\r\n   ", MoveRunner.OldCopy(j).ToArray());
            if (MessageBox.Show(this, "Delete the old copy?\r\n\r\n   " + list + "\r\n\r\n" + RemovalSummary.Size(bytes) + " will be freed. It is deleted for good and cannot be restored. Anything else you keep in the old folder stays.", "VGAC", MessageBoxButtons.YesNo) != DialogResult.Yes) return;
            string result = null;
            Run(delegate { result = Mover().DeleteOld(); Say(result ?? "The old copy was deleted."); }, delegate { details.Text = result ?? "The old copy was deleted."; RefreshMove(details); });
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

        void BuildPlan(TextBox summaryBox)
        {
            removePlan = RemovalPlanner.Build(host.Lay, MachineFile.Load(host.Lay.ProjectRoot), Ledger.Load(host.Lay.ProjectRoot), host.Comfy);
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
            BuildPlanKeepingChecks();
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
            if (!host.Quiesce(this, "removal")) return;
            DoRemoval(summaryBox, ticked, typed.Text, false);
        }

        void DoRemoval(TextBox summaryBox, List<string> ticked, string typedWord, bool allowPermanent)
        {
            RemovalResult res = null;
            Run(delegate
            {
                res = RemovalRunner.Run(removePlan, ticked, typedWord, host.Lay, MachineFile.Load(host.Lay.ProjectRoot), Ledger.Load(host.Lay.ProjectRoot), host.Comfy, new WindowsRecycler(), host.Log, delegate(string s) { Say(s); }, allowPermanent);
            }, delegate
            {
                if (res.RecycleFailed.Count > 0)
                {
                    string list = string.Join("\r\n   ", res.RecycleFailed.ToArray());
                    if (MessageBox.Show(this, "The Recycle Bin could not be used for:\r\n   " + list + "\r\n\r\nNothing was deleted for these. Delete them for good instead? That cannot be undone.", "VGAC", MessageBoxButtons.YesNo) == DialogResult.Yes)
                    {
                        BuildPlanKeepingChecks();
                        DoRemoval(summaryBox, ticked, typedWord, true);
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
                BuildPlanKeepingChecks();
            });
        }

        void BuildPlanKeepingChecks()
        {
            var keep = Ticked();
            var tb = new TextBox();
            BuildPlan(tb);
            for (int i = 0; i < removeList.Items.Count; i++) removeList.SetItemChecked(i, keep.Contains(((string)removeList.Items[i]).Split(':')[0]));
        }
    }
}
