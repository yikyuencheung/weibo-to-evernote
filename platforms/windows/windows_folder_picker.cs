using System;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

namespace WeiboEvernoteInbox
{
    internal static class WindowsFolderPicker
    {
        private const uint FOS_PICKFOLDERS = 0x00000020;
        private const uint FOS_FORCEFILESYSTEM = 0x00000040;
        private const uint FOS_PATHMUSTEXIST = 0x00000800;
        private const uint FOS_DONTADDTORECENT = 0x02000000;
        private const uint SIGDN_FILESYSPATH = 0x80058000;
        private const uint DESKTOP_READOBJECTS = 0x0001;
        private const uint DESKTOP_CREATEWINDOW = 0x0002;
        private const uint DESKTOP_WRITEOBJECTS = 0x0080;
        private const uint DESKTOP_SWITCHDESKTOP = 0x0100;
        private const int ERROR_CANCELLED_HRESULT = unchecked((int)0x800704C7);

        [STAThread]
        private static int Main(string[] args)
        {
            IntPtr inputDesktop = NativeMethods.OpenInputDesktop(
                0,
                false,
                DESKTOP_READOBJECTS | DESKTOP_CREATEWINDOW | DESKTOP_WRITEOBJECTS | DESKTOP_SWITCHDESKTOP);
            if (inputDesktop != IntPtr.Zero && !NativeMethods.SetThreadDesktop(inputDesktop))
            {
                NativeMethods.CloseDesktop(inputDesktop);
                inputDesktop = IntPtr.Zero;
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            string initial = ReadArgument(args, "--initial");
            IFileDialog dialog = null;
            IShellItem initialItem = null;
            IShellItem selectedItem = null;
            Form owner = null;
            IntPtr selectedPath = IntPtr.Zero;

            try
            {
                owner = new Form();
                owner.Text = "Weibo Evernote Inbox";
                owner.StartPosition = FormStartPosition.CenterScreen;
                owner.FormBorderStyle = FormBorderStyle.FixedToolWindow;
                owner.ClientSize = new Size(1, 1);
                owner.ShowInTaskbar = true;
                owner.TopMost = true;
                owner.Opacity = 0.01;
                owner.Show();
                owner.Activate();
                owner.BringToFront();
                NativeMethods.SetForegroundWindow(owner.Handle);

                dialog = (IFileDialog)new FileOpenDialogComObject();
                uint options;
                dialog.GetOptions(out options);
                dialog.SetOptions(options | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | FOS_DONTADDTORECENT);
                dialog.SetTitle("選擇微博本機收件匣");
                dialog.SetOkButtonLabel("選擇資料夾 (&S)");

                if (!String.IsNullOrWhiteSpace(initial) && Directory.Exists(initial))
                {
                    Guid shellItemId = typeof(IShellItem).GUID;
                    int initialResult = NativeMethods.SHCreateItemFromParsingName(initial, IntPtr.Zero, ref shellItemId, out initialItem);
                    if (initialResult >= 0 && initialItem != null)
                    {
                        dialog.SetDefaultFolder(initialItem);
                        dialog.SetFolder(initialItem);
                    }
                }

                int showResult = dialog.Show(owner.Handle);
                if (showResult == ERROR_CANCELLED_HRESULT)
                {
                    return 2;
                }
                Marshal.ThrowExceptionForHR(showResult);

                dialog.GetResult(out selectedItem);
                selectedItem.GetDisplayName(SIGDN_FILESYSPATH, out selectedPath);
                string path = Marshal.PtrToStringUni(selectedPath);
                if (String.IsNullOrWhiteSpace(path))
                {
                    throw new InvalidOperationException("選擇器沒有回傳資料夾路徑");
                }
                WriteUtf8(Console.OpenStandardOutput(), path);
                return 0;
            }
            catch (Exception error)
            {
                try
                {
                    WriteUtf8(Console.OpenStandardError(), error.ToString());
                }
                catch (IOException)
                {
                }
                return 1;
            }
            finally
            {
                if (selectedPath != IntPtr.Zero)
                {
                    Marshal.FreeCoTaskMem(selectedPath);
                }
                ReleaseComObject(selectedItem);
                ReleaseComObject(initialItem);
                ReleaseComObject(dialog);
                if (owner != null)
                {
                    owner.Close();
                    owner.Dispose();
                }
                if (inputDesktop != IntPtr.Zero)
                {
                    NativeMethods.CloseDesktop(inputDesktop);
                }
            }
        }

        private static string ReadArgument(string[] args, string name)
        {
            for (int index = 0; index + 1 < args.Length; index++)
            {
                if (String.Equals(args[index], name, StringComparison.OrdinalIgnoreCase))
                {
                    return args[index + 1];
                }
            }
            return String.Empty;
        }

        private static void WriteUtf8(Stream stream, string value)
        {
            using (stream)
            {
                byte[] bytes = new UTF8Encoding(false).GetBytes(value ?? String.Empty);
                stream.Write(bytes, 0, bytes.Length);
                stream.Flush();
            }
        }

        private static void ReleaseComObject(object value)
        {
            if (value != null && Marshal.IsComObject(value))
            {
                Marshal.FinalReleaseComObject(value);
            }
        }
    }

    [ComImport]
    [Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")]
    internal class FileOpenDialogComObject
    {
    }

    [ComImport]
    [Guid("42F85136-DB7E-439C-85F1-E4075D135FC8")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IFileDialog
    {
        [PreserveSig]
        int Show(IntPtr parent);
        void SetFileTypes(uint count, [MarshalAs(UnmanagedType.LPArray, SizeParamIndex = 0)] COMDLG_FILTERSPEC[] filters);
        void SetFileTypeIndex(uint index);
        void GetFileTypeIndex(out uint index);
        void Advise(IntPtr events, out uint cookie);
        void Unadvise(uint cookie);
        void SetOptions(uint options);
        void GetOptions(out uint options);
        void SetDefaultFolder(IShellItem item);
        void SetFolder(IShellItem item);
        void GetFolder(out IShellItem item);
        void GetCurrentSelection(out IShellItem item);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
        void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string name);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
        void GetResult(out IShellItem item);
        void AddPlace(IShellItem item, int alignment);
        void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string extension);
        void Close(int result);
        void SetClientGuid(ref Guid guid);
        void ClearClientData();
        void SetFilter(IntPtr filter);
    }

    [ComImport]
    [Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IShellItem
    {
        void BindToHandler(IntPtr bindContext, ref Guid handler, ref Guid interfaceId, out IntPtr result);
        void GetParent(out IShellItem parent);
        void GetDisplayName(uint displayName, out IntPtr name);
        void GetAttributes(uint mask, out uint attributes);
        void Compare(IShellItem item, uint hint, out int order);
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    internal struct COMDLG_FILTERSPEC
    {
        [MarshalAs(UnmanagedType.LPWStr)]
        public string Name;
        [MarshalAs(UnmanagedType.LPWStr)]
        public string Spec;
    }

    internal static class NativeMethods
    {
        [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = true)]
        internal static extern int SHCreateItemFromParsingName(
            [MarshalAs(UnmanagedType.LPWStr)] string path,
            IntPtr bindContext,
            ref Guid interfaceId,
            out IShellItem item);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool SetForegroundWindow(IntPtr window);

        [DllImport("user32.dll", SetLastError = true)]
        internal static extern IntPtr OpenInputDesktop(
            uint flags,
            [MarshalAs(UnmanagedType.Bool)] bool inherit,
            uint desiredAccess);

        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool SetThreadDesktop(IntPtr desktop);

        [DllImport("user32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        internal static extern bool CloseDesktop(IntPtr desktop);
    }
}
