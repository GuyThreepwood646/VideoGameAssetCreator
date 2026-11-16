// "Check and repair": a read-only check of what VGAC itself installed (the app's Python and packages, the screens, uv and
// Node, the settings records), a plain line for each, and fixes that only ever touch what VGAC made. Anything linked or
// not made by VGAC is reported and left alone. C# 5 (see Core.cs).
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace Vgac.Starter
{
    public class CheckLine
    {
        public string Id;       // what is checked
        public string Status;   // ok | fix | info | notmine
        public string Text;     // one plain sentence
        public string FixId;    // set when Status is fix
        public CheckLine(string id, string status, string text, string fixId) { Id = id; Status = status; Text = text; FixId = fixId; }
    }

    public static class Recovery
    {
        // The settings file, or an empty one when it cannot be read (then nothing is shown to be VGAC's).
        public static MachineFile SettingsOrEmpty(string root)
        {
            try { return MachineFile.Load(root); } catch (StarterError) { return new MachineFile(); }
        }

        // The folder for the big files is VGAC's (its marker matches the settings): fixed-name leftovers inside it are VGAC's too.
        public static bool DataOurs(Layout l)
        {
            return DataFolders.Check(l.DataFolder, SettingsOrEmpty(l.ProjectRoot), l.ProjectRoot).State == DataState.Ours;
        }

        // Run at every start before anything reads these folders: finishes or undoes what an interrupted job left.
        // Returns plain notes for the log (empty when there was nothing).
        public static List<string> AtStart(Layout l, string exePath)
        {
            var notes = new List<string>();
            string n = DistSwap.Recover(l.FrontendDir);
            if (n != null) notes.Add(n);
            string venvOld = l.Venv + "-old";
            if (Directory.Exists(venvOld))
            {
                if (File.Exists(l.VenvPython)) { SafeFs.DeleteTree(venvOld); notes.Add("A leftover of an earlier Python rebuild was removed."); }
                else if (!Directory.Exists(l.Venv)) { SafeFs.MoveDir(venvOld, l.Venv); notes.Add("A Python rebuild that was cut off was undone; the earlier Python is back."); }
            }
            bool dataOurs = DataOurs(l);
            foreach (string target in new[] { Path.GetDirectoryName(l.UvExe), l.NodeDir })
            {
                string aside = target + ".old";
                if (!dataOurs || !Directory.Exists(aside) || SafeFs.IsLink(aside)) continue;   // fixed names inside VGAC's own folder only
                bool ok = File.Exists(Path.Combine(target, target == l.NodeDir ? "node.exe" : "uv.exe"));
                if (ok) { SafeFs.DeleteTree(aside); notes.Add("A leftover of an earlier tool update was removed."); }
                else if (!Directory.Exists(target)) { SafeFs.MoveDir(aside, target); notes.Add("A tool update that was cut off was undone."); }
            }
            // half-unpacked tool folders: the two fixed names only, and only inside the folder that is VGAC's
            foreach (string half in new[] { Path.GetDirectoryName(l.UvExe) + ".new", l.NodeDir + ".new" })
                if (dataOurs && Directory.Exists(half) && !SafeFs.IsLink(half)) { SafeFs.DeleteTree(half); notes.Add("A half-unpacked tool folder was removed."); }
            if (exePath != null)
            {
                string exeDir = Path.GetDirectoryName(exePath);
                if (File.Exists(Path.Combine(exeDir, "VGAC.exe"))) ExeAside.ClearRestoreNote(l.ProjectRoot);   // nothing is missing: the note would mislead
                string rec = ExeAside.RecoverMissing(exePath, l.ProjectRoot);
                if (rec != null) notes.Add(rec);
                string e = ExeAside.CleanupAtStart(exePath, l.ProjectRoot);
                if (e != null) notes.Add(e);
            }
            return notes;
        }
    }

    public static class Repair
    {
        public static List<CheckLine> Check(Layout l, IHost host, Ledger ledger)
        {
            var res = new List<CheckLine>();
            // the app's Python
            string vpy = l.VenvPython;
            if (!File.Exists(vpy)) res.Add(new CheckLine("python", "fix", "The app's Python is missing. It can be made again (about a minute).", "venv"));
            else if (host.Capture(vpy, "-c \"import sys; print(sys.version_info[0])\"") == null) res.Add(new CheckLine("python", "fix", "The app's Python does not start. It can be made again (about a minute).", "venv"));
            else
            {
                res.Add(new CheckLine("python", "ok", "The app's Python starts.", null));
                if (host.Capture(vpy, "-c \"import uvicorn, fastapi\"") == null) res.Add(new CheckLine("packages", "fix", "The app's Python packages are damaged or missing. They can be installed again.", "packages"));
                else res.Add(new CheckLine("packages", "ok", "The app's Python packages load.", null));
            }
            // the tools VGAC fetched
            if (File.Exists(l.UvExe) && host.Capture(l.UvExe, "--version") != null) res.Add(new CheckLine("uv", "ok", "The package tool (uv) works.", null));
            else res.Add(new CheckLine("uv", "fix", "The package tool (uv) is missing or does not start. It can be fetched again.", "uv"));
            string node = ProbeReader.FindNode(l, host);
            if (node != null) res.Add(new CheckLine("node", "ok", "Node.js is available for building the screens.", null));
            else res.Add(new CheckLine("node", "fix", "Node.js (used only to build the screens) is missing. It can be fetched again.", "node"));
            // the screens
            List<string> missing = DistSwap.MissingAssets(Path.Combine(l.FrontendDir, "dist"));
            if (missing.Count == 0) res.Add(new CheckLine("screens", "ok", "The screens are built and complete.", null));
            else res.Add(new CheckLine("screens", "fix", "The screens are missing or incomplete (" + missing[0] + "). They can be built again (about a minute).", "screens"));
            // places and records
            res.Add(DataFolderLine(l, host));
            try { MachineFile.Load(l.ProjectRoot); res.Add(new CheckLine("machine", "ok", "This computer's settings file reads.", null)); }
            catch (StarterError e) { res.Add(new CheckLine("machine", "info", e.Message + " " + e.Fix, null)); }
            if (File.Exists(l.StatePath))
            {
                try { Json.ParseObject(File.ReadAllText(l.StatePath, Encoding.UTF8)); res.Add(new CheckLine("state", "ok", "The record of what the install was made from reads.", null)); }
                catch (Exception) { res.Add(new CheckLine("state", "fix", "The record of what the install was made from is damaged. It can be written again (the steps run again once).", "state")); }
            }
            // leftovers of interrupted jobs, by their fixed names only
            var left = Leftovers(l, Recovery.DataOurs(l));
            if (left.Count > 0) res.Add(new CheckLine("leftovers", "fix", left.Count + " leftover item(s) from an interrupted job can be removed: " + string.Join(", ", left.ConvertAll<string>(delegate(string p) { return Path.GetFileName(p); }).ToArray()) + ".", "leftovers"));
            // what is not VGAC's to repair
            int linked = 0;
            foreach (object o in ledger.Items) { var d = o as Dictionary<string, object>; if (d != null && Convert.ToString(d.ContainsKey("kind") ? d["kind"] : "") == "linked") linked++; }
            if (linked > 0) res.Add(new CheckLine("linked", "notmine", linked + " tool(s) were linked, not installed by VGAC. They are never changed here; the app's own check tells you if one stops working.", null));
            return res;
        }

        static CheckLine DataFolderLine(Layout l, IHost host)
        {
            DataStatus ds = DataFolders.Check(l.DataFolder, Recovery.SettingsOrEmpty(l.ProjectRoot), l.ProjectRoot);
            if (ds.State == DataState.NotOurs)
            {
                if (ds.Problem) return new CheckLine("data", "info", "The folder for the big files (" + l.DataFolder + ") is not one VGAC will set up, move or remove in. " + ds.Why + " Choose another folder (Move the big files, or starter.dataFolder in local\\machine.json).", null);
                if (ds.Adoptable) return new CheckLine("data", "fix", "The folder for the big files (" + l.DataFolder + ") is not marked as VGAC's yet, but it holds nothing except what looks like VGAC's own folders. It can be adopted: Remove and Move will then act on " + string.Join(", ", ds.AdoptList.ToArray()) + ".", "adopt");
                return new CheckLine("data", "info", "The folder for the big files (" + l.DataFolder + ") is not shown to be VGAC's. " + ds.Why + " VGAC will not remove or move anything in it. Choose another folder.", null);
            }
            if (!Directory.Exists(l.DataFolder)) return new CheckLine("data", "info", "The folder for the big files (" + l.DataFolder + ") does not exist yet. It is made when needed.", null);
            try
            {
                string t = Path.Combine(l.DataFolder, ".vgac-write-test");
                File.WriteAllText(t, "x"); File.Delete(t);
            }
            catch (Exception) { return new CheckLine("data", "info", "Windows does not let VGAC write in " + l.DataFolder + ". Choose a folder in your own user area (Move the big files).", null); }
            long free = host.FreeBytes(l.DataFolder);
            if (free >= 0 && free < (1L << 30)) return new CheckLine("data", "info", "Less than 1 GB is free where the big files are (" + (free >> 20) + " MB).", null);
            return new CheckLine("data", "ok", "The folder for the big files is writable and has room.", null);
        }

        // Items left behind by an interrupted job. Fixed names only (no wildcards); the ones in the folder for the big files only when
        // that folder is VGAC's (`dataOurs`), so a shared folder's contents are never swept up.
        public static List<string> Leftovers(Layout l, bool dataOurs)
        {
            var res = new List<string>();
            foreach (string p in new[] { Path.Combine(l.FrontendDir, DistSwap.NewName), Path.Combine(l.FrontendDir, DistSwap.OldName) })
                if (Directory.Exists(p) && !SafeFs.IsLink(p)) res.Add(p);
            string venvOld = l.Venv + "-old";
            if (Directory.Exists(venvOld) && !SafeFs.IsLink(venvOld) && File.Exists(Path.Combine(venvOld, "pyvenv.cfg"))) res.Add(venvOld);
            foreach (string p in new[] { Path.GetDirectoryName(l.UvExe) + ".old", l.NodeDir + ".old", Path.GetDirectoryName(l.UvExe) + ".new", l.NodeDir + ".new" })
                if (dataOurs && Directory.Exists(p) && !SafeFs.IsLink(p)) res.Add(p);
            if (dataOurs && Directory.Exists(l.Downloads))
                foreach (string f in Directory.GetFiles(l.Downloads))
                    if (f.EndsWith(".bad", StringComparison.OrdinalIgnoreCase)) res.Add(f);
            return res;
        }

        // The setup steps a fix needs, in order.
        public static List<string> StepsFor(string fixId, Layout l)
        {
            var s = new List<string>();
            switch (fixId)
            {
                case "venv": if (!File.Exists(l.UvExe)) s.Add("uv"); s.Add("venv"); s.Add("packages"); break;
                case "packages": if (!File.Exists(l.UvExe)) s.Add("uv"); s.Add("packages"); break;
                case "uv": s.Add("uv"); break;
                case "node": s.Add("node"); break;
                case "screens": s.Add("screens"); break;
            }
            return s;
        }

        // Runs one fix. null when it worked; else a plain sentence. Never touches anything VGAC did not make.
        public static string Fix(string fixId, Layout l, IHost host, MachineFile machine, Ledger ledger, SetupLog log, Progress prog)
        {
            try
            {
                if (fixId == "leftovers")
                {
                    foreach (string p in Leftovers(l, Recovery.DataOurs(l))) { log.Line("repair: removing leftover " + p); SafeFs.DeleteTree(p); }
                    return null;
                }
                if (fixId == "adopt")
                {
                    // the window has shown the person exactly what will be treated as VGAC's and had a yes; checked again here
                    DataFolders.Adopt(l.DataFolder, machine, l.ProjectRoot);
                    log.Line("repair: the folder for the big files was adopted as VGAC's: " + l.DataFolder);
                    return null;
                }
                if (fixId == "state")
                {
                    log.Line("repair: removing the damaged record " + l.StatePath);
                    File.Delete(l.StatePath);
                    return null;
                }
                List<string> steps = StepsFor(fixId, l);
                if (steps.Count == 0) return "Nothing to do for " + fixId + ".";
                var runner = new SetupRunner(l, host, machine, ledger, log);
                runner.Run(steps, prog, false);
                return null;
            }
            catch (StarterError e) { log.Line("repair failed: " + e.Message); return e.Message + (e.Fix != null ? " " + e.Fix : ""); }
        }
    }
}
