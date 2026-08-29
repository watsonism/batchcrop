import os
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk, ImageOps

SUPPORTED = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tiff"}


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
        self.status = tk.Label(top, text="No folder loaded", bg="#2d2d2d", fg="#cccccc")
        self.status.pack(side=tk.LEFT, padx=8)
        self.size_label = tk.Label(top, text="", bg="#2d2d2d", fg="#aaaaaa")
        self.size_label.pack(side=tk.RIGHT, padx=8)

        self.path_label = tk.Label(self, text="", bg="#111111", fg="#ffffff", anchor="w", padx=8, pady=4)
        self.path_label.pack(fill=tk.X)

        self.canvas = tk.Canvas(self, bg="#111111", cursor="cross")
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        self.bind("<Left>", lambda e: self.prev())
        self.bind("<Right>", lambda e: self.next())
        self.bind("<Up>", lambda e: self.next())
        self.bind("<Down>", lambda e: self.prev())
        self.bind("<Return>", lambda e: self._on_return())
        self.bind("<Delete>", lambda e: self.delete_current())
        self.bind("<Escape>", lambda e: self._clear_crop())

        bottom = tk.Frame(self, bg="#2d2d2d")
        bottom.pack(fill=tk.X)
        tk.Button(bottom, text="Prev", command=self.prev, bg="#3a3a3a", fg="white").pack(
            side=tk.LEFT, padx=4, pady=4
        )
        tk.Button(bottom, text="Delete", command=self.delete_current, bg="#a22", fg="white").pack(
            side=tk.LEFT, padx=4, pady=4
        )
        tk.Button(bottom, text="Save Crop", command=self.save_crop, bg="#2a6", fg="white").pack(
            side=tk.LEFT, padx=4, pady=4
        )
        tk.Button(bottom, text="Next", command=self.next, bg="#3a3a3a", fg="white").pack(
            side=tk.LEFT, padx=4, pady=4
        )

        help_lbl = tk.Label(
            bottom,
            text="Left: prev   Right: next   Enter: save crop   Del: delete   Esc: clear crop",
            bg="#2d2d2d",
            fg="#888888",
        )
        help_lbl.pack(side=tk.RIGHT, padx=8)

    def _on_return(self):
        if self.deleting:
            return
        self.save_crop()

    # ------------------------------------------------------------------
    # Folder / navigation
    # ------------------------------------------------------------------
    def open_folder(self):
        folder = filedialog.askdirectory()
        if not folder:
            return
        self.folder = folder
        self.files = sorted(
            f for f in os.listdir(folder)
            if os.path.isfile(os.path.join(folder, f))
            and os.path.splitext(f)[1].lower() in SUPPORTED
        )
        if not self.files:
            messagebox.showinfo("BatchCrop", "No supported images in that folder.")
            return
        self.index = 0
        self.load_image()

    def load_image(self):
        self.canvas.delete("all")
        self.crop = None
        path = os.path.join(self.folder, self.files[self.index])
        self.image = Image.open(path)
        if self.image.mode not in ("RGB", "RGBA"):
            self.image = self.image.convert("RGB")
        self.original_format = self.image.mode
        self._fit_image()
        self.status.config(text=f"{self.index+1}/{len(self.files)}")
        self.path_label.config(text=path)
        self.size_label.config(text=f"{self.image.width} x {self.image.height}")

    def _clear_crop(self):
        self.crop = None
        self.canvas.delete("crop")

    def prev(self):
        if not self.files:
            return
        self.index = (self.index - 1) % len(self.files)
        self.load_image()

    def next(self):
        if not self.files:
            return
        self.index = (self.index + 1) % len(self.files)
        self.load_image()

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
        ratio = min(cw / self.image.width, ch / self.image.height)
        self.scale = ratio
        w = int(self.image.width * ratio)
        h = int(self.image.height * ratio)
        self.offset = ((cw - w) // 2, (ch - h) // 2)
        display = self.image.convert("RGB")
        self.tk_image = ImageTk.PhotoImage(display.resize((w, h), Image.LANCZOS))
        self.canvas.create_image(self.offset[0], self.offset[1], anchor=tk.NW, image=self.tk_image)
        if self.crop:
            self._draw_crop()

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
    def _on_press(self, event):
        if not self.image:
            return
        ix, iy = self._canvas_to_image(event.x, event.y)
        if self.crop:
            l, u, r, d = self.crop
            if l - 8 <= ix <= r + 8 and u - 8 <= iy <= d + 8:
                self.drag = ("move", ix - l, iy - u)
                return
        self.drag = ("new", ix, iy)
        self.crop = None
        self.canvas.delete("crop")

    def _on_drag(self, event):
        if not self.drag:
            return
        ix, iy = self._canvas_to_image(event.x, event.y)
        if self.drag[0] == "new":
            l = min(self.drag[1], ix)
            u = min(self.drag[2], iy)
            r = max(self.drag[1], ix)
            d = max(self.drag[2], iy)
            self.crop = (l, u, r, d)
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

    def _on_release(self, event):
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
            messagebox.showinfo("BatchCrop", "Draw a crop region first, then press Enter or Save Crop.")
            return
        x1, y1, x2, y2 = self.crop
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
        if x2 - x1 < 2 or y2 - y1 < 2:
            messagebox.showinfo("BatchCrop", "Crop region too small.")
            return
        cropped = self.image.crop((x1, y1, x2, y2))
        path = os.path.join(self.folder, self.files[self.index])
        ext = os.path.splitext(path)[1].lower()
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
        self.crop = None
        self.canvas.delete("crop")
        self.next()


if __name__ == "__main__":
    app = BatchCrop()
    app.mainloop()
