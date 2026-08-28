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
