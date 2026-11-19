// The tenth review: resume with uv's own junctions, From gone, content that differs at the same size and time, a gate that fails closed.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Vgac.Starter;

public static partial class T
{
    static void Safety7All()
    {
        T10ResumeWithAnInternalJunction();
        T10FromMissingIsNeverSwitchedTo();
        T10SameSizeAndTimeButOtherContent();
        T10TheGateFailsClosed();
        T10TheMarkerOfTheNewComfyUIIsNotCopiedOverAgain();
        T10TheWindowSaysToKeepTheOldFolderAfterFinishAnyway();
        T10ChoosingAFolderIsGated();
    }

    // uv's own Python folder holds junctions between its versions; the fixture has one
    static InstallScene MovableWithJunction()
    {
        var s = Movable();
        Junction(Path.Combine(s.Data, "python", "cpython-3.13-windows"), Path.Combine(s.Data, "python", "cpython-3.13"));
        return s;
    }

    static void T10ResumeWithAnInternalJunction()
    {
        // interrupted after python (with its junction) was copied, then Continue from "copying"
        var s = MovableWithJunction(); string to = Path.Combine(Tmp("target"), "t1a");
        StopMoveAt(s, to, "copied:uv-cache");
        Check(SafeFs.IsLink(Path.Combine(to, "python", "cpython-3.13-windows")), "(the junction was made in the new place)");
        string said = Mover(s, null).Continue(new Progress());
        Check(said == null && MoveRules.RestartPending(s.Root), "T1: Continue is not refused by the junction it made itself (from copying): " + said);
        // and from "switching", where there is no Abandon
        var s2 = MovableWithJunction(); string to2 = Path.Combine(Tmp("target"), "t1b");
        StopMoveAt(s2, to2, "journal:switching");
        string said2 = Mover(s2, null).Continue(new Progress());
        Check(said2 == null && MoveRules.RestartPending(s2.Root), "T1: and from switching: " + said2);
        // a link that leads OUTSIDE the item in the new place is still refused
        var s3 = MovableWithJunction(); string to3 = Path.Combine(Tmp("target"), "t1c");
        StopMoveAt(s3, to3, "copied:uv-cache");
        string outside = Tmp("t1-outside"); Put(Path.Combine(outside, "x.txt"), "x");
        Junction(Path.Combine(to3, "python", "sneaky"), outside);
        string said3 = Mover(s3, null).Continue(new Progress());
        Check(said3 != null && said3.Contains("link") && !MoveRules.RestartPending(s3.Root), "T1: a link leading outside is refused: " + said3);
    }

    static void T10FromMissingIsNeverSwitchedTo()
    {
        foreach (string how in new[] { "renamed", "marker gone", "a folder gone" })
        {
            var s = Movable(); string to = Path.Combine(Tmp("target"), "t2-" + how.Replace(" ", ""));
            StopMoveAt(s, to, "journal:switching");
            string from = s.Data;
            if (how == "renamed") Directory.Move(from, from + "-away");
            else if (how == "marker gone") File.Delete(Path.Combine(from, DataFolders.MarkerName));
            else SafeFs.DeleteTree(Path.Combine(from, "python"));
            string said = Mover(s, null).Continue(new Progress());
            Check(said != null && !MoveRules.RestartPending(s.Root), "T2 (" + how + "): Continue refuses, does not switch and does not ask for a restart: " + said);
        }
    }

    static void T10SameSizeAndTimeButOtherContent()
    {
        var s = Movable(); string to = Path.Combine(Tmp("target"), "t4");
        StopMoveAt(s, to, "journal:switching");
        string src = Path.Combine(s.Data, "python", "f1.bin"), dst = Path.Combine(to, "python", "f1.bin");
        var bytes = File.ReadAllBytes(dst); bytes[0] ^= 0xFF; File.WriteAllBytes(dst, bytes);
        File.SetLastWriteTimeUtc(dst, File.GetLastWriteTimeUtc(src));
        Check(new FileInfo(dst).Length == new FileInfo(src).Length && File.GetLastWriteTimeUtc(dst) == File.GetLastWriteTimeUtc(src), "(same size, same time, other content)");
        string said = null;
        for (int i = 0; i < 3; i++) { said = Mover(s, null).Continue(new Progress()); if (said == null) break; }
        Check(said == null && Hashing.Sha256File(src) == Hashing.Sha256File(dst), "T4: a file that fails the check is copied again whatever its size and time say: " + said);
    }

    static void T10TheGateFailsClosed()
    {
        var s = Movable(); string root = s.Root;
        Check(MoveGate.Blocking(root) == null, "(no journal: not blocked)");
        string jp = MoveRunner.JournalPath(root); Directory.CreateDirectory(Path.GetDirectoryName(jp));
        foreach (string text in new[] { "{ not json", "{\"from\":\"x\",\"to\":\"y\",\"state\":\"from-a-newer-version\",\"done\":[]}", "" })
        {
            File.WriteAllText(jp, text);
            string gate = MoveGate.Blocking(root);
            Check(gate != null && gate.Contains("cannot"), "T5: a record that exists but cannot be judged blocks: [" + text + "] " + gate);
        }
        // the way out: the window's discard (Abandon) forgets it, and the app may run again
        string said = Mover(s, null).Abandon();
        Check(said != null && !File.Exists(jp) && MoveGate.Blocking(root) == null, "T5: discarding the record is the way out: " + said);
        // another place that cannot be reached
        var s2 = Movable(); string to = Path.Combine(Tmp("target"), "t5"); StopMoveAt(s2, to, "journal:switching");
        string j2 = File.ReadAllText(MoveRunner.JournalPath(s2.Root)).Replace(to.Replace("\\", "\\\\"), "Q:\\\\not\\\\there");
        File.WriteAllText(MoveRunner.JournalPath(s2.Root), j2);
        Check(MoveGate.Blocking(s2.Root) != null, "T5: a record whose other place is not reachable still blocks");
    }

    static void T10TheMarkerOfTheNewComfyUIIsNotCopiedOverAgain()
    {
        var s = Movable(); string to = Path.Combine(Tmp("target"), "t6");
        StopMoveAt(s, to, "journal:switching");
        string marker = Path.Combine(to, "comfyui", ComfyGuard.MarkerName);
        File.WriteAllText(marker, "{\"made_by\":\"VGAC\",\"steps\":{\"venv\":\"half built\"}}");
        Mover(s, null).Continue(new Progress());
        Check(File.ReadAllText(marker).Contains("half built"), "T6: the installer's own record in the new place is not overwritten by the old one on Continue");
    }

    static void T10TheWindowSaysToKeepTheOldFolderAfterFinishAnyway()
    {
        var s = Movable(); string to = Path.Combine(Tmp("target"), "t3");
        s.Comfy.RetargetError = s.Comfy.RetargetBackError = "The settings could not be updated.";
        Mover(s, null).Start(to, new Progress());
        Check(Mover(s, null).ContinueAnyway(new Progress()) == null, "(finished anyway)");
        string text = MoveView.RestartText(s.Root);
        Check(text.Contains("keep") && text.Contains(Path.Combine(s.Data, "comfyui")) && !text.Contains("can delete"), "T3: after 'Finish anyway' the window says to KEEP the old folder, not that it can be deleted: " + text);
        // item 3: the leftover copy in the new folder keeps its files, loses its marker, and the window names it with its size
        string leftover = Path.Combine(to, "comfyui");
        Check(!File.Exists(Path.Combine(leftover, ComfyGuard.MarkerName)), "I3: the leftover ComfyUI copy in the new folder has no marker any more");
        Check(Directory.Exists(leftover) && Directory.GetFileSystemEntries(leftover).Length > 0, "I3: the leftover copy's files are not deleted");
        Check(text.Contains(leftover) && text.Contains("leftover copy of ComfyUI") && text.Contains("MB") && text.Contains("by hand"), "I3: the window names the leftover copy, its size and that it can be deleted by hand: " + text);
        var s2 = Movable(); string to2 = Path.Combine(Tmp("target"), "t3b");
        Mover(s2, null).Start(to2, new Progress());
        string text2 = MoveView.RestartText(s2.Root);
        Check(text2.Contains("delete it by hand") && !text2.Contains("keep"), "T3: after an ordinary move it says the old folder can be deleted: " + text2);
    }

    static void T10ChoosingAFolderIsGated()
    {
        var s = Movable(); string other = Path.Combine(Tmp("chosen"), "dev");
        Check(Proof.ChoiceProblem(other, s.Root) == null, "(an ordinary choice is fine)");
        StopMoveAt(s, Path.Combine(Tmp("target"), "t8"), "journal:switching");
        string why = Proof.ChoiceProblem(other, s.Root);
        Check(why != null && why.Contains("move"), "T8: choosing another folder for the big files is refused while a move is unfinished: " + why);
        Check(LauncherRules.ErrorOffersChoose(new StarterError("The folder for the big files (X) is, or lies under, a link.", "Choose a real folder (Choose another folder).")), "T9: a setup error that says to choose another folder offers the Choose button");
        Check(!LauncherRules.ErrorOffersChoose(new StarterError("Git is not installed.", "Install it.")), "and other errors do not");
    }
}
