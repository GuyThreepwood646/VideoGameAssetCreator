// The launcher's decisions, kept apart from windows and processes so they can be tested without either: what the page's
// bridge may do, what the status rows say, whether quitting needs a question, which processes belong to the starter,
// and which second start brings the first window forward. C# 5 on purpose (see Core.cs).
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading;

namespace Vgac.Starter
{
    // ---- the page's bridge: what is accepted ---------------------------------------------------------------------

    // Same rules as desktop/appbridge.js: the page may talk only from the backend's own origin, a count is a whole number
    // 0..99, an icon is a small PNG data URL and nothing else, text is plain and short.
    public static class BridgeRules
    {
        public const int MaxIconBytes = 16 * 1024;
        const string PngPrefix = "data:image/png;base64,";

        public static int CleanCount(object n)
        {
            double v;
            if (n == null) return 0;
            if (!double.TryParse(Convert.ToString(n, System.Globalization.CultureInfo.InvariantCulture), System.Globalization.NumberStyles.Float,
                    System.Globalization.CultureInfo.InvariantCulture, out v)) return 0;
            if (double.IsNaN(v) || double.IsInfinity(v)) return 0;
            v = Math.Floor(v);
            if (v <= 0) return 0;
            return v > 99 ? 99 : (int)v;
        }

        // The PNG's bytes, or null. Never a path, a URL or anything larger than a badge.
        public static byte[] CleanIcon(string dataUrl)
        {
            if (dataUrl == null || !dataUrl.StartsWith(PngPrefix, StringComparison.Ordinal)) return null;
            string b64 = dataUrl.Substring(PngPrefix.Length);
            if (b64.Length == 0 || b64.Length > (MaxIconBytes * 4 + 2) / 3 + 4) return null;
            foreach (char c in b64)
                if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '+' || c == '/' || c == '=')) return null;
            byte[] bytes;
            try { bytes = Convert.FromBase64String(b64); }
            catch (FormatException) { return null; }
            if (bytes.Length > MaxIconBytes || bytes.Length < 8) return null;
            byte[] magic = { 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A };
            for (int i = 0; i < magic.Length; i++) if (bytes[i] != magic[i]) return null;
            return bytes;
        }

        public static string CleanText(string s, int max)
        {
            if (s == null) return "";
            var sb = new StringBuilder();
            foreach (char c in s) sb.Append(c < ' ' ? ' ' : c);
            string t = sb.ToString();
            return t.Length > max ? t.Substring(0, max) : t;
        }
    }

    // ---- processes -------------------------------------------------------------------------------------------------

    public class ProcInfo
    {
        public int Pid;
        public int ParentPid;
        public string Name = "";
        public string ExePath = "";
        public string CommandLine = "";
        public long CreatedTicks;   // UTC, 0 when unknown
    }

    public interface IProcessSource
    {
        List<ProcInfo> List();
    }

    public interface IKiller
    {
        // Stops the process only if it is still the one that started at `createdTicks`; false when it was not stopped.
        bool Kill(int pid, long createdTicks);
    }

    public static class ProcessTree
    {
        public static List<int> Targets(List<ProcInfo> all, int rootPid)
        {
            var ids = new List<int>();
            foreach (ProcInfo p in TargetInfos(all, rootPid)) ids.Add(p.Pid);
            return ids;
        }

        // The root and everything below it, children before parents. A child counts only if it was created after its
        // parent: a process whose parent has died keeps the old parent id, and Windows reuses ids.
        public static List<ProcInfo> TargetInfos(List<ProcInfo> all, int rootPid)
        {
            var byParent = new Dictionary<int, List<ProcInfo>>();
            var byPid = new Dictionary<int, ProcInfo>();
            foreach (ProcInfo p in all)
            {
                byPid[p.Pid] = p;
                List<ProcInfo> l;
                if (!byParent.TryGetValue(p.ParentPid, out l)) { l = new List<ProcInfo>(); byParent[p.ParentPid] = l; }
                l.Add(p);
            }
            var order = new List<ProcInfo>();
            var seen = new HashSet<int>();
            Walk(rootPid, byParent, byPid, seen, order);
            return order;
        }

        static void Walk(int pid, Dictionary<int, List<ProcInfo>> byParent, Dictionary<int, ProcInfo> byPid, HashSet<int> seen, List<ProcInfo> order)
        {
            if (!seen.Add(pid)) return;
            List<ProcInfo> kids;
            if (byParent.TryGetValue(pid, out kids))
            {
                ProcInfo parent;
                byPid.TryGetValue(pid, out parent);
                foreach (ProcInfo k in kids)
                {
                    if (k.Pid == pid) continue;
                    if (parent != null && parent.CreatedTicks > 0 && k.CreatedTicks > 0 && k.CreatedTicks < parent.CreatedTicks) continue;
                    Walk(k.Pid, byParent, byPid, seen, order);
                }
            }
            ProcInfo me;
            if (byPid.TryGetValue(pid, out me)) order.Add(me);
        }

        // Stops the process tree of `rootPid`, children first. Every process is stopped only if it is still the one that
        // started at the recorded time (the killer checks through the handle it stops), so an id that was reused is never
        // stopped. A root start time of 0 means "unknown": nothing is stopped. If the process list cannot be read the root
        // alone is stopped (its identity is known). Returns the ids that were stopped.
        public static List<int> Stop(IProcessSource source, IKiller killer, int rootPid, long rootCreatedTicks)
        {
            var done = new List<int>();
            if (rootCreatedTicks <= 0) return done;
            List<ProcInfo> all;
            try { all = source.List(); } catch (Exception) { all = new List<ProcInfo>(); }
            foreach (ProcInfo p in TargetInfos(all, rootPid))
            {
                if (p.Pid == rootPid) continue;
                if (p.CreatedTicks <= 0) continue;   // identity unknown: left alone
                if (killer.Kill(p.Pid, p.CreatedTicks)) done.Add(p.Pid);
            }
            if (killer.Kill(rootPid, rootCreatedTicks)) done.Add(rootPid);
            return done;
        }
    }

    // What the starter wrote down about the backend it started, so a later start (after a crash) can tell whether the
    // backend still running is the same process. Only the backend is ever recorded, never an engine.
    public class OwnedRecord
    {
        public int Pid;
        public long CreatedTicks;
        public string Exe = "";
        public string CommandLine = "";
        public int StarterPid;              // the starter that wrote this record, and when it started
        public long StarterCreatedTicks;
    }

    public static class Adoption
    {
        public const long TickTolerance = 20000000; // 2 s in 100 ns ticks

        public static string FilePath(string projectRoot) { return Path.Combine(projectRoot, "local", "starter-owned.json"); }

        public static void Save(string projectRoot, OwnedRecord r)
        {
            string p = FilePath(projectRoot);
            Directory.CreateDirectory(Path.GetDirectoryName(p));
            var d = new Dictionary<string, object>();
            d["pid"] = r.Pid; d["created"] = r.CreatedTicks.ToString(); d["exe"] = r.Exe; d["cmd"] = r.CommandLine;
            d["starterPid"] = r.StarterPid; d["starterCreated"] = r.StarterCreatedTicks.ToString();
            string tmp = p + ".tmp";
            File.WriteAllText(tmp, Json.Write(d), new UTF8Encoding(false));
            if (File.Exists(p)) File.Delete(p);
            File.Move(tmp, p);
        }

        public static OwnedRecord Load(string projectRoot)
        {
            try
            {
                var d = Json.ParseObject(File.ReadAllText(FilePath(projectRoot), Encoding.UTF8));
                var r = new OwnedRecord();
                r.Pid = Convert.ToInt32(d["pid"]);
                r.CreatedTicks = Convert.ToInt64(d["created"]);
                r.Exe = Convert.ToString(d["exe"]);
                r.CommandLine = Convert.ToString(d["cmd"]);
                object sp, sc;
                if (d.TryGetValue("starterPid", out sp) && d.TryGetValue("starterCreated", out sc)) { r.StarterPid = Convert.ToInt32(sp); r.StarterCreatedTicks = Convert.ToInt64(sc); }
                return r;
            }
            catch (Exception) { return null; }
        }

        public static void Delete(string projectRoot)
        {
            try { File.Delete(FilePath(projectRoot)); } catch (Exception) { }
        }

        // The process still running is the recorded backend only when every one of these agree: the id, the creation time,
        // the program (the app's own Python, not just any python.exe) and the command line (vgac.main:app on this port).
        public static bool Matches(OwnedRecord r, ProcInfo live, string expectedExe, int port)
        {
            if (r == null || live == null || r.Pid <= 0 || live.Pid != r.Pid) return false;
            if (r.CreatedTicks <= 0 || live.CreatedTicks <= 0 || Math.Abs(live.CreatedTicks - r.CreatedTicks) > TickTolerance) return false;
            if (string.IsNullOrEmpty(r.Exe) || !string.Equals(live.ExePath, r.Exe, StringComparison.OrdinalIgnoreCase)) return false;
            if (!string.Equals(r.Exe, expectedExe, StringComparison.OrdinalIgnoreCase)) return false;
            if (!string.Equals(live.CommandLine, r.CommandLine, StringComparison.Ordinal)) return false;
            return live.CommandLine.IndexOf("vgac.main:app", StringComparison.OrdinalIgnoreCase) >= 0 && HasPort(live.CommandLine, port);
        }

        // "--port 8765" as a whole number: --port 87650 is another port.
        public static bool HasPort(string commandLine, int port)
        {
            return commandLine != null && System.Text.RegularExpressions.Regex.IsMatch(commandLine, "--port\\s+" + port + "(?!\\d)", System.Text.RegularExpressions.RegexOptions.IgnoreCase);
        }

        // The starter that wrote the record is still running (and is not this one): its backend is not up for taking over.
        public static bool WriterAlive(OwnedRecord r, int currentPid, long currentStartTicks, long runningStartTicksOfWriter)
        {
            if (r.StarterPid <= 0 || r.StarterCreatedTicks <= 0) return false;
            if (r.StarterPid == currentPid && Math.Abs(r.StarterCreatedTicks - currentStartTicks) <= TickTolerance) return false;
            return runningStartTicksOfWriter > 0 && Math.Abs(runningStartTicksOfWriter - r.StarterCreatedTicks) <= TickTolerance;
        }

        // The processes of `before` that are still, unchanged, in `after` (same id and the same start time).
        public static List<ProcInfo> StillSame(List<ProcInfo> before, List<ProcInfo> after)
        {
            var res = new List<ProcInfo>();
            foreach (ProcInfo b in before)
                foreach (ProcInfo a in after)
                    if (a.Pid == b.Pid && b.CreatedTicks > 0 && a.CreatedTicks > 0 && Math.Abs(a.CreatedTicks - b.CreatedTicks) <= TickTolerance) { res.Add(b); break; }
            return res;
        }

        // A process that looks like a backend on this port but is not recorded as ours.
        public static bool LooksLikeBackend(ProcInfo p, int port)
        {
            return p.CommandLine != null && p.Name != null
                && p.Name.StartsWith("python", StringComparison.OrdinalIgnoreCase)   // not a shell that merely mentions it
                && p.CommandLine.IndexOf("uvicorn vgac.main:app", StringComparison.OrdinalIgnoreCase) >= 0
                && HasPort(p.CommandLine, port);
        }
    }

    // ---- quit ------------------------------------------------------------------------------------------------------

    public class QuitInputs
    {
        public bool OwnsBackend;
        public bool SetupRunning;
        public int Jobs;           // running plus waiting, from the backend's /api/health queue
    }

    public class QuitPlan
    {
        public bool Ask;
        public string Message = "";
        public string Detail = "";
        public string OkLabel = "Quit";
        public string CancelLabel = "Cancel";
        public bool DefaultCancel;
        public bool Block;          // not a question: the thing cannot be done now
    }

    public static class QuitPolicy
    {
        // The test switch --exit-after quits by itself, but never while an owned backend has work in it.
        public static bool AutoExitAllowed(bool ownsBackend, int jobs) { return !(ownsBackend && jobs > 0); }

        // Nothing may start a backend once quitting has begun (for example setup that finishes as the person quits).
        public static bool MayStartBackend(bool quitting) { return !quitting; }

        public static QuitPlan Decide(QuitInputs q)
        {
            var plan = new QuitPlan();
            if (q.SetupRunning)
            {
                plan.Ask = true;
                plan.Message = "Quit while VGAC is setting up?";
                plan.Detail = "The setup stops now. Nothing is broken: start VGAC again and it carries on where it stopped.";
                plan.OkLabel = "Quit and stop setup";
                return plan;
            }
            if (!q.OwnsBackend) return plan; // nothing this launcher started is running: no question
            plan.Ask = true;
            plan.Message = "Quit Video Game Asset Creator?";
            plan.OkLabel = "Quit and stop them";
            var sb = new StringBuilder();
            sb.Append("This stops the app's engine, which this launcher started, and the graphics engines the app started with it. ");
            sb.Append("ComfyUI Desktop and anything else you started yourself is left running.");
            if (q.Jobs > 0)
            {
                sb.Append("\r\n\r\n").Append(q.Jobs == 1 ? "1 job is running or waiting and will be lost." : q.Jobs + " jobs are running or waiting and will be lost.");
                plan.DefaultCancel = true;
            }
            plan.Detail = sb.ToString();
            return plan;
        }
    }

    // ---- restarting ------------------------------------------------------------------------------------------------

    public static class RestartPolicy
    {
        // Replacing a backend this launcher does not own, or restarting one that has work in it, is asked first; Cancel is
        // the default in both cases. A restart that loses nothing is not asked.
        public static QuitPlan Decide(bool ownsBackend, int jobs, List<ProcInfo> foreign)
        {
            var plan = new QuitPlan();
            plan.CancelLabel = "Cancel"; plan.DefaultCancel = true;
            if (foreign != null && foreign.Count > 0)
            {
                var ids = new List<string>();
                foreach (ProcInfo p in foreign) ids.Add(p.Pid.ToString());
                plan.Ask = true;
                plan.Message = "The app's engine was not started by this launcher";
                plan.Detail = "A matching process is running (PID " + string.Join(", ", ids.ToArray()) + "). Restarting stops it and starts a new one owned by this launcher. Any job it is running is lost. Graphics engines it started keep running.";
                plan.OkLabel = "Stop it and start a new one";
                return plan;
            }
            if (ownsBackend && jobs > 0)
            {
                plan.Ask = true;
                plan.Message = "Restart the app's engine?";
                plan.Detail = (jobs == 1 ? "1 job is running or waiting and will be lost." : jobs + " jobs are running or waiting and will be lost.")
                    + "\r\n\r\nThe graphics engines the app started are stopped with it.";
                plan.OkLabel = "Restart and lose them";
                return plan;
            }
            plan.DefaultCancel = false;
            return plan;
        }
    }

    // ---- what the windows do with closes, clicks, keys and restarts (decided here, acted on in Program.cs) -----------------

    public enum CloseOutcome { Allow, AskQuit, Hide }
    public enum RestartStart { Go, Refused, BuildRunning }
    public enum FrontWindow { App, Status }
    public enum ShortcutAction { None, Quit, StatusWindow, Fullscreen }

    public static class LauncherRules
    {
        // The status window's X. Windows ending the session (or Task Manager) is never argued with. Before the app window
        // exists (or in the browser fallback) closing it means quit; afterwards it only hides, the tray stays.
        public static CloseOutcome StatusWindowClosing(bool quitting, bool systemEnding, bool appWindowOpen)
        {
            if (quitting || systemEnding) return CloseOutcome.Allow;
            return appWindowOpen ? CloseOutcome.Hide : CloseOutcome.AskQuit;
        }

        // The app window's X always means "quit" (asked first), never a plain close, unless quitting is already under way.
        public static CloseOutcome AppWindowClosing(bool quitting, bool hasQuitHandler)
        {
            return quitting || !hasQuitHandler ? CloseOutcome.Allow : CloseOutcome.AskQuit;
        }

        // Whether a restart may begin right now. Refused while another start or restart runs, after quit began, with no
        // backend object, or (separately told to the person) while the screens are being built.
        public static RestartStart Restart(bool engineBusy, bool hasBackend, bool quitting, bool buildRunning)
        {
            if (engineBusy || !hasBackend || quitting) return RestartStart.Refused;
            return buildRunning ? RestartStart.BuildRunning : RestartStart.Go;
        }

        // Which window a tray click, a double click or a second start brings forward.
        public static FrontWindow Target(bool appWindowOpen) { return appWindowOpen ? FrontWindow.App : FrontWindow.Status; }

        // Keys the launcher answers itself: Ctrl+Q quit, Ctrl+Shift+S status window, F11 fullscreen (app window).
        public static ShortcutAction Shortcut(bool ctrl, bool shift, bool alt, string key)
        {
            if (alt || key == null) return ShortcutAction.None;
            if (ctrl && !shift && key == "Q") return ShortcutAction.Quit;
            if (ctrl && shift && key == "S") return ShortcutAction.StatusWindow;
            if (!ctrl && !shift && key == "F11") return ShortcutAction.Fullscreen;
            return ShortcutAction.None;
        }

        // The log line for the end of a quit.
        public static string QuitLine(bool hadBackend, int pid, List<int> stopped)
        {
            if (!hadBackend) return "quit; no backend of ours was running";
            if (stopped != null && stopped.Contains(pid)) return "stopped on quit: backend (pid " + pid + ") and " + (stopped.Count - 1) + " process(es) below it";
            return "on quit the backend (pid " + pid + ") was not stopped";
        }

        // The log line when a page asks for a notification: tells "not asked for" from "asked for but Windows did not show it".
        public static string NotifyLine(string title, bool shown, string why)
        {
            return "notification requested by the page (title \"" + title + "\"): " + (shown ? "handed to Windows" : "not shown, " + why);
        }
    }

    // ---- one copy per project folder -------------------------------------------------------------------------------

    // A second start signals the first, waits a moment for the first to say it brought its window forward, and leaves.
    // Names are per project folder.
    public class InstanceGate : IDisposable
    {
        readonly Mutex mutex;
        readonly EventWaitHandle show, ack;
        readonly ManualResetEvent stop = new ManualResetEvent(false);

        InstanceGate(Mutex m, EventWaitHandle e, EventWaitHandle a) { mutex = m; show = e; ack = a; }

        public static string KeyFor(string root) { return Hashing.Sha256Text(root.ToLowerInvariant()).Substring(0, 16); }

        // The gate when this is the first copy, else null. For a second copy `answered` says whether the first copy
        // confirmed within `waitMs` that it brought its window forward; false means it is stuck or belongs to another user.
        public static InstanceGate TryOwn(string key, int waitMs, out bool answered)
        {
            answered = false;
            try
            {
                bool created;
                var m = new Mutex(true, "Local\\VGAC-starter-" + key, out created);
                var e = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\VGAC-starter-show-" + key);
                var a = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\VGAC-starter-ack-" + key);
                if (created) return new InstanceGate(m, e, a);
                try { a.Reset(); e.Set(); answered = a.WaitOne(waitMs); } catch (Exception) { }
                m.Dispose(); e.Dispose(); a.Dispose();
                return null;
            }
            catch (UnauthorizedAccessException) { return null; }   // the first copy runs as someone else: treated as running
        }

        // Called on the first copy when a second one starts. The handler must call Ack() once the window is forward.
        public void Listen(Action onSecondStart)
        {
            var t = new Thread(delegate()
            {
                try
                {
                    var handles = new WaitHandle[] { show, stop };
                    while (WaitHandle.WaitAny(handles) == 0)
                    {
                        try { onSecondStart(); } catch (Exception) { }
                    }
                }
                catch (ObjectDisposedException) { }
            });
            t.IsBackground = true;
            t.Start();
            listener = t;
        }

        Thread listener;

        public void Ack() { try { ack.Set(); } catch (Exception) { } }

        public void Dispose()
        {
            stop.Set();
            Thread l = listener;
            if (l != null) l.Join(500);
            try { mutex.ReleaseMutex(); } catch (Exception) { }
            mutex.Dispose();
            show.Dispose();
            ack.Dispose();
        }
    }

    // ---- this machine's launcher settings ------------------------------------------------------------------------

    // The few values of local/machine.json "desktop" the starter reads (same keys the Electron launcher reads).
    public class DesktopSettings
    {
        public string ComfyAUrl = "http://127.0.0.1:8188";
        public string ComfyBUrl = "http://127.0.0.1:8189";
        public string ComfyDesktopExe;       // desktop.comfyA.desktopExe
        public bool StartScriptSet;          // desktop.comfyB.startScript: no longer used

        public static DesktopSettings From(MachineFile m)
        {
            var s = new DesktopSettings();
            string v = m.GetString2("desktop", "comfyA", "url"); if (!string.IsNullOrEmpty(v)) s.ComfyAUrl = v.TrimEnd('/');
            v = m.GetString2("desktop", "comfyB", "url"); if (!string.IsNullOrEmpty(v)) s.ComfyBUrl = v.TrimEnd('/');
            s.ComfyDesktopExe = m.GetString2("desktop", "comfyA", "desktopExe");
            s.StartScriptSet = !string.IsNullOrEmpty(m.GetString2("desktop", "comfyB", "startScript"));
            return s;
        }
    }

    // ---- what the status rows say ----------------------------------------------------------------------------------

    public class ActionItem
    {
        public string Id, Label;
        public ActionItem(string id, string label) { Id = id; Label = label; }
    }

    public class Row
    {
        public string Key, Name, Url;
        public string State = "unknown";   // unknown | up | starting | building | down | error | off
        public string Message = "Checking";
        public int Pid;
        public bool Owned;
        public List<ActionItem> Actions = new List<ActionItem>();
    }

    public class GpuInfo
    {
        public bool Enabled = true;
        public string State;
        public bool Owned;
        public bool CanStart;
    }

    public class StatusInputs
    {
        public bool ComfyAUp, ComfyBUp, BackendUp, UiHtml;
        public GpuInfo A = new GpuInfo(), B = new GpuInfo();
        public bool GpusFromBackend;
        public bool BackendOwned;
        public int BackendPid;
        public double BackendAgeSeconds;
        public int BackendTimeoutSeconds = 120;
        public int BackendExitCode = -1;            // -1: has not exited
        public int ForeignBackendPid;               // a backend process this launcher did not start, not answering yet
        public bool BackendStartTried;
        public bool DistExists = true;
        public bool Building;
        public string BuildError;
        public bool DesktopExeExists;
        public bool StartScriptIgnored;
        public string ComfyAUrl = "http://127.0.0.1:8188", ComfyBUrl = "http://127.0.0.1:8189", BackendUrl = "http://127.0.0.1:8765";
    }

    public class StatusSnapshot
    {
        public List<Row> Rows = new List<Row>();
        public bool AllUp;
        public string Summary = "";
        public string Tooltip = "";
        public Row Get(string key) { foreach (Row r in Rows) if (r.Key == key) return r; return null; }
    }

    public static class StatusModel
    {
        const string StartScriptNote = ". The start script in the settings is no longer used; the app starts this engine itself.";

        public static StatusSnapshot Build(StatusInputs i)
        {
            var snap = new StatusSnapshot();
            snap.Rows.Add(ComfyA(i));
            snap.Rows.Add(ComfyB(i));
            snap.Rows.Add(Backend(i));
            snap.Rows.Add(Ui(i));
            // A card turned off in the app counts as fine: the app carries on with the other one.
            snap.AllUp = true;
            int up = 0, off = 0;
            foreach (Row r in snap.Rows)
            {
                if (r.State != "up" && r.State != "off") snap.AllUp = false;
                if (r.State == "up" || r.State == "off") up++;
                if (r.State == "off" || r.Message.IndexOf("Off in the app", StringComparison.Ordinal) >= 0) off++;
            }
            string offText = off > 0 ? " (" + off + " graphics card" + (off == 1 ? "" : "s") + " off in the app)" : "";
            snap.Summary = (snap.AllUp ? "All services up" : up + " of " + snap.Rows.Count + " services up") + offText;
            snap.Tooltip = Tooltip(snap);
            return snap;
        }

        // A tray tooltip holds at most 63 characters.
        public static string Tooltip(StatusSnapshot s)
        {
            int offN = 0, upN = 0;
            foreach (Row r in s.Rows) { if (r.State == "off") offN++; if (r.State == "up") upN++; }
            int total = s.Rows.Count - offN;
            string t = "Video Game Asset Creator: " + upN + "/" + total + " services up" + (offN > 0 ? " (" + offN + " card off)" : "");
            return t.Length > 63 ? t.Substring(0, 63) : t;
        }

        static Row Make(string key, string name, string url) { var r = new Row(); r.Key = key; r.Name = name; r.Url = url; return r; }

        static void Set(Row r, string state, string message, params ActionItem[] actions)
        {
            r.State = state; r.Message = message; r.Actions = new List<ActionItem>(actions);
        }

        static Row ComfyA(StatusInputs i)
        {
            var r = Make("comfyA", "ComfyUI A", i.ComfyAUrl);
            string off = !i.A.Enabled ? ". Off in the app" : "";
            string eng = i.GpusFromBackend ? i.A.State : null;
            if (i.ComfyAUp)
                Set(r, "up", (i.GpusFromBackend && i.A.Owned ? "Running (started by the app when a step needed it)" : "Running (started by ComfyUI Desktop)") + off);
            else if (eng == "starting")
                Set(r, "starting", "The app is starting it (about a minute)");
            else
            {
                string msg = (!i.A.Enabled ? "Not running" : "Open ComfyUI Desktop and start the instance that runs on this card") + off;
                if (i.A.Enabled && i.DesktopExeExists) Set(r, "down", msg, new ActionItem("openComfyDesktop", "Open ComfyUI Desktop"));
                else Set(r, "down", msg);
            }
            return r;
        }

        static Row ComfyB(StatusInputs i)
        {
            var r = Make("comfyB", "ComfyUI B (images and rigging)", i.ComfyBUrl);
            string note = i.StartScriptIgnored ? StartScriptNote : "";
            string eng = i.GpusFromBackend ? i.B.State : null;
            if (!i.B.Enabled)
            {
                if (i.ComfyBUp) Set(r, "off", "Off in the app (still running, not started by this launcher, left alone)" + note, new ActionItem("turnOnB", "Turn on in the app"));
                else Set(r, "off", "Off (turned off in the app)" + note, new ActionItem("startAnywayB", "Start anyway"));
            }
            else if (i.ComfyBUp)
            {
                // Restart only when the app itself started this one: a ComfyUI nobody here started is never stopped.
                if (i.GpusFromBackend && i.B.Owned) Set(r, "up", "Running (started by the app when a step needed it)" + note);
                else Set(r, "up", "Running (not started by this launcher, left alone on quit)" + note);
            }
            else if (eng == "starting")
                Set(r, "starting", "The app is starting it (about a minute)" + note);
            else if (!i.BackendUp)
                Set(r, "down", "Waiting for the app's engine, which starts it" + note);
            else if (eng == "failed")
                Set(r, "error", "The app could not start it. Open the log folder to see why" + note, new ActionItem("startB", "Try again"));
            else
                Set(r, "down", (i.B.CanStart ? "Not running. The app starts it by itself when a step needs it" : "Not running") + note,
                    new ActionItem("startB", "Start"));
            return r;
        }

        static Row Backend(StatusInputs i)
        {
            var r = Make("backend", "Backend (FastAPI)", i.BackendUrl + "/api/health");
            r.Owned = i.BackendOwned;
            r.Pid = i.BackendOwned ? i.BackendPid : 0;
            var restart = new ActionItem("restartBackend", "Restart");
            if (i.BackendUp)
                Set(r, "up", i.BackendOwned ? "Running (started by this launcher)" : "Running (not started by this launcher, left alone on quit)", restart);
            else if (i.BackendOwned)
            {
                if (i.BackendAgeSeconds > i.BackendTimeoutSeconds)
                    Set(r, "error", "Started " + (int)Math.Round(i.BackendAgeSeconds) + " s ago and still not answering; see backend.log", restart);
                else Set(r, "starting", "Starting (" + (int)Math.Round(i.BackendAgeSeconds) + " s)");
            }
            else if (i.BackendExitCode >= 0)
                Set(r, "error", "Exited with code " + i.BackendExitCode + "; see backend.log", new ActionItem("startBackend", "Start again"));
            else if (i.ForeignBackendPid > 0)
                Set(r, "starting", "Found a process not started by this launcher (PID " + i.ForeignBackendPid + "); waiting for it to answer", restart);
            else if (!i.BackendStartTried)
                Set(r, "starting", "Starting");
            else
                Set(r, "down", "Not running", new ActionItem("startBackend", "Start"));
            return r;
        }

        static Row Ui(StatusInputs i)
        {
            var r = Make("ui", "UI (screens served by the backend)", i.BackendUrl + "/");
            if (i.BackendUp && i.UiHtml) Set(r, "up", "Served by the backend");
            else if (i.Building) Set(r, "building", "Building the screens (npm run build)");
            else if (!string.IsNullOrEmpty(i.BuildError)) Set(r, "error", i.BuildError, new ActionItem("rebuild", "Build again"));
            else if (!i.DistExists) Set(r, "down", "The screens are not built", new ActionItem("rebuild", "Build"));
            else if (!i.BackendUp) Set(r, "down", "Waiting for the backend");
            else Set(r, "down", "The backend started before the screens were built; restart it to serve them", new ActionItem("restartBackend", "Restart backend"));
            return r;
        }
    }

    // ---- reading the backend's answers -------------------------------------------------------------------------

    public static class HealthReader
    {
        // Jobs running or waiting, from GET /api/health: for each card { current_job_id, queued, lent_to }, and for the host
        // also quick_job_id and helper_job_ids. Running jobs are counted once by id (a card lent to a job shows that job's
        // id again on the card that runs it), waiting jobs by number.
        public static int Jobs(string healthJson)
        {
            try
            {
                var d = Json.ParseObject(healthJson);
                object q;
                if (!d.TryGetValue("queue", out q)) return 0;
                var qd = q as Dictionary<string, object>;
                if (qd == null) return 0;
                var running = new HashSet<string>();
                int waiting = 0;
                foreach (object v in qd.Values)
                {
                    var one = v as Dictionary<string, object>;
                    if (one == null) continue;
                    foreach (string k in new[] { "current_job_id", "lent_to", "quick_job_id" })
                    {
                        object x;
                        if (one.TryGetValue(k, out x) && x != null && Convert.ToString(x).Length > 0) running.Add(Convert.ToString(x));
                    }
                    object helpers;
                    if (one.TryGetValue("helper_job_ids", out helpers) && helpers is System.Collections.IEnumerable && !(helpers is string))
                        foreach (object h in (System.Collections.IEnumerable)helpers) if (h != null && Convert.ToString(h).Length > 0) running.Add(Convert.ToString(h));
                    object queued;
                    if (one.TryGetValue("queued", out queued) && queued != null) { try { waiting += Convert.ToInt32(queued); } catch (Exception) { } }
                }
                return running.Count + waiting;
            }
            catch (Exception) { return 0; }
        }

        public static bool Ok(string healthJson)
        {
            try { object o; return Json.ParseObject(healthJson).TryGetValue("ok", out o) && o is bool && (bool)o; }
            catch (Exception) { return false; }
        }

        // GET /api/gpus: [ { "instance": "a", "enabled": true, "state": "...", "owned": false, "can_start": true }, ... ]
        public static Dictionary<string, GpuInfo> Gpus(string json)
        {
            var res = new Dictionary<string, GpuInfo>();
            try
            {
                var arr = new System.Web.Script.Serialization.JavaScriptSerializer().DeserializeObject(json) as System.Collections.IEnumerable;
                if (arr == null) return null;
                foreach (object o in arr)
                {
                    var d = o as Dictionary<string, object>;
                    if (d == null || !d.ContainsKey("instance")) continue;
                    var g = new GpuInfo();
                    object v;
                    g.Enabled = !(d.TryGetValue("enabled", out v) && v is bool && !(bool)v);
                    g.State = d.TryGetValue("state", out v) && v != null ? v.ToString() : null;
                    g.Owned = d.TryGetValue("owned", out v) && v is bool && (bool)v;
                    g.CanStart = d.TryGetValue("can_start", out v) && v is bool && (bool)v;
                    res[d["instance"].ToString()] = g;
                }
                return res;
            }
            catch (Exception) { return null; }
        }

        // library/prefs.json before the backend answers: { "disabled_instances": ["b"] }
        public static Dictionary<string, GpuInfo> GpusFromPrefs(string json)
        {
            try
            {
                var d = Json.ParseObject(json);
                object v;
                var off = new List<string>();
                if (d.TryGetValue("disabled_instances", out v) && v is System.Collections.IEnumerable && !(v is string))
                    foreach (object o in (System.Collections.IEnumerable)v) off.Add(Convert.ToString(o));
                var res = new Dictionary<string, GpuInfo>();
                foreach (string k in new[] { "a", "b" }) { var g = new GpuInfo(); g.Enabled = !off.Contains(k); res[k] = g; }
                return res;
            }
            catch (Exception) { return null; }
        }
    }
}
