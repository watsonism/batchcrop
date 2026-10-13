# BatchCrop

A minimal, keyboard-driven desktop app for manually cropping screenshots in bulk. Each crop overwrites the original file in place so you don't accumulate exports.

## Controls

- `Open Folder` — pick a directory of images
- `Open in File Explorer` (top bar) — reveal the current file in Explorer (opens the folder if no image is shown); right-click on the image does the same
- Draw a crop rectangle with the mouse
- `Enter` / `Save Crop` — apply crop, overwrite original, advance to next (with no crop drawn, it just advances without touching the file)
- `Right Arrow` — next image
- `Left Arrow` — previous image
- `Up/Down Arrow` — skip current image without cropping
- `Esc` — clear current crop selection
- `Refresh` / `F5` — re-read the folder: files moved, deleted or added outside the app (for example in Explorer) show up correctly, and your current image and selection are kept. It also happens automatically when you switch back to the window and the folder changed, and when a thumbnail's file turns out to be gone. A file that exists but cannot be read is shown as a dark red "unreadable" cell.
- **Organize** row (works on the gallery selection, or the current image if nothing is selected):
  - `Move to...` / `F6` — move the files to another folder (works across drives). Name clashes never overwrite: you choose keep both (`_1`, `_2`...), skip those, or cancel. Undoable.
  - `Copy to...` / `F7` — copy to another folder with the same clash handling, no Explorer needed. Undo moves the copies into the destination's `.batchcrop_trash` (nothing is permanently deleted).
  - `Batch rename...` / `Ctrl+F2` (or `F2` with several selected) — pattern with `{name}`, `{n}` counter (start and digit padding), `{date}` (modified date), plus find/replace; applies to the selection or the whole folder. Live preview, problems (duplicates, illegal characters, existing files) are flagged and block the rename; swaps and chains are safe. The extension is always kept. Undoable.
- `Undo` button / `Ctrl+Z` — undo the last crop-box edit on the current image, then (newest first) saved crops (original restored, with its timestamps), deletes (file restored from `.batchcrop_trash`) and renames; up to 50 steps, cleared when you open another folder. Cut/paste moves done in Explorer are not undoable here
- `Keep crop` checkbox / `K` — after saving a crop, the same crop box (same size and position) appears on the next image so you can just drag it up or down; if an image is too small it is shrunk to fit with the same ratio. `Esc` forgets it
- Drag a crop box's edges or corners (white handles) to resize it; Shift while resizing keeps its ratio. Drag inside the box to move it
- `Shift` + drag — lock a new crop to 1:1, or to the ratio of the crop you already had (draw outside the existing box; dragging inside it moves it)
- Sort dropdown (top bar) — Name A-Z, Name Z-A, Date old-new, Date new-old (date = modified time); the current image stays selected
- `G` / `Gallery` button — scrollable thumbnail grid. Double-click or Enter opens an image, arrows move, wheel scrolls.
  - **Multi-select** (Explorer-style): click selects one; `Ctrl`+click toggles; `Shift`+click selects a range; `Ctrl+Shift`+click adds a range; `Shift`+arrows extend from the anchor; `Ctrl+A` selects all; `Esc` or a click on empty space clears. Click and drag on the grid draws a selection box (`Ctrl`/`Shift` adds to the current selection; dragging past the top/bottom edge auto-scrolls). The top bar shows "N selected".
  - With several images selected, `Delete` (one confirmation listing the files, all go to `.batchcrop_trash`), `Ctrl+X` and `Ctrl+C` (all files go on the Windows clipboard, paste them anywhere in Explorer) act on the whole selection, and a multi-delete is a single `Ctrl+Z` step. Rename and cropping stay one image at a time.
- `Mouse wheel` — previous/next file
- `Ctrl +` / `Ctrl -` / `Ctrl+wheel` — zoom in/out (Ctrl+0 resets); middle-drag pans when zoomed
- `Rename` / `F2` — rename the current file (extension kept if omitted)
- `Ctrl+X` — cut file to the Windows clipboard (paste in Explorer); advances once the file has been moved out of the folder
- `Ctrl+C` — copy file to the Windows clipboard

## Run

```bash
python3 batchcrop.py
```

Requires Python 3.10+, Pillow, Tk.

## Build a standalone binary

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name BatchCrop batchcrop.py
```

## Project

MIT.

## Crash reports

If BatchCrop hits an unexpected error it writes a text report to a `crash-reports` folder next to `BatchCrop.exe`
(or to `%LOCALAPPDATA%\BatchCrop\crash-reports` if that folder is not writable). Nothing is sent over the network.

- Covers errors inside button and key handlers (which a windowed app would otherwise swallow silently), unhandled
  errors on the main thread and in worker threads, and hard crashes (the report is written on the next launch).
- Reports are redacted: no file names, folder names or user name. They contain the error, a traceback (file and line
  numbers only), app and Windows versions, a summary of the app state (counts, view, sort, zoom) and the last ~40
  action names.
- The same error repeating in one session is only reported once. For a non-fatal error a toast says a report was
  saved; for a fatal one a dialog shows where it is.
- `BatchCrop.exe --crash-test` raises a deliberate error to check the reporter, then closes itself.
  `--crash-test-fatal` does the same for an unhandled main-thread crash.
