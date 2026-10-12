# BatchCrop

A minimal, keyboard-driven desktop app for manually cropping screenshots in bulk. Each crop overwrites the original file in place so you don't accumulate exports.

## Controls

- `Open Folder` — pick a directory of images
- Draw a crop rectangle with the mouse
- `Enter` / `Save Crop` — apply crop, overwrite original, advance to next
- `Right Arrow` — next image
- `Left Arrow` — previous image
- `Up/Down Arrow` — skip current image without cropping
- `Esc` — clear current crop selection
- `Keep crop` checkbox / `K` — after saving a crop, the same crop box (same size and position) appears on the next image so you can just drag it up or down; if an image is too small it is shrunk to fit with the same ratio. `Esc` forgets it
- `Shift` + drag — lock a new crop to 1:1, or to the ratio of the crop you already had (draw outside the existing box; dragging inside it moves it)
- Sort dropdown (top bar) — Name A-Z, Name Z-A, Date old-new, Date new-old (date = modified time); the current image stays selected
- `G` / `Gallery` button — scrollable thumbnail grid. Click selects, double-click or Enter opens the image, arrows move, wheel scrolls; Delete, Rename, Cut and Copy act on the selected image
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
