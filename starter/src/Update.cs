// Updating after a pull: swapping in a rebuilt set of screens without a gap, getting out of the way of git (a running
// VGAC.exe cannot be overwritten, but it can be renamed), and running the pull itself. C# 5 (see Core.cs).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace Vgac.Starter
{
    // The screens are built into frontend\dist-new and then swapped in with two renames, so the running backend never
    // serves a half-built folder. Names are fixed, so a leftover can always be recognised as ours.
    public static class DistSwap
    {
        public const string NewName = "dist-new", OldName = "dist-old", DistName = "dist";

        static string P(string fd, string n) { return Path.Combine(fd, n); }

        // Files that the page names (src and href under assets/) and that are not in the folder.
        public static List<string> MissingAssets(string distDir)
        {
            var missing = new List<string>();
            string index = Path.Combine(distDir, "index.html");
            if (!File.Exists(index)) { missing.Add("index.html"); return missing; }
            string html = File.ReadAllText(index, Encoding.UTF8);
            foreach (Match m in Regex.Matches(html, "(?:src|href)=\"(/?assets/[^\"?#]+)"))
            {
                string rel = m.Groups[1].Value.TrimStart('/').Replace('/', '\\');
                if (!File.Exists(Path.Combine(distDir, rel))) missing.Add(rel);
            }
            return missing;
        }

        // null when the new screens are in place; else one plain sentence (nothing is changed then).
        public static string Swap(string frontendDir)
        {
            string dn = P(frontendDir, NewName), dist = P(frontendDir, DistName), old = P(frontendDir, OldName);
            if (!Directory.Exists(dn)) return "The new screens were not built.";
            List<string> missing = MissingAssets(dn);
            if (missing.Count > 0) return "The new screens are incomplete (" + missing[0] + " is missing), so they were not used.";
            try
            {
                if (Directory.Exists(old)) SafeFs.DeleteTree(old);
                bool hadDist = Directory.Exists(dist);
                SafeFs.Point("swap", "before-first-rename");
                if (hadDist) SafeFs.MoveDir(dist, old);
                SafeFs.Point("swap", "between-renames");
                try { SafeFs.MoveDir(dn, dist); }
                catch (Exception)
                {
                    if (hadDist && !Directory.Exists(dist) && Directory.Exists(old)) SafeFs.MoveDir(old, dist);
                    throw;
                }
                SafeFs.Point("swap", "before-delete-old");
                if (Directory.Exists(old)) SafeFs.DeleteTree(old);
                return null;
            }
            catch (IOException e) { return "The screens could not be swapped (" + e.Message + "); the old ones are still in place."; }
            catch (UnauthorizedAccessException e) { return "The screens could not be swapped (" + e.Message + "); the old ones are still in place."; }
        }

        // At start, before anything reads dist: finish or undo a swap that was cut off. Returns a plain note or null.
        public static string Recover(string frontendDir)
        {
            string dn = P(frontendDir, NewName), dist = P(frontendDir, DistName), old = P(frontendDir, OldName);
            string note = null;
            try
            {
                if (!Directory.Exists(dist))
                {
                    if (Directory.Exists(dn) && MissingAssets(dn).Count == 0) { SafeFs.MoveDir(dn, dist); note = "A screen update that was cut off was finished."; }
                    else if (Directory.Exists(old) && MissingAssets(old).Count == 0) { SafeFs.MoveDir(old, dist); note = "A screen update that was cut off was undone; the earlier screens are back."; }
                }
                if (Directory.Exists(dist))
                {
                    if (Directory.Exists(dn)) { SafeFs.DeleteTree(dn); note = note ?? "A leftover from an earlier screen build was removed."; }
                    if (Directory.Exists(old)) { SafeFs.DeleteTree(old); note = note ?? "A leftover from an earlier screen update was removed."; }
                }
            }
            catch (IOException e) { note = "Leftovers of a screen update could not be cleaned up (" + e.Message + ")."; }
            catch (UnauthorizedAccessException e) { note = "Leftovers of a screen update could not be cleaned up (" + e.Message + ")."; }
            return note;
        }
    }

    // Getting VGAC.exe out of git's way. Windows does not allow overwriting a running program but does allow renaming it.
    public static class ExeAside
    {
        public const string AsideName = "VGAC.old.exe";

        public static string RecordPath(string root) { return Path.Combine(root, "local", "exe-aside.json"); }

        public static string RestoreNotePath(string root) { return Path.Combine(root, "VGAC-RESTORE.txt"); }

        public static void ClearRestoreNote(string root) { try { File.Delete(RestoreNotePath(root)); } catch (Exception) { } }

        public static string AsidePath(string exePath) { return Path.Combine(Path.GetDirectoryName(exePath), AsideName); }

        // null when VGAC.exe now has another name (and what it was is recorded); else a plain sentence and nothing changed.
        public static string Aside(string exePath, string root) { return Aside(exePath, root, true); }

        // `published`: the file is the one the project publishes (its hash is the one in starter/CHECKSUMS.txt). A file the
        // person built themselves is renamed too but is never deleted afterwards.
        public static string Aside(string exePath, string root, bool published)
        {
            string aside = AsidePath(exePath);
            string record = RecordPath(root);
            try
            {
                if (File.Exists(aside))
                {
                    // an earlier leftover: only the app's own recorded, published file may be replaced
                    if (!IsOurLeftover(aside, root)) return "A file named " + AsideName + " is already there and VGAC did not make it. Move it away, then try again.";
                    if (!WasPublished(root)) return AsideName + " is your own build of VGAC.exe from an earlier update, so it is not replaced. Move or rename it, then try again.";
                    File.Delete(aside);
                }
                string sha = Hashing.Sha256File(exePath);
                var d = new Dictionary<string, object>();
                d["sha256"] = sha; d["path"] = aside; d["published"] = published; d["when"] = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ");
                Directory.CreateDirectory(Path.GetDirectoryName(record));
                File.WriteAllText(record, Json.Write(d), new UTF8Encoding(false));
                // If the computer dies between the rename and the checkout there is no VGAC.exe for a shortcut to start; this note says
                // what to do, in plain words, beside the project's files. It is removed as soon as the update is through.
                try { File.WriteAllText(RestoreNotePath(root), "VGAC.exe is missing because an update stopped halfway.\r\nRename " + AsideName + " to VGAC.exe (or run: git checkout VGAC.exe), then start VGAC again.\r\n"); } catch (Exception) { }
                SafeFs.Point("aside", "before-rename");
                try { File.Move(exePath, aside); }
                catch (Exception)
                {
                    try { File.Delete(record); } catch (Exception) { }
                    try { File.Delete(RestoreNotePath(root)); } catch (Exception) { }
                    throw;
                }
                return null;
            }
            catch (IOException e) { return "VGAC could not step aside to let the update in (" + e.Message + "). Nothing was changed."; }
            catch (UnauthorizedAccessException e) { return "VGAC could not step aside to let the update in (" + e.Message + "). Nothing was changed."; }
        }

        // The pull failed before it changed anything: the person's own file goes back under its name, byte for byte.
        public static void RestoreOver(string exePath, string root)
        {
            string aside = AsidePath(exePath);
            try
            {
                if (!File.Exists(aside) || !IsOurLeftover(aside, root)) return;
                if (File.Exists(exePath)) File.Delete(exePath);
                File.Move(aside, exePath);
                File.Delete(RecordPath(root));
                ClearRestoreNote(root);
            }
            catch (Exception) { }
        }

        // The pull did not put a new file in place: the old one gets its name back.
        public static void Restore(string exePath, string root)
        {
            string aside = AsidePath(exePath);
            try
            {
                if (File.Exists(aside) && !File.Exists(exePath) && IsOurLeftover(aside, root)) File.Move(aside, exePath);
                if (File.Exists(exePath)) { try { File.Delete(RecordPath(root)); } catch (Exception) { } ClearRestoreNote(root); }
            }
            catch (Exception) { }
        }

        public static bool WasPublished(string root)
        {
            try { object v; return !Json.ParseObject(File.ReadAllText(RecordPath(root), Encoding.UTF8)).TryGetValue("published", out v) || !(v is bool) || (bool)v; }
            catch (Exception) { return false; }
        }

        public static bool IsOurLeftover(string aside, string root)
        {
            try
            {
                var d = Json.ParseObject(File.ReadAllText(RecordPath(root), Encoding.UTF8));
                return string.Equals(Convert.ToString(d["sha256"]), Hashing.Sha256File(aside), StringComparison.OrdinalIgnoreCase);
            }
            catch (Exception) { return false; }
        }

        // A crash between the rename and the checkout leaves no VGAC.exe. If this very program was started from VGAC.old.exe and
        // VGAC.exe is missing, it takes its own name back. Returns a log sentence or null.
        public static string RecoverMissing(string runningPath, string root)
        {
            try
            {
                if (!string.Equals(Path.GetFileName(runningPath), AsideName, StringComparison.OrdinalIgnoreCase)) return null;
                string exe = Path.Combine(Path.GetDirectoryName(runningPath), "VGAC.exe");
                if (File.Exists(exe) || !IsOurLeftover(runningPath, root)) return null;
                File.Move(runningPath, exe);
                ClearRestoreNote(root);
                try { File.Delete(RecordPath(root)); } catch (Exception) { }
                return "VGAC.exe was missing after an update that stopped halfway; this copy took its name back.";
            }
            catch (Exception e) { return "VGAC.exe is missing and could not be put back (" + e.Message + "). Rename " + AsideName + " to VGAC.exe."; }
        }

        // At start: the old file is deleted only when it is the one that was recorded. Returns a log sentence or null.
        public static string CleanupAtStart(string exePath, string root)
        {
            if (string.Equals(Path.GetFileName(exePath), AsideName, StringComparison.OrdinalIgnoreCase)) return null;   // that is this very program
            string aside = AsidePath(exePath);
            if (!File.Exists(aside)) return null;
            if (!IsOurLeftover(aside, root)) return AsideName + " is there but is not the file VGAC recorded, so it was left alone.";
            if (!WasPublished(root)) return AsideName + " is your own build of VGAC.exe, so it was kept.";
            try { File.Delete(aside); File.Delete(RecordPath(root)); return "The earlier VGAC.exe (" + AsideName + ") was removed."; }
            catch (Exception e) { return AsideName + " could not be removed yet (" + e.Message + ")."; }
        }
    }

    public interface IGit
    {
        // Runs git with these arguments in `cwd`; returns the exit code (-1: git could not be started) and its output.
        int Run(string args, string cwd, out string output);
    }

    public class RealGit : IGit
    {
        public int TimeoutMs = 180000;

        public int Run(string args, string cwd, out string output)
        {
            output = "";
            try
            {
                var psi = new ProcessStartInfo("git", args);
                psi.WorkingDirectory = cwd; psi.UseShellExecute = false; psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true; psi.RedirectStandardError = true;
                psi.EnvironmentVariables["GIT_TERMINAL_PROMPT"] = "0";   // never wait for a password nobody can type
                using (var p = Process.Start(psi))
                {
                    // read on other threads so a git that never ends cannot hold this window past the limit
                    var rOut = System.Threading.Tasks.Task.Factory.StartNew<string>(delegate { try { return p.StandardOutput.ReadToEnd(); } catch (Exception) { return ""; } });
                    var rErr = System.Threading.Tasks.Task.Factory.StartNew<string>(delegate { try { return p.StandardError.ReadToEnd(); } catch (Exception) { return ""; } });
                    if (!p.WaitForExit(TimeoutMs))
                    {
                        long started = 0; try { started = p.StartTime.ToUniversalTime().Ticks; } catch (Exception) { }
                        try { ProcessTree.Stop(new WmiProcesses(), new ProcessKiller(), p.Id, started); } catch (Exception) { }
                        try { if (!p.HasExited) p.Kill(); } catch (Exception) { }
                        output = "git did not finish in time";
                        return -1;
                    }
                    rOut.Wait(3000); rErr.Wait(3000);
                    output = (rOut.IsCompleted ? rOut.Result : "") + (rErr.IsCompleted ? rErr.Result : "");
                    return p.ExitCode;
                }
            }
            catch (System.ComponentModel.Win32Exception) { return -1; }
        }
    }

    public class UpdateResult
    {
        public bool Updated;          // the pull brought something
        public bool ExeChanged;       // a new VGAC.exe is in place: restart to use it
        public string Message;        // plain words, always set
    }

    public static class GitUpdate
    {
        // Why the working tree looks mid-pull (or null): the marker files git leaves while a merge, rebase or lock is open.
        public static string MidPull(string root)
        {
            string gitDir = Path.Combine(root, ".git");
            if (File.Exists(gitDir))
            {
                // a worktree: ".git" is a file "gitdir: <path>"
                try { string t = File.ReadAllText(gitDir).Trim(); if (t.StartsWith("gitdir:")) gitDir = t.Substring(7).Trim(); } catch (Exception) { }
            }
            if (!Directory.Exists(gitDir)) return null;
            foreach (string n in new[] { "MERGE_HEAD", "index.lock", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD" })
                if (File.Exists(Path.Combine(gitDir, n)) || Directory.Exists(Path.Combine(gitDir, n))) return n;
            return null;
        }

        public const string MidPullText = "A pull did not finish. Close VGAC, then pull again.";

        public static bool ExePublished(string exePath, string checksumsFile)
        {
            try
            {
                string want = File.ReadAllLines(checksumsFile, Encoding.UTF8)[0].Split(' ')[0].Trim();
                return string.Equals(want, Hashing.Sha256File(exePath), StringComparison.OrdinalIgnoreCase);
            }
            catch (Exception) { return false; }
        }

        // VGAC.exe differs from the published checksum: built here, or a pull stopped halfway. Only noted, never blocking.
        public static string ExeDiffersNote(string exePath, string checksumsFile)
        {
            try
            {
                string first = File.ReadAllLines(checksumsFile, Encoding.UTF8)[0];
                string want = first.Split(' ')[0].Trim();
                if (want.Length != 64) return null;
                if (string.Equals(want, Hashing.Sha256File(exePath), StringComparison.OrdinalIgnoreCase)) return null;
                return "VGAC.exe is not the published one (built on this computer, or a pull did not finish).";
            }
            catch (Exception) { return null; }
        }

        // The update run from the app: step aside, pull (fast-forward only, never a merge), put the name back if nothing came.
        public static UpdateResult Pull(string root, string exePath, IGit git)
        {
            var r = new UpdateResult();
            string output;
            if (git.Run("--version", root, out output) != 0) { r.Message = "Git is not installed, so VGAC cannot update itself. Install Git for Windows, or update from a terminal."; return r; }
            string mid = MidPull(root);
            if (mid != null) { r.Message = MidPullText; return r; }
            if (git.Run("rev-parse --is-inside-work-tree", root, out output) != 0) { r.Message = "This folder is not a git clone, so there is nothing to pull."; return r; }
            string before; git.Run("rev-parse HEAD", root, out before);
            bool exeInside = exePath != null && File.Exists(exePath) && SafeFs.Inside(exePath, root);
            string oldSha = null;
            if (exeInside)
            {
                oldSha = Hashing.Sha256File(exePath);
                string why = ExeAside.Aside(exePath, root, ExePublished(exePath, Path.Combine(root, "starter", "CHECKSUMS.txt")));
                if (why != null) { r.Message = why; return r; }
                // git sees the renamed file as deleted, and refuses a pull that would change a file with local changes:
                // put the committed copy back under its name (a new file; the running one is the renamed one)
                string rel = exePath.Substring(Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar).Length + 1).Replace(Path.DirectorySeparatorChar, '/');
                string co;
                if (git.Run("checkout -- \"" + rel + "\"", root, out co) != 0)
                {
                    ExeAside.Restore(exePath, root);
                    r.Message = "VGAC could not prepare the update (" + LastLines(co, 2) + "). Nothing was changed.";
                    return r;
                }
            }
            int code = git.Run("pull --ff-only", root, out output);
            string after; git.Run("rev-parse HEAD", root, out after);
            bool exeBack = exeInside && File.Exists(exePath);
            bool headMoved = (before ?? "").Trim() != (after ?? "").Trim();
            if (code == -1)
            {
                // git was stopped (it took too long) or could not run: it may have stopped in the middle of writing files. Nothing is
                // tidied up here and nothing is claimed.
                r.Message = "Git was stopped because it took too long, and it may have stopped partway. Look at the project folder with git status before you carry on. If VGAC.exe is missing, rename " + ExeAside.AsideName + " to VGAC.exe (see VGAC-RESTORE.txt).";
                return r;
            }
            if (code != 0)
            {
                if (exeInside && !headMoved) ExeAside.RestoreOver(exePath, root);
                r.Message = "The update did not go through. Git said: " + LastLines(output, 3) + (headMoved ? " Git stopped partway: look at the project folder with git status before you carry on." : " Nothing of yours was changed.");
                return r;
            }
            // Up to date: nothing came, so VGAC.exe goes back to exactly what the person had (an own build included).
            if (exeInside && !headMoved) { ExeAside.RestoreOver(exePath, root); exeBack = false; }
            else ExeAside.ClearRestoreNote(root);
            r.Updated = headMoved;
            r.ExeChanged = exeBack && !string.Equals(oldSha, Hashing.Sha256File(exePath), StringComparison.OrdinalIgnoreCase);
            r.Message = !r.Updated ? "VGAC is already up to date." : (r.ExeChanged ? "VGAC was updated, including VGAC.exe. Restart VGAC to use the new version." : "VGAC was updated. Restart VGAC to use it.");
            return r;
        }

        public static string LastLines(string text, int n)
        {
            if (string.IsNullOrEmpty(text)) return "(nothing)";
            string[] lines = text.Replace("\r", "").Split('\n');
            var keep = new List<string>();
            for (int i = lines.Length - 1; i >= 0 && keep.Count < n; i--) if (lines[i].Trim().Length > 0) keep.Insert(0, lines[i].Trim());
            return string.Join(" ", keep.ToArray());
        }
    }

    // Updating while the app runs: asked like quit when work would be lost; refused when the engine is not ours.
    public static class UpdatePolicy
    {
        public static QuitPlan Decide(bool ownsBackend, int jobs, bool foreignBackend)
        {
            var plan = new QuitPlan();
            plan.DefaultCancel = true;
            if (foreignBackend)
            {
                plan.Ask = true; plan.Block = true;
                plan.Message = "The app's engine was not started by this launcher";
                plan.Detail = "VGAC cannot update it safely while it runs. Close it, then press Update.";
                return plan;
            }
            if (ownsBackend && jobs > 0)
            {
                plan.Ask = true;
                plan.Message = "Update the app now?";
                plan.OkLabel = "Update and lose them";
                plan.Detail = "The update restarts the app's engine. " + (jobs == 1 ? "1 job is running or waiting and will be lost." : jobs + " jobs are running or waiting and will be lost.");
                return plan;
            }
            plan.DefaultCancel = false;
            return plan;
        }
    }
}
