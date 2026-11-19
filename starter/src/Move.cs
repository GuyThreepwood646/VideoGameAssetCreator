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
        public List<string> Names = new List<string>();     // what the old place held when the move began (Continue refuses unless it still does)
        public List<string> Recopy = new List<string>();    // names whose check failed: copied again whatever size and time say
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

        // At start: VGAC has been restarted, so what the marker asked for is done.
        public static bool ClearRestartAtStart(string root)
        {
            try { if (!File.Exists(RestartPath(root))) return false; File.Delete(RestartPath(root)); return true; } catch (Exception) { return false; }
        }

        public static bool HasItem(string folder, string name)
        {
            foreach (MoveItem it in Items) if (string.Equals(it.Name, name, StringComparison.OrdinalIgnoreCase)) return PresentAt(folder, it);
            return false;
        }

        // What is not copied (again) for a name: ComfyUI's own Python environment, and, once the switch has begun and the installer has
        // written its record in the new place, that record too (the installer's work there is not overwritten with the old place's).
        public static string[] SkipList(string name, bool keepNewMarker)
        {
            if (name != "comfyui") return null;
            return keepNewMarker ? new[] { ".venv", ComfyGuard.MarkerName } : new[] { ".venv" };
        }

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
            if (t.StartsWith("\\\\") || t.StartsWith("//")) bad.Add("A network folder cannot hold the big files. Choose a folder on this computer's own drive.");
            else
            {
                try
                {
                    var di = new DriveInfo(Path.GetPathRoot(t));
                    if (di.DriveType != DriveType.Fixed && di.DriveType != DriveType.Removable) bad.Add("That drive is not a fixed or removable drive on this computer. Choose another folder.");
                }
                catch (Exception) { bad.Add("That drive cannot be checked. Choose another folder."); }
            }
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
                    // links the move itself made (uv's Python keeps some between its versions) lead inside their own folder and are fine;
                    // any other link, or a link in place of a folder, is not
                    foreach (MoveItem it in Items)
                    {
                        string ip = Path.Combine(t, it.Name);
                        string tp = Path.Combine(t, it.Name.Split('\\')[0]);
                        if (SafeFs.IsLink(tp) || SafeFs.IsLink(ip)) { bad.Add("The new folder holds a link where a folder should be (" + ip + "), so VGAC will not copy into it."); break; }
                        if (!Directory.Exists(ip)) continue;
                        string link = SafeFs.FirstExternalLink(ip, SkipFor(it.Name));
                        if (link != null) { bad.Add("The new folder holds a link that leads outside its folder (" + link + "), so VGAC will not copy into it. Remove the link, or abandon the move."); break; }
                    }
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
                string cur = Layout.From(root, MachineFile.Load(root)).DataFolder;
                MoveJournal j = MoveRunner.Load(root, cur);
                if (j != null) return "A move of the big files is not finished (" + (j.State == "switching" ? "it is switching to the new folder" : "it was copying") + "). Until it is, nothing else runs: open Maintenance, Move the big files, and continue it" + (j.State == "copying" ? ", or abandon it" : "") + ".";
                string why = MoveRunner.JudgeProblem(root, cur);
                if (why != null) return "A record of a move of the big files (local\\move.json) exists but VGAC cannot judge it (" + why + "), so nothing runs. Open Maintenance, Move the big files: put things back as the record expects (for example plug in the drive), or discard the record.";
                return null;
            }
            catch (Exception e) { return "VGAC cannot tell whether a move of the big files is unfinished (" + e.Message + "), so nothing runs. Fix local\\machine.json and local\\move.json, then start again."; }
        }
    }

    // What the window shows about a move, read fresh from the files each time (never from a layout loaded earlier).
    public static class MoveView
    {
        // What the window says after a switch, from the restart marker: the old folder can be deleted, or (when VGAC's own ComfyUI was left
        // there by 'Finish anyway') must be kept.
        public static string RestartText(string root)
        {
            string to = "the new folder", old = null, leftover = null; bool keep = false; long bytes = 0; var left = new List<string>();
            try
            {
                var d = Json.ParseObject(File.ReadAllText(MoveRules.RestartPath(root), Encoding.UTF8));
                to = Convert.ToString(d["moved_to"]); old = Convert.ToString(d["old_folder"]);
                object k; keep = d.TryGetValue("keep_old_comfyui", out k) && k is bool && (bool)k;
                object lo; if (d.TryGetValue("leftovers", out lo) && lo is System.Collections.IEnumerable && !(lo is string))
                    foreach (object x in (System.Collections.IEnumerable)lo) if (x != null) left.Add(Convert.ToString(x));
                object lc; if (d.TryGetValue("leftover_comfyui", out lc) && lc != null) leftover = Convert.ToString(lc);
                object lb; if (d.TryGetValue("leftover_bytes", out lb) && lb != null) bytes = Convert.ToInt64(lb);
            }
            catch (Exception) { }
            string head = "The big files are now in " + to + ". VGAC has to be restarted to use them: close this window and press the button below.";
            if (old == null) return head;
            string text;
            if (keep)
            {
                text = head + "\r\n\r\nVGAC's own ComfyUI was NOT moved: it keeps running from " + Path.Combine(old, "comfyui") + ". Keep the old folder (" + old + "); do not delete it.";
                if (leftover != null) text += "\r\n\r\nA leftover copy of ComfyUI is in " + leftover + " (" + (bytes / (1024.0 * 1024.0)).ToString("0.0") + " MB). It is no longer used by VGAC and is not running. It can be deleted by hand once you are happy with the new place.";
            }
            else text = head + "\r\n\r\n" + (left.Count > 0 ? "The old folder (" + old + ") is no longer used by VGAC." : OldFolderSentence(old));
            if (left.Count > 0)
            {
                // something the move did not copy is still in the old folder: each is named and kept, never offered for deletion here
                text += "\r\n\r\nThe old folder still holds things the move did not copy. They are not VGAC's to delete, so keep them:\r\n   " + string.Join("\r\n   ", left.ToArray());
            }
            return text;
        }

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
                object nm; if (d.TryGetValue("names", out nm) && nm is System.Collections.IEnumerable) foreach (object o in (System.Collections.IEnumerable)nm) j.Names.Add(Convert.ToString(o));
                object rc; if (d.TryGetValue("recopy", out rc) && rc is System.Collections.IEnumerable) foreach (object o in (System.Collections.IEnumerable)rc) j.Recopy.Add(Convert.ToString(o));
            }
            catch (Exception) { return null; }
            if (Validate(j, currentData) != null) { StaleFound = true; return null; }
            return j;
        }

        // null when there is no record of a move, or it is readable and fits; else why VGAC cannot judge it. A record that exists but cannot be
        // judged blocks everything (MoveGate) until the person discards it in the window, never silently ignored.
        public static string JudgeProblem(string root, string currentData)
        {
            string p = JournalPath(root);
            if (!File.Exists(p)) return null;
            MoveJournal j;
            try
            {
                var d = Json.ParseObject(File.ReadAllText(p, Encoding.UTF8));
                j = new MoveJournal();
                j.From = Convert.ToString(d["from"]); j.To = Convert.ToString(d["to"]); j.State = Convert.ToString(d["state"]);
                foreach (object o in (System.Collections.IEnumerable)d["done"]) j.Done.Add(Convert.ToString(o));
                // the names and the recopy list are judged too: a record with an unknown name must block the gate, not pass as "no record"
                object nm; if (d.TryGetValue("names", out nm) && nm is System.Collections.IEnumerable) foreach (object o in (System.Collections.IEnumerable)nm) j.Names.Add(Convert.ToString(o));
                object rc; if (d.TryGetValue("recopy", out rc) && rc is System.Collections.IEnumerable) foreach (object o in (System.Collections.IEnumerable)rc) j.Recopy.Add(Convert.ToString(o));
            }
            catch (Exception) { return "it cannot be read"; }
            string why = Validate(j, currentData);
            if (why == null) return null;
            return why == "unknown state" ? "it is from a version of VGAC that this one does not know" : (why == "the other place is not an acceptable folder" ? "the other folder is not reachable (is its drive plugged in?)" : why);
        }

        static string Validate(MoveJournal j, string currentData)
        {
            if (j.State != "copying" && j.State != "switching") return "unknown state";
            if (!Path.IsPathRooted(j.From ?? "") || !Path.IsPathRooted(j.To ?? "")) return "places are not absolute";
            foreach (string n in j.Done) if (!MoveRules.IsKnownName(n)) return "unknown name";
            foreach (string n in j.Names) if (!MoveRules.IsKnownName(n)) return "unknown name";
            foreach (string n in j.Recopy) if (!MoveRules.IsKnownName(n)) return "unknown name";
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
            d["from"] = j.From; d["to"] = j.To; d["state"] = j.State; d["done"] = j.Done; d["id"] = j.Id ?? ""; d["names"] = j.Names; d["recopy"] = j.Recopy;
            SafeFs.WriteAtomic(JournalPath(Root), Json.Write(d), null);
            SafeFs.Point("move", "journal:" + j.State);
        }

        // true when the record is gone (or was never there). A record that stays after the switch is reported, not ignored.
        bool DeleteJournal()
        {
            try { if (File.Exists(JournalPath(Root))) File.Delete(JournalPath(Root)); }
            catch (Exception) { if (File.Exists(JournalPath(Root))) return false; }
            SafeFs.Point("move", "journal-deleted");
            return !File.Exists(JournalPath(Root));
        }

        // The settings already name the new folder: the switch has happened (used when the old folder can no longer be checked).
        bool SettingsNameTo(MoveJournal j)
        {
            try { return string.Equals(Path.GetFullPath(DataNow()).TrimEnd('\\'), Path.GetFullPath(j.To).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase); }
            catch (Exception) { return false; }
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

        void RewriteLedger(string oldRoot, string newRoot, IEnumerable<string> onlyNames = null)
        {
            Ledger fresh = Ledger.Load(Root);
            if (onlyNames != null) fresh.RewritePathsUnder(oldRoot, newRoot, onlyNames); else fresh.RewritePaths(oldRoot, newRoot);
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
            var j = new MoveJournal(); j.From = from; j.To = full; j.Id = Guid.NewGuid().ToString("N"); j.Names = MoveRules.NamesIn(from);
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
            // The old place must still be the one that was copied: there, with this install's marker, holding everything recorded at the start.
            string fromWhy = FromProblem(j);
            if (fromWhy != null) return fromWhy;
            // Whatever the state, nothing earlier is trusted: the old place may have changed since it was copied (nothing runs there
            // while a move is unfinished, but a crash or a hand edit can still leave it different), and the new place may have been
            // damaged. Files already identical are not copied again; a file that fails the check is copied again whatever its size and time
            // say; extra files in the new place are ours (its marker says so) and are not a mismatch.
            List<string> foreignFiles = MoveRules.Problems(j.From, j.To, Root, -1, 0, j, j.State == "switching" ? null : comfy);
            if (foreignFiles.Count > 0) return string.Join(" ", foreignFiles.ToArray());
            List<string> blockers = MoveRules.Blockers(j.From, procs, OwnPid());
            if (blockers.Count > 0) return "Close these first, they have files open in the old folder: " + string.Join(", ", blockers.ToArray()) + ".";
            string r = EnsureStamped(j);
            if (r != null) return r;
            bool keepNewMarker = j.State == "switching" && File.Exists(Path.Combine(j.To, "comfyui", ComfyGuard.MarkerName));
            for (int attempt = 0; attempt < 2; attempt++)
            {
                j.Done.Clear();
                lastMismatch = false;
                r = CopyAll(j, prog, keepNewMarker);
                if (r == null) r = VerifyAll(j, prog, keepNewMarker);
                if (r == null || !lastMismatch) break;      // done, or a failure another try would not mend
            }
            if (r != null) return r;
            if (j.State == "copying") { j.State = "switching"; Save(j); }
            return Finish(j, force);
        }

        bool lastMismatch;

        string FromProblem(MoveJournal j)
        {
            if (!Directory.Exists(j.From))
            {
                // once the settings name the new folder the switch happened: the old folder is no longer needed to finish it
                if (j.State == "switching" && SettingsNameTo(j)) return null;
                return "The old folder (" + j.From + ") is not there (renamed, unplugged or on another drive letter?). Nothing was checked or switched. Put it back where it was, then continue.";
            }
            var m = MachineFile.Load(Root);
            string sid = DataFolders.SettingsId(m);
            if (string.IsNullOrEmpty(sid) || DataFolders.ReadMarkerId(j.From) != sid) return "The old folder (" + j.From + ") does not carry this install's VGAC marker, so it is not the one that was copied. Nothing was checked or switched.";
            if (j.Names.Count == 0)
            {
                // Only a hand-edited record has no names. Once the settings already name the new folder the switch happened, and the old
                // folder is no longer used, so there is nothing left to check: Continue may finish the switch. Otherwise nothing can be judged.
                if (j.State == "switching" && string.Equals(Path.GetFullPath(DataNow()).TrimEnd('\\'), Path.GetFullPath(j.To).TrimEnd('\\'),StringComparison.OrdinalIgnoreCase)) return null;
                return "The record of this move does not say what was copied, so VGAC cannot tell whether the old folder is whole. Abandon it and move again.";
            }
            foreach (string n in j.Names)
                if (!MoveRules.HasItem(j.From, n)) return "The old folder no longer holds " + n + ", which was there when the move began, so it is not whole. Nothing was checked or switched.";
            return null;
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
        string CopyAll(MoveJournal j, Progress prog, bool keepNewMarker)
        {
            var names = j.Names;
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
                    string[] skip = MoveRules.SkipList(name, keepNewMarker);
                    List<string[]> links;
                    SafeFs.ForceCopy = j.Recopy.Contains(name);
                    try { links = SafeFs.CopyItem(src, final, skip, rep); } finally { SafeFs.ForceCopy = false; }
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
        string VerifyAll(MoveJournal j, Progress prog, bool keepNewMarker)
        {
            foreach (string name in new List<string>(j.Done))
            {
                string src = Path.Combine(j.From, name), dst = Path.Combine(j.To, name);
                if (!Directory.Exists(src)) continue;
                if (prog != null) prog.Say("Checking " + name + "...");
                SafeFs.Point("move", "verify:" + name);
                List<string> bad;
                try { bad = SafeFs.Compare(src, dst, MoveRules.SkipList(name, keepNewMarker), true, true, prog == null ? null : prog.Cancelled, prog == null ? null : prog.Say); }
                catch (OperationCanceledException) { return "The check was stopped. Nothing was switched. Continue it later, or abandon the move."; }
                if (bad.Count > 0)
                {
                    j.Done.Remove(name);
                    if (!j.Recopy.Contains(name)) j.Recopy.Add(name);      // copied again next time, whatever size and time say
                    Save(j);
                    lastMismatch = true;
                    return "The copy of " + name + " does not match the original (" + bad[0] + "). Nothing was switched; the old place is untouched. Continue to copy it again, or abandon the move.";
                }
                if (j.Recopy.Remove(name)) Save(j);
            }
            return null;
        }

        // The one decision. The journal says "switching" first; every step after it is safe to repeat, so Continue finishes it from wherever
        // it stopped. There is no way back: to go back, move again.
        //   journal "switching", settings, ledger, restart marker, ComfyUI's engine settings (installer), ComfyUI's Python (installer), journal gone
        string Finish(MoveJournal j, bool force)
        {
            SafeFs.Point("move", "before-switch");
            // B10: looked at again just before the switch: something may have opened a file in the old folder during the copy
            List<string> late = MoveRules.Blockers(j.From, procs, OwnPid());
            if (late.Count > 0) return "Close these first, they have files open in the old folder: " + string.Join(", ", late.ToArray()) + ". Nothing was switched; press Continue again.";
            string flushed = SafeFs.FlushTree(j.To);   // B4: the copy is on the disk before the settings name it
            if (flushed != null) return "The big files are copied, but " + flushed + ". Nothing was switched. Press Continue to try again.";
            SetDataFolder(j.To);
            RewriteLedger(j.From, j.To, j.Names);   // B8: only the entries of what was copied
            string note = null; bool comfyLeftBehind = false;
            if (comfy != null && ComfyGuard.MarkerPresent(comfy.Root(j.To)))
            {
                // the installer's commands run with the app's own Python, whose environment points into the old place, which is still there
                SafeFs.Point("move", "before-retarget");
                string r = comfy.Retarget(j.From, j.To);
                if (r != null)
                {
                    if (!force) return "The files are moved, but VGAC's own ComfyUI could not be told: " + r + " Nothing runs until the move is finished: press Continue to try again, or 'Finish anyway', which ends the move WITHOUT moving VGAC's own ComfyUI (it then keeps running from the old folder, which you must keep).";
                    RewriteLedgerPath(Path.Combine(j.To, "comfyui"), Path.Combine(j.From, "comfyui"));
                    DropMarkerInLeftoverCopy(Path.Combine(j.To, "comfyui"));   // the copy in the new folder is no longer VGAC's ComfyUI
                    comfyLeftBehind = true;
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
            WriteRestartMarker(j, comfyLeftBehind, OldLeftovers(j));   // only now: everything that could still fail is behind us
            if (!DeleteJournal()) return "The files are switched to the new folder, but VGAC could not remove the move's record (local\\move.json). Restart VGAC; if it says a move is unfinished, open Maintenance and press Continue.";
            log.Line("move: switched to " + j.To + "; the old folder " + j.From + " is no longer used");
            return null;
        }

        void RewriteLedgerPath(string oldPath, string newPath) { RewriteLedger(oldPath, newPath); }

        // After 'Finish anyway' the ComfyUI files stay in the new folder, but its marker goes: no later start or check may take that
        // copy for the ComfyUI in use (which runs from the old folder). The files are never deleted here.
        void DropMarkerInLeftoverCopy(string copyDir)
        {
            try { string m = Path.Combine(copyDir, ComfyGuard.MarkerName); if (File.Exists(m)) File.Delete(m); log.Line("move: the marker of the ComfyUI copy left in " + copyDir + " was removed; its files stay"); }
            catch (Exception e) { log.Line("move: could not remove the marker of the ComfyUI copy in " + copyDir + " (" + e.Message + ")"); }
        }

        // What is in the old folder that the move did not copy (the moved names and the marker are not listed: their copies are VGAC's).
        // Saved in the restart marker so the window can name each one and say to keep it.
        List<string> OldLeftovers(MoveJournal j)
        {
            var res = new List<string>();
            try
            {
                if (!Directory.Exists(j.From)) return res;
                var moved = new HashSet<string>(j.Names, StringComparer.OrdinalIgnoreCase);
                foreach (string e in Directory.GetFileSystemEntries(j.From))
                {
                    string n = Path.GetFileName(e);
                    if (moved.Contains(n) || string.Equals(n, DataFolders.MarkerName, StringComparison.OrdinalIgnoreCase)) continue;
                    if (string.Equals(n, "tools", StringComparison.OrdinalIgnoreCase) && Directory.Exists(e))
                    {
                        // tools holds uv and node (moved) beside whatever else the person or an older version left: only the rest is listed
                        foreach (string t in Directory.GetFileSystemEntries(e))
                            if (!moved.Contains(Path.GetFileName(t)) && !moved.Contains("tools\\" + Path.GetFileName(t))) res.Add(t);
                        continue;
                    }
                    res.Add(e);
                }
            }
            catch (Exception) { }
            return res;
        }

        void WriteRestartMarker(MoveJournal j, bool comfyLeftBehind, List<string> leftovers)
        {
            var d = new Dictionary<string, object>();
            d["moved_to"] = j.To; d["old_folder"] = j.From;
            d["leftovers"] = leftovers ?? new List<string>();
            if (comfyLeftBehind)
            {
                d["keep_old_comfyui"] = true;     // the window must say to KEEP the old folder
                string copy = Path.Combine(j.To, "comfyui");
                d["leftover_comfyui"] = copy; d["leftover_bytes"] = SafeFs.Size(copy, null);   // the window names the copy and its size
            }
            SafeFs.WriteAtomic(MoveRules.RestartPath(Root), Json.Write(d), null);
            SafeFs.Point("move", "restart-marker");
        }

        // The old folder the record names, or null when it cannot be read.
        public static string RawFrom(string root)
        {
            try { return Convert.ToString(Json.ParseObject(File.ReadAllText(JournalPath(root), Encoding.UTF8))["from"]); }
            catch (Exception) { return null; }
        }

        // The state word of the record as written, or null when it cannot be read (no judgement; never changes anything).
        public static string RawState(string root)
        {
            try
            {
                var d = Json.ParseObject(File.ReadAllText(JournalPath(root), Encoding.UTF8));
                return Convert.ToString(d["state"]);
            }
            catch (Exception) { return null; }
        }

        // Gives up a move that has not switched: only the journal is forgotten. What was copied stays where it is, and its folder is said.
        public string Abandon()
        {
            var j = Current();
            if (j == null)
            {
                string why = JudgeProblem(Root, DataNow());
                if (why == null) return "There is no move to abandon.";
                // A record that says "switching" means the settings may already name the new place. Forgetting it would open the start
                // gate on a half-switched install, so it is kept (the window says to continue it instead).
                if (MoveRules.RestartPending(Root)) return "VGAC has been moved to a new folder and has to be restarted first, so the record is kept. Restart VGAC.";
                string rawFrom = RawFrom(Root);
                if (rawFrom != null && !string.Equals(SafeFs.Norm(rawFrom), SafeFs.Norm(DataNow()), StringComparison.OrdinalIgnoreCase)) return "The settings already name another folder for the big files, so the record is kept. Use Move the big files to continue or check it.";
                if (RawState(Root) == "switching")
                    return "The move has already started switching to the new folder, so its record is kept: forgetting it would start VGAC on a half-moved install. Use Move the big files to continue it.";
                DeleteJournal();      // a record VGAC cannot judge: the person asked to discard it (the window says what that means)
                log.Line("move: an unreadable record was discarded (" + why + ")");
                return "The record of a move (" + why + ") was discarded. Nothing else was changed. If a move had switched, check which folder is in use under Move the big files.";
            }
            if (j.State != "copying") return "The settings already name the new place, so the move cannot be abandoned: continue it to finish.";
            DeleteJournal();
            log.Line("move: abandoned; what was copied stays in " + j.To);
            return "The move was abandoned. Nothing was changed in the old place. What was copied so far is still in " + j.To + "; delete it by hand if you do not want it.";
        }
    }
}
