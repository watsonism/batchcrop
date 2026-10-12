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
