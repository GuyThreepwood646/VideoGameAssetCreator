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
        H1SharedStateLock();
        I1DiscardKeepsASwitchedRecord();
        I2SwitchedRecordWithoutNamesCanFinish();
        J1UpdateCrashKeepsAStartableExe();
        J2CommittedCopyMustMatchGit();
        K1NetworkFolderRefused();
        K2OldFolderLeftoversAreNamedNotDeleted();
        K3BinPromiseIsKeptOrSaid();
        K4ReadOnlyTargetIsReplaced();
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
        var res = RunShown(plan, new List<string> { "tools", "comfy" }, null, WindowLay(root), MachineFile.Load(root), Ledger.Load(root), s.Comfy, new FakeRecycler(), new SetupLog(root), null, null);
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

    // ---- I1: "Discard the record" never forgets a record that says the move has switched ------------------------------------------

    static void I1DiscardKeepsASwitchedRecord()
    {
        // a record VGAC cannot judge (an unknown name) that says "switching": discarding it is refused and the record stays
        var s = Movable();
        string jp = MoveRunner.JournalPath(s.Root);
        Directory.CreateDirectory(Path.GetDirectoryName(jp));
        string json = "{\"from\":\"" + s.Data.Replace("\\", "\\\\") + "\",\"to\":\"" + Tmp("i1-to").Replace("\\", "\\\\") + "\",\"state\":\"switching\",\"done\":[],\"names\":[\"bogus\"]}";
        File.WriteAllText(jp, json, Encoding.UTF8);
        string said = Mover(s, null).Abandon();
        Check(File.Exists(jp), "I1: a switching record that cannot be judged is not discarded: " + said);
        Check(said != null && said.Contains("kept"), "I1: the answer says the record is kept: " + said);
        Check(MoveGate.Blocking(s.Root) != null, "I1: a record with an unknown name keeps the start gate closed");

        // the same unjudgeable record while "copying" is still discarded, as before
        File.WriteAllText(jp, json.Replace("\"switching\"", "\"copying\""), Encoding.UTF8);
        Mover(s, null).Abandon();
        Check(!File.Exists(jp), "I1: a copying record that cannot be judged is still discarded");
    }

    // ---- I2: a switching record with no names does not dead-end once the settings name the new folder -------------------------

    static void I2SwitchedRecordWithoutNamesCanFinish()
    {
        var s = Movable();
        string jp = MoveRunner.JournalPath(s.Root);
        Directory.CreateDirectory(Path.GetDirectoryName(jp));
        string json = "{\"from\":\"" + Tmp("i2-from").Replace("\\", "\\\\") + "\",\"to\":\"" + s.Data.Replace("\\", "\\\\") + "\",\"state\":\"switching\",\"done\":[],\"names\":[]}";
        File.WriteAllText(jp, json, Encoding.UTF8);
        string said = Mover(s, null).Continue(new Progress());
        Check(said == null || !said.Contains("does not say what was copied"), "I2: a switched record with no names is not a dead end: " + said);
    }

    // ---- H1: the shared lock for machine.json and installed.json ------------------------------------------------------------------

    static void H1SharedStateLock()
    {
        string dir = Tmp("h1");
        bool ran = false;
        StateLock.Hold(dir, delegate { ran = true; Check(File.Exists(Path.Combine(dir, StateLock.FileName)), "H1: the lock file is there while the body runs"); });
        Check(ran && !File.Exists(Path.Combine(dir, StateLock.FileName)), "H1: the body runs once and the lock is released after it");

        // a fresh lock held by someone else: the save waits, then refuses plainly, and changes nothing
        string busy = Tmp("h1-busy"); Directory.CreateDirectory(busy);
        File.WriteAllText(Path.Combine(busy, StateLock.FileName), "other");
        int savedWait = StateLock.WaitMs; StateLock.WaitMs = 200;
        bool refused = false; bool bodyRan = false;
        try { StateLock.Hold(busy, delegate { bodyRan = true; }); }
        catch (StarterError e) { refused = e.Message.Contains("saving its settings"); }
        StateLock.WaitMs = savedWait;
        Check(refused && !bodyRan, "H1: a held lock refuses the save after the wait, and nothing is written");
        Check(File.Exists(Path.Combine(busy, StateLock.FileName)), "H1: the other holder's lock is not removed");

        // a lock left by a process that died is broken after the stale time
        string stale = Tmp("h1-stale"); Directory.CreateDirectory(stale);
        string sf = Path.Combine(stale, StateLock.FileName);
        File.WriteAllText(sf, "dead");
        File.SetLastWriteTimeUtc(sf, DateTime.UtcNow.AddMinutes(-5));
        bool staleRan = false;
        StateLock.Hold(stale, delegate { staleRan = true; });
        Check(staleRan, "H1: a lock older than the stale time is broken and the save goes on");

        // unique temporary names: a write never uses the fixed name path + ".tmp"
        string w = Path.Combine(Tmp("h1-tmp"), "installed.json");
        SafeFs.WriteAtomic(w, "{}", null);
        Check(File.Exists(w) && File.ReadAllText(w) == "{}", "H1: the atomic write lands");
        Check(!File.Exists(w + ".tmp") && Directory.GetFiles(Path.GetDirectoryName(w)).Length == 1, "H1: no fixed .tmp name is left behind");
    }

    // ---- J1: an update that is cut off at any point leaves a startable VGAC.exe -------------------------------------------------

    // A stand-in for git: `show HEAD:VGAC.exe` writes the committed copy; the heads differ when `moved` (the pull brought something).
    class CrashGit : IGit
    {
        public bool Moved; public bool Mismatch;
        public int RunToFile(string a, string c, string f) { File.WriteAllText(f, "MZ-HEAD-EXE"); return 0; }
        public int Run(string a, string c, out string o)
        {
            o = "";
            if (a.StartsWith("rev-parse HEAD:") || a.StartsWith("hash-object")) { o = Mismatch ? (a.StartsWith("hash-object") ? "bad" : "good") : "good"; return 0; }
            if (a.StartsWith("rev-parse HEAD")) { o = Moved && pulled ? "new\n" : "old\n"; return 0; }
            if (a.StartsWith("pull")) { pulled = true; return 0; }
            return 0;
        }
        bool pulled;
    }

    // B3: a read-only file in the way is cleared and replaced; nothing is left behind
    static void K4ReadOnlyTargetIsReplaced()
    {
        string dir = Tmp("k4"); Directory.CreateDirectory(dir);
        string p = Path.Combine(dir, "settings.json"); File.WriteAllText(p, "old");
        File.SetAttributes(p, FileAttributes.ReadOnly);
        SafeFs.WriteAtomic(p, "new", null);
        Check(File.ReadAllText(p) == "new" && !File.Exists(p + ".tmp"), "K4: a read-only file is replaced by the write, with no temporary file left");
    }

    // A8: an item the summary promised to the Recycle Bin is never deleted outright when the bin refuses it
    static void K3BinPromiseIsKeptOrSaid()
    {
        string dir = Tmp("k3"); Directory.CreateDirectory(dir);
        string file = Path.Combine(dir, "model.bin"); File.WriteAllText(file, "kept");
        var plan = new RemovalPlan();
        var g = new RemovalGroup { Id = "models", Title = "models", Sentence = "x" };
        var it = new RemovalItem { Id = "file:" + file, GroupId = "models", Kind = "ledger-file", Path = file, What = "model", IsFile = true, Bytes = 4, Tag = "4" };
        g.Items.Add(it); plan.Groups.Add(g);
        var refuses = new FakeRecycler { Refuse = true };
        var res = RunShown(plan, new List<string> { "models" }, null, Layout.From(dir, new MachineFile()), new MachineFile(), new Ledger(), null, refuses, new SetupLog(dir), null, null);
        Check(it.BinShown == true, "K3: the summary promised the Recycle Bin for this item");
        Check(File.Exists(file) && res.RecycleFailed.Contains(file), "K3: the bin refused, so the file is listed as not removed and is still there: " + string.Join("|", res.Skipped.ToArray()));
        // an item that was never shown is not removed at all
        string file2 = Path.Combine(dir, "other.bin"); File.WriteAllText(file2, "kept");
        var g2 = new RemovalGroup { Id = "models", Title = "models", Sentence = "x" };
        g2.Items.Add(new RemovalItem { Id = "file:" + file2, GroupId = "models", Kind = "ledger-file", Path = file2, What = "other", IsFile = true, Bytes = 4, Tag = "4" });
        var plan2 = new RemovalPlan(); plan2.Groups.Add(g2);
        RemovalRunner.Run(plan2, new List<string> { "models" }, null, Layout.From(dir, new MachineFile()), new MachineFile(), new Ledger(), null, new FakeRecycler(), new SetupLog(dir), null, null);
        Check(File.Exists(file2), "K3: an item that was not shown in the summary is not removed");
    }

    // A5: after the switch, what the move did not copy in the old folder is named and kept; "delete by hand" only when nothing is left
    static void K2OldFolderLeftoversAreNamedNotDeleted()
    {
        var s = Movable();
        Put(Path.Combine(s.Data, "their-notes.txt"), "not VGAC's");
        string to = Path.Combine(Tmp("k2-target"), "k2");
        Check(Mover(s, null).Start(to, new Progress()) == null, "K2: the move starts with a foreign file in the old folder");
        string text = MoveView.RestartText(s.Root);
        Check(text.Contains("their-notes.txt") && text.Contains("keep them") && !text.Contains("delete it by hand"), "K2: the leftover is named and kept, not offered for deletion: " + text);
        var s2 = Movable(); string to2 = Path.Combine(Tmp("k2-target"), "k2b");
        File.Delete(Path.Combine(s2.Data, "keepme.txt"));   // nothing left in the old folder except what the move copied
        Mover(s2, null).Start(to2, new Progress());
        string text2 = MoveView.RestartText(s2.Root);
        Check(text2.Contains("delete it by hand") && !text2.Contains("keep them"), "K2: with nothing left the old folder may be deleted by hand: " + text2);
    }

    // A6: a network folder is refused as a home for the big files
    static void K1NetworkFolderRefused()
    {
        string from = Tmp("k1-from"); string root = Tmp("k1-root");
        var unc = MoveRules.Problems(from, "\\\\server\\share\\bigfiles", root, -1, 0, null, null);
        Check(unc.Exists(x => x.Contains("network folder")), "K1: a UNC path is refused as a home for the big files: " + string.Join(" | ", unc.ToArray()));
    }

    // A3: the committed copy is checked against git before it replaces the running VGAC.exe
    static void J2CommittedCopyMustMatchGit()
    {
        string root = Tmp("j2"); string exe = Path.Combine(root, "VGAC.exe"); File.WriteAllText(exe, "OLD-EXE");
        var res = GitUpdate.Pull(root, exe, new CrashGit { Mismatch = true });
        Check(!res.Updated && File.ReadAllText(exe) == "OLD-EXE" && !File.Exists(Path.Combine(root, "VGAC.head.exe")), "J2: a committed copy that does not match git is refused; VGAC.exe is unchanged: " + res.Message);
        Check(res.Message != null && res.Message.Contains("Nothing was changed"), "J2: the answer says nothing was changed");
    }

    static void J1UpdateCrashKeepsAStartableExe()
    {
        string[] points = { "head-written", "exe-record", "exe-replaced", "pull-start", "pull-done" };
        foreach (bool moved in new[] { false, true })
        {
            foreach (string point in points)
            {
                string root = Tmp("j1-" + point + (moved ? "-moved" : "-same"));
                string exe = Path.Combine(root, "VGAC.exe");
                File.WriteAllText(exe, "OLD-EXE");
                var git = new CrashGit { Moved = moved };
                SafeFs.Hook = delegate(string what, string p) { if (what == "update" && p == point) throw new InvalidOperationException("cut off at " + point); };
                try { GitUpdate.Pull(root, exe, git); }
                catch (InvalidOperationException) { }
                finally { SafeFs.Hook = null; }
                bool startable = File.Exists(exe) && new FileInfo(exe).Length > 0;
                string now = startable ? File.ReadAllText(exe) : "";
                Check(startable && (now == "OLD-EXE" || now == "MZ-HEAD-EXE"), "J1: cut off at '" + point + "' (moved " + moved + "): VGAC.exe is still there and startable (now " + now + ")");
            }
        }
        // a whole update with nothing cut off: the committed copy is taken when the branch moved; the person's own build comes back when it did not
        string r1 = Tmp("j1-whole-moved"); string e1 = Path.Combine(r1, "VGAC.exe"); File.WriteAllText(e1, "OLD-EXE");
        var res1 = GitUpdate.Pull(r1, e1, new CrashGit { Moved = true });
        Check(res1.Updated && File.ReadAllText(e1) == "MZ-HEAD-EXE", "J1: a whole update that moved the branch runs the new VGAC.exe");
        string r2 = Tmp("j1-whole-same"); string e2 = Path.Combine(r2, "VGAC.exe"); File.WriteAllText(e2, "OWN-BUILD");
        var res2 = GitUpdate.Pull(r2, e2, new CrashGit { Moved = false });
        Check(!res2.Updated && File.ReadAllText(e2) == "OWN-BUILD", "J1: when nothing came, the person's own VGAC.exe is back exactly as it was");
    }

    // Answers `ls-files --error-unmatch` like git does: 0 for a listed path, 1 for any other.
    class TrackGit : IGit
    {
    public int RunToFile(string a, string c, string f) { return -1; }
        readonly string[] tracked;
        public TrackGit(params string[] paths) { tracked = paths; }
        public int Run(string args, string cwd, out string output)
        {
            output = "";
            if (!args.StartsWith("ls-files")) return -1;
            foreach (string t in tracked) if (args.Contains(t)) return 0;
            return 1;
        }
    }

    static void G4ALibraryStepAsideThatWasCutOffIsPutBack()
    {
        string root = Tmp("g4"); string bin = Path.Combine(root, "starter", "bin"); Directory.CreateDirectory(bin);
        Put(Path.Combine(bin, "A.dll.old"), "the only copy of A");
        Put(Path.Combine(bin, "B.dll"), "B new"); Put(Path.Combine(bin, "B.dll.old"), "B old");
        BinAside.CleanupAtStart(root, new TrackGit("starter/bin/A.dll", "starter/bin/B.dll"));
        Check(File.Exists(Path.Combine(bin, "A.dll")) && File.ReadAllText(Path.Combine(bin, "A.dll")) == "the only copy of A", "G4: a library whose plain name is missing gets its .old file back when git still tracks it (the crash came between the rename and the checkout)");
        Check(!File.Exists(Path.Combine(bin, "B.dll.old")) && File.ReadAllText(Path.Combine(bin, "B.dll")) == "B new", "G4: and a stepped-aside copy is deleted only when the plain name is there");
        // G4b: a library the owner removed on purpose (git does not track it) is NOT restored; its .old copy is kept, not deleted
        string rm = Tmp("g4b"); string rbin = Path.Combine(rm, "starter", "bin"); Directory.CreateDirectory(rbin);
        Put(Path.Combine(rbin, "Gone.dll.old"), "removed on purpose");
        BinAside.CleanupAtStart(rm, new TrackGit("starter/bin/A.dll"));
        Check(!File.Exists(Path.Combine(rbin, "Gone.dll")) && File.Exists(Path.Combine(rbin, "Gone.dll.old")), "G4b: an untracked library is not brought back, and its copy is left alone");
        // G4c: with no answer from git, nothing is restored either
        string nm = Tmp("g4c"); string nbin = Path.Combine(nm, "starter", "bin"); Directory.CreateDirectory(nbin);
        Put(Path.Combine(nbin, "Keep.dll.old"), "only copy");
        BinAside.CleanupAtStart(nm, new NoGit());
        Check(!File.Exists(Path.Combine(nbin, "Keep.dll")) && File.Exists(Path.Combine(nbin, "Keep.dll.old")), "G4c: without git's answer the copy is kept, not restored");
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
