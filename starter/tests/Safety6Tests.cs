// The ninth review: while a move is unfinished nothing may change the old place or run on it; Continue re-checks everything.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Vgac.Starter;

public static partial class T
{
    static void Safety6All()
    {
        G1ATheAppDoesNotRunOnAHalfMovedPlace();
        G1BContinueCopiesWhatChangedMeanwhile();
        G2AnOldFileThatWentAwayDoesNotMakeContinueLoop();
        G3NothingIsWrittenThroughALinkInAResumedTarget();
        G4ALibraryStepAsideThatWasCutOffIsPutBack();
    }

    static string StopMoveAt(InstallScene s, string to, string point)
    {
        SafeFs.Hook = delegate(string what, string p) { if (what == "move" && p == point) throw new Boom("stop at " + point); };
        try { return Mover(s, null).Start(to, new Progress()); }
        catch (Boom) { return "stopped"; }
        finally { SafeFs.Hook = null; }
    }

    // ---- G1 A: the switch cannot finish (ComfyUI's installer refuses) ----------------------------------------------------------------

    static void G1ATheAppDoesNotRunOnAHalfMovedPlace()
    {
        var s = Movable(); string to = Path.Combine(Tmp("target"), "g1a");
        s.Comfy.RetargetError = s.Comfy.RetargetBackError = "The settings could not be updated.";
        string said = Mover(s, null).Start(to, new Progress());
        Check(said != null && said.Contains("could not be told"), "(the switch stopped at ComfyUI's installer: " + said + ")");
        Check(!MoveRules.RestartPending(s.Root), "G1: the restart marker is not written before ComfyUI is retargeted, so the window and its message agree");
        string root = s.Root;
        // the rule, in code: nothing that changes things runs while the journal exists
        var plan = RemovalPlanner.Build(WindowLay(root), MachineFile.Load(root), Ledger.Load(root), s.Comfy);
        var res = RemovalRunner.Run(plan, new List<string> { "tools", "comfy" }, null, WindowLay(root), MachineFile.Load(root), Ledger.Load(root), s.Comfy, new FakeRecycler(), new SetupLog(root), null, null);
        Check(res.Removed.Count == 0 && Directory.Exists(Path.Combine(s.Data, "python")) && Directory.Exists(Path.Combine(to, "python")), "G1: Remove refuses while a move is unfinished and removes nothing in either place");
        string fix = Repair.Fix("leftovers", WindowLay(root), new CaptureHost(), MachineFile.Load(root), Ledger.Load(root), new SetupLog(root), new Progress());
        Check(fix != null && fix.Contains("move"), "G1: Repair refuses while a move is unfinished: " + fix);
        string gate = MoveGate.Blocking(root);
        Check(gate != null && gate.Contains("not finished"), "G1: the one rule says so in plain words: " + gate);
        // the way out of a retarget that fails every time
        string out1 = Mover(s, null).ContinueAnyway(new Progress());
        Check(out1 == null && MoveRunner.Load(root, WindowLay(root).DataFolder) == null && MoveRules.RestartPending(root), "G1: 'finish anyway' ends the move and asks for the restart: " + out1);
        string ledgerPath = null;
        foreach (object o in Ledger.Load(root).Items) { var d = (Dictionary<string, object>)o; if (Convert.ToString(d["what"]).Contains("own ComfyUI")) ledgerPath = Convert.ToString(d["path"]); }
        Check(ledgerPath != null && SafeFs.Norm(ledgerPath) == SafeFs.Norm(Path.Combine(s.Data, "comfyui")), "G1: ComfyUI was not moved, so the ledger still names where it really is (" + ledgerPath + ")");
        Check(MoveGate.Blocking(root) != null, "G1: and until the restart everything still refuses");
    }

    // ---- G1 B: the machine died after the journal said "switching", the app ran on the old place, Continue later ----------------------------

    static void G1BContinueCopiesWhatChangedMeanwhile()
    {
        var s = Movable(); string to = Path.Combine(Tmp("target"), "g1b");
        StopMoveAt(s, to, "journal:switching");
        Eq(MoveRunner.Load(s.Root, WindowLay(s.Root).DataFolder).State, "switching", "(stopped at 'switching', settings still on the old place)");
        // the app ran on the old place meanwhile: a new file, a changed file
        Put(Path.Combine(s.Data, "python", "made-meanwhile.bin"), "new output");
        File.WriteAllText(Path.Combine(s.Data, "python", "f1.bin"), "changed meanwhile");
        Check(Mover(s, null).Continue(new Progress()) == null, "(Continue)");
        Check(File.Exists(Path.Combine(to, "python", "made-meanwhile.bin")) && File.ReadAllText(Path.Combine(to, "python", "f1.bin")) == "changed meanwhile", "G1: what changed in the old place meanwhile is in the new place (Continue checks again from 'switching')");
        // the new place was damaged meanwhile
        var s2 = Movable(); string to2 = Path.Combine(Tmp("target"), "g1b2");
        StopMoveAt(s2, to2, "journal:switching");
        File.Delete(Path.Combine(to2, "python", "f2.bin"));
        Check(Mover(s2, null).Continue(new Progress()) == null && File.Exists(Path.Combine(to2, "python", "f2.bin")), "G1: a file missing from the new place is copied again before the switch");
        // the new place lost its marker
        var s3 = Movable(); string to3 = Path.Combine(Tmp("target"), "g1b3");
        StopMoveAt(s3, to3, "journal:switching");
        File.Delete(Path.Combine(to3, DataFolders.MarkerName));
        string r3 = Mover(s3, null).Continue(new Progress());
        Check(r3 != null && !MoveRules.RestartPending(s3.Root), "G1: a new place without its marker is not switched to: " + r3);
    }

    // ---- G2 ------------------------------------------------------------------------------------------------------------------------------

    static void G2AnOldFileThatWentAwayDoesNotMakeContinueLoop()
    {
        var s = Movable(); string to = Path.Combine(Tmp("target"), "g2");
        StopMoveAt(s, to, "copied:python");
        File.Delete(Path.Combine(s.Data, "python", "f4.bin"));          // the old place lost a file after it was copied
        Check(Mover(s, null).Continue(new Progress()) == null, "G2: Continue is not stuck when the old place lost a file that is in the new one");
        Check(MoveRules.RestartPending(s.Root), "G2: and the move ends");
    }

    // ---- G3 ------------------------------------------------------------------------------------------------------------------------------

    static void G3NothingIsWrittenThroughALinkInAResumedTarget()
    {
        var s = Movable(); string to = Path.Combine(Tmp("target"), "g3");
        StopMoveAt(s, to, "copied:python");
        string elsewhere = Tmp("g3-elsewhere"); Put(Path.Combine(elsewhere, "keep.txt"), "not VGAC's");
        SafeFs.DeleteTree(Path.Combine(to, "uv-cache"));
        Junction(Path.Combine(to, "uv-cache"), elsewhere);
        string said = Mover(s, null).Continue(new Progress());
        Check(said != null && said.Contains("link"), "G3: a link in the resumed target stops Continue: " + said);
        Check(Directory.GetFileSystemEntries(elsewhere).Length == 1 && File.ReadAllText(Path.Combine(elsewhere, "keep.txt")) == "not VGAC's", "G3: nothing was written through it");
        // a target without the marker, holding folders with the names VGAC uses, is not adopted on the strength of its names
        var s2 = Movable(); string to2 = Path.Combine(Tmp("target"), "g3b");
        StopMoveAt(s2, to2, "copied:python");
        File.Delete(Path.Combine(to2, DataFolders.MarkerName));
        string said2 = Mover(s2, null).Continue(new Progress());
        Check(said2 != null && !MoveRules.RestartPending(s2.Root), "G3: a resumed target without the marker is refused: " + said2);
        // copying never writes through a link in the destination
        string src = Tmp("g3-src"); Put(Path.Combine(src, "sub", "a.txt"), "x");
        string dst = Tmp("g3-dst"); string outside = Tmp("g3-out"); Junction(Path.Combine(dst, "sub"), outside);
        var rep = new FsReport(); SafeFs.CopyItem(src, dst, null, rep);
        Check(rep.Links.Count > 0 && Directory.GetFileSystemEntries(outside).Length == 0, "G3: the copy itself refuses to write through a link");
    }

    // ---- G4 ------------------------------------------------------------------------------------------------------------------------------

    static void G4ALibraryStepAsideThatWasCutOffIsPutBack()
    {
        string root = Tmp("g4"); string bin = Path.Combine(root, "starter", "bin"); Directory.CreateDirectory(bin);
        Put(Path.Combine(bin, "A.dll.old"), "the only copy of A");
        Put(Path.Combine(bin, "B.dll"), "B new"); Put(Path.Combine(bin, "B.dll.old"), "B old");
        BinAside.CleanupAtStart(root);
        Check(File.Exists(Path.Combine(bin, "A.dll")) && File.ReadAllText(Path.Combine(bin, "A.dll")) == "the only copy of A", "G4: a library whose plain name is missing gets its .old file back (the crash came between the rename and the checkout)");
        Check(!File.Exists(Path.Combine(bin, "B.dll.old")) && File.ReadAllText(Path.Combine(bin, "B.dll")) == "B new", "G4: and a stepped-aside copy is deleted only when the plain name is there");
        // a crash inside BinAside is a crash point of the update harness
        var seen = new List<string>();
        SafeFs.Hook = delegate(string what, string p) { if (what == "aside") seen.Add(p); };
        string proj = Tmp("g4-proj"); Directory.CreateDirectory(Path.Combine(proj, "starter", "bin")); File.WriteAllText(Path.Combine(proj, "starter", "bin", "X.dll"), "x");
        var done = new List<string>();
        BinAside.Aside(proj, new SpyGit(), done);
        SafeFs.Hook = null;
        Check(seen.Any(x => x.StartsWith("bin")), "G4: BinAside has crash points");
    }
}
