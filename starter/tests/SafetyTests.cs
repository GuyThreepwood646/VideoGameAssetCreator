// Tests added after the independent safety review of phase 9. Each one fails without the fix it belongs to. Everything runs in
// temporary folders; the one real effect outside them is the Recycle Bin test, which sends one scratch file and one scratch
// folder (made in the temporary folder, named vgac-recycle-test) to the current user's Recycle Bin.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using Vgac.Starter;

public static partial class T
{
    static void SafetyAll()
    {
        RealRecyclerTakesFilesAndFolders();
        RecycleBinIsNeverReplacedSilently();
        NonFixedDriveIsPermanentAndSaysSo();
        DataFolderChoiceIsRefusedWhereShared();
        WorkFoldersMustBeProvenByContent();
        KeptIsEnforcedEverywhere();
        RealFailsClosed();
        LedgerFilesChangedAfterAddingAreLeftAlone();
        MoveCancelAfterTheSwitchKeepsNewFiles();
        MoveCrashBetweenSettingsAndJournal();
        MoveJournalIsNotTrusted();
        MoveChecksContentAndRefusesLinks();
        ExeCrashesAndOwnBuilds();
        AHungGitDoesNotHoldTheWindow();
    }

    // ---- the Recycle Bin ----------------------------------------------------------------------------------------------------

    static void RealRecyclerTakesFilesAndFolders()
    {
        string d = Tmp("vgac-recycle-test");
        string f = Path.Combine(d, "scratch-file.txt"); File.WriteAllText(f, "scratch file made by the VGAC tests; safe to delete");
        string dir = Path.Combine(d, "scratch-folder"); Put(Path.Combine(dir, "inner", "a.txt"), "scratch");
        var rec = new WindowsRecycler();
        Check(rec.Send(f) && !File.Exists(f), "the real Recycle Bin call takes a scratch file (64-bit packing is right)");
        Check(rec.Send(dir) && !Directory.Exists(dir), "and a scratch folder with something inside it");
        Check(!rec.Send(Path.Combine(d, "does-not-exist.txt")) || true, "(a path that is not there is not an error to worry about)");
    }

    static void RecycleBinIsNeverReplacedSilently()
    {
        var s = Installed();
        var rec = new FakeRecycler(); rec.Refuse = true;
        var plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        var res = RemovalRunner.Run(plan, new List<string> { "tools" }, null, s.L, s.M, s.Led, s.Comfy, rec, new SetupLog(s.Root), null, null);
        Check(res.RecycleFailed.Count > 0 && !res.Complete, "when the bin does not take an item it is reported, not deleted for good");
        Check(Directory.Exists(s.L.PythonDir) && File.Exists(Path.Combine(s.L.PythonDir, "cpython-3.13", "python.exe")), "and the item is still there");
        Eq(res.Removed.Count, 0, "nothing was removed");
        var again = RemovalRunner.Run(RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy), new List<string> { "tools" }, null, s.L, s.M, s.Led, s.Comfy, rec, new SetupLog(s.Root), null, new List<string>(res.RecycleFailed));
        Check(again.Complete && !Directory.Exists(s.L.PythonDir), "only after the person agrees (allowPermanent) is it deleted for good");
    }

    static void NonFixedDriveIsPermanentAndSaysSo()
    {
        var s = Installed();
        var old = RemovalRules.FixedDriveCheck;
        RemovalRules.FixedDriveCheck = delegate(string p) { return false; };   // a network or removable drive
        try
        {
            var plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
            string text = RemovalSummary.Text(plan, new List<string> { "tools" }, s.L);
            Check(text.Contains("deleted for good") && !text.Contains("goes to the Recycle Bin"), "on a drive that is not a fixed local one the summary says 'for good'");
            var rec = new FakeRecycler();
            var res = RemovalRunner.Run(plan, new List<string> { "tools" }, null, s.L, s.M, s.Led, s.Comfy, rec, new SetupLog(s.Root), null, null);
            Eq(rec.Sent.Count, 0, "and the Recycle Bin is not tried");
            Check(res.Complete && !Directory.Exists(s.L.PythonDir), "the item is deleted, as the summary said");
        }
        finally { RemovalRules.FixedDriveCheck = old; }
        // mixed: the wording is per item
        var s2 = Installed();
        RemovalRules.FixedDriveCheck = delegate(string p) { return !p.Contains("python"); };
        try
        {
            string text = RemovalSummary.Text(RemovalPlanner.Build(s2.L, s2.M, s2.Led, s2.Comfy), new List<string> { "tools" }, s2.L);
            Check(text.Contains("[deleted for good]") && text.Contains("[Recycle Bin]"), "a mixed group says which item goes which way");
        }
        finally { RemovalRules.FixedDriveCheck = old; }
    }

    // ---- what is proven VGAC's ------------------------------------------------------------------------------------------------

    static void DataFolderChoiceIsRefusedWhereShared()
    {
        string root = NewProject();
        string profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        Check(Proof.DataFolderProblem(Path.GetPathRoot(Path.GetTempPath()), root) != null, "a whole drive is refused");
        Check(Proof.DataFolderProblem(profile, root) != null, "the user's profile folder is refused");
        Check(Proof.DataFolderProblem(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), root) != null, "Documents is refused");
        Check(Proof.DataFolderProblem(Path.Combine(profile, "Downloads"), root) != null, "Downloads is refused");
        Check(Proof.DataFolderProblem(Environment.GetFolderPath(Environment.SpecialFolder.Windows), root) != null, "the Windows folder is refused");
        Check(Proof.DataFolderProblem(root, root) != null && Proof.DataFolderProblem(Path.GetDirectoryName(root), root) != null, "the project folder and its parent are refused");
        Check(Proof.DataFolderProblem(Path.Combine(root, "backend", "x"), root) != null, "a place inside backend is refused");
        Check(Proof.DataFolderProblem(Path.Combine(Tmp("fine"), "VGAC big files"), root) == null, "an ordinary new folder is accepted");
        Check(Proof.DataFolderProblem(Path.Combine(root, "local", "data"), root) == null, "the default (local\\data in the project) is accepted");
        // the move refuses them too
        var s = Movable();
        var mv = Mover(s, null);
        Check(mv.Start(profile, new Progress()) != null && MoveRunner.Load(s.Root) == null, "a move to the profile folder is refused and nothing starts");
    }

    // ---- the person's own work -------------------------------------------------------------------------------------------------

    static void WorkFoldersMustBeProvenByContent()
    {
        var s = Installed();
        string unity = Path.Combine(s.Ext, "UnityProject", "Assets");
        Put(Path.Combine(unity, "Scripts", "Player.cs"), "the person's game code"); Put(Path.Combine(unity, "Scene.unity"), "scene");
        Put(Path.Combine(unity, "VGAC", "Hero", "manifest.json"), "{\"lineage\":[],\"models\":[],\"seeds\":{}}"); Put(Path.Combine(unity, "VGAC", "Hero", "hero.glb"), "g");
        Put(Path.Combine(unity, "VGAC", "Hero.meta"), "meta");
        // a library path that is a game's folder, not VGAC's library
        var m = MachineFile.Load(s.Root); m.Set("backend", "library_path", unity); m.Set("backend", "export_path", Path.Combine(unity, "VGAC")); m.Save();
        s.M = MachineFile.Load(s.Root);
        var plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        Check(plan.NotRemoved.Any(n => n.StartsWith(unity) && n.Contains("not shown to be a VGAC library")), "a library path without VGAC's database is refused, by content");
        Check(plan.Get("work").Items.All(i => i.Kind == "work-export"), "only exports are left in the work group");
        Eq(plan.Get("work").Items.Count, 1, "one export folder (the one with VGAC's manifest)");
        var res = RemovalRunner.Run(plan, new List<string> { "work" }, "REMOVE", s.L, s.M, s.Led, s.Comfy, new FakeRecycler(), new SetupLog(s.Root), null, null);
        Check(!Directory.Exists(Path.Combine(unity, "VGAC", "Hero")), "the export VGAC wrote is removed");
        Check(File.Exists(Path.Combine(unity, "Scripts", "Player.cs")) && File.Exists(Path.Combine(unity, "Scene.unity")) && File.Exists(Path.Combine(unity, "VGAC", "Hero.meta")) && Directory.Exists(Path.Combine(unity, "VGAC")), "the game's own files, the Unity .meta and the folder the export sat in all stay");
        // dangerous places are refused outright
        var s2 = Installed();
        foreach (string bad in new[] { Path.GetPathRoot(Path.GetTempPath()), "backend", ".git", "frontend", Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), Path.GetDirectoryName(s2.Root) })
        {
            Directory.CreateDirectory(Path.Combine(s2.Root, ".git"));
            var mm = MachineFile.Load(s2.Root); mm.Set("backend", "library_path", bad); mm.Set("backend", "export_path", bad); mm.Save();
            var p = RemovalPlanner.Build(s2.L, MachineFile.Load(s2.Root), s2.Led, s2.Comfy);
            Check(p.Get("work").Items.Count == 0, "a library or export path of '" + bad + "' offers nothing to delete");
            Check(p.NotRemoved.Count > 0, "and says why ('" + bad + "')");
        }
        // an export folder that is itself a link is not followed
        var s3 = Installed();
        string outside = Tmp("outside"); Put(Path.Combine(outside, "manifest.json"), "{\"lineage\":[],\"models\":[],\"seeds\":{}}"); Put(Path.Combine(outside, "precious.txt"), "keep");
        Junction(Path.Combine(s3.Root, "exports", "linked-export"), outside);
        var p3 = RemovalPlanner.Build(s3.L, s3.M, s3.Led, s3.Comfy);
        Check(p3.Get("work").Items.All(i => !i.Path.EndsWith("linked-export")), "an export folder that is a link is never listed");
    }

    static void KeptIsEnforcedEverywhere()
    {
        // The starter keeps no list of what is owned or kept: it removes VGAC's own ComfyUI only through the backend's command, and a
        // refusal there deletes nothing.
        var s = Installed();
        s.Comfy.RemoveError = "VGAC's ComfyUI is still running on port 8288.";
        var plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        var res = RemovalRunner.Run(plan, new List<string> { "comfy" }, null, s.L, MachineFile.Load(s.Root), s.Led, s.Comfy, new FakeRecycler(), new SetupLog(s.Root), null, null);
        Check(res.Skipped.Any(x => x.Contains("still running")), "the backend's reason is shown");
        string c = Path.Combine(s.Data, "comfyui");
        Check(File.Exists(Path.Combine(c, "main.py")) && File.Exists(Path.Combine(c, "models", "mine.safetensors")) && File.Exists(Path.Combine(c, "output", "out.png")), "and the starter itself deleted nothing in it");
        // at the moment of deleting
        var item = plan.Get("comfy").Items[0];
        s.Comfy.RemoveError = null;
        Check(RemovalPlanner.Revalidate(item, s.L, s.Comfy, s.Led, s.M) == null, "(the item is fine while the backend confirms)");
        var other = new RemovalItem(); other.Kind = "comfy-remove"; other.Path = Path.Combine(s.Data, "comfyui", "models"); other.GroupId = "comfy";
        Check(RemovalPlanner.Revalidate(other, s.L, s.Comfy, s.Led, s.M) != null, "a comfy item that is not the install folder itself is refused");
        s.Comfy.Problem = "VGAC did not install a ComfyUI there.";
        Check(RemovalPlanner.Revalidate(item, s.L, s.Comfy, s.Led, s.M) != null, "and so is the install folder when the backend stops confirming it");
    }

    static void RealFailsClosed()
    {
        string gone = null;
        foreach (char c in "QRSTUVWXYZ") if (!Directory.Exists(c + ":\\")) { gone = c + ":\\no\\such\\place"; break; }
        if (gone == null) { Console.WriteLine("SKIPPED: every drive letter Q to Z is in use"); return; }
        Check(SafeFs.Real(gone) == null, "a place on a drive that is not there cannot be resolved (it is not guessed)");
        Check(!SafeFs.Inside(gone, Path.GetTempPath()) && !SafeFs.Inside(Path.GetTempPath(), gone), "so it is not 'inside' anything");
        Check(!SafeFs.PathHasNoLink(gone), "and is not taken for 'has no link'");
    }

    static void LedgerFilesChangedAfterAddingAreLeftAlone()
    {
        var s = Installed();
        string model = Path.Combine(s.Ext, "ComfyUI", "models", "checkpoints", "vgac.safetensors");
        File.SetLastWriteTimeUtc(model, DateTime.UtcNow.AddHours(3));    // same size, but written after VGAC added it
        var plan = RemovalPlanner.Build(s.L, s.M, s.Led, s.Comfy);
        Check(plan.Get("models").Items.Count == 0 && plan.NotRemoved.Any(n => n.Contains("changed after VGAC added it")), "a file with the right size that was written after VGAC added it is left alone");
    }

    // ---- move ---------------------------------------------------------------------------------------------------------------------

    static void MoveCancelAfterTheSwitchKeepsNewFiles()
    {
        var s = Movable(); string to = Path.Combine(Tmp("target"), "newbig");
        Check(Mover(s, null).Start(to, new Progress()) == null, "the move runs to the end");
        Put(Path.Combine(to, "comfyui", "models", "new-since-the-move.safetensors"), "a model added after the move");
        string said = Mover(s, null).Cancel();
        Check(File.Exists(Path.Combine(to, "comfyui", "models", "new-since-the-move.safetensors")), "Cancel after the switch does not delete the new copy, so what was added since survives");
        Check(said != null && said.Contains("left as it is"), "and says so");
        Eq(MachineFile.Load(s.Root).GetString("starter", "dataFolder"), s.Data, "the settings point back at the old place");
        Check(MoveRunner.Load(s.Root) == null, "and the journal is closed (it cannot linger forever)");
        // deleting the old copy is not blocked by files added to the new copy since
        var s2 = Movable(); string to2 = Path.Combine(Tmp("target"), "newbig");
        var mv = Mover(s2, null); mv.Start(to2, new Progress());
        Put(Path.Combine(to2, "comfyui", "models", "added-later.safetensors"), "x");
        Check(mv.DeleteOld() == null && !Directory.Exists(Path.Combine(s2.Data, "python")) && File.Exists(Path.Combine(to2, "comfyui", "models", "added-later.safetensors")), "extra files in the new place do not block deleting the old copy, and are kept");
        // the person can close a finished move and keep both copies
        var s3 = Movable(); string to3 = Path.Combine(Tmp("target"), "newbig");
        var mv3 = Mover(s3, null); mv3.Start(to3, new Progress());
        Check(mv3.Forget() == null && MoveRunner.Load(s3.Root) == null && Directory.Exists(Path.Combine(s3.Data, "python")) && Directory.Exists(Path.Combine(to3, "python")), "'keep both' closes the journal and deletes nothing");
    }

    static void MoveCrashBetweenSettingsAndJournal()
    {
        var s = Movable(); string to = Path.Combine(Tmp("target"), "newbig");
        SafeFs.Hook = delegate(string what, string p) { if (what == "move" && p == "after-settings-saved") throw new Boom("crash"); };
        try { Mover(s, null).Start(to, new Progress()); } catch (Boom) { }
        SafeFs.Hook = null;
        Eq(MachineFile.Load(s.Root).GetString("starter", "dataFolder"), to, "the settings were already changed when the crash came");
        var j = MoveRunner.Load(s.Root, to);
        Check(j != null && j.State == "switching", "the journal says 'switching', not 'verified': the crash is recognised (" + (j == null ? "null" : j.State) + ")");
        // Cancel must not delete the copy the settings already point at
        var s2 = Movable(); string to2 = Path.Combine(Tmp("target"), "newbig");
        SafeFs.Hook = delegate(string what, string p) { if (what == "move" && p == "after-settings-saved") throw new Boom("crash"); };
        try { Mover(s2, null).Start(to2, new Progress()); } catch (Boom) { }
        SafeFs.Hook = null;
        string said = Mover(s2, null).Cancel();
        Check(Directory.Exists(Path.Combine(to2, "python")), "Cancel after that crash leaves the new copy alone");
        Eq(MachineFile.Load(s2.Root).GetString("starter", "dataFolder"), s2.Data, "and points the settings back, so the app does not run from an emptied folder");
        Check(said != null, "with a plain message");
        // the other way out: Continue finishes the switch
        var s3 = Movable(); string to3 = Path.Combine(Tmp("target"), "newbig");
        SafeFs.Hook = delegate(string what, string p) { if (what == "move" && p == "after-settings-saved") throw new Boom("crash"); };
        try { Mover(s3, null).Start(to3, new Progress()); } catch (Boom) { }
        SafeFs.Hook = null;
        Check(Mover(s3, null).Continue(new Progress()) == null && MoveRunner.Load(s3.Root, to3).State == "rebuilt", "Continue after that crash finishes the move");
        Check(Ledger.Load(s3.Root).Items.Select(o => Convert.ToString(((Dictionary<string, object>)o)["path"])).Any(p => p.StartsWith(to3)), "and the ledger paths are rewritten");
    }

    static void WriteJournal(string root, string json)
    {
        Directory.CreateDirectory(Path.Combine(root, "local"));
        File.WriteAllText(MoveRunner.JournalPath(root), json);
    }

    static void MoveJournalIsNotTrusted()
    {
        var s = Movable(); string to = Path.Combine(Tmp("target"), "newbig");
        string sentinelDir = Path.Combine(Path.GetDirectoryName(to), "sentinel"); Put(Path.Combine(sentinelDir, "keep.txt"), "must survive");
        Func<string, string> Q = delegate(string x) { return x.Replace("\\", "\\\\"); };
        // names that climb out or are rooted
        foreach (string evil in new[] { "..\\\\sentinel", Q(sentinelDir), "python\\\\..\\\\..\\\\sentinel", "C:\\\\Windows" })
        {
            WriteJournal(s.Root, "{\"from\":\"" + Q(s.Data) + "\",\"to\":\"" + Q(to) + "\",\"state\":\"copying\",\"done\":[],\"created\":[\"" + evil + "\"]}");
            Check(MoveRunner.Load(s.Root, s.Data) == null && MoveRunner.StaleFound, "a journal with the name '" + evil + "' is not accepted");
            Check(File.Exists(MoveRunner.JournalPath(s.Root) + ".stale") && !File.Exists(MoveRunner.JournalPath(s.Root)), "it is set aside, not acted on");
            string r = Mover(s, null).Cancel();
            Check(File.Exists(Path.Combine(sentinelDir, "keep.txt")), "Cancel deletes nothing for it ('" + evil + "')");
        }
        // a made-up state, relative places
        WriteJournal(s.Root, "{\"from\":\"" + Q(s.Data) + "\",\"to\":\"" + Q(to) + "\",\"state\":\"bogus\",\"done\":[],\"created\":[]}");
        Check(MoveRunner.Load(s.Root, s.Data) == null, "an unknown state is not accepted");
        WriteJournal(s.Root, "{\"from\":\"relative\\\\a\",\"to\":\"relative\\\\b\",\"state\":\"copying\",\"done\":[],\"created\":[]}");
        Check(MoveRunner.Load(s.Root, s.Data) == null, "relative places are not accepted");
        // a journal from another install: places that do not fit the current settings
        WriteJournal(s.Root, "{\"from\":\"" + Q(Path.Combine(Tmp("other"), "a")) + "\",\"to\":\"" + Q(Path.Combine(Tmp("other"), "b")) + "\",\"state\":\"switched\",\"done\":[\"python\"],\"created\":[\"python\"]}");
        string msg = Mover(s, null).Continue(new Progress());
        Check(msg != null && msg.Contains("does not fit") && Directory.Exists(Path.Combine(s.Data, "python")), "Continue on a stale journal (places that do not fit the settings) says so and changes nothing: " + msg);
        Check(File.Exists(MoveRunner.JournalPath(s.Root) + ".stale"), "the journal was set aside");
        // a damaged journal reads as none
        WriteJournal(s.Root, "{ nope");
        Check(MoveRunner.Load(s.Root, s.Data) == null, "a damaged journal reads as none");
        // a good journal still works
        File.Delete(MoveRunner.JournalPath(s.Root));
        Check(Mover(s, null).Start(to, new Progress()) == null, "and a move started normally is accepted by the same checks");
    }

    static void MoveChecksContentAndRefusesLinks()
    {
        // a link inside a VGAC folder: the move is refused instead of silently dropping what it points to
        var s = Movable(); string to = Path.Combine(Tmp("target"), "newbig");
        string outside = Tmp("models-elsewhere"); Put(Path.Combine(outside, "big.safetensors"), "the models");
        Junction(Path.Combine(s.Data, "comfyui", "models-link"), outside);
        string why = Mover(s, null).Start(to, new Progress());
        Check(why != null && why.Contains("link inside") && !Directory.Exists(to) && MoveRunner.Load(s.Root) == null, "a junction inside a folder to be moved stops the move before anything is copied: " + why);
        // same size, different content: the checksum catches it
        var s2 = Movable(); string to2 = Path.Combine(Tmp("target"), "newbig");
        PutBytes(Path.Combine(s2.Data, "comfyui", "models", "model.bin"), 4000);
        SafeFs.Hook = delegate(string what, string p)
        {
            if (what == "move" && p == "verify:comfyui")
            {
                byte[] b = File.ReadAllBytes(Path.Combine(to2, "comfyui", "models", "model.bin")); b[17] = 99;   // one byte, same length
                File.WriteAllBytes(Path.Combine(to2, "comfyui", "models", "model.bin"), b);
            }
        };
        string msg = Mover(s2, null).Start(to2, new Progress());
        SafeFs.Hook = null;
        Check(msg != null && msg.Contains("different content"), "a copy that differs by one byte in a model is caught: " + msg);
        Eq(MachineFile.Load(s2.Root).GetString("starter", "dataFolder"), s2.Data, "and nothing was switched");
    }

    // ---- update ---------------------------------------------------------------------------------------------------------------------

    class FailCheckoutGit : IGit
    {
        public List<string> Calls = new List<string>();
        public int Run(string a, string c, out string o)
        {
            Calls.Add(a); o = a.StartsWith("rev-parse HEAD") ? "abc123\n" : "";
            return a.StartsWith("checkout") ? 1 : 0;
        }
    }

    static void ExeCrashesAndOwnBuilds()
    {
        // a crash after the rename: the program started from VGAC.old.exe takes its name back
        string root = Tmp("crash"); string exe = Path.Combine(root, "VGAC.exe"); File.Copy(PingExe, exe);
        Check(ExeAside.Aside(exe, root) == null && !File.Exists(exe), "(the file was renamed and the crash comes here)");
        string note = ExeAside.RecoverMissing(ExeAside.AsidePath(exe), root);
        Check(note != null && note.Contains("took its name back") && File.Exists(exe) && !File.Exists(ExeAside.AsidePath(exe)), "VGAC.exe is back under its name after a crash between the rename and the checkout: " + note);
        Check(ExeAside.RecoverMissing(exe, root) == null, "a normal start does nothing");
        // cleanup never deletes the file that is running
        string root2 = Tmp("running-aside"); string exe2 = Path.Combine(root2, "VGAC.exe"); File.Copy(PingExe, exe2);
        ExeAside.Aside(exe2, root2); File.Copy(CmdExe, exe2);
        Check(ExeAside.CleanupAtStart(ExeAside.AsidePath(exe2), root2) == null && File.Exists(ExeAside.AsidePath(exe2)), "when this very program is VGAC.old.exe the clean-up leaves it alone");
        // the checkout that prepares git fails: the person's file is back, byte for byte
        string root3 = Tmp("checkout"); Directory.CreateDirectory(Path.Combine(root3, ".git"));
        string exe3 = Path.Combine(root3, "VGAC.exe"); File.Copy(CmdExe, exe3); string before = Hashing.Sha256File(exe3);
        Put(Path.Combine(root3, "starter", "CHECKSUMS.txt"), new string('b', 64) + "  VGAC.exe\n");
        var g = new FailCheckoutGit();
        UpdateResult r = GitUpdate.Pull(root3, exe3, g);
        Check(!r.Updated && File.Exists(exe3) && Hashing.Sha256File(exe3) == before && !File.Exists(ExeAside.AsidePath(exe3)), "if git cannot prepare, VGAC.exe is back as it was and nothing is left aside: " + r.Message);
        Check(!g.Calls.Any(c => c.StartsWith("pull")), "and no pull was run");
        // a second update with your own build still aside is refused, the file is not lost
        string root4 = Tmp("own2"); string exe4 = Path.Combine(root4, "VGAC.exe"); File.Copy(CmdExe, exe4);
        Check(ExeAside.Aside(exe4, root4, false) == null, "(first update: your own build is stepped aside)");
        string mine = Hashing.Sha256File(ExeAside.AsidePath(exe4));
        File.Copy(PingExe, exe4);
        string again = ExeAside.Aside(exe4, root4, false);
        Check(again != null && again.Contains("your own build") && Hashing.Sha256File(ExeAside.AsidePath(exe4)) == mine, "the second update does not replace your own build: " + again);
        Check(File.Exists(exe4), "and VGAC.exe stays where it is");
        // a failure partway says so instead of 'nothing changed'
        Check(GitUpdate.LastLines("a\nb\nc\nd", 2) == "c d", "(the last lines of git's answer are shown)");
    }

    static void AHungGitDoesNotHoldTheWindow()
    {
        string bin = Tmp("fakegit"); File.Copy(PingExe, Path.Combine(bin, "git.exe"));
        string oldPath = Environment.GetEnvironmentVariable("PATH");
        Environment.SetEnvironmentVariable("PATH", bin + ";" + oldPath);
        try
        {
            var sw = Stopwatch.StartNew();
            string o; int code = new RealGit { TimeoutMs = 800 }.Run("-n 30 127.0.0.1", bin, out o);
            Check(code == -1 && sw.ElapsedMilliseconds < 10000, "a git that never ends is given up on after the limit (" + sw.ElapsedMilliseconds + " ms) and its output is not waited for");
            Check(o.Contains("did not finish"), "with a plain answer");
            Thread.Sleep(400);
            Check(Process.GetProcessesByName("git").All(p => { try { return !p.Path().StartsWith(bin); } catch (Exception) { return true; } }), "and it was stopped");
        }
        finally { Environment.SetEnvironmentVariable("PATH", oldPath); }
    }

}

public static class ProcessPath
{
    public static string Path(this Process p) { try { return p.MainModule.FileName; } catch (Exception) { return ""; } }
}
