import atexit
import os
import platform
import re
import shutil
import tempfile
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
        self.pending_cut = None
        self.cut_job = None
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
        self.gcanvas.bind("<Button-1>", self._gal_click)
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
        self.bind("<Escape>", lambda e: self._clear_crop())
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
            text="Left: prev   Right: next   Enter: save crop   Del: delete   Esc: clear crop   Wheel: files   G: gallery   K: keep crop   Drag edges/corners: resize   Shift+drag: lock ratio   Ctrl+/-: zoom   F2: rename   Ctrl+Z: undo   Ctrl+X: cut   Ctrl+C: copy",
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
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        x = cw // 2
        y = ch - 60
        pad_x = 18
        pad_y = 10
        txt = self.canvas.create_text(x, y, text=text, fill="#ffffff", font=("TkDefaultFont", 13, "bold"), tags="toast_text")
        bbox = self.canvas.bbox(txt)
        self.canvas.create_rectangle(
            bbox[0] - pad_x, bbox[1] - pad_y, bbox[2] + pad_x, bbox[3] + pad_y,
            fill="#000000", outline="#444444", width=1, tags="toast_bg"
        )
        self.canvas.tag_lower("toast_bg", txt)
        self.toast_job = self.after(TOAST_MS, self._clear_toast)

    def _clear_toast(self):
        self.canvas.delete("toast_bg")
        self.canvas.delete("toast_text")
        self.toast_job = None

    # ------------------------------------------------------------------
    # Cut / Copy / Reveal
    # ------------------------------------------------------------------
    def _current_path(self):
        if not self.files or not self.image or not self.folder:
            return None
        return os.path.join(self.folder, self.files[self.index])

    def _set_file_clipboard(self, path, move):
        """Put a file on the system clipboard (CF_HDROP + Preferred DropEffect).

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

        path = os.path.normpath(os.path.abspath(path))
        # DROPFILES header: pFiles=20, pt=(0,0), fNC=0, fWide=1
        header = (20).to_bytes(4, "little") + bytes(8) + bytes(4) + (1).to_bytes(4, "little")
        hdrop = make_global(header + (path + "\0\0").encode("utf-16-le"))
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

    def _put_on_clipboard(self, path, move):
        if not self._set_file_clipboard(path, move):
            # Fallback (non-Windows / failure): plain text path
            self.clipboard_clear()
            self.clipboard_append(path)
            return False
        return True

    def cut_current(self):
        """Ctrl+X: mark file as cut on the clipboard. File is NOT moved here.
        Once it is pasted elsewhere (and disappears from this folder), the
        watcher advances to the next image."""
        path = self._current_path()
        if not path:
            return
        real = self._put_on_clipboard(path, move=True)
        self.pending_cut = path
        self._show_toast(("Cut: " if real else "Cut (path only): ") + os.path.basename(path))
        self._schedule_cut_watch()

    def copy_current(self):
        path = self._current_path()
        if not path:
            return
        real = self._put_on_clipboard(path, move=False)
        self.pending_cut = None
        self._show_toast(("Copied: " if real else "Copied (path only): ") + os.path.basename(path))

    def _schedule_cut_watch(self):
        if self.cut_job is None:
            self.cut_job = self.after(500, self._check_cut)

    def _check_cut(self):
        self.cut_job = None
        path = self.pending_cut
        if not path:
            return
        if os.path.exists(path):
            self._schedule_cut_watch()
            return
        # File was moved away (pasted elsewhere): drop it and go to next image
        self.pending_cut = None
        folder_norm = os.path.normcase(os.path.normpath(self.folder or ""))
        if os.path.normcase(os.path.normpath(os.path.dirname(path))) != folder_norm:
            return
        name = os.path.basename(path)
        if name not in self.files:
            return
        idx = self.files.index(name)
        was_current = idx == self.index
        del self.files[idx]
        if not self.files:
            self.image = None
            self.canvas.delete("all")
            self.status.config(text="0/0")
            self.path_label.config(text="")
            self.size_label.config(text="")
            return
        if idx < self.index:
            self.index -= 1
        self.index %= len(self.files)
        if was_current:
            self.load_image()
        else:
            self.status.config(text=f"{self.index+1}/{len(self.files)}")
        self._gal_redraw()

    def rename_current(self):
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
        if self.pending_cut == path:
            self.pending_cut = None
        self._push_undo({"kind": "rename", "old": path, "new": target})
        self.files[self.index] = new
        self._sort_files(keep=new)
        self.path_label.config(text=os.path.normpath(target))
        self.status.config(text=f"{self.index+1}/{len(self.files)}")
        self._gal_redraw()
        self._show_toast("Renamed: " + new)

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
            self.after_idle(self._fit_image)
        else:
            if not self.files:
                return
            self.gallery_on = True
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

    def _gal_draw_cell(self, i, x, y):
        c = self.gcanvas
        name = self.files[i]
        path = os.path.join(self.folder, name)
        selected = i == self.index
        c.create_rectangle(x + 2, y + 2, x + G_CELL_W - 2, y + G_CELL_H - 2,
                           fill="#2a3a30" if selected else "#1a1a1a",
                           outline="#00ff88" if selected else "#2a2a2a", width=2 if selected else 1)
        try:
            key = (path, os.path.getmtime(path))
        except OSError:
            key = (path, 0)
        photo = self.thumb_cache.get(key)
        cx = x + G_CELL_W // 2
        if photo is not None:
            c.create_image(cx, y + G_PAD + THUMB // 2, image=photo)
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
        for k in done:
            f = self.thumb_pending.pop(k)
            try:
                self.thumb_cache[k] = ImageTk.PhotoImage(f.result())
            except Exception:
                self.thumb_cache[k] = ImageTk.PhotoImage(Image.new("RGB", (THUMB, THUMB), "#552222"))
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

    def _gal_click(self, event):
        i = self._gal_index_at(event)
        if i is not None and i != self.index:
            self.index = i
            self.load_image()

    def _gal_double(self, event):
        i = self._gal_index_at(event)
        if i is not None:
            self.index = i
            self.load_image()
            self.toggle_gallery()

    def _gal_move(self, rows):
        if not self.files:
            return
        new = self.index + rows * self._gal_cols()
        if 0 <= new < len(self.files):
            self.index = new
            self.load_image()
            self._gal_redraw(ensure_visible=True)

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
        self.index = (self.index - 1) % len(self.files)
        self.load_image()
        self._gal_redraw(ensure_visible=True)

    def next(self):
        if not self.files:
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
        path = os.path.join(self.folder, self.files[self.index])

        self.deleting = True
        dlg = tk.Toplevel(self)
        dlg.title("Delete file")
        dlg.configure(bg="#2d2d2d")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        tk.Label(dlg, text="Move this file to trash?", bg="#2d2d2d", fg="white").pack(
            padx=12, pady=(12, 4)
        )
        path_box = tk.Text(dlg, width=80, height=3, bg="#111111", fg="white", wrap="word")
        path_box.insert("1.0", path)
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
        self._do_delete()

    def _do_delete(self):
        if not self.files or not self.image:
            return
        path = os.path.join(self.folder, self.files[self.index])
        try:
            dest = os.path.join(self.folder, ".batchcrop_trash")
            os.makedirs(dest, exist_ok=True)
            base = os.path.basename(path)
            target = os.path.join(dest, base)
            if os.path.exists(target):
                root, ext = os.path.splitext(base)
                target = os.path.join(dest, f"{root}_1{ext}")
            os.rename(path, target)
        except Exception as e:
            messagebox.showerror("BatchCrop", f"Delete failed:\n{e}")
            return
        self._push_undo({"kind": "delete", "path": path, "trash": target})
        del self.files[self.index]
        if not self.files:
            self.image = None
            self.canvas.delete("all")
            self.status.config(text="0/0")
            self.path_label.config(text="")
            self.size_label.config(text="")
            return
        self.index %= len(self.files)
        self.load_image()

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
