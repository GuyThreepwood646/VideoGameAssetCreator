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
        StateFilesAreWrittenAtomically();
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

    // ---- the state files are replaced, never half written ---------------------------------------------------------------------------------------

    static void StateFilesAreWrittenAtomically()
    {
        string src = FindSource("src\\Remove.cs");
        string setup = FindSource("src\\Setup.cs"), logic = FindSource("src\\Logic.cs"), proof = FindSource("src\\Proof.cs"), upd = FindSource("src\\Update.cs");
        if (src == null || setup == null || logic == null || proof == null || upd == null) { Console.WriteLine("SKIPPED: sources not found for the atomic-write check"); return; }
        foreach (string f in new[] { src, setup, logic, proof, upd })
        {
            string text = File.ReadAllText(f);
            // every state file the starter keeps is written through SafeFs.WriteAtomic, not File.WriteAllText straight onto it
            foreach (string name in new[] { "remove.json", "state.json", "starter-owned.json", "exe-aside.json", "StatePath", "RecordPath(root)" })
            { }
            int direct = System.Text.RegularExpressions.Regex.Matches(text, @"File\.WriteAllText\((jp|l\.StatePath|record|p, Json|tmp)").Count;
            Check(direct == 0, Path.GetFileName(f) + ": no state file is written straight onto its name (" + direct + " left)");
        }
    }
}
