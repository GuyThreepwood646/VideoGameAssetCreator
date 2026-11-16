// Moving the big files (the data folder) to another folder or drive. The old place stays whole and in use until the copy
// is verified byte for byte and one small file is switched; every step is written to local/move.json so an interruption at any
// point can be continued or cancelled. Only folders VGAC can prove it made are moved, nothing in move.json is trusted without
// checking, and cancelling never deletes a copy that the app may already have been using. C# 5 (see Core.cs).
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace Vgac.Starter
{
    public class MoveJournal
    {
        public string From, To;
        public string State = "copying";            // copying | copied | verified | switching | switched | rebuilt
        public List<string> Done = new List<string>();      // names completely copied (renamed in from their .part)
        public List<string> Created = new List<string>();   // everything this move made in the new folder (so Cancel removes only that)
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
            string n = name.EndsWith(".part", StringComparison.OrdinalIgnoreCase) ? name.Substring(0, name.Length - 5) : name;
            foreach (MoveItem i in Items) if (string.Equals(i.Name, n, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
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

        public static MoveItem ItemFor(string name)
        {
            string n = name.EndsWith(".part", StringComparison.OrdinalIgnoreCase) ? name.Substring(0, name.Length - 5) : name;
            foreach (MoveItem i in Items) if (string.Equals(i.Name, n, StringComparison.OrdinalIgnoreCase)) return i;
            return null;
        }

        // Plain problems with a chosen folder (empty list: fine).
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
                        if (string.Equals(n, DataFolders.MarkerName, StringComparison.OrdinalIgnoreCase)) continue;   // the new place's own marker
                        bool known = false;
                        foreach (string c in resuming.Created) if (c.Split('\\')[0].Replace(".part", "") == n.Replace(".part", "")) known = true;
                        if (!known) { bad.Add("That folder has files in it that VGAC did not put there (" + n + ")."); break; }
                    }
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
            // links are never copied; a folder that holds one is not moved at all rather than losing what it points to
            foreach (string name in NamesIn(from))
            {
                // links between places inside the same folder (uv's Python keeps some) are copied as links; any other is refused
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

    public class MoveRunner
    {
        readonly Layout l; readonly MachineFile machine; readonly Ledger ledger; readonly IComfyInstall comfy; readonly IProcessSource procs; readonly SetupLog log;
        public Func<string, long> Free = delegate(string f) { try { return new DriveInfo(Path.GetPathRoot(Path.GetFullPath(f))).AvailableFreeSpace; } catch (Exception) { return -1; } };
        public Func<int> OwnPid = delegate { return System.Diagnostics.Process.GetCurrentProcess().Id; };

        public MoveRunner(Layout layout, MachineFile machine, Ledger ledger, IComfyInstall comfy, IProcessSource procs, SetupLog log)
        {
            l = layout; this.machine = machine; this.ledger = ledger; this.comfy = comfy; this.procs = procs; this.log = log;
        }

        public static string JournalPath(string root) { return Path.Combine(root, "local", "move.json"); }

        // The journal, or null. A journal that is not a plain list of this program's own fixed names, or whose places do not
        // fit the current settings, is never acted on: it is set aside as move.json.stale and StaleFound says so.
        public static bool StaleFound;

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
                foreach (object o in (System.Collections.IEnumerable)d["done"]) j.Done.Add(Convert.ToString(o));
                foreach (object o in (System.Collections.IEnumerable)d["created"]) j.Created.Add(Convert.ToString(o));
            }
            catch (Exception) { return null; }
            string why = Validate(j, currentData);
            if (why != null)
            {
                StaleFound = true;
                try { string stale = p + ".stale"; if (File.Exists(stale)) File.Delete(stale); File.Move(p, stale); } catch (Exception) { }
                return null;
            }
            return j;
        }

        static readonly string[] States = { "copying", "copied", "verified", "switching", "switched", "rebuilt" };

        static string Validate(MoveJournal j, string currentData)
        {
            if (Array.IndexOf(States, j.State) < 0) return "unknown state";
            if (!Path.IsPathRooted(j.From ?? "") || !Path.IsPathRooted(j.To ?? "")) return "places are not absolute";
            foreach (string n in j.Done) if (!MoveRules.IsKnownName(n) || n.EndsWith(".part")) return "unknown name";
            foreach (string n in j.Created) if (!MoveRules.IsKnownName(n)) return "unknown name";
            if (currentData != null)
            {
                // The settings name one of the two places, and the OTHER one must be an acceptable folder as well (not a drive, a
                // profile or a system place, and not inside or around the one in use), so a journal cannot point at anything.
                string cur = SafeFs.Norm(currentData);
                string other = cur == SafeFs.Norm(j.From) ? j.To : (cur == SafeFs.Norm(j.To) ? j.From : null);
                if (other == null) return "places do not fit the settings";
                // Before the switch the old place is the one in use; after it, the new one. A record that says otherwise is not acted on.
                bool usingFrom = cur == SafeFs.Norm(j.From);
                if ((j.State == "copying" || j.State == "copied" || j.State == "verified") && !usingFrom) return "the settings already use the new place";
                if ((j.State == "switched" || j.State == "rebuilt") && usingFrom) return "the settings still use the old place";
                if (Proof.DataFolderProblem(other, null) != null) return "the other place is not an acceptable folder";
                if (SafeFs.Inside(other, currentData) || SafeFs.Inside(currentData, other)) return "the two places overlap";
            }
            return null;
        }

        void Save(MoveJournal j)
        {
            string p = JournalPath(l.ProjectRoot);
            Directory.CreateDirectory(Path.GetDirectoryName(p));
            var d = new Dictionary<string, object>();
            d["from"] = j.From; d["to"] = j.To; d["state"] = j.State; d["done"] = j.Done; d["created"] = j.Created;
            string tmp = p + ".tmp";
            File.WriteAllText(tmp, Json.Write(d), new UTF8Encoding(false));
            if (File.Exists(p)) File.Delete(p);
            File.Move(tmp, p);
        }

        MoveJournal Current() { return Load(l.ProjectRoot, CurrentDataFolder()); }

        // The data folder the settings name now (the layout given to this runner may predate a switch).
        string CurrentDataFolder() { return Layout.From(l.ProjectRoot, MachineFile.Load(l.ProjectRoot)).DataFolder; }

        public long NeedBytes()
        {
            long n = 0;
            foreach (string name in MoveRules.NamesIn(l.DataFolder)) n += SafeFs.Size(Path.Combine(l.DataFolder, name), null);
            return n;
        }

        // Begins a move to `to` (null when it ran to the end of the copy, verification and switch; else one plain sentence).
        public string Start(string to, Progress prog)
        {
            if (Current() != null) return "A move is already under way. Continue it or cancel it first.";
            // Only a folder shown to be VGAC's (its marker matches this install's settings) is ever moved.
            DataStatus ds = DataFolders.Check(l.DataFolder, MachineFile.Load(l.ProjectRoot), l.ProjectRoot);
            if (ds.State != DataState.Ours) return DataFolders.ExplainNotClaimed(l.DataFolder, ds) + " Nothing was changed. Use 'Use this folder for VGAC only' in Check and repair if it holds only VGAC's own folders, or choose another folder.";
            if (MoveRules.NamesIn(l.DataFolder).Count == 0) return "There is nothing of VGAC's in " + l.DataFolder + " to move. Nothing was changed.";
            string full = Path.GetFullPath(to);
            List<string> bad = MoveRules.Problems(l.DataFolder, full, l.ProjectRoot, Free(full), NeedBytes(), null, comfy);
            if (bad.Count > 0) return string.Join(" ", bad.ToArray());
            List<string> blockers = MoveRules.Blockers(l.DataFolder, procs, OwnPid());
            if (blockers.Count > 0) return "Close these first, they have files open in the old folder: " + string.Join(", ", blockers.ToArray()) + ".";
            var j = new MoveJournal(); j.From = l.DataFolder; j.To = full;
            Save(j);
            DataFolders.Stamp(full, MachineFile.Load(l.ProjectRoot));   // the new place is VGAC's from now on (it was new or empty), under the same id
            log.Line("move: started " + j.From + " -> " + j.To);
            return Continue(prog);
        }

        // Carries on from the journal (also after an interruption). null when the copy is verified and switched.
        public string Continue(Progress prog)
        {
            var j = Current();
            if (j == null) return MoveRunner.StaleFound ? "The record of an earlier move does not fit this install, so it was set aside (move.json.stale). Nothing was changed." : "There is no move to continue.";
            if (j.State == "copying")
            {
                List<string> foreignFiles = MoveRules.Problems(j.From, j.To, l.ProjectRoot, -1, 0, j, comfy);
                if (foreignFiles.Count > 0) return string.Join(" ", foreignFiles.ToArray());
                List<string> blockers = MoveRules.Blockers(j.From, procs, OwnPid());
                if (blockers.Count > 0) return "Close these first, they have files open in the old folder: " + string.Join(", ", blockers.ToArray()) + ".";
                string r = CopyAll(j, prog);
                if (r != null) return r;
            }
            if (j.State == "copied") { string r = VerifyAll(j, prog); if (r != null) return r; }
            if (j.State == "verified" || j.State == "switching") Switch(j);
            if (j.State == "switched") { string r = Rebuild(j); if (r != null) return r; }
            return null;
        }

        string CopyAll(MoveJournal j, Progress prog)
        {
            var names = MoveRules.NamesIn(j.From);
            for (int i = 0; i < names.Count; i++)
            {
                string name = names[i];
                if (j.Done.Contains(name)) continue;
                if (prog != null) { prog.Say("Copying " + name + "..."); prog.Fraction((double)i / names.Count); if (prog.Cancelled()) return "The copy was stopped. Continue it later, or cancel it."; }
                string src = Path.Combine(j.From, name), part = Path.Combine(j.To, name + ".part"), final = Path.Combine(j.To, name);
                try
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(final));
                    if (!j.Created.Contains(name + ".part")) { j.Created.Add(name + ".part"); Save(j); }
                    if (Directory.Exists(part)) SafeFs.DeleteTree(part);                      // a half copy from before: ours (named in the journal)
                    if (Directory.Exists(final) && j.Created.Contains(name)) SafeFs.DeleteTree(final);
                    var rep = new FsReport();
                    string skip = MoveRules.SkipFor(name);
                    List<string[]> links = SafeFs.CopyItem(src, part, skip == null ? null : new[] { skip }, rep);
                    if (rep.Links.Count > 0) return "A link inside " + name + " could not be copied (" + rep.Links[0] + "). Nothing was changed in the old place.";
                    SafeFs.Point("move", "before-rename:" + name);
                    SafeFs.MoveDir(part, final);
                    // journalled BEFORE the links are made, so a failure there leaves a copy that Cancel and a retry both know about
                    j.Created.Remove(name + ".part");
                    j.Created.Add(name);
                    Save(j);
                    SafeFs.Point("move", "before-links:" + name);
                    SafeFs.MakeLinks(final, links, rep);
                    if (rep.Links.Count > 0) return "A link inside " + name + " could not be made again in the copy (" + rep.Links[0] + "). Continue to try again, or cancel the move; the old place is untouched.";
                    j.Done.Add(name);
                    Save(j);
                }
                catch (IOException e) { return "Copying " + name + " stopped: " + e.Message + " Nothing was changed in the old place. Free some room or choose another folder, then continue."; }
                catch (UnauthorizedAccessException e) { return "Windows did not allow writing " + name + " in the new folder (" + e.Message + ")."; }
            }
            j.State = "copied"; Save(j);
            return null;
        }

        // Every file compared by size and SHA-256 (the folders hold the person's models too, so size alone is not enough).
        string VerifyAll(MoveJournal j, Progress prog)
        {
            foreach (string name in j.Done)
            {
                string src = Path.Combine(j.From, name), dst = Path.Combine(j.To, name);
                if (!Directory.Exists(src)) continue;
                if (prog != null) prog.Say("Checking " + name + "...");
                SafeFs.Point("move", "verify:" + name);
                string skip = MoveRules.SkipFor(name);
                List<string> bad;
                try
                {
                    bad = SafeFs.Compare(src, dst, skip == null ? null : new[] { skip }, true, false, prog == null ? null : prog.Cancelled, prog == null ? null : prog.Say);
                }
                catch (OperationCanceledException) { return "The check was stopped. Nothing was switched. Continue it later, or cancel the move."; }
                if (bad.Count > 0) return "The copy of " + name + " does not match the original (" + bad[0] + "). Nothing was switched; the old place is untouched. Cancel and try again.";
            }
            j.State = "verified"; Save(j);
            return null;
        }

        // The one decision: from now on the big files are in the new folder. The journal says "switching" BEFORE the settings
        // change, so a crash in between is recognised (the settings, not the journal, say which place is in use).
        void Switch(MoveJournal j)
        {
            j.State = "switching"; Save(j);
            SafeFs.Point("move", "before-switch");
            machine.Set("starter", "dataFolder", Layout.Stored(l.ProjectRoot, j.To));   // relative inside the project, so a moved project keeps working
            machine.Save();
            SafeFs.Point("move", "after-settings-saved");
            RewriteLedger(j.From, j.To);
            j.State = "switched"; Save(j);
            log.Line("move: switched to " + j.To);
        }

        void RewriteLedger(string oldRoot, string newRoot)
        {
            string o = SafeFs.Norm(oldRoot);
            for (int i = 0; i < ledger.Items.Count; i++)
            {
                var d = ledger.Items[i] as Dictionary<string, object>;
                if (d == null || !d.ContainsKey("path")) continue;
                string np = Path.GetFullPath(Convert.ToString(d["path"])).TrimEnd('\\');
                if (np.ToLowerInvariant() == o || np.ToLowerInvariant().StartsWith(o + "\\")) d["path"] = newRoot.TrimEnd('\\') + np.Substring(oldRoot.TrimEnd('\\').Length);
            }
            ledger.Save();
        }

        // The app's own ComfyUI is told where it is now and makes its Python again (its installer fetches and checks its packages again). The backend's own Python is
        // made again by the normal start, which sees that the data folder changed.
        string Rebuild(MoveJournal j)
        {
            if (comfy != null && ComfyGuard.MarkerPresent(comfy.Root(j.To)))
            {
                // The commands run with the app's own Python, whose environment still points into the old place: the old copy is
                // deleted only after this step, and only on the person's confirmation.
                string r = comfy.Retarget(j.From, j.To);
                if (r != null) return "The files are moved, but VGAC's own ComfyUI could not be told: " + r + " Continue to try again.";
                r = comfy.RebuildVenv(j.To);
                if (r != null) return "The files are moved, but VGAC's own ComfyUI Python could not be made again: " + r + " VGAC itself works; its own ComfyUI will not start until this is done (it needs the internet). Continue to try again, or Cancel to go back to the old place.";
            }
            j.State = "rebuilt"; Save(j);
            return null;
        }

        // Which place the settings really use (the settings file, not the journal, is the truth).
        bool UsingNewPlace(MoveJournal j)
        {
            string now = MachineFile.Load(l.ProjectRoot).GetString("starter", "dataFolder");
            return now != null && SafeFs.Norm(now) == SafeFs.Norm(j.To);
        }

        // Cancel. Before the switch: remove only what this move copied into the new folder (the old place was never changed).
        // Once the new place is, or may have been, in use: point the settings back at the old place and LEAVE the new copy
        // alone, because the app may have written to it; it is the person's to delete.
        public string Cancel()
        {
            var j = Current();
            if (j == null) return null;
            if (j.State == "switched" || j.State == "rebuilt" || j.State == "switching" || UsingNewPlace(j))
            {
                var m = MachineFile.Load(l.ProjectRoot);
                m.Set("starter", "dataFolder", Layout.Stored(l.ProjectRoot, j.From)); m.Save();
                RewriteLedger(j.To, j.From);
                // VGAC's own ComfyUI: its engine settings were pointed at the new place by retarget; they go back too. If that is
                // refused, everything is put back on the new place (a consistent, working state) and the move stays open.
                if (comfy != null && ComfyGuard.MarkerPresent(comfy.Root(j.From)))
                {
                    string back = comfy.Retarget(j.To, j.From);
                    if (back != null)
                    {
                        m.Set("starter", "dataFolder", Layout.Stored(l.ProjectRoot, j.To)); m.Save();
                        RewriteLedger(j.From, j.To);
                        log.Line("move: the undo was refused by ComfyUI's installer (" + back + "); staying on " + j.To);
                        return "The move was not undone: VGAC's own ComfyUI could not be pointed back at the old place (" + back + "). Everything stays in the new place and works as it was. Try Cancel again, or Continue the move.";
                    }
                }
                try { File.Delete(JournalPath(l.ProjectRoot)); } catch (Exception) { }
                log.Line("move: undone, pointing back at " + j.From + "; the copy in " + j.To + " was left in place");
                return "VGAC points at the old place again. The copy in " + j.To + " was left as it is, because the app may have used it. Delete it yourself if you do not want it.";
            }
            // The new place was claimed by this move (new or empty, stamped with this install's id). Only then are the names this
            // journal lists removed from it, and only as real places inside it.
            string sid = DataFolders.SettingsId(MachineFile.Load(l.ProjectRoot));
            bool claimedByThisMove = !string.IsNullOrEmpty(sid) && DataFolders.ReadMarkerId(j.To) == sid;
            foreach (string n in j.Created)
            {
                if (!MoveRules.IsKnownName(n)) continue;
                string p = Path.Combine(j.To, n);
                if (!Directory.Exists(p) || SafeFs.IsLink(p) || !SafeFs.Inside(p, j.To)) continue;
                if (!claimedByThisMove) { log.Line("move: left " + p + " alone: VGAC cannot show that this new place is its own"); continue; }
                SafeFs.DeleteTree(p);
            }
            if (claimedByThisMove) { try { File.Delete(Path.Combine(j.To, DataFolders.MarkerName)); } catch (Exception) { } }
            try { string t = Path.Combine(j.To, "tools"); if (Directory.Exists(t) && Directory.GetFileSystemEntries(t).Length == 0) Directory.Delete(t); } catch (Exception) { }
            try { if (Directory.Exists(j.To) && Directory.GetFileSystemEntries(j.To).Length == 0) Directory.Delete(j.To); } catch (Exception) { }
            try { File.Delete(JournalPath(l.ProjectRoot)); } catch (Exception) { }
            return null;
        }

        // The move is finished and the person keeps both copies: the journal goes, nothing else.
        public string Forget()
        {
            var j = Current();
            if (j == null || j.State != "rebuilt") return "There is no finished move to close.";
            try { File.Delete(JournalPath(l.ProjectRoot)); } catch (Exception) { }
            log.Line("move: finished; the old copy in " + j.From + " was kept");
            return null;
        }

        // What is left in the old folder after a finished move, by name.
        public static List<string> OldCopy(MoveJournal j)
        {
            var res = new List<string>();
            foreach (string n in j.Done) { string p = Path.Combine(j.From, n); if (MoveRules.IsKnownName(n) && Directory.Exists(p)) res.Add(p); }
            return res;
        }

        // After "rebuilt": delete the old copy, but only names that were copied, only after comparing every file with the new
        // copy again (extra files in the new place, added since, do not matter; a file that differs does).
        public string DeleteOld() { return DeleteOld(null, null); }

        public string DeleteOld(Func<bool> cancelled, Action<string> say)
        {
            var j = Current();
            if (j == null || j.State != "rebuilt") return "There is no finished move whose old copy can be deleted.";
            foreach (string n in j.Done)
            {
                string src = Path.Combine(j.From, n), dst = Path.Combine(j.To, n);
                if (!Directory.Exists(src)) continue;
                string skip = MoveRules.SkipFor(n);
                List<string> bad;
                try { bad = SafeFs.Compare(src, dst, skip == null ? null : new[] { skip }, true, true, cancelled, say); }
                catch (OperationCanceledException) { return "The check was stopped. Nothing was deleted."; }
                if (bad.Count > 0) return "The old copy of " + n + " was not deleted: it no longer matches the new one (" + bad[0] + ").";
            }
            var blockers = MoveRules.Blockers(j.From, procs, OwnPid());
            if (blockers.Count > 0) return "Close these first: " + string.Join(", ", blockers.ToArray()) + ".";
            // the old place must still carry this install's marker: it was VGAC's, so its fixed-name folders may go
            string sid = DataFolders.SettingsId(MachineFile.Load(l.ProjectRoot));
            if (string.IsNullOrEmpty(sid) || DataFolders.ReadMarkerId(j.From) != sid) return "The old place no longer carries VGAC's marker, so nothing in it was deleted.";
            foreach (string n in j.Done)
            {
                string src = Path.Combine(j.From, n);
                if (!MoveRules.IsKnownName(n) || !Directory.Exists(src) || SafeFs.IsLink(src) || !SafeFs.Inside(src, j.From)) continue;
                FsReport r = SafeFs.DeleteTree(src);
                if (r.Locked.Count > 0) return "Some old files are in use and were kept: " + r.Locked[0];
            }
            try { string tools = Path.Combine(j.From, "tools"); if (Directory.Exists(tools) && !SafeFs.IsLink(tools) && Directory.GetFileSystemEntries(tools).Length == 0) Directory.Delete(tools); } catch (Exception) { }
            try
            {
                if (!Proof.IsDangerousFolder(j.From) && Directory.Exists(j.From) && ledger.Has(j.From, "folder"))
                {
                    bool onlyMarker = true;
                    foreach (string e in Directory.GetFileSystemEntries(j.From)) if (!string.Equals(Path.GetFileName(e), DataFolders.MarkerName, StringComparison.OrdinalIgnoreCase)) onlyMarker = false;
                    if (onlyMarker) { File.Delete(Path.Combine(j.From, DataFolders.MarkerName)); Directory.Delete(j.From); ledger.RemoveUnder(j.From); ledger.Save(); }
                }
            } catch (Exception) { }
            try { File.Delete(JournalPath(l.ProjectRoot)); } catch (Exception) { }
            log.Line("move: the old copy was deleted");
            return null;
        }
    }
}
