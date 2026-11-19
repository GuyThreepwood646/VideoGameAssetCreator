// Windows calls the launcher needs: starting the backend with its output going straight to a log file (no pipe through
// this program), listing processes with their command lines (WMI, no PowerShell), the taskbar badge, and letting a second
// start bring the first window forward. No WebView2 type is named here.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace Vgac.Starter
{
    public static class ErrorBoxes
    {
        [DllImport("kernel32.dll")] static extern uint SetErrorMode(uint mode);

        // Windows' own error boxes ("unsupported application", "file not found") are not shown for programs this one starts.
        public static void Off() { SetErrorMode(0x0001 | 0x8000); }   // FAILCRITICALERRORS | NOOPENFILEERRORBOX
    }

    public static class NativeProcess
    {
        [StructLayout(LayoutKind.Sequential)]
        struct SECURITY_ATTRIBUTES { public int nLength; public IntPtr lpSecurityDescriptor; [MarshalAs(UnmanagedType.Bool)] public bool bInheritHandle; }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        struct STARTUPINFO
        {
            public int cb; public string lpReserved; public string lpDesktop; public string lpTitle;
            public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
            public short wShowWindow, cbReserved2; public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct PROCESS_INFORMATION { public IntPtr hProcess, hThread; public int dwProcessId, dwThreadId; }

        [StructLayout(LayoutKind.Sequential)]
        struct STARTUPINFOEX { public STARTUPINFO StartupInfo; public IntPtr lpAttributeList; }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool CreateProcessW(string app, StringBuilder cmd, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr env, string cwd,
            ref STARTUPINFOEX si, out PROCESS_INFORMATION pi);

        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, int flags, ref IntPtr size);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr prev, IntPtr retSize);
        [DllImport("kernel32.dll")]
        static extern void DeleteProcThreadAttributeList(IntPtr list);

        const uint EXTENDED_STARTUPINFO_PRESENT = 0x80000;
        static readonly IntPtr PROC_THREAD_ATTRIBUTE_HANDLE_LIST = (IntPtr)0x20002;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr CreateFileW(string name, uint access, uint share, ref SECURITY_ATTRIBUTES sa, uint disposition, uint flags, IntPtr template);

        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);

        const uint CREATE_NO_WINDOW = 0x08000000, CREATE_UNICODE_ENVIRONMENT = 0x400;
        const int STARTF_USESTDHANDLES = 0x100;

        public class Started
        {
            public int Pid;
            public IntPtr Handle;       // kept open: while it is open Windows cannot give this id to another process
            public long CreatedTicks;   // read from the handle, never from a list
        }

        // For a child nobody needs to own.
        public static int StartLogged(string exe, string args, string cwd, string logFile, Dictionary<string, string> extraEnv)
        {
            Started s = Start(exe, args, cwd, logFile, extraEnv);
            ProcessIdentity.Close(s.Handle);
            return s.Pid;
        }

        // Starts `exe args` with its output appended to `logFile`, handing the child its own handle to the file. The child
        // is not tied to this program: no job object, no pipe, so if this program dies the child keeps running and keeps
        // writing its log. The caller owns the returned process handle.
        public static Started Start(string exe, string args, string cwd, string logFile, Dictionary<string, string> extraEnv)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(logFile));
            File.AppendAllText(logFile, "\r\n[" + DateTime.Now.ToString("yyyy-MM-ddTHH:mm:ss") + "] VGAC launcher: " + exe + " " + args + "\r\n", new UTF8Encoding(false));
            var sa = new SECURITY_ATTRIBUTES();
            sa.nLength = Marshal.SizeOf(typeof(SECURITY_ATTRIBUTES));
            sa.bInheritHandle = true;
            IntPtr log = CreateFileW(logFile, 0x4 /* FILE_APPEND_DATA */, 3 /* share read+write */, ref sa, 4 /* OPEN_ALWAYS */, 0x80, IntPtr.Zero);
            if (log == (IntPtr)(-1)) throw new IOException("Windows did not allow opening " + logFile + " (error " + Marshal.GetLastWin32Error() + ").");
            IntPtr nul = CreateFileW("NUL", 0x80000000 /* GENERIC_READ */, 3, ref sa, 3 /* OPEN_EXISTING */, 0x80, IntPtr.Zero);
            IntPtr envBlock = IntPtr.Zero, attrList = IntPtr.Zero, handleArray = IntPtr.Zero;
            bool attrReady = false;
            try
            {
                var si = new STARTUPINFOEX();
                si.StartupInfo.cb = Marshal.SizeOf(typeof(STARTUPINFOEX));
                si.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
                si.StartupInfo.hStdInput = nul; si.StartupInfo.hStdOutput = log; si.StartupInfo.hStdError = log;
                // Only these handles are inherited by the child. Without the list every inheritable handle of this program
                // (another program's pipe, a file being written) would be passed on and held by the backend for its lifetime.
                var inheritable = new List<IntPtr>();
                inheritable.Add(log);
                if (nul != (IntPtr)(-1)) inheritable.Add(nul);
                handleArray = Marshal.AllocHGlobal(IntPtr.Size * inheritable.Count);
                for (int i = 0; i < inheritable.Count; i++) Marshal.WriteIntPtr(handleArray, i * IntPtr.Size, inheritable[i]);
                IntPtr size = IntPtr.Zero;
                InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref size);
                attrList = Marshal.AllocHGlobal(size);
                if (!InitializeProcThreadAttributeList(attrList, 1, 0, ref size)) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                attrReady = true;
                if (!UpdateProcThreadAttribute(attrList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, handleArray, (IntPtr)(IntPtr.Size * inheritable.Count), IntPtr.Zero, IntPtr.Zero))
                    throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                si.lpAttributeList = attrList;
                envBlock = Marshal.StringToHGlobalUni(EnvBlock(extraEnv));
                var cmd = new StringBuilder("\"" + exe + "\" " + args);
                PROCESS_INFORMATION pi;
                if (!CreateProcessW(exe, cmd, IntPtr.Zero, IntPtr.Zero, true, CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT, envBlock, cwd, ref si, out pi))
                    throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
                CloseHandle(pi.hThread);
                var r = new Started();
                r.Pid = pi.dwProcessId; r.Handle = pi.hProcess; r.CreatedTicks = ProcessIdentity.StartTicks(pi.hProcess);
                return r;
            }
            finally
            {
                if (attrReady) DeleteProcThreadAttributeList(attrList);
                if (attrList != IntPtr.Zero) Marshal.FreeHGlobal(attrList);
                if (handleArray != IntPtr.Zero) Marshal.FreeHGlobal(handleArray);
                if (envBlock != IntPtr.Zero) Marshal.FreeHGlobal(envBlock);
                CloseHandle(log);
                if (nul != (IntPtr)(-1)) CloseHandle(nul);
            }
        }

        // This program's environment plus the extra values, as the double-null-terminated block CreateProcess wants.
        public static string EnvBlock(Dictionary<string, string> extra)
        {
            var all = new SortedDictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (System.Collections.DictionaryEntry e in Environment.GetEnvironmentVariables()) all[(string)e.Key] = (string)e.Value;
            if (extra != null) foreach (var kv in extra) all[kv.Key] = kv.Value;
            var sb = new StringBuilder();
            foreach (var kv in all) sb.Append(kv.Key).Append('=').Append(kv.Value).Append('\0');
            sb.Append('\0');
            return sb.ToString();
        }
    }

    // Every process with its command line, from WMI (no PowerShell).
    public class WmiProcesses : IProcessSource
    {
        public List<ProcInfo> List()
        {
            var res = new List<ProcInfo>();
            try
            {
                using (var s = new System.Management.ManagementObjectSearcher("SELECT ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine, CreationDate FROM Win32_Process"))
                {
                s.Options.Timeout = TimeSpan.FromSeconds(6);
                using (var rows = s.Get())
                {
                    foreach (System.Management.ManagementBaseObject o in rows)
                    {
                        var p = new ProcInfo();
                        p.Pid = Convert.ToInt32(o["ProcessId"]);
                        p.ParentPid = Convert.ToInt32(o["ParentProcessId"]);
                        p.Name = Convert.ToString(o["Name"]) ?? "";
                        p.ExePath = Convert.ToString(o["ExecutablePath"]) ?? "";
                        p.CommandLine = Convert.ToString(o["CommandLine"]) ?? "";
                        string cd = Convert.ToString(o["CreationDate"]);
                        if (!string.IsNullOrEmpty(cd))
                        {
                            try { p.CreatedTicks = System.Management.ManagementDateTimeConverter.ToDateTime(cd).ToUniversalTime().Ticks; } catch (Exception) { }
                        }
                        res.Add(p);
                    }
                }
                }
            }
            catch (Exception) { }
            return res;
        }

        public ProcInfo Find(int pid)
        {
            foreach (ProcInfo p in List()) if (p.Pid == pid) return p;
            return null;
        }
    }

    // Process handles: who a process really is (its start time), whether it still runs, and stopping it through the same
    // handle that was checked, so an id that was reused in between is never stopped.
    public static class ProcessIdentity
    {
        [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
        [DllImport("kernel32.dll")] static extern bool GetProcessTimes(IntPtr h, out long create, out long exit, out long kernel, out long user);
        [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr h, out uint code);
        [DllImport("kernel32.dll")] static extern bool TerminateProcess(IntPtr h, uint code);

        const uint Terminate = 0x1, QueryLimited = 0x1000, Sync = 0x100000, StillActive = 259;

        // UTC ticks of the process's start, or 0 when it cannot be read.
        public static long StartTicks(IntPtr h)
        {
            long c, e, k, u;
            if (h == IntPtr.Zero || !GetProcessTimes(h, out c, out e, out k, out u) || c <= 0) return 0;
            try { return DateTime.FromFileTimeUtc(c).Ticks; } catch (Exception) { return 0; }
        }

        // A handle to the process with this id, only when it started at `expectedTicks`; else zero.
        public static IntPtr Open(int pid, long expectedTicks)
        {
            if (expectedTicks <= 0) return IntPtr.Zero;
            IntPtr h = OpenProcess(Terminate | QueryLimited | Sync, false, pid);
            if (h == IntPtr.Zero) return IntPtr.Zero;
            long t = StartTicks(h);
            if (t <= 0 || Math.Abs(t - expectedTicks) > Adoption.TickTolerance) { CloseHandle(h); return IntPtr.Zero; }
            return h;
        }

        // Start time of the process with this id (0 when there is none), for "is that program still running".
        public static long StartTicksOf(int pid)
        {
            IntPtr h = OpenProcess(QueryLimited, false, pid);
            if (h == IntPtr.Zero) return 0;
            try { uint code; if (GetExitCodeProcess(h, out code) && code != StillActive) return 0; return StartTicks(h); }
            finally { CloseHandle(h); }
        }

        public static bool IsRunning(IntPtr h)
        {
            uint code;
            return h != IntPtr.Zero && GetExitCodeProcess(h, out code) && code == StillActive;
        }

        public static int ExitCode(IntPtr h)
        {
            uint code;
            return h != IntPtr.Zero && GetExitCodeProcess(h, out code) && code != StillActive ? (int)code : -1;
        }

        public static bool Kill(IntPtr h) { return h != IntPtr.Zero && TerminateProcess(h, 1); }
        public static void Close(IntPtr h) { if (h != IntPtr.Zero) CloseHandle(h); }
    }

    public class ProcessKiller : IKiller
    {
        // Stops the process only if it is still the one that started at `createdTicks`. Unknown time: never stops anything.
        public bool Kill(int pid, long createdTicks)
        {
            IntPtr h = ProcessIdentity.Open(pid, createdTicks);
            if (h == IntPtr.Zero) return false;
            try { return ProcessIdentity.Kill(h); }
            finally { ProcessIdentity.Close(h); }
        }
    }

    // The badge on the taskbar button (what Electron's setOverlayIcon showed).
    public static class Taskbar
    {
        [ComImport, Guid("56FDF344-FD6D-11d0-958A-006097C9A090"), ClassInterface(ClassInterfaceType.None)]
        class TaskbarListClass { }

        [ComImport, Guid("ea1afb91-9e28-4b86-90e9-9e9f8a5eefaf"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface ITaskbarList3
        {
            // ITaskbarList
            [PreserveSig] int HrInit();
            [PreserveSig] int AddTab(IntPtr hwnd);
            [PreserveSig] int DeleteTab(IntPtr hwnd);
            [PreserveSig] int ActivateTab(IntPtr hwnd);
            [PreserveSig] int SetActiveAlt(IntPtr hwnd);
            // ITaskbarList2
            [PreserveSig] int MarkFullscreenWindow(IntPtr hwnd, [MarshalAs(UnmanagedType.Bool)] bool fullscreen);
            // ITaskbarList3
            [PreserveSig] int SetProgressValue(IntPtr hwnd, ulong completed, ulong total);
            [PreserveSig] int SetProgressState(IntPtr hwnd, int flags);
            [PreserveSig] int RegisterTab(IntPtr hwndTab, IntPtr hwndMDI);
            [PreserveSig] int UnregisterTab(IntPtr hwndTab);
            [PreserveSig] int SetTabOrder(IntPtr hwndTab, IntPtr hwndInsertBefore);
            [PreserveSig] int SetTabActive(IntPtr hwndTab, IntPtr hwndMDI, uint reserved);
            [PreserveSig] int ThumbBarAddButtons(IntPtr hwnd, uint count, IntPtr buttons);
            [PreserveSig] int ThumbBarUpdateButtons(IntPtr hwnd, uint count, IntPtr buttons);
            [PreserveSig] int ThumbBarSetImageList(IntPtr hwnd, IntPtr himl);
            [PreserveSig] int SetOverlayIcon(IntPtr hwnd, IntPtr hIcon, [MarshalAs(UnmanagedType.LPWStr)] string description);
        }

        [DllImport("user32.dll")] static extern bool DestroyIcon(IntPtr h);

        static ITaskbarList3 list;

        // `png` null clears the badge. Returns whether Windows took it.
        public static bool SetOverlay(IntPtr hwnd, byte[] png, string description)
        {
            try
            {
                if (list == null) { list = (ITaskbarList3)new TaskbarListClass(); list.HrInit(); }
                if (png == null) return list.SetOverlayIcon(hwnd, IntPtr.Zero, "") == 0;
                using (var ms = new MemoryStream(png))
                using (var bmp = new Bitmap(ms))
                {
                    IntPtr h = bmp.GetHicon();
                    try { return list.SetOverlayIcon(hwnd, h, description) == 0; }
                    finally { DestroyIcon(h); }
                }
            }
            catch (Exception) { return false; }
        }
    }

    public static class Foreground
    {
        [DllImport("user32.dll")] static extern bool AllowSetForegroundWindow(int pid);
        [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);

        // A second copy calls this before it signals the first, so Windows lets the first come to the front.
        public static void AllowAny() { try { AllowSetForegroundWindow(-1); } catch (Exception) { } }
        public static void Front(IntPtr h) { try { SetForegroundWindow(h); } catch (Exception) { } }
    }

    // The launcher's icon: the V on a dark rounded square, the same picture as desktop/build/icon.png, drawn here so
    // nothing has to be shipped beside the exe. The exe's own icon is a file built from the same drawing (starter\icon.ico).
    public static class AppIcon
    {
        public static Bitmap Draw(int size)
        {
            var bmp = new Bitmap(size, size, System.Drawing.Imaging.PixelFormat.Format32bppArgb);
            using (var g = Graphics.FromImage(bmp))
            {
                g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
                g.Clear(Color.Transparent);
                float s = size / 256f;
                using (var path = RoundRect(16 * s, 16 * s, 224 * s, 224 * s, 40 * s))
                using (var fill = new SolidBrush(Color.FromArgb(0x24, 0x27, 0x2d)))
                using (var edge = new Pen(Color.FromArgb(0x4d, 0x52, 0x5c), Math.Max(1f, 4 * s)))
                {
                    g.FillPath(fill, path);
                    g.DrawPath(edge, path);
                }
                using (var ink = new Pen(Color.FromArgb(0x6e, 0xa4, 0xe8), 24 * s))
                {
                    ink.StartCap = System.Drawing.Drawing2D.LineCap.Round; ink.EndCap = System.Drawing.Drawing2D.LineCap.Round;
                    ink.LineJoin = System.Drawing.Drawing2D.LineJoin.Round;
                    g.DrawLines(ink, new[] { new PointF((128 - 60) * s, 70 * s), new PointF(128 * s, 196 * s), new PointF((128 + 60) * s, 70 * s) });
                }
            }
            return bmp;
        }

        static System.Drawing.Drawing2D.GraphicsPath RoundRect(float x, float y, float w, float h, float r)
        {
            var p = new System.Drawing.Drawing2D.GraphicsPath();
            float d = r * 2;
            p.AddArc(x, y, d, d, 180, 90); p.AddArc(x + w - d, y, d, d, 270, 90);
            p.AddArc(x + w - d, y + h - d, d, d, 0, 90); p.AddArc(x, y + h - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }

        // The window and tray icon: the exe's own icon when it has one, else the drawing.
        public static Icon Get(int size)
        {
            try
            {
                Icon own = Icon.ExtractAssociatedIcon(System.Windows.Forms.Application.ExecutablePath);
                if (own != null) return own;
            }
            catch (Exception) { }
            using (var b = Draw(size)) return Icon.FromHandle(b.GetHicon());
        }
    }
}
