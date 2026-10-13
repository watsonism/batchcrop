import atexit
import datetime
import os
import platform
import re
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from PIL import Image, ImageTk, ImageOps

SUPPORTED = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff"}

TOAST_MS = 1800
UNDO_LIMIT = 50

SORT_MODES = ["Name A-Z", "Name Z-A", "Date old-new", "Date new-old"]
THUMB = 150
G_PAD = 10
G_CELL_W = THUMB + 2 * G_PAD
G_CELL_H = THUMB + 2 * G_PAD + 20


def _natural_key(name):
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", name)]


class BatchCrop(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("BatchCrop")
        self.geometry("1100x720")
        self.configure(bg="#1e1e1e")

        self.folder = None
        self.files = []
        self.index = 0
        self.image = None
        self.original_format = None
        self.tk_image = None
        self.crop = None
        self.drag = None
        self.scale = 1.0
        self.offset = (0, 0)
        self.delete_warning = True
        self.deleting = False
        self.toast_job = None
        self.pending_cut = []
        self.cut_job = None
        self.selection = set()     # selected file names (gallery view)
        self.sel_anchor = None
        self.rubber = None         # rubber-band rectangle in gallery canvas coords
        self.rb_origin = None
        self.rb_moved = False
        self.rb_base = set()
        self.rb_xy = (0, 0)
        self.rb_job = None
        self.toast_text = ""
        self.thumb_failed = set()   # thumbnail keys that could not be read
        self._last_focus_check = 0.0
        self._refresh_job = None
        self.last_dest = None      # last folder used by Move to / Copy to
        self.zoom = 1.0
        self.pan = (0, 0)
        self.pan_drag = None
        self.sort_var = tk.StringVar(value=SORT_MODES[0])
        self.undo_stack = []      # file operations: crop save, delete, rename
        self.crop_hist = []       # crop-box edits on the current image
        self.undo_dir = None      # lazily created temp dir holding pre-crop backups
        self.undo_counter = 0
        atexit.register(self._cleanup_undo)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.gallery_on = False
        self.keep_crop_var = tk.BooleanVar(value=False)
        self.last_crop = None
        self.drag_ratio = 1.0
        self.thumb_cache = {}
        self.thumb_pending = {}
        self.thumb_job = None
        self.thumb_pool = ThreadPoolExecutor(max_workers=2)

        self._build_ui()
        self.bind("<Configure>", lambda e: self.after_idle(self._fit_image))

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        top = tk.Frame(self, bg="#2d2d2d")
        top.pack(fill=tk.X)

        tk.Button(top, text="Open Folder", command=self.open_folder,
                  bg="#3a3a3a", fg="white", padx=8).pack(side=tk.LEFT, padx=4, pady=4)
        tk.Button(top, text="Open in File Explorer", command=self.open_in_explorer,
                  bg="#3a3a3a", fg="white", padx=8).pack(side=tk.LEFT, padx=4, pady=4)
        tk.Button(top, text="Refresh (F5)", command=self.refresh_folder,
                  bg="#3a3a3a", fg="white", padx=8).pack(side=tk.LEFT, padx=4, pady=4)
        self.sort_menu = tk.OptionMenu(top, self.sort_var, *SORT_MODES, command=lambda _v: self.apply_sort())
        self.sort_menu.config(bg="#3a3a3a", fg="white", activebackground="#4a4a4a", activeforeground="white",
                              highlightthickness=0)
        self.sort_menu["menu"].config(bg="#3a3a3a", fg="white")
        self.sort_menu.pack(side=tk.LEFT, padx=4, pady=4)
        self.gallery_btn = tk.Button(top, text="Gallery (G)", command=self.toggle_gallery,
                                     bg="#3a3a3a", fg="white", padx=8)
        self.gallery_btn.pack(side=tk.LEFT, padx=4, pady=4)
        self.status = tk.Label(top, text="No folder loaded", bg="#2d2d2d", fg="#cccccc")
        self.status.pack(side=tk.LEFT, padx=8)
        self.size_label = tk.Label(top, text="", bg="#2d2d2d", fg="#aaaaaa")
        self.size_label.pack(side=tk.RIGHT, padx=8)
        self.zoom_label = tk.Label(top, text="", bg="#2d2d2d", fg="#00ff88")
        self.zoom_label.pack(side=tk.RIGHT, padx=8)

        org = tk.Frame(self, bg="#262626")
        org.pack(fill=tk.X)
        tk.Label(org, text="Organize:", bg="#262626", fg="#888888").pack(side=tk.LEFT, padx=(8, 2))
        for text, cmd in (("Move to... (F6)", self.move_to_folder),
                          ("Copy to... (F7)", self.copy_to_folder),
                          ("Batch rename... (Ctrl+F2)", self.batch_rename)):
            tk.Button(org, text=text, command=cmd, bg="#3a3a3a", fg="white", padx=8).pack(
                side=tk.LEFT, padx=4, pady=3)

        self.path_label = tk.Label(self, text="", bg="#111111", fg="#ffffff", anchor="w", padx=8, pady=4)
        self.path_label.pack(fill=tk.X)

        self.canvas = tk.Canvas(self, bg="#111111", cursor="cross")
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Button-3>", self._on_right_click)

        self.gallery_frame = tk.Frame(self, bg="#111111")
        self.gallery_sb = tk.Scrollbar(self.gallery_frame, orient=tk.VERTICAL, command=self._gal_yview)
        self.gallery_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.gcanvas = tk.Canvas(self.gallery_frame, bg="#111111", highlightthickness=0,
                                 yscrollcommand=self.gallery_sb.set)
        self.gcanvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.gcanvas.bind("<Configure>", lambda e: self._gal_redraw())
        self.gcanvas.bind("<ButtonPress-1>", self._gal_press)
        self.gcanvas.bind("<B1-Motion>", self._gal_drag)
        self.gcanvas.bind("<ButtonRelease-1>", self._gal_release)
        self.gcanvas.bind("<Double-Button-1>", self._gal_double)
        self.gcanvas.bind("<Button-3>", self._on_right_click)

        self.bind("<Left>", lambda e: self.prev())
        self.bind("<Right>", lambda e: self.next())
        self.bind("<Up>", lambda e: self._gal_move(-1) if self.gallery_on else self.next())
        self.bind("<Down>", lambda e: self._gal_move(1) if self.gallery_on else self.prev())
        self.bind("<k>", lambda e: self._toggle_keep_crop())
        self.bind("<K>", lambda e: self._toggle_keep_crop())
        self.bind("<g>", lambda e: self.toggle_gallery())
        self.bind("<G>", lambda e: self.toggle_gallery())
        self.bind("<Return>", lambda e: self._on_return())
        self.bind("<Delete>", lambda e: self.delete_current())
        self.bind("<Escape>", lambda e: self._on_escape())
        self.bind("<Control-a>", lambda e: self.select_all())
        self.bind("<Control-A>", lambda e: self.select_all())
        self.bind("<Shift-Left>", lambda e: self._shift_arrow(-1))
        self.bind("<Shift-Right>", lambda e: self._shift_arrow(1))
        self.bind("<Shift-Up>", lambda e: self._shift_arrow(-1, vertical=True))
        self.bind("<Shift-Down>", lambda e: self._shift_arrow(1, vertical=True))
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", self._on_wheel)
        self.bind("<Button-5>", self._on_wheel)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan_move)
        for k in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            self.bind(k, lambda e: self.zoom_by(1.25))
        for k in ("<Control-minus>", "<Control-KP_Subtract>"):
            self.bind(k, lambda e: self.zoom_by(1 / 1.25))
        self.bind("<Control-0>", lambda e: self.zoom_reset())
        self.bind("<Control-z>", lambda e: self.undo())
        self.bind("<Control-Z>", lambda e: self.undo())
        self.bind("<F2>", lambda e: self.rename_current())
        self.bind("<Control-F2>", lambda e: self.batch_rename())
        self.bind("<F5>", lambda e: self.refresh_folder())
        self.bind("<F6>", lambda e: self.move_to_folder())
        self.bind("<F7>", lambda e: self.copy_to_folder())
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<Control-x>", lambda e: self.cut_current())
        self.bind("<Control-X>", lambda e: self.cut_current())
        self.bind("<Control-c>", lambda e: self.copy_current())
        self.bind("<Control-C>", lambda e: self.copy_current())

        bottom = tk.Frame(self, bg="#2d2d2d")
        bottom.pack(fill=tk.X)
        self.bottom = bottom
        self.undo_btn = tk.Button(bottom, text="Undo (Ctrl+Z)", command=self.undo, bg="#3a3a3a", fg="white",
                                  disabledforeground="#777777", state=tk.DISABLED)
        self.undo_btn.pack(side=tk.LEFT, padx=4, pady=4)
        tk.Button(bottom, text="Prev", command=self.prev, bg="#3a3a3a", fg="white").pack(
            side=tk.LEFT, padx=4, pady=4
        )
        tk.Button(bottom, text="Cut (Ctrl+X)", command=self.cut_current, bg="#a60", fg="white").pack(
            side=tk.LEFT, padx=4, pady=4
        )
        tk.Button(bottom, text="Copy (Ctrl+C)", command=self.copy_current, bg="#3a3a3a", fg="white").pack(
            side=tk.LEFT, padx=4, pady=4
        )
        tk.Button(bottom, text="Rename (F2)", command=self.rename_current, bg="#3a3a3a", fg="white").pack(
            side=tk.LEFT, padx=4, pady=4
        )
        tk.Button(bottom, text="Delete", command=self.delete_current, bg="#a22", fg="white").pack(
            side=tk.LEFT, padx=4, pady=4
        )
        tk.Button(bottom, text="Save Crop", command=self.save_crop, bg="#2a6", fg="white").pack(
            side=tk.LEFT, padx=4, pady=4
        )
        tk.Checkbutton(bottom, text="Keep crop (K)", variable=self.keep_crop_var, command=self._on_keep_toggle,
                       bg="#2d2d2d", fg="white", selectcolor="#1e1e1e", activebackground="#2d2d2d",
                       activeforeground="white").pack(side=tk.LEFT, padx=4, pady=4)
        tk.Button(bottom, text="Next", command=self.next, bg="#3a3a3a", fg="white").pack(
            side=tk.LEFT, padx=4, pady=4
        )

        help_lbl = tk.Label(
            bottom,
            text="Left: prev   Right: next   Enter: save crop   Del: delete   Esc: clear crop   Wheel: files   G: gallery   K: keep crop   Drag edges/corners: resize   Shift+drag: lock ratio   Ctrl+/-: zoom   F2: rename   F5: refresh   F6/F7: move/copy to   Ctrl+Z: undo   Ctrl+X: cut   Ctrl+C: copy",
            bg="#2d2d2d",
            fg="#888888",
        )
        help_lbl.pack(side=tk.RIGHT, padx=8)

    def _on_return(self):
        if self.deleting:
            return
        if self.gallery_on:
            self.toggle_gallery()
            return
        self.save_crop()

    # ------------------------------------------------------------------
    # Toast
    # ------------------------------------------------------------------
    def _show_toast(self, text):
        if self.toast_job is not None:
            self.after_cancel(self.toast_job)
            self.toast_job = None
        self._clear_toast()
        self.toast_text = text
        self._draw_toast()
        self.toast_job = self.after(TOAST_MS, self._clear_toast)

    def _draw_toast(self):
        if not self.toast_text:
            return
        c = self.gcanvas if self.gallery_on else self.canvas
        c.delete("toast_bg")
        c.delete("toast_text")
        cw = c.winfo_width() or 800
        ch = c.winfo_height() or 600
        x = cw // 2
        y = ch - 60 + (c.canvasy(0) if self.gallery_on else 0)
        pad_x = 18
        pad_y = 10
        txt = c.create_text(x, y, text=self.toast_text, fill="#ffffff", font=("TkDefaultFont", 13, "bold"),
                            tags="toast_text")
        bbox = c.bbox(txt)
        c.create_rectangle(
            bbox[0] - pad_x, bbox[1] - pad_y, bbox[2] + pad_x, bbox[3] + pad_y,
            fill="#000000", outline="#444444", width=1, tags="toast_bg"
        )
        c.tag_lower("toast_bg", txt)

    def _clear_toast(self):
        for c in (self.canvas, self.gcanvas):
            c.delete("toast_bg")
            c.delete("toast_text")
        self.toast_text = ""
        self.toast_job = None

    # ------------------------------------------------------------------
    # Cut / Copy / Reveal
    # ------------------------------------------------------------------
    def _current_path(self):
        if not self.files or not self.image or not self.folder:
            return None
        return os.path.join(self.folder, self.files[self.index])

    def _set_file_clipboard(self, paths, move):
        """Put file(s) on the system clipboard (CF_HDROP + Preferred DropEffect).

        On Windows this is the same thing Explorer's Ctrl+X / Ctrl+C does, so
        the file can be pasted in Explorer. Returns False if unsupported.
        """
        if platform.system() != "Windows":
            return False
        import ctypes
        from ctypes import wintypes

        k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
        k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k32.GlobalAlloc.restype = wintypes.HGLOBAL
        k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        u32.OpenClipboard.argtypes = [wintypes.HWND]
        u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        u32.SetClipboardData.restype = wintypes.HANDLE
        u32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
        u32.RegisterClipboardFormatW.restype = wintypes.UINT

        GMEM_MOVEABLE, GMEM_ZEROINIT, CF_HDROP = 0x0002, 0x0040, 15

        def make_global(data):
            h = k32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, len(data))
            p = k32.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            k32.GlobalUnlock(h)
            return h

        if isinstance(paths, str):
            paths = [paths]
        paths = [os.path.normpath(os.path.abspath(p)) for p in paths]
        # DROPFILES header: pFiles=20, pt=(0,0), fNC=0, fWide=1
        header = (20).to_bytes(4, "little") + bytes(8) + bytes(4) + (1).to_bytes(4, "little")
        hdrop = make_global(header + ("\0".join(paths) + "\0\0").encode("utf-16-le"))
        fmt = u32.RegisterClipboardFormatW("Preferred DropEffect")
        effect = make_global((2 if move else 1).to_bytes(4, "little"))

        opened = False
        for _ in range(10):
            if u32.OpenClipboard(None):
                opened = True
                break
            self.after(20)
        if not opened:
            k32.GlobalFree(hdrop)
            k32.GlobalFree(effect)
            return False
        try:
            u32.EmptyClipboard()
            ok1 = u32.SetClipboardData(CF_HDROP, hdrop)
            ok2 = u32.SetClipboardData(fmt, effect)
            return bool(ok1 and ok2)
        finally:
            u32.CloseClipboard()

    def _put_on_clipboard(self, paths, move):
        if not self._set_file_clipboard(paths, move):
            # Fallback (non-Windows / failure): plain text path(s)
            self.clipboard_clear()
            self.clipboard_append("\n".join([paths] if isinstance(paths, str) else paths))
            return False
        return True

    # ------------------------------------------------------------------
    # Targets: the selection in gallery view, otherwise the current image
    # ------------------------------------------------------------------
    def _target_names(self):
        if self.gallery_on and self.selection:
            names = [f for f in self.files if f in self.selection]
            if names:
                return names
        if self.files and self.image and 0 <= self.index < len(self.files):
            return [self.files[self.index]]
        return []

    def _target_paths(self):
        return [os.path.join(self.folder, n) for n in self._target_names()]

    @staticmethod
    def _describe(paths):
        return os.path.basename(paths[0]) if len(paths) == 1 else f"{len(paths)} files"

    def cut_current(self):
        """Ctrl+X: mark file(s) as cut on the clipboard. Nothing is moved here.
        Once pasted elsewhere (and gone from this folder), the watcher drops
        them from the list and moves on."""
        paths = self._target_paths()
        if not paths:
            return
        real = self._put_on_clipboard(paths, move=True)
        self.pending_cut = list(paths)
        self._show_toast(("Cut: " if real else "Cut (path only): ") + self._describe(paths))
        self._schedule_cut_watch()

    def copy_current(self):
        paths = self._target_paths()
        if not paths:
            return
        real = self._put_on_clipboard(paths, move=False)
        self.pending_cut = []
        self._show_toast(("Copied: " if real else "Copied (path only): ") + self._describe(paths))

    def _schedule_cut_watch(self):
        if self.cut_job is None:
            self.cut_job = self.after(500, self._check_cut)

    def _check_cut(self):
        self.cut_job = None
        if not self.pending_cut:
            return
        gone = [p for p in self.pending_cut if not os.path.exists(p)]
        self.pending_cut = [p for p in self.pending_cut if p not in gone]
        if self.pending_cut:
            self._schedule_cut_watch()
        if not gone:
            return
        folder_norm = os.path.normcase(os.path.normpath(self.folder or ""))
        names = {os.path.basename(p) for p in gone
                 if os.path.normcase(os.path.normpath(os.path.dirname(p))) == folder_norm}
        names &= set(self.files)
        if not names:
            return
        self._remove_from_list(names)

    def _remove_from_list(self, names):
        """Drop files from the list (they left the folder) and keep a sensible current image."""
        old_index = self.index
        cur = self.files[old_index] if 0 <= old_index < len(self.files) else None
        removed_before = sum(1 for i, f in enumerate(self.files) if f in names and i < old_index)
        self.files = [f for f in self.files if f not in names]
        self.selection -= names
        if not self.files:
            self.image = None
            self.selection = set()
            self.canvas.delete("all")
            self.status.config(text="0/0")
            self.path_label.config(text="")
            self.size_label.config(text="")
            self._gal_redraw()
            return
        if cur in self.files:
            self.index = self.files.index(cur)
            self.status.config(text=f"{self.index+1}/{len(self.files)}")
            self._gal_redraw()
        else:
            self.index = min(old_index - removed_before, len(self.files) - 1)
            self.load_image()

    def rename_current(self):
        if self.gallery_on and len(self.selection) > 1:
            self.batch_rename()      # like Explorer: F2 on several files renames them together
            return
        path = self._current_path()
        if not path:
            return
        old = os.path.basename(path)
        new = simpledialog.askstring("Rename", "New file name:", initialvalue=old, parent=self)
        if new is None:
            return
        new = new.strip()
        if not new or new == old:
            return
        if any(c in new for c in '<>:"/\\|?*') or new.endswith("."):
            messagebox.showerror("BatchCrop", "Invalid file name.")
            return
        # keep the original extension if the user left it off
        if not os.path.splitext(new)[1]:
            new += os.path.splitext(old)[1]
        target = os.path.join(self.folder, new)
        # case-only renames are fine on Windows; anything else must not clobber
        if os.path.exists(target) and os.path.normcase(target) != os.path.normcase(path):
            messagebox.showerror("BatchCrop", f"'{new}' already exists.")
            return
        try:
            os.rename(path, target)
        except Exception as e:
            messagebox.showerror("BatchCrop", f"Rename failed:\n{e}")
            return
        if path in self.pending_cut:
            self.pending_cut.remove(path)
        self.selection.discard(os.path.basename(path))
        self._push_undo({"kind": "rename", "old": path, "new": target})
        self.files[self.index] = new
        if self.gallery_on:
            self.selection = {new}
            self.sel_anchor = new
        self._sort_files(keep=new)
        self.path_label.config(text=os.path.normpath(target))
        self.status.config(text=f"{self.index+1}/{len(self.files)}")
        self._gal_redraw()
        self._show_toast("Renamed: " + new)

    # ------------------------------------------------------------------
    # Refresh (F5): re-read the folder so moved / added / deleted files show correctly
    # ------------------------------------------------------------------
    def _scan_folder(self):
        return [
            f for f in os.listdir(self.folder)
            if os.path.splitext(f)[1].lower() in SUPPORTED and os.path.isfile(os.path.join(self.folder, f))
        ]

    def _on_focus_in(self, event):
        """Coming back from Explorer: refresh if the folder changed behind our back."""
        if event.widget is not self or not self.folder:
            return
        now = time.time()
        if now - self._last_focus_check < 1.0:
            return
        self._last_focus_check = now
        try:
            if set(self._scan_folder()) != set(self.files):
                self.refresh_folder()
        except OSError:
            pass

    def refresh_folder(self, quiet=False):
        self._refresh_job = None
        if not self.folder:
            if not quiet:
                self._show_toast("Open a folder first")
            return False
        if not os.path.isdir(self.folder):
            self._show_toast("The folder no longer exists")
            return False
        try:
            listing = self._scan_folder()
        except OSError as e:
            messagebox.showerror("BatchCrop", f"Could not read the folder:\n{e}")
            return False
        old, new = set(self.files), set(listing)
        added, gone = len(new - old), len(old - new)
        self.thumb_failed.clear()          # give unreadable files another chance
        for k in [k for k in self.thumb_cache if not os.path.exists(k[0])]:
            del self.thumb_cache[k]
        if not listing:
            self.pending_cut = []
            self._remove_from_list(old)
            self._show_toast("Refreshed: no images left in this folder")
            return True
        old_index = self.index
        cur = self.files[old_index] if 0 <= old_index < len(self.files) else None
        self.files = listing
        self.selection &= new
        self.pending_cut = [p for p in self.pending_cut if os.path.exists(p)]
        self._sort_files(keep=cur if cur in new else None)
        if cur in new:
            changed_current = False
        else:
            self.index = min(old_index, len(self.files) - 1)
            changed_current = True
        if not self.image or changed_current:
            self.load_image()              # redraws the gallery too
        else:
            self._update_status()
            self._gal_redraw(ensure_visible=True)
        if added or gone:
            self._show_toast(f"Refreshed: {added} new, {gone} gone")
        elif not quiet:
            self._show_toast("Refreshed: no changes")
        return bool(added or gone)

    # ------------------------------------------------------------------
    # Organize: move to / copy to / batch rename
    # ------------------------------------------------------------------
    def _ask_dest(self, title):
        start = self.last_dest if self.last_dest and os.path.isdir(self.last_dest) else (
            os.path.dirname(self.folder) if self.folder else None)
        d = filedialog.askdirectory(title=title, initialdir=start, parent=self)
        if not d:
            return None
        d = os.path.normpath(d)
        self.last_dest = d
        return d

    def _same_dir(self, a, b):
        return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))

    def _plan_transfer(self, names, dest):
        """(src, dst) pairs. Never overwrites: asks what to do about name clashes."""
        items = [(os.path.join(self.folder, n), os.path.join(dest, n)) for n in names]
        clashes = [n for n in names if os.path.exists(os.path.join(dest, n))]
        if clashes:
            ans = messagebox.askyesnocancel(
                "Name conflicts",
                f"{len(clashes)} file(s) already exist in the destination (for example {clashes[0]}).\n\n"
                "Yes = keep both (new copies get _1, _2 ...)\n"
                "No = skip those files\n"
                "Cancel = do nothing", parent=self)
            if ans is None:
                return None
            if ans:
                items = [(src, self._unique_trash_target(dest, os.path.basename(src)) if os.path.exists(dst) else dst)
                         for src, dst in items]
            else:
                items = [(src, dst) for src, dst in items if not os.path.exists(dst)]
        return items

    def _transfer(self, move):
        names = self._target_names()
        if not names:
            self._show_toast("Nothing to " + ("move" if move else "copy"))
            return
        what = names[0] if len(names) == 1 else f"{len(names)} files"
        dest = self._ask_dest(f"{'Move' if move else 'Copy'} {what} to...")
        if not dest:
            return
        if self._same_dir(dest, self.folder):
            self._show_toast("Pick a different folder than the current one")
            return
        items = self._plan_transfer(names, dest)
        if not items:
            if items is not None:
                self._show_toast("Nothing to do: every file was skipped")
            return
        self.config(cursor="watch")
        self.update_idletasks()
        done, failed = [], []
        try:
            for src, dst in items:
                try:
                    if move:
                        shutil.move(src, dst)
                    else:
                        shutil.copy2(src, dst)
                    done.append((src, dst))
                except Exception as e:
                    failed.append((os.path.basename(src), e))
        finally:
            self.config(cursor="")
        if failed:
            messagebox.showerror("BatchCrop", f"{len(failed)} file(s) failed:\n{failed[0][0]}: {failed[0][1]}")
        if not done:
            return
        label = os.path.basename(dest) or dest
        summary = os.path.basename(done[0][0]) if len(done) == 1 else f"{len(done)} files"
        if move:
            self._push_undo({"kind": "move_many", "items": done})
            moved = {os.path.basename(src) for src, _ in done}
            self.pending_cut = [p for p in self.pending_cut if os.path.basename(p) not in moved]
            self.sel_anchor = None
            self._remove_from_list(moved)
            self.selection = set()
            self._gal_redraw()
            self._show_toast(f"Moved {summary} to {label}")
        else:
            self._push_undo({"kind": "copy_many", "items": done})
            self._show_toast(f"Copied {summary} to {label}")

    def move_to_folder(self):
        self._transfer(move=True)

    def copy_to_folder(self):
        self._transfer(move=False)

    # --- batch rename -------------------------------------------------
    _BAD_CHARS = '<>:"/\\|?*'
    _RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}

    def _compute_batch(self, names, pattern, start, digits, find, repl):
        """Preview: list of (old, new, error). Extension is always kept."""
        out = []
        for k, old in enumerate(names):
            stem, ext = os.path.splitext(old)
            try:
                mdate = datetime.datetime.fromtimestamp(
                    os.path.getmtime(os.path.join(self.folder, old))).strftime("%Y-%m-%d")
            except OSError:
                mdate = "unknown-date"
            new_stem = (pattern.replace("{name}", stem)
                        .replace("{n}", str(start + k).zfill(max(1, digits)))
                        .replace("{date}", mdate))
            if find:
                new_stem = new_stem.replace(find, repl)
            new = new_stem + ext
            err = None
            if not new_stem.strip():
                err = "empty name"
            elif any(c in new_stem for c in self._BAD_CHARS):
                err = "illegal character"
            elif new_stem.endswith((".", " ")):
                err = "ends with . or space"
            elif new_stem.upper() in self._RESERVED:
                err = "reserved name"
            out.append((old, new, err))
        # duplicates inside the batch, and clashes with files that are not part of it
        seen = {}
        for i, (old, new, err) in enumerate(out):
            if err:
                continue
            key = os.path.normcase(new)
            if key in seen:
                out[i] = (old, new, "duplicate name")
                j = seen[key]
                if not out[j][2]:
                    out[j] = (out[j][0], out[j][1], "duplicate name")
            else:
                seen[key] = i
        batch = {os.path.normcase(o) for o, _, _ in out}
        for i, (old, new, err) in enumerate(out):
            if err or new == old:
                continue
            if os.path.exists(os.path.join(self.folder, new)) and os.path.normcase(new) not in batch:
                out[i] = (old, new, "already exists")
        return out

    def _rename_set(self, pairs):
        """Rename (old_path, new_path) pairs through temporary names so swaps and
        chains work; roll everything back if anything fails."""
        stage1, stage2 = [], []
        try:
            for i, (o, n) in enumerate(pairs):
                tmp = f"{o}.bcr{i}.tmp"
                k = 0
                while os.path.exists(tmp):
                    k += 1
                    tmp = f"{o}.bcr{i}_{k}.tmp"
                os.rename(o, tmp)
                stage1.append((tmp, o, n))
            for tmp, o, n in stage1:
                os.rename(tmp, n)
                stage2.append((tmp, o, n))
        except Exception:
            for tmp, o, n in reversed(stage2):
                try:
                    os.rename(n, tmp)
                except OSError:
                    pass
            for tmp, o, n in reversed(stage1):
                try:
                    os.rename(tmp, o)
                except OSError:
                    pass
            raise

    def _apply_name_changes(self, mapping):
        """Update list/selection/pending-cut after files were renamed ({old_name: new_name})."""
        cur = self.files[self.index] if self.files and 0 <= self.index < len(self.files) else None
        self.files = [mapping.get(f, f) for f in self.files]
        self.selection = {mapping.get(f, f) for f in self.selection}
        if self.sel_anchor in mapping:
            self.sel_anchor = mapping[self.sel_anchor]
        olds = {os.path.join(self.folder, o): os.path.join(self.folder, n) for o, n in mapping.items()}
        self.pending_cut = [olds.get(p, p) for p in self.pending_cut]
        keep = mapping.get(cur, cur)
        self._sort_files(keep=keep)
        if self.files:
            self.path_label.config(text=os.path.normpath(os.path.join(self.folder, self.files[self.index])))
        self._update_status()
        self._gal_redraw()

    def _do_rename_many(self, pairs):
        try:
            self._rename_set(pairs)
        except Exception as e:
            messagebox.showerror("BatchCrop", f"Batch rename failed (nothing was changed):\n{e}")
            return False
        self._push_undo({"kind": "rename_many", "items": pairs})
        self._apply_name_changes({os.path.basename(o): os.path.basename(n) for o, n in pairs})
        self._show_toast(f"Renamed {len(pairs)} file(s)")
        return True

    def batch_rename(self):
        if not self.files or not self.folder:
            self._show_toast("Open a folder first")
            return
        sel_names = self._target_names()
        dlg = tk.Toplevel(self)
        self._br_dialog = dlg
        dlg.title("Batch rename")
        dlg.configure(bg="#2d2d2d")
        dlg.transient(self)
        dlg.grab_set()
        fg, bg = "#dddddd", "#2d2d2d"
        scope = tk.StringVar(value="sel")
        pat = tk.StringVar(value="{name}")
        start = tk.StringVar(value="1")
        digits = tk.StringVar(value="3")
        find = tk.StringVar()
        repl = tk.StringVar()
        self._br = {"scope": scope, "pattern": pat, "start": start, "digits": digits, "find": find,
                    "repl": repl}

        def row(r, text, widget):
            tk.Label(dlg, text=text, bg=bg, fg=fg, anchor="e").grid(row=r, column=0, sticky="e", padx=(12, 6), pady=3)
            widget.grid(row=r, column=1, sticky="we", padx=(0, 12), pady=3)

        sc = tk.Frame(dlg, bg=bg)
        for text, val in ((f"Selected / current ({len(sel_names)})", "sel"),
                          (f"All images in this folder ({len(self.files)})", "all")):
            tk.Radiobutton(sc, text=text, variable=scope, value=val, bg=bg, fg=fg, selectcolor="#1e1e1e",
                           activebackground=bg, activeforeground=fg).pack(side=tk.LEFT, padx=(0, 12))
        row(0, "Apply to:", sc)
        row(1, "Pattern:", tk.Entry(dlg, textvariable=pat, width=48))
        tk.Label(dlg, text="Tokens: {name} original name   {n} counter   {date} modified date   (extension is kept)",
                 bg=bg, fg="#888888").grid(row=2, column=1, sticky="w", padx=(0, 12))
        nums = tk.Frame(dlg, bg=bg)
        tk.Label(nums, text="Start at", bg=bg, fg=fg).pack(side=tk.LEFT)
        tk.Spinbox(nums, from_=0, to=999999, width=7, textvariable=start).pack(side=tk.LEFT, padx=(4, 14))
        tk.Label(nums, text="Digits", bg=bg, fg=fg).pack(side=tk.LEFT)
        tk.Spinbox(nums, from_=1, to=8, width=3, textvariable=digits).pack(side=tk.LEFT, padx=4)
        row(3, "Counter:", nums)
        row(4, "Find:", tk.Entry(dlg, textvariable=find, width=48))
        row(5, "Replace with:", tk.Entry(dlg, textvariable=repl, width=48))
        box = tk.Frame(dlg, bg=bg)
        lb = tk.Listbox(box, width=90, height=12, bg="#111111", fg="white", activestyle="none")
        sb = tk.Scrollbar(box, command=lb.yview)
        lb.config(yscrollcommand=sb.set)
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        box.grid(row=6, column=0, columnspan=2, padx=12, pady=(6, 4), sticky="nsew")
        info = tk.Label(dlg, text="", bg=bg, fg="#aaaaaa", anchor="w")
        info.grid(row=7, column=0, columnspan=2, sticky="we", padx=12)
        btns = tk.Frame(dlg, bg=bg)
        btns.grid(row=8, column=0, columnspan=2, pady=10)
        apply_btn = tk.Button(btns, text="Rename", width=12, bg="#2a6", fg="white", disabledforeground="#777777")
        apply_btn.pack(side=tk.LEFT, padx=6)
        tk.Button(btns, text="Cancel", width=12, command=dlg.destroy).pack(side=tk.LEFT, padx=6)
        self._br.update({"listbox": lb, "apply": apply_btn, "info": info})

        def to_int(v, default):
            try:
                return int(v.get())
            except (ValueError, tk.TclError):
                return default

        state = {"plan": []}

        def refresh(*_):
            names = list(self.files) if scope.get() == "all" else sel_names
            plan = self._compute_batch(names, pat.get(), to_int(start, 1), to_int(digits, 1), find.get(), repl.get())
            state["plan"] = plan
            lb.delete(0, tk.END)
            bad = changed = 0
            for old, new, err in plan:
                if err:
                    bad += 1
                    lb.insert(tk.END, f"{old}   ->   ! {new}   ({err})")
                    lb.itemconfig(tk.END, foreground="#ff7777")
                else:
                    if new != old:
                        changed += 1
                    lb.insert(tk.END, f"{old}   ->   {new}" + ("" if new != old else "   (unchanged)"))
            info.config(text=f"{changed} will be renamed" + (f", {bad} problem(s): fix them to continue" if bad else ""),
                        fg="#ff7777" if bad else "#aaaaaa")
            apply_btn.config(state=tk.NORMAL if (changed and not bad) else tk.DISABLED)

        def do_apply():
            pairs = [(os.path.join(self.folder, o), os.path.join(self.folder, n))
                     for o, n, err in state["plan"] if not err and n != o]
            dlg.destroy()
            if pairs:
                self._do_rename_many(pairs)

        apply_btn.config(command=do_apply)
        for v in (pat, start, digits, find, repl, scope):
            v.trace_add("write", refresh)
        refresh()
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        self.wait_window(dlg)

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------
    def _update_undo_btn(self):
        can = bool(self.crop_hist or self.undo_stack)
        self.undo_btn.config(state=tk.NORMAL if can else tk.DISABLED)

    def _push_crop_hist(self):
        self.crop_hist.append(tuple(self.crop) if self.crop else None)
        del self.crop_hist[:-UNDO_LIMIT]
        self._update_undo_btn()

    def _push_undo(self, entry):
        self.undo_stack.append(entry)
        while len(self.undo_stack) > UNDO_LIMIT:
            self._discard_entry(self.undo_stack.pop(0))
        self._update_undo_btn()

    def _make_backup(self, path):
        """Copy the file (with its timestamps) to a temp dir before it is overwritten."""
        try:
            if self.undo_dir is None:
                self.undo_dir = tempfile.mkdtemp(prefix="batchcrop_undo_")
            self.undo_counter += 1
            backup = os.path.join(self.undo_dir, f"{self.undo_counter}_{os.path.basename(path)}")
            shutil.copy2(path, backup)
            return backup
        except Exception:
            return None

    def _discard_entry(self, entry):
        b = entry.get("backup")
        if b:
            try:
                os.remove(b)
            except OSError:
                pass

    def _clear_undo(self):
        for e in self.undo_stack:
            self._discard_entry(e)
        self.undo_stack = []
        self.crop_hist = []
        self._update_undo_btn()

    def _cleanup_undo(self):
        if self.undo_dir:
            shutil.rmtree(self.undo_dir, ignore_errors=True)
            self.undo_dir = None

    def _on_close(self):
        self._cleanup_undo()
        self.destroy()

    def _select_file(self, name):
        """Make `name` (a file in self.folder) the current image, adding it to the list if needed."""
        if name not in self.files:
            self.files.append(name)
        self._sort_files(keep=name)
        if self.gallery_on:
            self.selection = {name}
            self.sel_anchor = name
        self.load_image()

    def undo(self):
        # 1) crop-box edits on the current image come first
        if self.crop_hist and self.image:
            self.crop = self.crop_hist.pop()
            self.drag = None
            if self.crop:
                self._clamp_crop()
            self.canvas.delete("crop")
            self._draw_crop()
            self._update_crop_size_label()
            self._update_undo_btn()
            self._show_toast("Undid crop box change")
            return
        # 2) then file operations, newest first
        if not self.undo_stack:
            self._show_toast("Nothing to undo")
            return
        entry = self.undo_stack[-1]
        kind = entry["kind"]
        try:
            if kind == "crop":
                path = entry["path"]
                if not os.path.exists(path):
                    raise FileNotFoundError(f"{os.path.basename(path)} is no longer in its folder")
                shutil.copy2(entry["backup"], path)
                name = os.path.basename(path)
                self._select_file(name)
                self.crop = self._fit_crop_to_image(entry["rect"])
                self.crop_hist = []
                self._draw_crop()
                self._update_crop_size_label()
                msg = "Undid crop: " + name
            elif kind == "delete_many":
                items = entry["items"]
                for src, trash in items:
                    if not os.path.exists(trash):
                        raise FileNotFoundError(f"{os.path.basename(src)} is no longer in .batchcrop_trash")
                    if os.path.exists(src):
                        raise FileExistsError(f"{os.path.basename(src)} already exists in the folder")
                for src, trash in items:
                    os.rename(trash, src)
                names = [os.path.basename(src) for src, _ in items]
                for n in names:
                    if n not in self.files:
                        self.files.append(n)
                self._select_file(names[0])
                if self.gallery_on:
                    self.selection = set(names)
                    self._gal_redraw()
                msg = "Restored: " + (names[0] if len(names) == 1 else f"{len(names)} files")
            elif kind == "move_many":
                items = entry["items"]
                for src, dst in items:
                    if not os.path.exists(dst):
                        raise FileNotFoundError(f"{os.path.basename(dst)} is no longer in {os.path.dirname(dst)}")
                    if os.path.exists(src):
                        raise FileExistsError(f"{os.path.basename(src)} already exists in the original folder")
                for src, dst in items:
                    shutil.move(dst, src)
                back = [os.path.basename(src) for src, _ in items if self._same_dir(os.path.dirname(src), self.folder)]
                for n in back:
                    if n not in self.files:
                        self.files.append(n)
                if back:
                    self._select_file(back[0])
                    if self.gallery_on:
                        self.selection = set(back)
                        self._gal_redraw()
                msg = "Moved back: " + (back[0] if len(back) == 1 else f"{len(items)} files")
            elif kind == "copy_many":
                # copies go to the destination's trash folder instead of being deleted
                moved = 0
                for _, dst in entry["items"]:
                    if os.path.exists(dst):
                        tdir = os.path.join(os.path.dirname(dst), ".batchcrop_trash")
                        os.makedirs(tdir, exist_ok=True)
                        shutil.move(dst, self._unique_trash_target(tdir, os.path.basename(dst)))
                        moved += 1
                msg = f"Undid copy ({moved} file(s) moved to the destination's .batchcrop_trash)"
            elif kind == "rename_many":
                items = entry["items"]          # (old_path, new_path)
                newset = {os.path.normcase(n) for _, n in items}
                for old, new in items:
                    if not os.path.exists(new):
                        raise FileNotFoundError(f"{os.path.basename(new)} no longer exists")
                    if os.path.exists(old) and os.path.normcase(old) not in newset:
                        raise FileExistsError(f"{os.path.basename(old)} already exists")
                self._rename_set([(n, o) for o, n in items])
                self._apply_name_changes({os.path.basename(n): os.path.basename(o) for o, n in items})
                msg = f"Undid rename of {len(items)} files"
            elif kind == "delete":
                path, trash = entry["path"], entry["trash"]
                if not os.path.exists(trash):
                    raise FileNotFoundError("The deleted file is no longer in .batchcrop_trash")
                if os.path.exists(path):
                    raise FileExistsError(f"{os.path.basename(path)} already exists in the folder")
                os.rename(trash, path)
                name = os.path.basename(path)
                self._select_file(name)
                msg = "Restored: " + name
            else:  # rename
                old, new = entry["old"], entry["new"]
                if not os.path.exists(new):
                    raise FileNotFoundError(f"{os.path.basename(new)} no longer exists")
                if os.path.exists(old) and os.path.normcase(old) != os.path.normcase(new):
                    raise FileExistsError(f"{os.path.basename(old)} already exists")
                os.rename(new, old)
                if os.path.basename(new) in self.files:
                    self.files.remove(os.path.basename(new))
                self._select_file(os.path.basename(old))
                msg = "Undid rename: " + os.path.basename(old)
        except Exception as e:
            messagebox.showerror("BatchCrop", f"Undo failed:\n{e}")
            return
        self.undo_stack.pop()
        self._discard_entry(entry)
        self._update_undo_btn()
        self._show_toast(msg)

    def _update_status(self):
        if not self.files:
            return
        text = f"{self.index+1}/{len(self.files)}"
        if self.gallery_on and len(self.selection) > 1:
            text += f"   ({len(self.selection)} selected)"
        self.status.config(text=text)

    # ------------------------------------------------------------------
    # Sorting
    # ------------------------------------------------------------------
    def _sort_files(self, keep=None):
        """Sort self.files per the selected mode, keeping the selected file."""
        if keep is None and self.files and 0 <= self.index < len(self.files):
            keep = self.files[self.index]
        mode = self.sort_var.get()
        if mode.startswith("Date"):
            def mtime(f):
                try:
                    return os.path.getmtime(os.path.join(self.folder, f))
                except OSError:
                    return 0
            self.files.sort(key=lambda f: (mtime(f), _natural_key(f)), reverse=mode.endswith("new-old"))
        else:
            self.files.sort(key=_natural_key, reverse=mode.endswith("Z-A"))
        if keep in self.files:
            self.index = self.files.index(keep)
        else:
            self.index = 0

    def apply_sort(self):
        if not self.files:
            return
        self._sort_files()
        self.status.config(text=f"{self.index+1}/{len(self.files)}")
        self._gal_redraw(ensure_visible=True)
        self._show_toast("Sorted: " + self.sort_var.get())

    # ------------------------------------------------------------------
    # Gallery
    # ------------------------------------------------------------------
    def toggle_gallery(self):
        if self.gallery_on:
            self.gallery_on = False
            self.gallery_frame.pack_forget()
            self.canvas.pack(fill=tk.BOTH, expand=True, padx=6, pady=6, before=self.bottom)
            self.gallery_btn.config(text="Gallery (G)", bg="#3a3a3a")
            self.selection = set()     # multi-select only applies in gallery view
            self.sel_anchor = None
            self._update_status()
            self.after_idle(self._fit_image)
        else:
            if not self.files:
                return
            self.gallery_on = True
            self.selection = {self.files[self.index]}
            self.sel_anchor = self.files[self.index]
            self.canvas.pack_forget()
            self.gallery_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6, before=self.bottom)
            self.gallery_btn.config(text="Single (G)", bg="#2a6")
            self.update_idletasks()
            self._gal_redraw(ensure_visible=True)

    def _gal_cols(self):
        return max(1, (self.gcanvas.winfo_width() or G_CELL_W) // G_CELL_W)

    def _gal_yview(self, *args):
        self.gcanvas.yview(*args)
        self._gal_redraw()

    def _gal_ensure_visible(self):
        cols = self._gal_cols()
        row = self.index // cols
        top = row * G_CELL_H
        h = self.gcanvas.winfo_height() or 600
        total = max(1, ((len(self.files) + cols - 1) // cols) * G_CELL_H)
        cur = self.gcanvas.canvasy(0)
        if top < cur:
            self.gcanvas.yview_moveto(top / total)
        elif top + G_CELL_H > cur + h:
            self.gcanvas.yview_moveto((top + G_CELL_H - h) / total)

    def _gal_redraw(self, ensure_visible=False):
        if not self.gallery_on:
            return
        c = self.gcanvas
        c.delete("all")
        if not self.files:
            return
        self.selection &= set(self.files)
        self._update_status()
        cols = self._gal_cols()
        rows = (len(self.files) + cols - 1) // cols
        w = c.winfo_width() or 800
        h = c.winfo_height() or 600
        c.configure(scrollregion=(0, 0, w, rows * G_CELL_H))
        if ensure_visible:
            self._gal_ensure_visible()
        y0 = c.canvasy(0)
        first = max(0, int(y0 // G_CELL_H))
        last = min(rows - 1, int((y0 + h) // G_CELL_H))
        for r in range(first, last + 1):
            for col in range(cols):
                i = r * cols + col
                if i >= len(self.files):
                    break
                self._gal_draw_cell(i, col * G_CELL_W, r * G_CELL_H)
        if self.rubber:
            c.create_rectangle(*self.rubber, outline="#4aa3ff", fill="#4aa3ff", stipple="gray25", tags="rubber")
        if self.toast_text:
            self._draw_toast()

    def _gal_draw_cell(self, i, x, y):
        c = self.gcanvas
        name = self.files[i]
        path = os.path.join(self.folder, name)
        focus = i == self.index
        selected = name in self.selection
        c.create_rectangle(x + 2, y + 2, x + G_CELL_W - 2, y + G_CELL_H - 2,
                           fill="#1f3550" if selected else ("#2a3a30" if focus else "#1a1a1a"),
                           outline="#00ff88" if focus else ("#4aa3ff" if selected else "#2a2a2a"),
                           width=2 if (focus or selected) else 1)
        try:
            key = (path, os.path.getmtime(path))
        except OSError:
            key = (path, 0)
        photo = self.thumb_cache.get(key)
        cx = x + G_CELL_W // 2
        if photo is not None:
            c.create_image(cx, y + G_PAD + THUMB // 2, image=photo)
        elif key in self.thumb_failed:
            c.create_rectangle(cx - THUMB // 2, y + G_PAD, cx + THUMB // 2, y + G_PAD + THUMB,
                               fill="#3a1f1f", outline="#552222")
            c.create_text(cx, y + G_PAD + THUMB // 2, text="unreadable", fill="#cc7777")
        else:
            c.create_text(cx, y + G_PAD + THUMB // 2, text="...", fill="#666666")
            self._thumb_request(key)
        shown = name if len(name) <= 22 else name[:10] + "..." + name[-9:]
        c.create_text(cx, y + G_PAD + THUMB + 12, text=shown, fill="#cccccc", font=("TkDefaultFont", 8))

    def _thumb_request(self, key):
        if key in self.thumb_pending:
            return
        path = key[0]

        def work():
            with Image.open(path) as im:
                im.draft("RGB", (THUMB * 2, THUMB * 2))
                im = ImageOps.exif_transpose(im)
                im.thumbnail((THUMB, THUMB))
                return im.convert("RGB")

        self.thumb_pending[key] = self.thumb_pool.submit(work)
        if self.thumb_job is None:
            self.thumb_job = self.after(40, self._thumb_poll)

    def _thumb_poll(self):
        self.thumb_job = None
        done = [k for k, f in self.thumb_pending.items() if f.done()]
        if len(self.thumb_cache) > 800:
            self.thumb_cache.clear()
        missing = False
        for k in done:
            f = self.thumb_pending.pop(k)
            try:
                self.thumb_cache[k] = ImageTk.PhotoImage(f.result())
            except Exception:
                if not os.path.exists(k[0]):
                    missing = True         # the file left the folder: the list is stale
                else:
                    self.thumb_failed.add(k)   # exists but unreadable: show it as such, don't retry forever
        if missing and self._refresh_job is None:
            self._refresh_job = self.after_idle(lambda: self.refresh_folder(quiet=True))
        if done:
            self._gal_redraw()
        if self.thumb_pending:
            self.thumb_job = self.after(40, self._thumb_poll)

    def _gal_index_at(self, event):
        cols = self._gal_cols()
        col = int(event.x // G_CELL_W)
        row = int(self.gcanvas.canvasy(event.y) // G_CELL_H)
        if col >= cols or col < 0 or row < 0:
            return None
        i = row * cols + col
        return i if i < len(self.files) else None

    def _gal_hits(self, rect):
        """Names of thumbnails whose cell intersects rect (gallery canvas coordinates)."""
        x1, y1, x2, y2 = rect
        cols = self._gal_cols()
        c1, c2 = max(0, int(x1 // G_CELL_W)), min(cols - 1, int(x2 // G_CELL_W))
        r1, r2 = max(0, int(y1 // G_CELL_H)), int(y2 // G_CELL_H)
        names = set()
        for r in range(r1, r2 + 1):
            for col in range(c1, c2 + 1):
                i = r * cols + col
                if 0 <= i < len(self.files):
                    names.add(self.files[i])
        return names

    def _gal_range(self, a_name, b_name):
        if a_name not in self.files or b_name not in self.files:
            return {b_name}
        i, j = sorted((self.files.index(a_name), self.files.index(b_name)))
        return set(self.files[i:j + 1])

    def _gal_press(self, event):
        ctrl, shift = bool(event.state & 0x4), bool(event.state & 0x1)
        self.rb_origin = (event.x, self.gcanvas.canvasy(event.y))
        self.rb_moved = False
        self.rb_base = set(self.selection) if (ctrl or shift) else set()
        i = self._gal_index_at(event)
        if i is None:
            if not (ctrl or shift):
                self.selection = set()     # click on empty space clears the selection
                self.sel_anchor = None
                self._gal_redraw()
            return
        name = self.files[i]
        if ctrl and not shift:
            self.selection = (self.selection - {name}) if name in self.selection else (self.selection | {name})
            self.sel_anchor = name
        elif shift:
            anchor = self.sel_anchor if self.sel_anchor in self.files else self.files[self.index]
            rng = self._gal_range(anchor, name)
            self.selection = (self.selection | rng) if ctrl else rng
        else:
            self.selection = {name}
            self.sel_anchor = name
        # a band dragged from here extends this selection only when Ctrl/Shift is held
        self.rb_base = set(self.selection) if (ctrl or shift) else set()
        if i != self.index:
            self.index = i
            self.load_image()      # also redraws
        else:
            self._gal_redraw()

    def _gal_update_band(self, x, wy):
        x0, y0 = self.rb_origin
        y1 = self.gcanvas.canvasy(wy)
        self.rubber = (min(x0, x), min(y0, y1), max(x0, x), max(y0, y1))
        self.selection = self.rb_base | self._gal_hits(self.rubber)
        self._gal_redraw()

    def _gal_drag(self, event):
        if not self.rb_origin:
            return
        x0, y0 = self.rb_origin
        if not self.rb_moved and abs(event.x - x0) < 5 and abs(self.gcanvas.canvasy(event.y) - y0) < 5:
            return
        self.rb_moved = True
        self.rb_xy = (event.x, event.y)
        self._gal_update_band(event.x, event.y)
        if self.rb_job is None:
            self.rb_job = self.after(60, self._gal_autoscroll)

    def _gal_autoscroll(self):
        self.rb_job = None
        if not (self.rb_origin and self.rb_moved):
            return
        x, y = self.rb_xy
        h = self.gcanvas.winfo_height() or 600
        if y < 20 or y > h - 20:
            self.gcanvas.yview_scroll(-1 if y < 20 else 1, "units")
            self._gal_update_band(x, y)
        self.rb_job = self.after(60, self._gal_autoscroll)

    def _gal_release(self, event):
        if self.rb_job is not None:
            self.after_cancel(self.rb_job)
            self.rb_job = None
        moved = self.rb_moved
        self.rb_origin, self.rb_moved, self.rubber = None, False, None
        if moved:
            self._gal_redraw()

    def _gal_double(self, event):
        i = self._gal_index_at(event)
        if i is not None:
            self.selection = {self.files[i]}
            self.index = i
            self.load_image()
            self.toggle_gallery()

    def _gal_move(self, rows, extend=False):
        if not self.files:
            return
        new = self.index + rows * self._gal_cols()
        if 0 <= new < len(self.files):
            self._gal_goto(new, extend)

    def _gal_goto(self, new, extend=False):
        """Move the focus to index `new`; with extend, grow the selection from the anchor (Shift+arrows)."""
        new %= len(self.files)
        if extend:
            anchor = self.sel_anchor if self.sel_anchor in self.files else self.files[self.index]
            self.sel_anchor = anchor
            self.selection = self._gal_range(anchor, self.files[new])
        else:
            self.selection = {self.files[new]}
            self.sel_anchor = self.files[new]
        self.index = new
        self.load_image()
        self._gal_redraw(ensure_visible=True)

    def _shift_arrow(self, delta, vertical=False):
        if not self.gallery_on:       # same as the plain arrows in single view
            if vertical:
                (self.next if delta < 0 else self.prev)()      # Up = next, Down = prev
            else:
                (self.prev if delta < 0 else self.next)()
            return
        if not self.files:
            return
        step = delta * (self._gal_cols() if vertical else 1)
        new = self.index + step
        if 0 <= new < len(self.files):
            self._gal_goto(new, extend=True)

    def select_all(self):
        if self.gallery_on and self.files:
            self.selection = set(self.files)
            self._gal_redraw()

    def _on_escape(self):
        if self.gallery_on:
            self.selection = set()
            self.sel_anchor = None
            self._gal_redraw()
        else:
            self._clear_crop()

    def _on_right_click(self, event):
        path = self._current_path()
        if not path:
            return
        self._reveal_in_file_browser(path)

    def open_in_explorer(self):
        """Toolbar button: reveal the current file, or open the folder if no file is shown."""
        if not self.folder:
            self._show_toast("Open a folder first")
            return
        path = self._current_path() if self.files and self.image else None
        if path and os.path.exists(path):
            self._reveal_in_file_browser(path)
        else:
            self._open_directory(self.folder)

    def _open_directory(self, directory):
        system = platform.system()
        try:
            if system == "Windows":
                subprocess.Popen(["explorer.exe", os.path.normpath(directory)])
            elif system == "Darwin":
                subprocess.Popen(["open", directory])
            else:
                subprocess.Popen(["xdg-open", directory])
        except Exception as e:
            messagebox.showerror("BatchCrop", f"Could not open file browser:\n{e}")

    def _reveal_in_file_browser(self, path):
        system = platform.system()
        try:
            if system == "Windows":
                # explorer /select needs backslashes (askdirectory returns forward slashes)
                subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)])
            elif system == "Darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                directory = os.path.dirname(path)
                opened = False
                for candidate in ("nautilus", "dolphin", "thunar", "nemo", "pcmanfm"):
                    try:
                        subprocess.Popen([candidate, "--select", path])
                        opened = True
                        break
                    except FileNotFoundError:
                        continue
                if not opened:
                    subprocess.Popen(["xdg-open", directory])
        except Exception as e:
            messagebox.showerror("BatchCrop", f"Could not open file browser:\n{e}")

    # ------------------------------------------------------------------
    # Folder / navigation
    # ------------------------------------------------------------------
    def open_folder(self):
        folder = filedialog.askdirectory()
        if not folder:
            return
        self._clear_undo()
        self.folder = os.path.normpath(folder)  # askdirectory returns forward slashes on Windows
        self.files = [
            f for f in os.listdir(folder)
            if os.path.isfile(os.path.join(folder, f))
            and os.path.splitext(f)[1].lower() in SUPPORTED
        ]
        self._sort_files()
        if not self.files:
            messagebox.showinfo("BatchCrop", "No supported images in that folder.")
            return
        self.index = 0
        self.load_image()

    def load_image(self):
        self.canvas.delete("all")
        self.crop = None
        self.crop_hist = []
        self.zoom = 1.0
        self.pan = (0, 0)
        path = os.path.join(self.folder, self.files[self.index])
        self.image = Image.open(path)
        if self.image.mode not in ("RGB", "RGBA"):
            self.image = self.image.convert("RGB")
        self.original_format = self.image.mode
        if self.keep_crop_var.get() and self.last_crop:
            self.crop = self._fit_crop_to_image(self.last_crop)
        self._fit_image()
        self.status.config(text=f"{self.index+1}/{len(self.files)}")
        self.path_label.config(text=os.path.normpath(path))
        self._update_crop_size_label()
        self._gal_redraw()

    def _clear_crop(self):
        if self.crop:
            self._push_crop_hist()
        self.crop = None
        self.last_crop = None
        self.canvas.delete("crop")

    def prev(self):
        if not self.files:
            return
        if self.gallery_on:
            self._gal_goto(self.index - 1)
            return
        self.index = (self.index - 1) % len(self.files)
        self.load_image()
        self._gal_redraw(ensure_visible=True)

    def next(self):
        if not self.files:
            return
        if self.gallery_on:
            self._gal_goto(self.index + 1)
            return
        self.index = (self.index + 1) % len(self.files)
        self.load_image()
        self._gal_redraw(ensure_visible=True)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------
    def delete_current(self):
        if not self.files or not self.image or self.deleting:
            return
        names = self._target_names()
        if not names:
            return
        self._delete_targets = names

        self.deleting = True
        dlg = tk.Toplevel(self)
        dlg.title("Delete file")
        dlg.configure(bg="#2d2d2d")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        if len(names) == 1:
            question, body, box_h = "Move this file to trash?", os.path.join(self.folder, names[0]), 3
        else:
            shown = names[:12]
            body = "\n".join(shown) + (f"\n... and {len(names) - 12} more" if len(names) > 12 else "")
            question, box_h = f"Move these {len(names)} files to trash?", min(len(shown) + (1 if len(names) > 12 else 0), 10)
        tk.Label(dlg, text=question, bg="#2d2d2d", fg="white").pack(padx=12, pady=(12, 4))
        path_box = tk.Text(dlg, width=80, height=box_h, bg="#111111", fg="white", wrap="word")
        path_box.insert("1.0", body)
        path_box.config(state="disabled")
        path_box.pack(padx=12, pady=4)

        warn_var = tk.BooleanVar(value=self.delete_warning)
        chk = tk.Checkbutton(
            dlg,
            text="Warn before deleting",
            variable=warn_var,
            bg="#2d2d2d",
            fg="#cccccc",
            selectcolor="#3a3a3a",
            activebackground="#2d2d2d",
            activeforeground="#cccccc",
        )
        chk.pack(padx=12, pady=(4, 8), anchor="w")

        btns = tk.Frame(dlg, bg="#2d2d2d")
        btns.pack(pady=(0, 12))
        cancel_btn = tk.Button(btns, text="Cancel", width=10, command=dlg.destroy)
        cancel_btn.pack(side=tk.LEFT, padx=6)
        delete_btn = tk.Button(
            btns, text="Delete", width=10, bg="#a22", fg="white",
            command=lambda: self._confirm_delete(dlg, warn_var.get())
        )
        delete_btn.pack(side=tk.LEFT, padx=6)

        dlg.bind("<Return>", lambda e: self._confirm_delete(dlg, warn_var.get()))
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        delete_btn.focus_set()

        self.wait_window(dlg)
        self.deleting = False

    def _confirm_delete(self, dlg, warn_enabled):
        self.delete_warning = bool(warn_enabled)
        dlg.destroy()
        self._do_delete_many(self._delete_targets)

    def _do_delete(self):
        """Move the current image to the trash folder."""
        if not self.files or not self.image:
            return
        self._do_delete_many([self.files[self.index]])

    @staticmethod
    def _unique_trash_target(dest, base):
        target = os.path.join(dest, base)
        root, ext = os.path.splitext(base)
        n = 1
        while os.path.exists(target):
            target = os.path.join(dest, f"{root}_{n}{ext}")
            n += 1
        return target

    def _do_delete_many(self, names):
        dest = os.path.join(self.folder, ".batchcrop_trash")
        done, failed = [], []
        try:
            os.makedirs(dest, exist_ok=True)
        except Exception as e:
            messagebox.showerror("BatchCrop", f"Delete failed:\n{e}")
            return
        for n in names:
            src = os.path.join(self.folder, n)
            try:
                target = self._unique_trash_target(dest, n)
                os.rename(src, target)
                done.append((src, target))
            except Exception as e:
                failed.append((n, e))
        if failed:
            messagebox.showerror("BatchCrop", f"Could not delete {len(failed)} file(s):\n{failed[0][0]}: {failed[0][1]}")
        if not done:
            return
        self._push_undo({"kind": "delete_many", "items": done})
        removed = {os.path.basename(src) for src, _ in done}
        self.pending_cut = [p for p in self.pending_cut if os.path.basename(p) not in removed]
        self.sel_anchor = None
        self._remove_from_list(removed)
        self.selection = set()
        self._gal_redraw()
        self._show_toast("Moved to trash: " + (next(iter(removed)) if len(removed) == 1 else f"{len(removed)} files"))

    # ------------------------------------------------------------------
    # Rendering / transforms
    # ------------------------------------------------------------------
    def _fit_image(self):
        if not self.image:
            return
        self.canvas.delete("all")
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        fit = min(cw / self.image.width, ch / self.image.height)
        self.scale = fit * self.zoom
        w = max(1, int(self.image.width * self.scale))
        h = max(1, int(self.image.height * self.scale))
        ox = (cw - w) // 2 + self.pan[0]
        oy = (ch - h) // 2 + self.pan[1]
        self.offset = (ox, oy)
        # Render only the visible part so high zoom levels stay fast
        vx1, vy1 = max(0, -ox), max(0, -oy)
        vx2, vy2 = min(w, cw - ox), min(h, ch - oy)
        if vx2 > vx1 and vy2 > vy1:
            sx1 = vx1 / self.scale
            sy1 = vy1 / self.scale
            sx2 = min(self.image.width, vx2 / self.scale)
            sy2 = min(self.image.height, vy2 / self.scale)
            region = self.image.convert("RGB").resize(
                (vx2 - vx1, vy2 - vy1), Image.LANCZOS if self.scale < 1 else Image.NEAREST,
                box=(sx1, sy1, sx2, sy2),
            )
            self.tk_image = ImageTk.PhotoImage(region)
            self.canvas.create_image(ox + vx1, oy + vy1, anchor=tk.NW, image=self.tk_image)
        if self.crop:
            self._draw_crop()
        self.zoom_label.config(text=f"{round(self.zoom * 100)}%" if self.zoom != 1.0 else "")

    # ------------------------------------------------------------------
    # Zoom / pan / wheel
    # ------------------------------------------------------------------
    def zoom_by(self, factor, cx=None, cy=None):
        if not self.image:
            return
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        if cx is None:
            cx, cy = cw / 2, ch / 2
        new_zoom = max(0.25, min(32.0, self.zoom * factor))
        if abs(new_zoom - self.zoom) < 1e-9:
            return
        # keep the image point under (cx, cy) fixed
        ix, iy = self._canvas_to_image(cx, cy)
        fit = min(cw / self.image.width, ch / self.image.height)
        new_scale = fit * new_zoom
        w = int(self.image.width * new_scale)
        h = int(self.image.height * new_scale)
        new_ox = cx - ix * new_scale
        new_oy = cy - iy * new_scale
        self.zoom = new_zoom
        self.pan = (int(new_ox - (cw - w) // 2), int(new_oy - (ch - h) // 2))
        if abs(self.zoom - 1.0) < 1e-9:
            self.pan = (0, 0)
        self._fit_image()

    def zoom_reset(self):
        self.zoom = 1.0
        self.pan = (0, 0)
        self._fit_image()

    def _on_wheel(self, event):
        if self.gallery_on:
            if getattr(event, "num", 0) == 4:
                step = -3
            elif getattr(event, "num", 0) == 5:
                step = 3
            else:
                step = -3 if event.delta > 0 else 3
            self.gcanvas.yview_scroll(step, "units")
            self._gal_redraw()
            return
        ctrl = bool(event.state & 0x4)
        if getattr(event, "num", 0) == 4:
            up = True
        elif getattr(event, "num", 0) == 5:
            up = False
        else:
            up = event.delta > 0
        if ctrl:
            self.zoom_by(1.15 if up else 1 / 1.15, event.x, event.y)
        elif up:
            self.prev()
        else:
            self.next()

    def _on_pan_start(self, event):
        self.pan_drag = (event.x, event.y, self.pan)

    def _on_pan_move(self, event):
        if not self.pan_drag or not self.image:
            return
        x0, y0, (px, py) = self.pan_drag
        self.pan = (px + event.x - x0, py + event.y - y0)
        self._fit_image()

    def _draw_crop(self):
        self.canvas.delete("crop")
        if not self.crop:
            return
        l, u, r, d = self.crop
        l = l * self.scale + self.offset[0]
        u = u * self.scale + self.offset[1]
        r = r * self.scale + self.offset[0]
        d = d * self.scale + self.offset[1]
        self.canvas.create_rectangle(l, u, r, d, outline="#00ff88", width=2, tags="crop")
        self.canvas.create_rectangle(l, u, r, d, fill="#00ff88", stipple="gray50", tags="crop")
        mx, my = (l + r) / 2, (u + d) / 2
        for hx, hy in ((l, u), (mx, u), (r, u), (r, my), (r, d), (mx, d), (l, d), (l, my)):
            self.canvas.create_rectangle(hx - 4, hy - 4, hx + 4, hy + 4, fill="#ffffff", outline="#00ff88",
                                         tags="crop")

    def _canvas_to_image(self, x, y):
        return (x - self.offset[0]) / self.scale, (y - self.offset[1]) / self.scale

    def _clamp_crop(self):
        if not self.crop:
            return
        x1, y1, x2, y2 = self.crop
        x1 = max(0, min(x1, self.image.width))
        y1 = max(0, min(y1, self.image.height))
        x2 = max(0, min(x2, self.image.width))
        y2 = max(0, min(y2, self.image.height))
        self.crop = (x1, y1, x2, y2)

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------
    def _toggle_keep_crop(self):
        self.keep_crop_var.set(not self.keep_crop_var.get())
        self._on_keep_toggle()

    def _on_keep_toggle(self):
        on = self.keep_crop_var.get()
        if on and self.crop:
            self.last_crop = tuple(self.crop)
        self._show_toast("Keep crop: " + ("ON" if on else "OFF"))

    def _fit_crop_to_image(self, crop):
        """Reuse a previous crop on the current image: shrink (same ratio) if it
        does not fit, then clamp the position inside the image."""
        l, u, r, d = crop
        w, h = r - l, d - u
        iw, ih = self.image.width, self.image.height
        k = min(1.0, iw / w, ih / h)
        w, h = w * k, h * k
        l = max(0, min(l, iw - w))
        u = max(0, min(u, ih - h))
        return (l, u, l + w, u + h)

    def _on_press(self, event):
        if not self.image:
            return
        ix, iy = self._canvas_to_image(event.x, event.y)
        prev_ratio = 1.0
        self._push_crop_hist()
        if self.crop:
            handle = self._hit_handle(event.x, event.y)
            if handle:
                self.drag = ("resize", handle, tuple(self.crop))
                return
            l, u, r, d = self.crop
            if l - 8 <= ix <= r + 8 and u - 8 <= iy <= d + 8:
                self.drag = ("move", ix - l, iy - u)
                return
            if (d - u) > 0 and (r - l) > 0:
                prev_ratio = (r - l) / (d - u)
        # Shift+drag keeps the previous crop's ratio (1:1 if there was none)
        self.drag_ratio = prev_ratio
        self.drag = ("new", ix, iy)
        self.crop = None
        self.canvas.delete("crop")

    def _on_drag(self, event):
        if not self.drag:
            return
        ix, iy = self._canvas_to_image(event.x, event.y)
        if self.drag[0] == "new":
            ax, ay = self.drag[1], self.drag[2]
            if event.state & 0x1:  # Shift held: lock ratio
                ratio = self.drag_ratio
                dx, dy = ix - ax, iy - ay
                sx = 1 if dx >= 0 else -1
                sy = 1 if dy >= 0 else -1
                w, h = abs(dx), abs(dy)
                if h * ratio < w:
                    h = w / ratio
                else:
                    w = h * ratio
                room_w = max(0.0, (self.image.width - ax) if sx > 0 else ax)
                room_h = max(0.0, (self.image.height - ay) if sy > 0 else ay)
                if w > 0 and h > 0:
                    k = min(1.0, room_w / w, room_h / h)
                    w, h = w * k, h * k
                ix, iy = ax + sx * w, ay + sy * h
            l = min(ax, ix)
            u = min(ay, iy)
            r = max(ax, ix)
            d = max(ay, iy)
            self.crop = (l, u, r, d)
        elif self.drag[0] == "resize":
            self._resize_crop(ix, iy, bool(event.state & 0x1))
        else:
            _, ox, oy = self.drag
            w = self.crop[2] - self.crop[0]
            h = self.crop[3] - self.crop[1]
            l = max(0, min(ix - ox, self.image.width - w))
            u = max(0, min(iy - oy, self.image.height - h))
            self.crop = (l, u, l + w, u + h)
        self._clamp_crop()
        self._draw_crop()
        self._update_crop_size_label()

    HANDLE_CURSORS = {
        "nw": "top_left_corner", "ne": "top_right_corner",
        "sw": "bottom_left_corner", "se": "bottom_right_corner",
        "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
        "e": "sb_h_double_arrow", "w": "sb_h_double_arrow",
    }

    def _hit_handle(self, cx, cy):
        """Return 'n','ne','e',... if the canvas point is on a crop edge/corner."""
        if not self.crop:
            return None
        l, u, r, d = self.crop
        l = l * self.scale + self.offset[0]
        u = u * self.scale + self.offset[1]
        r = r * self.scale + self.offset[0]
        d = d * self.scale + self.offset[1]
        # tolerance shrinks for small boxes so the interior stays grabbable
        tol = max(3, min(9, (r - l) / 3, (d - u) / 3))
        if not (l - tol <= cx <= r + tol and u - tol <= cy <= d + tol):
            return None
        near_l, near_r = abs(cx - l) <= tol, abs(cx - r) <= tol
        near_u, near_d = abs(cy - u) <= tol, abs(cy - d) <= tol
        v = "n" if near_u else "s" if near_d else ""
        h = "w" if near_l else "e" if near_r else ""
        return (v + h) or None

    def _on_motion(self, event):
        if self.drag or not self.image:
            return
        handle = self._hit_handle(event.x, event.y)
        if handle:
            cur = self.HANDLE_CURSORS[handle]
        elif self.crop:
            ix, iy = self._canvas_to_image(event.x, event.y)
            l, u, r, d = self.crop
            cur = "fleur" if (l <= ix <= r and u <= iy <= d) else "cross"
        else:
            cur = "cross"
        if str(self.canvas.cget("cursor")) != cur:
            self.canvas.config(cursor=cur)

    def _resize_crop(self, ix, iy, shift):
        MIN = 2
        handle, (l0, u0, r0, d0) = self.drag[1], self.drag[2]
        iw, ih = self.image.width, self.image.height
        l, u, r, d = l0, u0, r0, d0
        if "w" in handle:
            l = max(0, min(ix, r0 - MIN))
        if "e" in handle:
            r = min(iw, max(ix, l0 + MIN))
        if "n" in handle:
            u = max(0, min(iy, d0 - MIN))
        if "s" in handle:
            d = min(ih, max(iy, u0 + MIN))
        if shift and (d0 - u0) > 0:
            ratio = (r0 - l0) / (d0 - u0)
            if len(handle) == 2:
                # corner: keep the opposite corner fixed
                fx = r0 if "w" in handle else l0
                fy = d0 if "n" in handle else u0
                sx = -1 if "w" in handle else 1
                sy = -1 if "n" in handle else 1
                w = max(MIN, abs(ix - fx))
                h = max(MIN, abs(iy - fy))
                if h * ratio < w:
                    h = w / ratio
                else:
                    w = h * ratio
                room_w = fx if sx < 0 else iw - fx
                room_h = fy if sy < 0 else ih - fy
                k = min(1.0, room_w / w, room_h / h)
                w, h = w * k, h * k
                l, r = (fx - w, fx) if sx < 0 else (fx, fx + w)
                u, d = (fy - h, fy) if sy < 0 else (fy, fy + h)
            elif handle in ("e", "w"):
                w = r - l
                h = w / ratio
                if h > ih:
                    h = ih
                    w = h * ratio
                    if handle == "e":
                        r = l + w
                    else:
                        l = r - w
                cy = (u0 + d0) / 2
                u = max(0, min(cy - h / 2, ih - h))
                d = u + h
            else:
                h = d - u
                w = h * ratio
                if w > iw:
                    w = iw
                    h = w / ratio
                    if handle == "s":
                        d = u + h
                    else:
                        u = d - h
                cx = (l0 + r0) / 2
                l = max(0, min(cx - w / 2, iw - w))
                r = l + w
        self.crop = (l, u, r, d)

    def _on_release(self, event):
        # a click that changed nothing should not leave a no-op undo step
        if self.drag and self.crop_hist and self.crop_hist[-1] == (tuple(self.crop) if self.crop else None):
            self.crop_hist.pop()
            self._update_undo_btn()
        self.drag = None

    def _update_crop_size_label(self):
        if self.crop:
            x1, y1, x2, y2 = self.crop
            self.size_label.config(text=f"crop: {int(x2-x1)} x {int(y2-y1)}")
        else:
            self.size_label.config(text=f"{self.image.width} x {self.image.height}")

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    def save_crop(self):
        if not self.image:
            return
        if not self.crop:
            # nothing to save: Enter/Save Crop just skips to the next image
            self.next()
            return
        x1, y1, x2, y2 = self.crop
        # round position and size separately so a moved crop keeps its exact size
        x1, y1 = int(round(x1)), int(round(y1))
        x2, y2 = x1 + int(round(x2 - self.crop[0])), y1 + int(round(y2 - self.crop[1]))
        x2, y2 = min(x2, self.image.width), min(y2, self.image.height)
        if x2 - x1 < 2 or y2 - y1 < 2:
            messagebox.showinfo("BatchCrop", "Crop region too small.")
            return
        cropped = self.image.crop((x1, y1, x2, y2))
        path = os.path.join(self.folder, self.files[self.index])
        ext = os.path.splitext(path)[1].lower()
        backup = self._make_backup(path)
        if ext in (".jpg", ".jpeg"):
            save = cropped.convert("RGB")
            save.save(path, quality=95, subsampling=0)
        elif ext == ".png":
            save = cropped
            if self.original_format != "RGBA":
                save = save.convert("RGB")
            save.save(path, optimize=True)
        elif ext == ".webp":
            save = cropped.convert("RGB")
            save.save(path, quality=95)
        elif ext == ".tiff":
            cropped.save(path, compression="tiff_lzw")
        else:
            target = ImageOps.exif_transpose(cropped)
            target.save(path)
        if backup:
            self._push_undo({"kind": "crop", "path": path, "backup": backup, "rect": (x1, y1, x2, y2)})
        self.last_crop = (x1, y1, x2, y2)
        self.crop = None
        self.canvas.delete("crop")
        self.next()


if __name__ == "__main__":
    app = BatchCrop()
    app.mainloop()
