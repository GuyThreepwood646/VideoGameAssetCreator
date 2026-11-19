// File handling for the maintenance jobs (update, repair, move, remove): measuring, copying and deleting without ever
// following a link (junction or symbolic link) out of the folder being handled, and knowing what a path really is.
// C# 5 on purpose (see Core.cs).
using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace Vgac.Starter
{
    public class FsReport
    {
        public int Files;
        public long Bytes;
        public List<string> Unlinked = new List<string>();     // links that were removed as links (their target was not entered)
        public List<string> Locked = new List<string>();       // paths that could not be deleted, with the reason
        public List<string> Links = new List<string>();        // links found and not followed (copy and measure)
    }

    public static class SafeFs
    {
        // Tests set this to stop a job at an exact point (by throwing); it is null in the program.
        public static Action<string, string> Hook;
        // Set by a caller that has found a copy wrong: a file is then copied again even when its size and time look the same.
        [ThreadStatic] public static bool ForceCopy;

        public static void Point(string what, string path) { var h = Hook; if (h != null) h(what, path); }

        // Writes a file so that a crash leaves either the old one or the new one, never none: the new text goes to a temporary file
        // beside it, which then REPLACES the old (File.Replace; the old one kept as `backup` when asked).
        // uniqueTemp: the temporary file gets a name no other writer uses (machine.json and installed.json, which the backend also
        // writes under the shared lock). Other files keep the fixed name: a move's crash checks read the fixed name as its own.
        public static void WriteAtomic(string path, string text, string backup, bool uniqueTemp = false)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            string tmp = uniqueTemp ? path + "." + Guid.NewGuid().ToString("N") + ".tmp" : path + ".tmp";
            File.WriteAllText(tmp, text, new UTF8Encoding(false));
            Point("save", path + " (written)");
            if (File.Exists(path))
            {
                // Windows (an antivirus, the indexer) can hold the file for a moment: try again, then overwrite in place (the old file is never
                // deleted first, so there is always a file)
                for (int i = 0; ; i++)
                {
                    try { File.Replace(tmp, path, backup); break; }
                    catch (IOException)
                    {
                        if (i < 40) { System.Threading.Thread.Sleep(50); continue; }
                        throw;           // never overwritten in place: the old file stays whole, and the caller is told
                    }
                }
            }
            else File.Move(tmp, path);
            Point("save", path + " (replaced)");
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr CreateFileW(string name, uint access, uint share, IntPtr sa, uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern uint GetFinalPathNameByHandleW(IntPtr h, StringBuilder buf, uint len, uint flags);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);

        [StructLayout(LayoutKind.Sequential)]
        struct ByHandleInfo
        {
            public uint Attributes; public uint c0, c1, a0, a1, w0, w1; public uint VolumeSerial, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
        }

        [DllImport("kernel32.dll", SetLastError = true)] static extern bool GetFileInformationByHandle(IntPtr h, out ByHandleInfo info);

        // What this very file or folder is, as the file system numbers it (volume and index). A folder that was deleted and made
        // again with the same name has another number, unlike its creation time, which Windows may carry over to the new one.
        // "" when it cannot be told.
        public static string FileId(string path)
        {
            IntPtr h = CreateFileW(path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero);
            if (h == (IntPtr)(-1)) return "";
            try
            {
                ByHandleInfo i;
                return GetFileInformationByHandle(h, out i) ? i.VolumeSerial + ":" + i.IndexHigh + ":" + i.IndexLow : "";
            }
            finally { CloseHandle(h); }
        }

        public static string Norm(string p)
        {
            return Path.GetFullPath(p).TrimEnd('\\', '/').ToLowerInvariant();
        }

        public static bool IsLink(string path)
        {
            try { return (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0; }
            catch (Exception) { return false; }
        }

        // The real location of a path: every link in it resolved. A path that does not exist yet is its nearest existing
        // parent's real location plus the missing names. null when it cannot be told (never a guess): callers treat that as unsafe.
        public static string Real(string path)
        {
            string full = Path.GetFullPath(path).TrimEnd('\\', '/');
            if (full.Length == 2 && full[1] == ':') full += "\\";           // "F:" alone means the current folder on F; a drive root is "F:\"
            string tail = "";
            string cur = full;
            while (cur != null && !Directory.Exists(cur) && !File.Exists(cur))
            {
                tail = "\\" + Path.GetFileName(cur) + tail;
                cur = Path.GetDirectoryName(cur);
            }
            if (cur == null) return null;
            IntPtr h = CreateFileW(cur, 0, 7, IntPtr.Zero, 3, 0x02000000 /* BACKUP_SEMANTICS: also opens folders */, IntPtr.Zero);
            if (h == (IntPtr)(-1)) return null;
            try
            {
                var sb = new StringBuilder(1024);
                uint n = GetFinalPathNameByHandleW(h, sb, (uint)sb.Capacity, 0);
                if (n == 0 || n >= sb.Capacity) return null;
                string r = sb.ToString();
                if (r.StartsWith("\\\\?\\UNC\\")) r = "\\\\" + r.Substring(8);
                else if (r.StartsWith("\\\\?\\")) r = r.Substring(4);
                return (r.TrimEnd('\\') + tail).ToLowerInvariant();
            }
            finally { CloseHandle(h); }
        }

        // `child` is the same as or below `root` once every link in both is resolved.
        public static bool Inside(string child, string root)
        {
            string c = Real(child), r = Real(root);
            if (c == null || r == null) return false;
            return c == r || c.StartsWith(r + "\\");
        }

        // No link anywhere in the path (so deleting it cannot touch anything but itself).
        public static bool PathHasNoLink(string path)
        {
            string r = Real(path);
            return r != null && r == Norm(path);
        }

        // Renames a folder, trying again for a moment when Windows or an antivirus program is still holding a file in it
        // (common right after a folder was written).
        public static void MoveDir(string from, string to)
        {
            for (int n = 1; ; n++)
            {
                try { Directory.Move(from, to); return; }
                catch (IOException) { if (n >= 8) throw; }
                catch (UnauthorizedAccessException) { if (n >= 8) throw; }
                System.Threading.Thread.Sleep(400 * n);
            }
        }

        // A link anywhere inside the folder (not counting the folder itself).
        public static bool ContainsLink(string path)
        {
            if (!Directory.Exists(path)) return false;
            string[] kids;
            try { kids = Directory.GetFileSystemEntries(path); } catch (Exception) { return true; }
            foreach (string k in kids)
            {
                if (IsLink(k)) return true;
                if (Directory.Exists(k) && ContainsLink(k)) return true;
            }
            return false;
        }

        public static long Size(string path, FsReport rep)
        {
            long total = 0;
            if (File.Exists(path)) { try { return new FileInfo(path).Length; } catch (IOException) { return 0; } }
            if (!Directory.Exists(path)) return 0;
            if (IsLink(path)) { if (rep != null) rep.Links.Add(path); return 0; }
            string[] kids;
            try { kids = Directory.GetFileSystemEntries(path); } catch (Exception) { return 0; }
            foreach (string k in kids)
            {
                if (IsLink(k)) { if (rep != null) rep.Links.Add(k); continue; }
                if (Directory.Exists(k)) total += Size(k, rep);
                else { try { total += new FileInfo(k).Length; } catch (IOException) { } }
            }
            return total;
        }

        // Deletes a file or folder tree. A link is removed as a link and never entered; a path that is itself a link is
        // unlinked and its target left whole. A file that cannot be deleted (open, antivirus) is recorded and skipped.
        public static FsReport DeleteTree(string path) { var r = new FsReport(); DeleteInto(path, r); return r; }

        static void DeleteInto(string p, FsReport r)
        {
            Point("delete", p);
            try
            {
                FileAttributes a = File.GetAttributes(p);
                if ((a & FileAttributes.ReparsePoint) != 0)
                {
                    if ((a & FileAttributes.Directory) != 0) Directory.Delete(p, false); else File.Delete(p);
                    r.Unlinked.Add(p);
                    return;
                }
                if ((a & FileAttributes.Directory) != 0)
                {
                    foreach (string k in Directory.GetFileSystemEntries(p)) DeleteInto(k, r);
                    try { Directory.Delete(p, false); }
                    catch (IOException e) { r.Locked.Add(p + " (" + e.Message + ")"); }
                    catch (UnauthorizedAccessException e) { r.Locked.Add(p + " (" + e.Message + ")"); }
                }
                else
                {
                    long len = 0;
                    try { len = new FileInfo(p).Length; } catch (IOException) { }
                    if ((a & FileAttributes.ReadOnly) != 0) File.SetAttributes(p, FileAttributes.Normal);
                    File.Delete(p);
                    r.Files++; r.Bytes += len;
                }
            }
            catch (FileNotFoundException) { }
            catch (DirectoryNotFoundException) { }
            catch (IOException e) { r.Locked.Add(p + " (" + e.Message + ")"); }
            catch (UnauthorizedAccessException e) { r.Locked.Add(p + " (" + e.Message + ")"); }
        }

        // Copies a folder tree (links are not copied or followed, they are listed). `skipTop` names are left out at the top
        // level of `src`. Files go to a temporary name and are renamed, so a stop never leaves a half-written file under
        // its real name.
        public static void CopyTree(string src, string dst, ICollection<string> skipTop, FsReport rep)
        {
            Directory.CreateDirectory(dst);
            foreach (string k in Directory.GetFileSystemEntries(src))
            {
                string name = Path.GetFileName(k);
                if (skipTop != null && skipTop.Contains(name)) continue;
                string to = Path.Combine(dst, name);
                if (IsLink(k)) { rep.Links.Add(k); continue; }
                if (Directory.Exists(k)) CopyTree(k, to, null, rep);
                else
                {
                    Point("copy", k);
                    string tmp = to + ".copying";
                    File.Copy(k, tmp, true);
                    if (File.Exists(to)) File.Delete(to);
                    File.Move(tmp, to);
                    rep.Files++; rep.Bytes += new FileInfo(to).Length;
                }
            }
        }

        // The first link inside a folder that leads OUTSIDE it (or is not a folder link), or null. A folder link whose target is
        // inside the same folder (uv's Python folder keeps such links between its versions) is copied as a link, see CopyItem.
        public static string FirstExternalLink(string root, string skipTop)
        {
            string rr = Real(root);
            return rr == null ? root : ExternalWalk(root, rr, skipTop);
        }

        static string ExternalWalk(string path, string rootReal, string skipTop)
        {
            string[] kids;
            try { kids = Directory.GetFileSystemEntries(path); } catch (Exception) { return path; }
            foreach (string k in kids)
            {
                if (skipTop != null && string.Equals(Path.GetFileName(k), skipTop, StringComparison.OrdinalIgnoreCase) && Path.GetDirectoryName(k) == path.TrimEnd('\\') && path.Length > 0 && Real(path) == rootReal) continue;
                if (IsLink(k))
                {
                    string t = Real(k);
                    if (!Directory.Exists(k) || t == null || !(t == rootReal || t.StartsWith(rootReal + "\\"))) return k;
                    continue;
                }
                if (Directory.Exists(k)) { string f = ExternalWalk(k, rootReal, null); if (f != null) return f; }
            }
            return null;
        }

        // Copies one item's folder tree. Links inside it that point to another place inside the same item are not followed or lost:
        // they are made again in the copy (a directory junction to the same relative place). Any other link is listed in `rep.Links`
        // and not copied (the caller refuses such a folder before copying).
        public static List<string[]> CopyItem(string src, string dst, ICollection<string> skipTop, FsReport rep)
        {
            string rr = Real(src);
            var links = new List<string[]>();
            CopyItemInto(src, dst, skipTop, rep, rr, src, links);
            return links;
        }

        // Makes the links again inside the finished copy (after its folder has its final name, so they point at the final place).
        public static void MakeLinks(string finalDir, List<string[]> links, FsReport rep)
        {
            foreach (string[] lk in links)
            {
                string linkPath = Path.Combine(finalDir, lk[0]), target = Path.Combine(finalDir, lk[1]);
                if (!Directory.Exists(target)) { rep.Links.Add(lk[0]); continue; }
                Directory.CreateDirectory(Path.GetDirectoryName(linkPath));
                if (IsLink(linkPath) && Real(linkPath) == Real(target)) continue;      // made by an earlier try
                if (!MakeJunction(linkPath, target)) { rep.Links.Add(lk[0]); continue; }
                string a = Real(linkPath), b = Real(target);
                if (a == null || a != b) rep.Links.Add(lk[0]);
            }
        }

        static void CopyItemInto(string src, string dst, ICollection<string> skipTop, FsReport rep, string rootReal, string rootSrc, List<string[]> links)
        {
            // never write through a link that is already in the destination (or above it)
            if (IsLink(dst) || !PathHasNoLink(dst)) { rep.Links.Add(dst); return; }
            Directory.CreateDirectory(dst);
            foreach (string k in Directory.GetFileSystemEntries(src))
            {
                string name = Path.GetFileName(k);
                if (skipTop != null && skipTop.Contains(name)) continue;
                string to = Path.Combine(dst, name);
                if (IsLink(k))
                {
                    string t = Real(k);
                    if (Directory.Exists(k) && t != null && t.StartsWith(rootReal + "\\")) links.Add(new[] { k.Substring(rootSrc.TrimEnd('\\').Length + 1), t.Substring(rootReal.Length + 1) });
                    else rep.Links.Add(k);
                    continue;
                }
                if (Directory.Exists(k)) CopyItemInto(k, to, null, rep, rootReal, rootSrc, links);
                else
                {
                    Point("copy", k);
                    // already there with the same size and time (an earlier, interrupted copy): not copied again
                    if (IsLink(to)) { rep.Links.Add(to); continue; }
                    if (!ForceCopy && File.Exists(to) && new FileInfo(to).Length == new FileInfo(k).Length && File.GetLastWriteTimeUtc(to) == File.GetLastWriteTimeUtc(k)) { rep.Files++; continue; }
                    string tmp = to + ".copying";
                    File.Copy(k, tmp, true);
                    if (File.Exists(to)) File.Delete(to);
                    File.Move(tmp, to);
                    rep.Files++; rep.Bytes += new FileInfo(to).Length;
                }
            }
        }

        static bool MakeJunction(string link, string target)
        {
            try
            {
                var psi = new System.Diagnostics.ProcessStartInfo("cmd.exe", "/c mklink /J \"" + link + "\" \"" + target + "\"");
                psi.UseShellExecute = false; psi.CreateNoWindow = true; psi.RedirectStandardOutput = true; psi.RedirectStandardError = true;
                using (var p = System.Diagnostics.Process.Start(psi)) { p.StandardOutput.ReadToEnd(); p.StandardError.ReadToEnd(); p.WaitForExit(); return p.ExitCode == 0; }
            }
            catch (Exception) { return false; }
        }

        // Differences between two trees (missing, extra, different size), `skipTop` names left out of the source side.
        // When `hashToo` is true the files are compared by SHA-256 as well.
        public static List<string> Compare(string src, string dst, ICollection<string> skipTop, bool hashToo) { return Compare(src, dst, skipTop, hashToo, false); }

        // `ignoreExtra`: files that are only in `dst` are fine (the new copy may have grown since).
        public static List<string> Compare(string src, string dst, ICollection<string> skipTop, bool hashToo, bool ignoreExtra)
        {
            return Compare(src, dst, skipTop, hashToo, ignoreExtra, null, null);
        }

        // `cancelled` is asked before each file (true: stop; OperationCanceledException), `say` is told which file is being checked.
        public static List<string> Compare(string src, string dst, ICollection<string> skipTop, bool hashToo, bool ignoreExtra, Func<bool> cancelled, Action<string> say)
        {
            var bad = new List<string>();
            // the new copy is compared as a real folder: reached through a link, it could be the old copy itself
            if (IsLink(dst) || !PathHasNoLink(dst)) { bad.Add("the new copy is, or is reached through, a link: " + dst); return bad; }
            cmpCancel = cancelled; cmpSay = say;
            try { CompareInto(src, dst, skipTop, hashToo, ignoreExtra, bad, true); }
            finally { cmpCancel = null; cmpSay = null; }
            return bad;
        }

        [ThreadStatic] static Func<bool> cmpCancel;
        [ThreadStatic] static Action<string> cmpSay;

        // The first link inside a folder (not counting `skipTop` at its top level), or null.
        public static string FirstLink(string path, string skipTop)
        {
            if (!Directory.Exists(path)) return null;
            string[] kids;
            try { kids = Directory.GetFileSystemEntries(path); } catch (Exception) { return path; }
            foreach (string k in kids)
            {
                if (skipTop != null && string.Equals(Path.GetFileName(k), skipTop, StringComparison.OrdinalIgnoreCase)) continue;
                if (IsLink(k)) return k;
                if (Directory.Exists(k)) { string f = FirstLink(k, null); if (f != null) return f; }
            }
            return null;
        }

        static void CompareInto(string src, string dst, ICollection<string> skipTop, bool hashToo, bool ignoreExtra, List<string> bad, bool top)
        {
            var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string k in Directory.GetFileSystemEntries(src))
            {
                string name = Path.GetFileName(k);
                if (top && skipTop != null && skipTop.Contains(name)) continue;
                if (IsLink(k))
                {
                    // a link inside the folder is made again in the copy as a link (see CopyItem), not compared as content
                    names.Add(name);
                    if (!IsLink(Path.Combine(dst, name))) bad.Add("link not made again in the copy " + Path.Combine(dst, name));
                    continue;
                }
                names.Add(name);
                string to = Path.Combine(dst, name);
                // a link where the old place has real files would make them "match" themselves (a link back into the old copy)
                if (IsLink(to)) { bad.Add("a link in the new copy where the old place has real files: " + to); continue; }
                if (Directory.Exists(k))
                {
                    if (!Directory.Exists(to)) { bad.Add("missing folder " + to); continue; }
                    CompareInto(k, to, null, hashToo, ignoreExtra, bad, false);
                }
                else
                {
                    if (cmpCancel != null && cmpCancel()) throw new OperationCanceledException();
                    if (cmpSay != null) cmpSay(k);
                    if (!File.Exists(to)) { bad.Add("missing file " + to); continue; }
                    if (new FileInfo(k).Length != new FileInfo(to).Length) { bad.Add("different size " + to); continue; }
                    if (hashToo && Hashing.Sha256File(k) != Hashing.Sha256File(to)) bad.Add("different content " + to);
                }
            }
            if (!ignoreExtra && Directory.Exists(dst))
                foreach (string k in Directory.GetFileSystemEntries(dst))
                    if (!names.Contains(Path.GetFileName(k)) && !(top && skipTop != null && skipTop.Contains(Path.GetFileName(k)))) bad.Add("extra " + k);
        }
    }
}
