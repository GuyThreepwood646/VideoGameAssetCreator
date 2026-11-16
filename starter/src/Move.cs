// Moving the big files (the data folder) to another folder or drive. Copy, check, switch, restart: nothing else.
//
//   1. The target is checked (no link anywhere in its path, empty, room, not inside the old place or the project).
//   2. The folders VGAC made are copied and every file is compared (size and SHA-256). An interrupted copy is continued where it
//      stopped (a file already copied is not copied again); it can also be abandoned, which only forgets the journal: what was copied so
//      far stays in the new folder (its path is said) and is the person's to delete. Move deletes nothing, ever.
//   3. The settings, the ledger and VGAC's own ComfyUI (through its installer) are switched to the new place, and VGAC must be restarted:
//      until it is, the window offers nothing else.
//   4. The old folder is left as it is. It is no longer used and the person may delete it by hand. To go back, move again.
//
// Two journal states only (local/move.json): "copying" and "switching". There is no undo, no cancel after the switch, no deleting of the
// old copy. C# 5 (see Core.cs).
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace Vgac.Starter
{
    public class MoveJournal
    {
        public string From, To;
        public string State = "copying";                    // copying | switching
        public string Id = "";                              // this move: written into the new place's marker, so a copy of another move is never continued
        public List<string> Done = new List<string>();      // names completely copied and linked
    }

    public class MoveItem
    {
        public string Name;     // relative to the data folder, always one of the fixed names below
        public string Kind;     // what proves it
        public MoveItem(string name, string kind) { Name = name; Kind = kind; }
    }

    public static class MoveRules
    {
        // The only things in the data folder that VGAC makes. Each is moved only when it is proven VGAC's.
        public static readonly MoveItem[] Items = {
            new MoveItem("tools\\uv", "uv"), new MoveItem("tools\\node", "node"), new MoveItem("python", "python"),
            new MoveItem("uv-cache", "uv-cache"), new MoveItem("downloads", "downloads"), new MoveItem("webview2", "webview2"), new MoveItem("comfyui", "comfyui")
        };

        public static bool IsKnownName(string name)
        {
            foreach (MoveItem i in Items) if (string.Equals(i.Name, name, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }

        // Written when the settings have been switched: from then until VGAC is started again, the window offers nothing but Restart.
        public static string RestartPath(string root) { return Path.Combine(root, "local", "restart-needed.json"); }
        public static bool RestartPending(string root) { return File.Exists(RestartPath(root)); }

        // A fixed-name folder that is there (the app's own ComfyUI also needs the backend's install marker, which the backend writes).
        static bool PresentAt(string folder, MoveItem it)
        {
            string p = Path.Combine(folder, it.Name);
            if (!Directory.Exists(p) || SafeFs.IsLink(p)) return false;
            return it.Kind != "comfyui" || ComfyGuard.MarkerPresent(p);
        }

        // The VGAC folders present in `folder`, by name. The caller has already checked that the folder is VGAC's (its marker).
        public static List<string> NamesIn(string folder)
        {
            var res = new List<string>();
            foreach (MoveItem it in Items) if (PresentAt(folder, it)) res.Add(it.Name);
            return res;
        }

        public static string SkipFor(string name) { return name == "comfyui" ? ".venv" : null; }

        // True when a link is in the path of `p`, or of the nearest folder above it that exists (a target that is not made yet can still lie
        // under a junction).
        public static bool LinkInPath(string p)
        {
            string e = p;
            while (e != null && !Directory.Exists(e)) e = Path.GetDirectoryName(e);
            return e != null && (SafeFs.IsLink(e) || !SafeFs.PathHasNoLink(e));
        }

        // Plain problems with a chosen folder (empty list: fine). `resuming`: the journal of the move that is being continued; only its own
        // unfinished copy may already be in the folder.
        public static List<string> Problems(string from, string to, string projectRoot, long freeBytes, long needBytes, MoveJournal resuming, IComfyInstall comfy)
        {
            var bad = new List<string>();
            if (string.IsNullOrWhiteSpace(to)) { bad.Add("Choose a folder."); return bad; }
            string t;
            try { t = Path.GetFullPath(to); } catch (Exception) { bad.Add("That is not a usable folder name."); return bad; }
            if (SafeFs.Norm(t) == SafeFs.Norm(from)) bad.Add("That is where the big files already are.");
            else if (SafeFs.Inside(t, from)) bad.Add("The new folder cannot be inside the old one.");
            else if (SafeFs.Inside(from, t)) bad.Add("The new folder cannot contain the old one.");
            if (SafeFs.Inside(projectRoot, t)) bad.Add("The new folder cannot contain the project folder.");
            else if (SafeFs.Inside(t, projectRoot) && !SafeFs.Inside(t, Path.Combine(projectRoot, "local"))) bad.Add("Inside the project folder, only its local folder can hold the big files.");
            if (LinkInPath(t)) bad.Add("That folder is, or lies under, a link (a junction or a mapped folder). Choose a real folder.");
            if (t.Length > 110) bad.Add("That folder's name is too long: ComfyUI's files have long names and Windows would stop at its limit. Choose a shorter path.");
            string shared = Proof.DataFolderProblem(t, null);
            if (shared != null && bad.Count == 0) bad.Add(shared);
            if (Directory.Exists(t) && Directory.GetFileSystemEntries(t).Length > 0)
            {
                bool ours = resuming != null && SafeFs.Norm(resuming.To) == SafeFs.Norm(t);
                if (ours)
                {
                    foreach (string e in Directory.GetFileSystemEntries(t))
                    {
                        string n = Path.GetFileName(e);
                        if (string.Equals(n, DataFolders.MarkerName, StringComparison.OrdinalIgnoreCase) || string.Equals(n, DataFolders.MarkerName + ".tmp", StringComparison.OrdinalIgnoreCase)) continue;
                        bool known = false;
                        foreach (MoveItem it in Items) if (string.Equals(it.Name.Split('\\')[0], n, StringComparison.OrdinalIgnoreCase)) known = true;
                        if (!known) { bad.Add("That folder has files in it that VGAC did not put there (" + n + ")."); break; }
                    }
                    string link = SafeFs.FirstLink(t, null);
                    if (link != null) bad.Add("The new folder holds a link (" + link + "), so VGAC will not copy into it. Remove the link, or abandon the move.");
                    string mid = DataFolders.ReadMarkerMove(t);
                    if (mid != null && mid != resuming.Id) bad.Add("That folder holds the unfinished copy of another move, not this one.");
                }
                else bad.Add("That folder already has files in it. Choose an empty or new folder.");
            }
            if (freeBytes >= 0 && freeBytes < needBytes + needBytes / 10) bad.Add("The copy needs about " + RemovalSummary.Size(needBytes + needBytes / 10) + "; only " + RemovalSummary.Size(freeBytes) + " is free there.");
            // VGAC's own ComfyUI: moved only when the backend's installer confirms it is VGAC's and can be handled; else the move is refused.
            if (comfy != null && ComfyGuard.MarkerPresent(comfy.Root(from)))
            {
                string cwhy = comfy.State(from).Problem;
                if (cwhy != null) bad.Add("VGAC's own ComfyUI is in this folder, and its installer did not confirm that it can be moved: " + cwhy + " Nothing was changed.");
            }
            // a folder that holds a link leading outside it is not moved at all rather than losing what it points to
            foreach (string name in NamesIn(from))
            {
                // links between places inside the same folder (uv's Python folder keeps some) are copied as links; any other is refused
                string link = SafeFs.FirstExternalLink(Path.Combine(from, name), SkipFor(name));
                if (link != null) { bad.Add("There is a link inside " + name + " (" + link + ") that leads outside it. VGAC does not copy such links, so it will not move this folder. Replace the link with the real files first."); break; }
            }
            return bad;
        }

        // Programs that have files open in the folder to be moved (by program path or command line). Never stopped by VGAC.
        public static List<string> Blockers(string folder, IProcessSource procs, int ownPid)
        {
            var res = new List<string>();
            string f = SafeFs.Norm(folder);
            foreach (ProcInfo p in procs.List())
            {
                if (p.Pid == ownPid) continue;
                string exe = (p.ExePath ?? "").ToLowerInvariant(), cmd = (p.CommandLine ?? "").ToLowerInvariant();
                if (exe.StartsWith(f + "\\") || cmd.Contains(f + "\\")) res.Add(p.Name + " (PID " + p.Pid + ")");
            }
            return res;
        }
    }

    // THE rule: while a move is unfinished, or has switched and VGAC has not been restarted, nothing runs on either place. The app and its
    // engines do not start, and Remove, Repair, Update and setup refuse with this sentence. (Only Continue, Abandon and Restart work.) Read
    // from the files each time; null when nothing blocks.
    public static class MoveGate
    {
        public static string Blocking(string root)
        {
            try
            {
                if (MoveRules.RestartPending(root)) return "VGAC has been moved to a new folder for the big files and has to be restarted first. Close it and start it again.";
                MoveJournal j = MoveRunner.Load(root, Layout.From(root, MachineFile.Load(root)).DataFolder);
                if (j == null) return null;
                return "A move of the big files is not finished (" + (j.State == "switching" ? "it is switching to the new folder" : "it was copying") + "). Until it is, nothing else runs: open Maintenance, Move the big files, and continue it" + (j.State == "copying" ? ", or abandon it" : "") + ".";
            }
            catch (Exception) { return null; }
        }
    }

    // What the window shows about a move, read fresh from the files each time (never from a layout loaded earlier).
    public static class MoveView
    {
        public static string OldFolderSentence(string oldFolder)
        {
            return "The old folder (" + oldFolder + ") is no longer used by VGAC. You can delete it by hand once you are happy with the new place.";
        }
    }

    public class MoveRunner
    {
        readonly Layout l; readonly IComfyInstall comfy; readonly IProcessSource procs; readonly SetupLog log;
        public Func<string, long> Free = delegate(string f) { try { return new DriveInfo(Path.GetPathRoot(Path.GetFullPath(f))).AvailableFreeSpace; } catch (Exception) { return -1; } };
        public Func<int> OwnPid = delegate { return System.Diagnostics.Process.GetCurrentProcess().Id; };

        // The layout given only says where the project is: which data folder is in use is read from the settings file at each use.
        public MoveRunner(Layout layout, MachineFile machine, Ledger ledger, IComfyInstall comfy, IProcessSource procs, SetupLog log)
        {
            l = layout; this.comfy = comfy; this.procs = procs; this.log = log;
        }

        string Root { get { return l.ProjectRoot; } }
        string DataNow() { return Layout.From(Root, MachineFile.Load(Root)).DataFolder; }

        public static string JournalPath(string root) { return Path.Combine(root, "local", "move.json"); }

        // The journal, or null. A journal that is not a plain list of this program's own fixed names, or whose places do not fit the
        // current settings, is ignored (StaleFound says so); nothing is renamed or written by reading it.
        [ThreadStatic] public static bool StaleFound;

        public static MoveJournal Load(string root) { return Load(root, null); }

        public static MoveJournal Load(string root, string currentData)
        {
            StaleFound = false;
            string p = JournalPath(root);
            if (!File.Exists(p)) return null;
            MoveJournal j;
            try
            {
                var d = Json.ParseObject(File.ReadAllText(p, Encoding.UTF8));
                j = new MoveJournal();
                j.From = Convert.ToString(d["from"]); j.To = Convert.ToString(d["to"]); j.State = Convert.ToString(d["state"]);
                object mid; if (d.TryGetValue("id", out mid) && mid != null) j.Id = Convert.ToString(mid);
                foreach (object o in (System.Collections.IEnumerable)d["done"]) j.Done.Add(Convert.ToString(o));
            }
            catch (Exception) { return null; }
            if (Validate(j, currentData) != null) { StaleFound = true; return null; }
            return j;
        }

        static string Validate(MoveJournal j, string currentData)
        {
            if (j.State != "copying" && j.State != "switching") return "unknown state";
            if (!Path.IsPathRooted(j.From ?? "") || !Path.IsPathRooted(j.To ?? "")) return "places are not absolute";
            foreach (string n in j.Done) if (!MoveRules.IsKnownName(n)) return "unknown name";
            if (currentData != null)
            {
                string cur = SafeFs.Norm(currentData);
                bool usingFrom = cur == SafeFs.Norm(j.From), usingTo = cur == SafeFs.Norm(j.To);
                if (!usingFrom && !usingTo) return "places do not fit the settings";
                if (j.State == "copying" && !usingFrom) return "the settings already use the new place";
                string other = usingFrom ? j.To : j.From;
                if (Proof.DataFolderProblem(other, null) != null) return "the other place is not an acceptable folder";
                if (SafeFs.Inside(other, currentData) || SafeFs.Inside(currentData, other)) return "the two places overlap";
            }
            return null;
        }

        void Save(MoveJournal j)
        {
            var d = new Dictionary<string, object>();
            d["from"] = j.From; d["to"] = j.To; d["state"] = j.State; d["done"] = j.Done; d["id"] = j.Id ?? "";
            SafeFs.WriteAtomic(JournalPath(Root), Json.Write(d), null);
            SafeFs.Point("move", "journal:" + j.State);
        }

        void DeleteJournal()
        {
            try { File.Delete(JournalPath(Root)); } catch (Exception) { }
            SafeFs.Point("move", "journal-deleted");
        }

        MoveJournal Current() { return Load(Root, DataNow()); }

        // The settings file and the ledger are read again for every change: the backend's commands rewrite them too.
        void SetDataFolder(string place)
        {
            var m = MachineFile.Load(Root);
            m.Set("starter", "dataFolder", Layout.Stored(Root, place));
            m.Save();
            SafeFs.Point("move", "settings:" + place);
        }

        void RewriteLedger(string oldRoot, string newRoot)
        {
            Ledger fresh = Ledger.Load(Root);
            fresh.RewritePaths(oldRoot, newRoot);
            fresh.Save();
            SafeFs.Point("move", "ledger:" + newRoot);
        }

        public long NeedBytes()
        {
            long n = 0;
            string data = DataNow();
            foreach (string name in MoveRules.NamesIn(data)) n += SafeFs.Size(Path.Combine(data, name), null);
            return n;
        }

        // Begins a move to `to` (null when it ran to the end; else one plain sentence).
        public string Start(string to, Progress prog)
        {
            if (MoveRules.RestartPending(Root)) return "VGAC has been moved and has to be restarted first. Close it and start it again, then move again if you want to.";
            if (Current() != null) return "A move is already under way. Continue it or abandon it first.";
            string from = DataNow();
            // Only a folder shown to be VGAC's (its marker matches this install's settings) is ever moved.
            DataStatus ds = DataFolders.Check(from, MachineFile.Load(Root), Root);
            if (ds.State != DataState.Ours) return DataFolders.ExplainNotClaimed(from, ds) + " Nothing was changed. Use 'Use this folder for VGAC only' in Check and repair if it holds only VGAC's own folders, or choose another folder.";
            if (MoveRules.NamesIn(from).Count == 0) return "There is nothing of VGAC's in " + from + " to move. Nothing was changed.";
            string full = Path.GetFullPath(to);
            List<string> bad = MoveRules.Problems(from, full, Root, Free(full), NeedBytes(), null, comfy);
            if (bad.Count > 0) return string.Join(" ", bad.ToArray());
            List<string> blockers = MoveRules.Blockers(from, procs, OwnPid());
            if (blockers.Count > 0) return "Close these first, they have files open in the old folder: " + string.Join(", ", blockers.ToArray()) + ".";
            var j = new MoveJournal(); j.From = from; j.To = full; j.Id = Guid.NewGuid().ToString("N");
            Save(j);
            log.Line("move: started " + j.From + " -> " + j.To);
            return Continue(prog);
        }

        // Carries on from the journal (also after an interruption). null when the move is complete and VGAC has to be restarted.
        public string Continue(Progress prog) { return ContinueCore(prog, false); }

        // The way out of a switch that cannot finish because ComfyUI's installer keeps refusing: the move ends WITHOUT moving VGAC's own
        // ComfyUI, which then keeps running from the old folder (the ledger says so again).
        public string ContinueAnyway(Progress prog) { return ContinueCore(prog, true); }

        string ContinueCore(Progress prog, bool force)
        {
            var j = Current();
            if (j == null) return MoveRunner.StaleFound ? "The record of an earlier move does not fit this install, so it is ignored. Nothing was changed." : "There is no move to continue.";
            if (force && j.State != "switching") return "'Finish anyway' only applies to a move that is already switching.";
            // Whatever the state, nothing earlier is trusted: the old place may have changed since it was copied (nothing runs there
            // while a move is unfinished, but a crash or a hand edit can still leave it different), and the new place may have been
            // damaged. Files already identical are not copied again; extra files in the new place are ours (its marker says so) and are
            // not a mismatch.
            List<string> foreignFiles = MoveRules.Problems(j.From, j.To, Root, -1, 0, j, j.State == "switching" ? null : comfy);
            if (foreignFiles.Count > 0) return string.Join(" ", foreignFiles.ToArray());
            List<string> blockers = MoveRules.Blockers(j.From, procs, OwnPid());
            if (blockers.Count > 0) return "Close these first, they have files open in the old folder: " + string.Join(", ", blockers.ToArray()) + ".";
            string r = EnsureStamped(j);
            j.Done.Clear();
            if (r == null) r = CopyAll(j, prog);
            if (r == null) r = VerifyAll(j, prog);
            if (r != null) return r;
            if (j.State == "copying") { j.State = "switching"; Save(j); }
            return Finish(j, force);
        }

        // The new place carries this install's marker with this move's id (written first thing, and again by Continue when a crash came
        // before it). A folder that carries another install's marker, or another move's id, is never used.
        string EnsureStamped(MoveJournal j)
        {
            var m = MachineFile.Load(Root);
            string sid = DataFolders.SettingsId(m), mid = DataFolders.ReadMarkerId(j.To);
            if (mid == null)
            {
                // a folder is never taken for the move's own because its folders have the right names: with no marker it must be empty
                foreach (string e in Directory.Exists(j.To) ? Directory.GetFileSystemEntries(j.To) : new string[0])
                    if (!string.Equals(Path.GetFileName(e), DataFolders.MarkerName + ".tmp", StringComparison.OrdinalIgnoreCase)) return "The new folder has files in it but no VGAC marker, so VGAC will not copy into it: " + j.To + ". Choose another folder.";
                DataFolders.Stamp(j.To, m, j.Id);
            }
            else if (mid != sid || DataFolders.ReadMarkerMove(j.To) != j.Id) return "The new folder already carries a VGAC marker that is not this move's, so VGAC will not copy into it. Choose another folder.";
            SafeFs.Point("move", "stamped");
            return null;
        }

        // Each folder is copied straight into its final place; a file that is already there with the same size and time is not copied
        // again, so an interrupted copy resumes. Nothing is deleted.
        string CopyAll(MoveJournal j, Progress prog)
        {
            var names = MoveRules.NamesIn(j.From);
            for (int i = 0; i < names.Count; i++)
            {
                string name = names[i];
                if (j.Done.Contains(name)) continue;
                if (prog != null) { prog.Say("Copying " + name + "..."); prog.Fraction((double)i / names.Count); if (prog.Cancelled()) return "The copy was stopped. Continue it later, or abandon it."; }
                string src = Path.Combine(j.From, name), final = Path.Combine(j.To, name);
                try
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(final));
                    var rep = new FsReport();
                    string skip = MoveRules.SkipFor(name);
                    List<string[]> links = SafeFs.CopyItem(src, final, skip == null ? null : new[] { skip }, rep);
                    if (rep.Links.Count > 0) return "A link got in the way of copying " + name + " (" + rep.Links[0] + "), so VGAC stopped. Nothing was changed in the old place, and nothing was written through the link.";
                    SafeFs.Point("move", "copied:" + name);
                    SafeFs.MakeLinks(final, links, rep);
                    if (rep.Links.Count > 0) return "A link inside " + name + " could not be made again in the copy (" + rep.Links[0] + "). Continue to try again, or abandon the move; the old place is untouched.";
                    j.Done.Add(name);
                    Save(j);
                }
                catch (IOException e) { return "Copying " + name + " stopped: " + e.Message + " Nothing was changed in the old place. Free some room or choose another folder, then continue."; }
                catch (UnauthorizedAccessException e) { return "Windows did not allow writing " + name + " in the new folder (" + e.Message + ")."; }
            }
            return null;
        }

        // Every file compared by size and SHA-256 (the folders hold the person's models too, so size alone is not enough).
        string VerifyAll(MoveJournal j, Progress prog)
        {
            foreach (string name in new List<string>(j.Done))
            {
                string src = Path.Combine(j.From, name), dst = Path.Combine(j.To, name);
                if (!Directory.Exists(src)) continue;
                if (prog != null) prog.Say("Checking " + name + "...");
                SafeFs.Point("move", "verify:" + name);
                string skip = MoveRules.SkipFor(name);
                List<string> bad;
                try { bad = SafeFs.Compare(src, dst, skip == null ? null : new[] { skip }, true, true, prog == null ? null : prog.Cancelled, prog == null ? null : prog.Say); }
                catch (OperationCanceledException) { return "The check was stopped. Nothing was switched. Continue it later, or abandon the move."; }
                if (bad.Count > 0)
                {
                    j.Done.Remove(name); Save(j);      // copied again by the next Continue
                    return "The copy of " + name + " does not match the original (" + bad[0] + "). Nothing was switched; the old place is untouched. Continue to copy it again, or abandon the move.";
                }
            }
            return null;
        }

        // The one decision. The journal says "switching" first; every step after it is safe to repeat, so Continue finishes it from wherever
        // it stopped. There is no way back: to go back, move again.
        //   journal "switching", settings, ledger, restart marker, ComfyUI's engine settings (installer), ComfyUI's Python (installer), journal gone
        string Finish(MoveJournal j, bool force)
        {
            SafeFs.Point("move", "before-switch");
            SetDataFolder(j.To);
            RewriteLedger(j.From, j.To);
            string note = null;
            if (comfy != null && ComfyGuard.MarkerPresent(comfy.Root(j.To)))
            {
                // the installer's commands run with the app's own Python, whose environment points into the old place, which is still there
                SafeFs.Point("move", "before-retarget");
                string r = comfy.Retarget(j.From, j.To);
                if (r != null)
                {
                    if (!force) return "The files are moved, but VGAC's own ComfyUI could not be told: " + r + " Nothing runs until the move is finished: press Continue to try again, or 'Finish anyway', which ends the move WITHOUT moving VGAC's own ComfyUI (it then keeps running from the old folder, which you must keep).";
                    RewriteLedgerPath(Path.Combine(j.To, "comfyui"), Path.Combine(j.From, "comfyui"));
                    note = "VGAC's own ComfyUI was not moved: it keeps running from " + Path.Combine(j.From, "comfyui") + ", so keep that folder.";
                }
                else
                {
                    SafeFs.Point("move", "after-retarget");
                    r = comfy.RebuildVenv(j.To);
                    if (r != null)
                    {
                        if (!force) return "The files are moved, but VGAC's own ComfyUI Python could not be made again: " + r + " VGAC itself works, but nothing runs until the move is finished. Press Continue to try again, or 'Finish anyway': then ComfyUI will not start until Check and repair has made its Python again (it needs the internet).";
                        note = "VGAC's own ComfyUI's Python was not made again: Check and repair offers to make it (it needs the internet).";
                    }
                }
            }
            if (note != null) log.Line("move: " + note);
            WriteRestartMarker(j);         // only now: everything that could still fail is behind us
            DeleteJournal();
            log.Line("move: switched to " + j.To + "; the old folder " + j.From + " is no longer used");
            return null;
        }

        void RewriteLedgerPath(string oldPath, string newPath) { RewriteLedger(oldPath, newPath); }

        void WriteRestartMarker(MoveJournal j)
        {
            var d = new Dictionary<string, object>();
            d["moved_to"] = j.To; d["old_folder"] = j.From;
            SafeFs.WriteAtomic(MoveRules.RestartPath(Root), Json.Write(d), null);
            SafeFs.Point("move", "restart-marker");
        }

        // Gives up a move that has not switched: only the journal is forgotten. What was copied stays where it is, and its folder is said.
        public string Abandon()
        {
            var j = Current();
            if (j == null) return "There is no move to abandon.";
            if (j.State != "copying") return "The settings already name the new place, so the move cannot be abandoned: continue it to finish.";
            DeleteJournal();
            log.Line("move: abandoned; what was copied stays in " + j.To);
            return "The move was abandoned. Nothing was changed in the old place. What was copied so far is still in " + j.To + "; delete it by hand if you do not want it.";
        }
    }
}
