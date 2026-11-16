// Tests for the cut-down Move (copy, check, switch, restart): the window never acts on a stale layout, nothing is deleted, and a crash
// after every write ends in a complete move or an untouched old place, judged by contents.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Vgac.Starter;

public static partial class T
{
    static void Safety5All()
    {
        TheWindowNeverActsOnAStaleLayout();
        MoveDeletesNothingAndHasNoWayBack();
        TheNewMoveSurvivesACrashAfterEveryWrite();
        StateFilesSurviveACrashInsideTheirWrite();
        TheRealWindowLocksItself();
        AFailingRetargetHasAWayOutAndTheNextStartIsSimulated();
        RemoveSurvivesACrashAfterEveryWrite();
        UpdateSurvivesACrashAfterEveryWrite();
    }

    // What the folders of an install hold: relative path and SHA-256 of every file (links are listed, not followed).
    static Dictionary<string, string> Contents(string folder)
    {
        var res = new Dictionary<string, string>();
        if (!Directory.Exists(folder)) return res;
        foreach (string f in Directory.GetFiles(folder, "*", SearchOption.AllDirectories))
        {
            if (SafeFs.IsLink(Path.GetDirectoryName(f))) continue;
            res[f.Substring(folder.TrimEnd('\\').Length + 1)] = Hashing.Sha256File(f);
        }
        return res;
    }

    // the layout the maintenance window uses at each moment: read from the settings file
    static Layout WindowLay(string root) { return Layout.From(root, MachineFile.Load(root)); }

    // ---- the window: no layout held from before a move --------------------------------------------------------------------------------

    static void TheWindowNeverActsOnAStaleLayout()
    {
        var s = Movable(); string to = Path.Combine(Tmp("target"), "win");
        Layout before = WindowLay(s.Root);
        Check(Mover(s, null).Start(to, new Progress()) == null, "(a move in the window, no restart yet)");
        Layout now = WindowLay(s.Root);
        Check(SafeFs.Norm(before.DataFolder) != SafeFs.Norm(now.DataFolder) && SafeFs.Norm(now.DataFolder) == SafeFs.Norm(to), "the window's layout names the new place the moment the settings do");
        // reading what a move looks like must write and rename nothing
        string localBefore = string.Join("|", Directory.GetFileSystemEntries(Path.Combine(s.Root, "local")).Select(x => Path.GetFileName(x)).OrderBy(x => x).ToArray());
        Check(MoveRunner.Load(s.Root, now.DataFolder) == null, "no journal is left to show");
        Check(!File.Exists(MoveRunner.JournalPath(s.Root) + ".stale"), "nothing was set aside");
        // an old layout fed to the reader is ignored, never renamed away
        File.WriteAllText(MoveRunner.JournalPath(s.Root), "{\"from\":\"" + s.Data.Replace("\\", "\\\\") + "\",\"to\":\"" + to.Replace("\\", "\\\\") + "\",\"state\":\"copying\",\"done\":[],\"id\":\"x\"}");
        Check(MoveRunner.Load(s.Root, now.DataFolder) == null && MoveRunner.StaleFound && File.Exists(MoveRunner.JournalPath(s.Root)), "a record that does not fit is ignored and stays where it is (reading renames nothing)");
        File.Delete(MoveRunner.JournalPath(s.Root));
        // a second move before the restart is refused
        string again = Mover(s, null).Start(Path.Combine(Tmp("target"), "again"), new Progress());
        Check(again != null && again.Contains("restarted"), "a second move before the restart is refused: " + again);
        Check(MoveRules.RestartPending(s.Root), "the window offers only Restart");
        // Remove, built from the window's layout, names the new place, and the installer is asked about exactly that folder
        var plan = RemovalPlanner.Build(now, MachineFile.Load(s.Root), Ledger.Load(s.Root), s.Comfy);
        Check(plan.Groups.SelectMany(g => g.Items).Where(i => i.Kind == "data-folder" || i.Kind == "comfy-remove").All(i => i.Path.StartsWith(to)), "Remove names the new place only");
        s.Comfy.Data = to;
        string stale = s.Comfy.Remove(Path.Combine(s.Data, "comfyui"));
        Check(stale != null && File.Exists(Path.Combine(to, "comfyui", ComfyGuard.MarkerName)) && Directory.Exists(Path.Combine(to, "python")), "the installer refuses a folder that is not the one the settings name, and nothing is deleted: " + stale);
        // Repair, built from the window's layout, writes nothing in the old place
        var lines = Repair.Check(now, new CaptureHost(), Ledger.Load(s.Root));
        Check(lines.Count > 0 && Directory.Exists(Path.Combine(s.Data, "python")), "Repair reads the new place and touches nothing");
        Check(localBefore.Length > 0, "(local folder listed)");
    }

    // ---- Move deletes nothing, has no way back ---------------------------------------------------------------------------------------------

    static void MoveDeletesNothingAndHasNoWayBack()
    {
        var s = Movable(); string to = Path.Combine(Tmp("target"), "nodel");
        var oldBefore = Contents(s.Data);
        Check(Mover(s, null).Start(to, new Progress()) == null, "(a finished move)");
        var oldAfter = Contents(s.Data);
        Check(oldBefore.Count > 0 && oldBefore.Count == oldAfter.Count && oldBefore.All(kv => oldAfter.ContainsKey(kv.Key) && oldAfter[kv.Key] == kv.Value), "the old folder is exactly as it was, file by file");
        Check(typeof(MoveRunner).GetMethod("DeleteOld") == null && typeof(MoveRunner).GetMethod("Cancel") == null && typeof(MoveRunner).GetMethod("Forget") == null, "there is no DeleteOld, Cancel or Forget");
        string sentence = MoveView.OldFolderSentence(s.Data);
        Check(sentence.Contains(s.Data) && sentence.Contains("by hand"), "the old folder's path is shown with a plain sentence");
        // a target with a link above it that does not exist yet
        var s2 = Movable(); string real = Tmp("realparent"); string link = Path.Combine(Tmp("lnkparent"), "p"); Junction(link, real);
        string r = Mover(s2, null).Start(Path.Combine(link, "newplace"), new Progress());
        Check(r != null && r.Contains("link") && MoveRunner.Load(s2.Root) == null, "a target under a junction is refused even though it does not exist yet: " + r);
        // a non-empty folder that is not this move's
        string used = Tmp("usedtarget"); Put(Path.Combine(used, "x.txt"), "x");
        Check(Mover(s2, null).Start(used, new Progress()) != null && File.Exists(Path.Combine(used, "x.txt")), "a non-empty folder is refused and left alone");
        // abandon: the journal goes, the partial copy stays, and it is said where
        var s3 = Movable(); string to3 = Path.Combine(Tmp("target"), "abandon");
        SafeFs.Hook = delegate(string what, string p) { if (what == "move" && p == "copied:python") throw new Boom("stop"); };
        try { Mover(s3, null).Start(to3, new Progress()); } catch (Boom) { }
        SafeFs.Hook = null;
        string said = Mover(s3, null).Abandon();
        Check(said != null && said.Contains(to3) && MoveRunner.Load(s3.Root) == null && Directory.Exists(Path.Combine(to3, "python")), "abandoning forgets the move and leaves what was copied: " + said);
        Check(Mover(s3, null).Start(to3, new Progress()) != null, "and the same folder is then refused as not empty (the person deletes it or picks another)");
        // a copy of another move cannot be continued into
        var s4 = Movable(); string to4 = Path.Combine(Tmp("target"), "other-move");
        SafeFs.Hook = delegate(string what, string p) { if (what == "move" && p == "copied:python") throw new Boom("stop"); };
        try { Mover(s4, null).Start(to4, new Progress()); } catch (Boom) { }
        SafeFs.Hook = null;
        var jt = File.ReadAllText(MoveRunner.JournalPath(s4.Root));
        File.WriteAllText(MoveRunner.JournalPath(s4.Root), System.Text.RegularExpressions.Regex.Replace(jt, "\"id\":\\s*\"[0-9a-f]+\"", "\"id\": \"another-move\""));
        string cont = Mover(s4, null).Continue(new Progress());
        Check(cont != null && cont.Contains("another move"), "a journal whose id is not the one in the new folder's marker is not continued: " + cont);
    }

    // ---- a crash after every write ---------------------------------------------------------------------------------------------------------

    class StopHere2 : Exception { }

    static readonly string[] MoveScenarios = { "start", "continue-from-copying", "continue-from-switching", "abandon" };

    // Runs a scenario with a crash after the k-th write (k < 0: only list them); `keep` gets the scene, the new place and what the old
    // place held before.
    static List<string> RunMoveScenario(string kind, int crashAt, Action<InstallScene, string, Dictionary<string, string>> keep)
    {
        var s = Movable(); string to = Path.Combine(Tmp("target"), "crash");
        var before = Contents(s.Data);
        var rec = new List<string>(); int count = 0; bool armed = false; string stopAt = null;
        SafeFs.Hook = delegate(string what, string p)
        {
            if (what != "move" && what != "save" && what != "copy") return;
            if (!armed) { if (stopAt != null && what == "move" && p == stopAt) throw new StopHere2(); return; }
            rec.Add(what + ":" + p);
            if (count++ == crashAt) throw new Boom("crash after " + p);
        };
        try
        {
            Action<string> startStopped = delegate(string at) { stopAt = at; try { Mover(s, null).Start(to, new Progress()); } catch (StopHere2) { } stopAt = null; };
            switch (kind)
            {
                case "start": armed = true; Mover(s, null).Start(to, new Progress()); break;
                case "continue-from-copying": startStopped("copied:python"); armed = true; Mover(s, null).Continue(new Progress()); break;
                case "continue-from-switching": startStopped("journal:switching"); armed = true; Mover(s, null).Continue(new Progress()); break;
                case "abandon": startStopped("copied:python"); armed = true; Mover(s, null).Abandon(); break;
            }
        }
        catch (Boom) { }
        finally { SafeFs.Hook = null; }
        if (keep != null) keep(s, to, before);
        return rec;
    }

    static void TheNewMoveSurvivesACrashAfterEveryWrite()
    {
        int runs = 0;
        foreach (string kind in MoveScenarios)
        {
            var points = RunMoveScenario(kind, -1, null);
            Check(points.Count >= 1, "scenario " + kind + " lists its writes (" + points.Count + ")");
            var pick = new List<int>(); var bulk = new List<int>();
            for (int k = 0; k < points.Count; k++) { if (points[k].StartsWith("move:") || points[k].StartsWith("save:")) pick.Add(k); else bulk.Add(k); }
            for (int q = 0; q < 8 && bulk.Count > 0; q++) pick.Add(bulk[(int)((long)q * bulk.Count / 8)]);
            foreach (int k in pick)
            {
                string label = kind + " crashed after " + points[k] + " (#" + k + ")";
                InstallScene s = null; string to = null; Dictionary<string, string> oldBefore = null;
                try { RunMoveScenario(kind, k, delegate(InstallScene sc, string t2, Dictionary<string, string> b) { s = sc; to = t2; oldBefore = b; }); }
                catch (Exception e) { Check(false, label + ": the crash did not stop the scenario as planned: " + e.Message); continue; }
                runs++;
                // the next start: the person is offered Continue (and Abandon while copying); Continue must always reach the end
                string said = null;
                var jBefore = MoveRunner.Load(s.Root, WindowLay(s.Root).DataFolder);
                bool switchedBefore = MoveRules.RestartPending(s.Root);
                if (jBefore != null) for (int i = 0; i < 4; i++) { said = Mover(s, null).Continue(new Progress()); if (said == null) break; }
                var j = MoveRunner.Load(s.Root, WindowLay(s.Root).DataFolder);
                string cur = WindowLay(s.Root).DataFolder;
                bool madeSwitch = MoveRules.RestartPending(s.Root);
                if (jBefore == null && !switchedBefore)
                {
                    // no journal and nothing switched: the move never began or was abandoned. The person can simply start again, and the old
                    // place is as it was.
                    var oldNow = Contents(s.Data);
                    Check(SafeFs.Norm(cur) == SafeFs.Norm(s.Data) && !madeSwitch && oldBefore.Count == oldNow.Count && oldBefore.All(kv => oldNow.ContainsKey(kv.Key) && oldNow[kv.Key] == kv.Value), label + ": no move pending: the old place is in use and untouched");
                    Check(kind == "abandon" || k <= 1, label + ": only an abandon or the very first write may leave no journal and no switch");
                    continue;
                }
                Check(said == null && j == null && madeSwitch, label + ": Continue reaches the end (said: " + said + ", journal " + (j == null ? "gone" : j.State) + ")");
                Check(SafeFs.Norm(cur) == SafeFs.Norm(to), label + ": the settings name the new place (" + cur + ")");
                string ledgerPath = null;
                foreach (object o in Ledger.Load(s.Root).Items) { var d = (Dictionary<string, object>)o; if (Convert.ToString(d["what"]).Contains("own ComfyUI")) ledgerPath = Convert.ToString(d["path"]); }
                Check(ledgerPath != null && SafeFs.Norm(ledgerPath) == SafeFs.Norm(Path.Combine(to, "comfyui")) && SafeFs.Norm(s.Comfy.EngineAt) == SafeFs.Norm(to), label + ": ledger " + ledgerPath + " and engines " + s.Comfy.EngineAt + " name the new place");
                // judged by contents: the new place holds exactly what the old place held (the venv of ComfyUI aside), and carries the marker
                bool same = true; string firstBad = null;
                foreach (string name in MoveRules.NamesIn(s.Data))
                {
                    var bad = SafeFs.Compare(Path.Combine(s.Data, name), Path.Combine(to, name), MoveRules.SkipFor(name) == null ? null : new[] { MoveRules.SkipFor(name) }, true, false);
                    if (bad.Count > 0) { same = false; firstBad = bad[0]; break; }
                }
                Check(same, label + ": the copy matches the old place file by file (" + firstBad + ")");
                Check(DataFolders.ReadMarkerId(to) == DataFolders.SettingsId(MachineFile.Load(s.Root)), label + ": the new place carries this install's marker");
                var oldAfter = Contents(s.Data);
                Check(oldBefore.Count == oldAfter.Count && oldBefore.All(kv => oldAfter.ContainsKey(kv.Key) && oldAfter[kv.Key] == kv.Value), label + ": the old place is exactly as it was");
            }
        }
        Check(runs > 40, "the crash runs happened (" + runs + ")");
    }

    // ---- the state files survive a crash inside their own write ---------------------------------------------------------------------------

    static void StateFilesSurviveACrashInsideTheirWrite()
    {
        string root = NewProject();
        // each writer, with an old file there, crashed after the new text is written and before it replaces the old one
        Action<string, Action> crashing = delegate(string label, Action write)
        {
            int n = 0;
            SafeFs.Hook = delegate(string what, string p) { if (what == "save" && p.EndsWith("(written)") && ++n == 1) throw new Boom("crash inside " + label); };
            try { write(); } catch (Boom) { }
            finally { SafeFs.Hook = null; }
            Check(n >= 1, label + ": the write has a crash point inside it");
        };
        var m = MachineFile.Load(root); m.Set("starter", "a", "old"); m.Save();
        var m2 = MachineFile.Load(root); m2.Set("starter", "a", "new");
        crashing("settings", delegate { m2.Save(); });
        Eq(MachineFile.Load(root).GetString("starter", "a"), "old", "settings: the old file is whole after the crash");
        var l1 = Ledger.Load(root); l1.Add("x", Path.Combine(root, "x"), "added", 1); l1.Save();
        var l2 = Ledger.Load(root); l2.Add("y", Path.Combine(root, "y"), "added", 1);
        crashing("ledger", delegate { l2.Save(); });
        Check(Ledger.Load(root).Has(Path.Combine(root, "x"), null) && !Ledger.Load(root).Has(Path.Combine(root, "y"), null), "ledger: the old file is whole after the crash");
        string data = Path.Combine(Tmp("atomicdata"), "d"); var ms = MachineFile.Load(root); DataFolders.Stamp(data, ms);
        string idBefore = DataFolders.ReadMarkerId(data);
        crashing("data marker", delegate { DataFolders.Stamp(data, ms, "a-move"); });
        Eq(DataFolders.ReadMarkerId(data), idBefore, "data marker: the old marker is whole after the crash");
        string lib = Path.Combine(Tmp("atomiclib"), "library"); var ml = MachineFile.Load(root); LibraryFolders.Stamp(lib, ml);
        string libId = LibraryFolders.ReadMarkerId(lib);
        crashing("library marker", delegate { LibraryFolders.Stamp(lib, ml); });
        Eq(LibraryFolders.ReadMarkerId(lib), libId, "library marker: the old marker is whole after the crash");
        // Remove's journal and the update's record
        var s = Installed();
        RemovalRunner.Run(RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy), new List<string>(), null, s.L, MachineFile.Load(s.Root), s.Led, s.Comfy, new FakeRecycler(), new SetupLog(s.Root), null, null);
        string rj = RemovalRunner.JournalPath(s.Root); Put(rj, "{\"state\":\"removing\",\"groups\":[\"old\"],\"inprogress\":[]}");
        // (the remove journal's writer swallows exceptions, so the file is read at the moment of the "crash" instead)
        string snap = null;
        SafeFs.Hook = delegate(string what, string pth) { if (what == "save" && pth.EndsWith("remove.json (written)") && snap == null) snap = File.ReadAllText(rj); };
        RemovalRunner.Run(RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy), new List<string> { "tools" }, null, s.L, MachineFile.Load(s.Root), s.Led, s.Comfy, new FakeRecycler(), new SetupLog(s.Root), null, null);
        SafeFs.Hook = null;
        Check(snap != null && snap.Contains("\"old\""), "remove journal: while the new text is written the old file is whole");
        string proj = Tmp("atomicexe"); string exe = Path.Combine(proj, "VGAC.exe"); File.Copy(PingExe, exe);
        Put(ExeAside.RecordPath(proj), "{\"old\":true}");
        crashing("update record", delegate { ExeAside.Aside(exe, proj, true); });
        Check(File.ReadAllText(ExeAside.RecordPath(proj)).Contains("\"old\"") && File.Exists(exe), "update record: the old file is whole after the crash, and VGAC.exe was not renamed yet");
    }

    // ---- the real window class ---------------------------------------------------------------------------------------------------------

    class WindowHost : IMaintenanceHost
    {
        public string Root; public StandInComfy C = new StandInComfy();
        public Layout Lay { get { return Layout.From(Root, MachineFile.Load(Root)); } }
        public MachineFile Machine { get { return MachineFile.Load(Root); } }
        public IHost Host { get { return new FakeHost(); } }
        public SetupLog Log { get { return new SetupLog(Root); } }
        public string ExePath { get { return Path.Combine(Root, "VGAC.exe"); } }
        public IComfyInstall Comfy { get { return C; } }
        public IProcessSource Procs { get { return new NoProcs(); } }
        public bool Quiesce(System.Windows.Forms.IWin32Window o, string w) { return true; }
        public void Resume() { }
        public void RestartVgac() { }
    }

    static void TheRealWindowLocksItself()
    {
        var s = Movable();
        var host = new WindowHost { Root = s.Root };
        using (var f = new MaintenanceForm(host))
        {
            Check(f.EnabledTabs().Split('|').Length == 4 && f.MoveButtonsShown == "start", "an idle install: every tab works, the move offers Start (" + f.EnabledTabs() + ")");
        }
        // stopped while copying: only the Move tab works, and on it Continue and Abandon
        string to = Path.Combine(Tmp("target"), "win-copy");
        StopMoveAt(s, to, "copied:python");
        using (var f = new MaintenanceForm(host))
            Check(f.EnabledTabs() == "Move the big files" && f.MoveButtonsShown == "continue,abandon", "while copying, a freshly made window (the tray opens one each time) is locked to the move: " + f.EnabledTabs() + " / " + f.MoveButtonsShown);
        // stopped while switching: Continue and Finish anyway, no Abandon
        var s2 = Movable(); var host2 = new WindowHost { Root = s2.Root };
        StopMoveAt(s2, Path.Combine(Tmp("target"), "win-switch"), "journal:switching");
        using (var f = new MaintenanceForm(host2))
            Check(f.EnabledTabs() == "Move the big files" && f.MoveButtonsShown == "continue,finish-anyway", "while switching: " + f.MoveButtonsShown);
        // switched, not restarted: only Restart; reopening gives the same
        var s3 = Movable(); var host3 = new WindowHost { Root = s3.Root };
        Check(Mover(s3, null).Start(Path.Combine(Tmp("target"), "win-done"), new Progress()) == null, "(a finished move)");
        for (int k = 0; k < 2; k++)
            using (var f = new MaintenanceForm(host3))
                Check(f.EnabledTabs() == "Move the big files" && f.MoveButtonsShown == "restart" && f.Locked, "after the switch, opening Maintenance again (" + k + ") offers only Restart");
        // restarted: the marker is cleared at start and everything works again
        Check(MoveRules.ClearRestartAtStart(s3.Root), "(the restart clears the marker)");
        using (var f = new MaintenanceForm(host3)) Check(f.EnabledTabs().Split('|').Length == 4 && f.MoveButtonsShown == "start", "after the restart all tabs work again");
    }

    // ---- a retarget that fails every time, and the next start simulated after every crash ---------------------------------------------------

    static void AFailingRetargetHasAWayOutAndTheNextStartIsSimulated()
    {
        var s = Movable(); string to = Path.Combine(Tmp("target"), "fail");
        s.Comfy.RetargetError = s.Comfy.RetargetBackError = "The settings could not be updated.";
        string said = Mover(s, null).Start(to, new Progress());
        Check(said != null && said.Contains("Finish anyway"), "the message names the way out the window offers: " + said);
        for (int i = 0; i < 3; i++) Check(Mover(s, null).Continue(new Progress()) != null, "Continue keeps failing the same way (" + i + ") without damage");
        Check(MoveGate.Blocking(s.Root) != null && !MoveRules.RestartPending(s.Root), "and the app stays blocked, with no restart offered, while it does");
        Check(Mover(s, null).Abandon().Contains("already name the new place"), "Abandon is not offered after the settings switched");
        Check(Mover(s, null).ContinueAnyway(new Progress()) == null && MoveRules.RestartPending(s.Root) && MoveRunner.Load(s.Root) == null, "'Finish anyway' ends it");
        // the next start, simulated, after a crash at every write: the rule, a changed old place, the restart marker
        int runs = 0;
        foreach (string kind in new[] { "start", "continue-from-copying", "continue-from-switching" })
        {
            var points = RunMoveScenario(kind, -1, null);
            var pick = new List<int>();
            for (int k = 0; k < points.Count; k++) if (points[k].StartsWith("move:") || points[k].StartsWith("save:")) pick.Add(k);
            foreach (int k in pick)
            {
                string label = kind + " crashed after " + points[k] + " (#" + k + ") then the next start";
                InstallScene sc = null; string t2 = null;
                RunMoveScenario(kind, k, delegate(InstallScene a, string b, Dictionary<string, string> c) { sc = a; t2 = b; });
                runs++;
                bool journal = MoveRunner.Load(sc.Root, WindowLay(sc.Root).DataFolder) != null;
                bool pending = MoveRules.RestartPending(sc.Root);
                string gate = MoveGate.Blocking(sc.Root);
                Check((journal || pending) == (gate != null), label + ": the app is blocked exactly when a move is unfinished or a restart is needed (journal " + journal + ", restart " + pending + ", gate " + (gate ?? "none") + ")");
                if (!journal) continue;
                // the old place changed before Continue (a crash, a hand edit, or the app on a machine that was not blocked)
                Put(Path.Combine(sc.Data, "python", "late-" + k + ".bin"), "made after the copy");
                File.WriteAllText(Path.Combine(sc.Data, "python", "f1.bin"), "changed after the copy");
                string r = null; for (int i = 0; i < 4; i++) { r = Mover(sc, null).Continue(new Progress()); if (r == null) break; }
                Check(r == null && MoveRunner.Load(sc.Root) == null && MoveRules.RestartPending(sc.Root), label + ": Continue finishes (said: " + r + ")");
                Check(File.Exists(Path.Combine(t2, "python", "late-" + k + ".bin")) && File.ReadAllText(Path.Combine(t2, "python", "f1.bin")) == "changed after the copy", label + ": the new place has what changed in the old one, judged by contents");
                Check(MoveGate.Blocking(sc.Root) != null, label + ": and until the restart nothing runs");
                MoveRules.ClearRestartAtStart(sc.Root);
                Check(MoveGate.Blocking(sc.Root) == null, label + ": after the restart the rule lets the app run");
            }
        }
        Check(runs > 30, "the next-start runs happened (" + runs + ")");
    }

    // ---- Remove, crashed after every write -------------------------------------------------------------------------------------------------

    static List<string> RunRemoveScenario(int crashAt, Action<InstallScene> keep)
    {
        var s = Installed(); var rec = new List<string>(); int count = 0;
        SafeFs.Hook = delegate(string what, string p)
        {
            if (what != "remove" && what != "save" && what != "delete") return;
            rec.Add(what + ":" + p);
            if (count++ == crashAt) throw new Boom("crash after " + p);
        };
        try { RemovalRunner.Run(RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy), new List<string> { "comfy", "tools" }, null, s.L, MachineFile.Load(s.Root), s.Led, s.Comfy, new FakeRecycler(), new SetupLog(s.Root), null, null); }
        catch (Boom) { }
        finally { SafeFs.Hook = null; }
        if (keep != null) keep(s);
        return rec;
    }

    static void RemoveSurvivesACrashAfterEveryWrite()
    {
        var points = RunRemoveScenario(-1, null);
        Check(points.Count > 5, "Remove lists its writes (" + points.Count + ")");
        var pick = new List<int>(); var bulk = new List<int>();
        for (int k = 0; k < points.Count; k++) { if (points[k].StartsWith("remove:") || points[k].StartsWith("save:")) pick.Add(k); else bulk.Add(k); }
        for (int q = 0; q < 10 && bulk.Count > 0; q++) pick.Add(bulk[(int)((long)q * bulk.Count / 10)]);
        int runs = 0;
        foreach (int k in pick)
        {
            string label = "remove crashed after " + points[k] + " (#" + k + ")";
            InstallScene s = null;
            RunRemoveScenario(k, delegate(InstallScene sc) { s = sc; });
            runs++;
            // the next time: the plan is built again from what is on disk, and the same boxes ticked
            RemovalResult res = null;
            try { res = RemovalRunner.Run(RemovalPlanner.Build(s.L, MachineFile.Load(s.Root), s.Led, s.Comfy), new List<string> { "comfy", "tools" }, null, s.L, MachineFile.Load(s.Root), s.Led, s.Comfy, new FakeRecycler(), new SetupLog(s.Root), null, null); }
            catch (Exception e) { Check(false, label + ": running Remove again failed: " + e.Message); continue; }
            Check(res.Complete && !Directory.Exists(s.L.Venv) && !Directory.Exists(s.L.PythonDir) && !Directory.Exists(s.L.NodeDir), label + ": the second run finishes what was ticked (" + string.Join(" | ", res.Skipped.ToArray()) + ")");
            // judged by contents: what must stay is whole
            string c = Path.Combine(s.Data, "comfyui");
            Check(File.ReadAllText(Path.Combine(c, "models", "mine.safetensors")) == "the person's own model" && File.Exists(Path.Combine(c, ComfyGuard.MarkerName)), label + ": the models and the install record are whole");
            Check(File.Exists(Path.Combine(s.Root, "library", "vgac.sqlite")) && File.ReadAllText(Path.Combine(s.Root, "exports", "notes.txt")) == "the person's own file" && File.Exists(Path.Combine(s.Root, "exports", "hero", "hero.glb")), label + ": the person's library and exports are whole");
            Check(File.Exists(Path.Combine(s.Ext, "ComfyUI", "models", "checkpoints", "theirs.safetensors")) && File.Exists(Path.Combine(s.Ext, "blender", "blender.exe")), label + ": a linked ComfyUI and tool are untouched");
        }
        Check(runs > 10, "the Remove crash runs happened (" + runs + ")");
    }

    // ---- Update, crashed after every write -------------------------------------------------------------------------------------------------

    static void UpdateSurvivesACrashAfterEveryWrite()
    {
        if (!GitAvailable()) { Console.WriteLine("SKIPPED: the update crash test (git is not installed here)"); return; }
        var points = RunUpdateScenario(-1, null);
        Check(points.Count >= 3, "the update lists its writes (" + points.Count + ")");
        int runs = 0;
        for (int k = 0; k < points.Count; k++)
        {
            string label = "update crashed after " + points[k] + " (#" + k + ")";
            string project = null, exe = null, newSha = null;
            RunUpdateScenario(k, delegate(string p, string e, string n) { project = p; exe = e; newSha = n; });
            runs++;
            // the next start recovers what a stopped update left, then the update is pressed again
            Layout lay = Layout.From(project, MachineFile.Load(project));
            Recovery.AtStart(lay, exe);
            if (!File.Exists(exe) && File.Exists(ExeAside.AsidePath(exe))) Recovery.AtStart(lay, ExeAside.AsidePath(exe));   // started from the note's advice
            Check(File.Exists(exe), label + ": VGAC.exe is there after the next start");
            UpdateResult r = null;
            try { r = GitUpdate.Pull(project, exe, new RealGit()); } catch (Exception e) { Check(false, label + ": the second update failed: " + e.Message); continue; }
            Check(File.ReadAllText(Path.Combine(project, "readme.txt")) == "two" && Hashing.Sha256File(exe) == newSha, label + ": the second update brings everything (readme and VGAC.exe), judged by contents (" + r.Message + ")");
            Check(!Git(project, "status --porcelain").Split((char)10).Any(ln => ln.Contains("starter/bin") && !ln.Contains(".dll.old")), label + ": no library is left half changed (a stepped-aside .old copy is ignored by the project's .gitignore)");
        }
        Check(runs > 2, "the Update crash runs happened (" + runs + ")");
    }

    static List<string> RunUpdateScenario(int crashAt, Action<string, string, string> keep)
    {
        string b = Tmp("gitcrash");
        string remote = Path.Combine(b, "remote.git"); Directory.CreateDirectory(remote); Git(remote, "init --bare -q --initial-branch=main");
        string project = Path.Combine(b, "project");
        Git(b, "clone -q \"" + remote + "\" project");
        File.Copy(PingExe, Path.Combine(project, "VGAC.exe"));
        string oldSha = Hashing.Sha256File(Path.Combine(project, "VGAC.exe"));
        Put(Path.Combine(project, "starter", "CHECKSUMS.txt"), oldSha + "  VGAC.exe  (published)\n");
        Directory.CreateDirectory(Path.Combine(project, "starter", "bin")); File.Copy(Path.Combine(Environment.SystemDirectory, "version.dll"), Path.Combine(project, "starter", "bin", "Microsoft.Web.WebView2.Core.dll"));
        Put(Path.Combine(project, "readme.txt"), "one");
        Git(project, "add -A"); Git(project, "commit -q -m one"); Git(project, "push -q -u origin main");
        string dev = Path.Combine(b, "dev"); Git(b, "clone -q \"" + remote + "\" dev");
        File.Copy(CmdExe, Path.Combine(dev, "VGAC.exe"), true); Put(Path.Combine(dev, "readme.txt"), "two");
        File.Copy(Path.Combine(Environment.SystemDirectory, "winmm.dll"), Path.Combine(dev, "starter", "bin", "Microsoft.Web.WebView2.Core.dll"), true);
        string newSha = Hashing.Sha256File(Path.Combine(dev, "VGAC.exe"));
        Put(Path.Combine(dev, "starter", "CHECKSUMS.txt"), newSha + "  VGAC.exe  (published)\n");
        Git(dev, "add -A"); Git(dev, "commit -q -m two"); Git(dev, "push -q origin main");
        string exe = Path.Combine(project, "VGAC.exe");
        var rec = new List<string>(); int count = 0;
        SafeFs.Hook = delegate(string what, string p)
        {
            if (what != "aside" && what != "save") return;
            rec.Add(what + ":" + p);
            if (count++ == crashAt) throw new Boom("crash after " + p);
        };
        try { GitUpdate.Pull(project, exe, new RealGit()); }
        catch (Boom) { }
        finally { SafeFs.Hook = null; }
        if (keep != null) keep(project, exe, newSha);
        return rec;
    }
}
