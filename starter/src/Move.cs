// Moving the big files (the data folder) to another folder or drive. The old place stays whole and in use until the copy
// is verified and one small file is switched; every step is written to local/move.json so an interruption at any point
// can be continued or cancelled. The rules are in the phase 9 design note. C# 5 (see Core.cs).
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace Vgac.Starter
{
    public class MoveJournal
    {
        public string From, To;
        public string State = "copying";            // copying | copied | verified | switched | rebuilt
        public List<string> Done = new List<string>();      // names completely copied (renamed in from their .part)
        public List<string> Created = new List<string>();   // everything this move made in the new folder (so Cancel removes only that)
    }

    public static class MoveRules
    {
        // The only things in the data folder that VGAC made; anything else a person keeps there is not moved or touched.
        public static readonly string[] Names = { "tools", "python", "uv-cache", "downloads", "webview2", "comfyui" };

        public static List<string> NamesIn(string folder)
        {
            var res = new List<string>();
            foreach (string n in Names)
            {
                string p = Path.Combine(folder, n);
                if (Directory.Exists(p) && !SafeFs.IsLink(p)) res.Add(n);
            }
            return res;
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
            if (Directory.Exists(t) && Directory.GetFileSystemEntries(t).Length > 0)
            {
                bool ours = resuming != null && SafeFs.Norm(resuming.To) == SafeFs.Norm(t);
                if (ours)
                {
                    foreach (string e in Directory.GetFileSystemEntries(t))
                    {
                        string n = Path.GetFileName(e);
                        if (!resuming.Created.Contains(n) && !resuming.Created.Contains(n.Replace(".part", ""))) { bad.Add("That folder has files in it that VGAC did not put there (" + n + ")."); break; }
                    }
                }
                else bad.Add("That folder already has files in it. Choose an empty or new folder.");
            }
            if (freeBytes >= 0 && freeBytes < needBytes + needBytes / 10) bad.Add("The copy needs about " + RemovalSummary.Size(needBytes + needBytes / 10) + "; only " + RemovalSummary.Size(freeBytes) + " is free there.");
            if (comfy != null && comfy.Present(from) && !comfy.Offered(from)) bad.Add("VGAC's own ComfyUI is installed, and its install record is from an older version, so it cannot be moved yet.");
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

        public static MoveJournal Load(string root)
        {
            try
            {
                var d = Json.ParseObject(File.ReadAllText(JournalPath(root), Encoding.UTF8));
                var j = new MoveJournal();
                j.From = Convert.ToString(d["from"]); j.To = Convert.ToString(d["to"]); j.State = Convert.ToString(d["state"]);
                foreach (object o in (System.Collections.IEnumerable)d["done"]) j.Done.Add(Convert.ToString(o));
                foreach (object o in (System.Collections.IEnumerable)d["created"]) j.Created.Add(Convert.ToString(o));
                return j;
            }
            catch (Exception) { return null; }
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

        public long NeedBytes()
        {
            long n = 0;
            foreach (string name in MoveRules.NamesIn(l.DataFolder)) n += SafeFs.Size(Path.Combine(l.DataFolder, name), null);
            return n;
        }

        // Begins a move to `to` (null when it ran to the end of the copy and verification; else one plain sentence).
        public string Start(string to, Progress prog)
        {
            var existing = Load(l.ProjectRoot);
            if (existing != null) return "A move is already under way. Continue it or cancel it first.";
            string full = Path.GetFullPath(to);
            List<string> bad = MoveRules.Problems(l.DataFolder, full, l.ProjectRoot, Free(full), NeedBytes(), null, comfy);
            if (bad.Count > 0) return string.Join(" ", bad.ToArray());
            List<string> blockers = MoveRules.Blockers(l.DataFolder, procs, OwnPid());
            if (blockers.Count > 0) return "Close these first, they have files open in the old folder: " + string.Join(", ", blockers.ToArray()) + ".";
            var j = new MoveJournal(); j.From = l.DataFolder; j.To = full;
            Save(j);
            log.Line("move: started " + j.From + " -> " + j.To);
            return Continue(prog);
        }

        // Carries on from the journal (also after an interruption). null when the copy is verified and, if it got that far,
        // switched; else one plain sentence. Switching happens here so that "verified" and "switched" are one decision.
        public string Continue(Progress prog)
        {
            var j = Load(l.ProjectRoot);
            if (j == null) return "There is no move to continue.";
            if (j.State == "copying")
            {
                List<string> foreignFiles = MoveRules.Problems(j.From, j.To, l.ProjectRoot, -1, 0, j, comfy);
                if (foreignFiles.Count > 0) return string.Join(" ", foreignFiles.ToArray());
                List<string> blockers = MoveRules.Blockers(j.From, procs, OwnPid());
                if (blockers.Count > 0) return "Close these first, they have files open in the old folder: " + string.Join(", ", blockers.ToArray()) + ".";
                string r = CopyAll(j, prog);
                if (r != null) return r;
            }
            if (j.State == "copied") { string r = VerifyAll(j); if (r != null) return r; }
            if (j.State == "verified") Switch(j);
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
                    Directory.CreateDirectory(j.To);
                    if (!j.Created.Contains(name + ".part")) { j.Created.Add(name + ".part"); Save(j); }
                    if (Directory.Exists(part)) SafeFs.DeleteTree(part);                      // a half copy from before: ours (named in the journal)
                    if (Directory.Exists(final) && j.Created.Contains(name)) SafeFs.DeleteTree(final);
                    var rep = new FsReport();
                    SafeFs.CopyTree(src, part, name == "comfyui" ? new[] { ".venv" } : null, rep);
                    if (rep.Links.Count > 0) log.Line("move: " + rep.Links.Count + " link(s) in " + name + " were not copied");
                    SafeFs.Point("move", "before-rename:" + name);
                    SafeFs.MoveDir(part, final);
                    j.Created.Remove(name + ".part");
                    j.Created.Add(name); j.Done.Add(name);
                    Save(j);
                }
                catch (IOException e) { return "Copying " + name + " stopped: " + e.Message + " Nothing was changed in the old place. Free some room or choose another folder, then continue."; }
                catch (UnauthorizedAccessException e) { return "Windows did not allow writing " + name + " in the new folder (" + e.Message + ")."; }
            }
            j.State = "copied"; Save(j);
            return null;
        }

        string VerifyAll(MoveJournal j)
        {
            foreach (string name in j.Done)
            {
                string src = Path.Combine(j.From, name), dst = Path.Combine(j.To, name);
                if (!Directory.Exists(src)) continue;
                SafeFs.Point("move", "verify:" + name);
                List<string> bad = SafeFs.Compare(src, dst, name == "comfyui" ? new[] { ".venv" } : null, name == "tools");
                if (bad.Count > 0) return "The copy of " + name + " does not match the original (" + bad[0] + "). Nothing was switched; the old place is untouched. Cancel and try again.";
            }
            j.State = "verified"; Save(j);
            return null;
        }

        // The one decision: from now on the big files are in the new folder.
        void Switch(MoveJournal j)
        {
            SafeFs.Point("move", "before-switch");
            machine.Set("starter", "dataFolder", j.To);
            machine.Save();
            string oldRoot = SafeFs.Norm(j.From);
            for (int i = 0; i < ledger.Items.Count; i++)
            {
                var d = ledger.Items[i] as Dictionary<string, object>;
                if (d == null || !d.ContainsKey("path")) continue;
                string p = Convert.ToString(d["path"]);
                string np = Path.GetFullPath(p).TrimEnd('\\');
                if (np.ToLowerInvariant() == oldRoot || np.ToLowerInvariant().StartsWith(oldRoot + "\\")) d["path"] = j.To.TrimEnd('\\') + np.Substring(j.From.TrimEnd('\\').Length);
            }
            ledger.Save();
            j.State = "switched"; Save(j);
            log.Line("move: switched to " + j.To);
        }

        // The app's own ComfyUI is told where it is now and makes its Python again (offline). The backend's own Python is
        // made again by the normal start, which sees that the data folder changed.
        string Rebuild(MoveJournal j)
        {
            if (comfy != null && comfy.Present(j.To))
            {
                string r = comfy.Retarget(j.From, j.To);
                if (r != null) return "The files are moved, but VGAC's own ComfyUI could not be told: " + r + " Continue to try again.";
                r = comfy.RebuildVenv(j.To);
                if (r != null) return "The files are moved, but VGAC's own ComfyUI Python could not be made again: " + r + " Continue to try again.";
            }
            j.State = "rebuilt"; Save(j);
            return null;
        }

        // Cancel before the switch: remove only what this move made in the new folder. The old place was never changed.
        public string Cancel()
        {
            var j = Load(l.ProjectRoot);
            if (j == null) return null;
            if (j.State == "switched" || j.State == "rebuilt")
            {
                // already using the new folder: undo means pointing back at the old one, which is still whole
                machine.Set("starter", "dataFolder", j.From); machine.Save();
                string oldTo = SafeFs.Norm(j.To);
                for (int i = 0; i < ledger.Items.Count; i++)
                {
                    var d = ledger.Items[i] as Dictionary<string, object>;
                    if (d == null || !d.ContainsKey("path")) continue;
                    string np = Path.GetFullPath(Convert.ToString(d["path"])).TrimEnd('\\');
                    if (np.ToLowerInvariant() == oldTo || np.ToLowerInvariant().StartsWith(oldTo + "\\")) d["path"] = j.From.TrimEnd('\\') + np.Substring(j.To.TrimEnd('\\').Length);
                }
                ledger.Save();
                log.Line("move: undone, pointing back at " + j.From);
            }
            foreach (string n in j.Created)
            {
                string p = Path.Combine(j.To, n);
                if (Directory.Exists(p) && !SafeFs.IsLink(p)) SafeFs.DeleteTree(p);
            }
            try { if (Directory.Exists(j.To) && Directory.GetFileSystemEntries(j.To).Length == 0) Directory.Delete(j.To); } catch (Exception) { }
            try { File.Delete(JournalPath(l.ProjectRoot)); } catch (Exception) { }
            return null;
        }

        // What is left in the old folder after a finished move, by name and size.
        public static List<string> OldCopy(MoveJournal j)
        {
            var res = new List<string>();
            foreach (string n in j.Done) { string p = Path.Combine(j.From, n); if (Directory.Exists(p)) res.Add(p); }
            return res;
        }

        // After "rebuilt": delete the old copy, but only names that were copied, only after comparing them with the new copy again.
        public string DeleteOld()
        {
            var j = Load(l.ProjectRoot);
            if (j == null || j.State != "rebuilt") return "There is no finished move whose old copy can be deleted.";
            foreach (string n in j.Done)
            {
                string src = Path.Combine(j.From, n), dst = Path.Combine(j.To, n);
                if (!Directory.Exists(src)) continue;
                List<string> bad = SafeFs.Compare(src, dst, n == "comfyui" ? new[] { ".venv" } : null, false);
                if (bad.Count > 0) return "The old copy of " + n + " was not deleted: it no longer matches the new one (" + bad[0] + ").";
            }
            var blockers = MoveRules.Blockers(j.From, procs, OwnPid());
            if (blockers.Count > 0) return "Close these first: " + string.Join(", ", blockers.ToArray()) + ".";
            foreach (string n in j.Done)
            {
                string src = Path.Combine(j.From, n);
                if (!Directory.Exists(src) || SafeFs.IsLink(src)) continue;
                FsReport r = SafeFs.DeleteTree(src);
                if (r.Locked.Count > 0) return "Some old files are in use and were kept: " + r.Locked[0];
            }
            try { if (Directory.Exists(j.From) && Directory.GetFileSystemEntries(j.From).Length == 0 && ledger.Has(j.From, "folder")) { Directory.Delete(j.From); ledger.RemoveUnder(j.From); ledger.Save(); } } catch (Exception) { }
            try { File.Delete(JournalPath(l.ProjectRoot)); } catch (Exception) { }
            log.Line("move: the old copy was deleted");
            return null;
        }
    }
}
