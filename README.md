# ⚽ Highlighter

Turns long football videos from a goal-mounted action camera into a short
highlight reel of the **goals, saves, close misses and celebrations**.

It runs on your own computer and opens in your web browser. Nothing is
uploaded to the internet.

## How it finds the moments

With the camera in the goal, the exciting moments all happen right in front of it:

- **Motion:** when play comes near the goal, players and the ball fill the
  picture and it changes a lot from frame to frame. A ball hitting the net
  shakes the camera. When play is at the other end, the picture barely moves.
- **Sound:** the sideline gets loud for goals, saves and near misses.

Highlighter scores every half-second of the video on both signals and picks the
peaks. Each clip starts about 7 seconds before the moment, to show the build-up,
and runs about 7 seconds after it, to catch the celebration. You then review the
clips, tag them, and make the reel.

## Setting it up (one time)

1. **Install Python 3.10 or newer** from https://www.python.org/downloads/
   (on Windows, tick *"Add Python to PATH"* in the installer).
2. **Install ffmpeg**, the video tool Highlighter uses:
   - Windows: open PowerShell and run `winget install Gyan.FFmpeg`
   - Mac: install [Homebrew](https://brew.sh), then run `brew install ffmpeg`
3. Download this project (green **Code** button → **Download ZIP**) and unzip it.

## Using it

- **Windows:** double-click `run.bat`
- **Mac/Linux:** run `./run.sh` in a terminal

Your browser opens at http://localhost:5000. Then:

1. **Add videos:** drag them onto the page, or copy them into the `videos`
   folder and press *Refresh*. Each one is analysed automatically. A 30-minute
   video takes a few minutes.
2. **Review:** the timeline shows the action level across the match, with the
   moments it found shaded in. Click any moment to watch it.
   - Tag it as ⚽ Goal, 🧤 Save, 😬 Close miss or 🎉 Celebration.
   - Use **Keep** or **Leave out** to choose what goes in the reel.
   - Use **−2s / +2s** or **⇤ here / here ⇥** to trim the clip.
   - Missed something? Play to that point and press **+ Add clip here**.
   - Too many or too few moments? Move the **fewer ↔ more** slider.
3. **Make highlight reel:** choose this match or all matches, the quality, fades,
   and slow-motion replays after goals. The finished MP4 can be downloaded, and
   it's also saved in the `exports` folder.

## Using it from your phone

Your computer does the video processing. Your phone is the remote: you send
videos from your gallery, review the moments, and save the finished reel back
to your phone.

1. Start Highlighter on the computer (double-click `run.bat`).
2. On the computer, click **📱 Use on phone**. It shows a QR code and a 6-digit PIN.
3. With your phone on the **same Wi-Fi**, scan the QR code with the camera,
   or type the address into Chrome.
4. Enter the PIN. The phone remembers it, so you only do this once.
5. Tap **+ Add videos** and pick your match videos from the gallery.

While a video is being sent, keep the screen on and Chrome open. Action camera
files are big (a 30-minute match can be several GB), so this takes a few
minutes over Wi-Fi. If it gets interrupted, pick the same video again and it
carries on from where it stopped. Once a video is sent, the computer takes over
and you can lock your phone. Come back later to review.

When the reel is made, tap **Save to phone**. It goes into your Downloads, and
you can share it from there to WhatsApp, Facebook and so on.

### Phone won't connect?

- **Windows Firewall** (most likely). The first time you run Highlighter,
  Windows asks whether to let Python use the network. Tick **Private networks**
  and click **Allow access**. If you missed it, open *Start → "Allow an app
  through Windows Firewall" → Change settings*, find **Python**, tick the
  **Private** box, then click OK.
- Check the phone is on your home Wi-Fi, not mobile data, and not on a "guest"
  Wi-Fi network.
- Your home Wi-Fi should be set as a **Private** network in Windows
  (*Settings → Network & internet → Wi-Fi → your network → Private network*).
- The address can change if your computer restarts. Click **📱 Use on phone**
  again for the current one.

The PIN stops anyone else on your Wi-Fi from getting in. It's stored in
`data/pin.txt`. Delete that file and restart to get a new PIN, which also signs
out every phone. To turn phone access off completely, set
`HIGHLIGHTER_LOCAL_ONLY=1` before starting.

### Keyboard shortcuts (on the computer)

| Key | Action |
|-----|--------|
| `N` / `P` | Next / previous moment (plays it) |
| `K` | Keep / leave out |
| `1` `2` `3` `4` | Goal / Save / Close miss / Celebration |
| `A` | Add a clip at the current point |
| `Space` | Play / pause |
| `←` / `→` | Back / forward 5 seconds |
| `Delete` | Remove the moment |

## Tips

- If a match is very windy or has music playing nearby, the sound signal is
  less useful. Move the slider towards *more* and leave out the false alarms.
- The original videos are never changed. The `data` folder holds the analysis
  and previews and can be deleted at any time.
- To keep videos somewhere else, set `HIGHLIGHTER_VIDEOS` to that folder before
  starting.

## For developers

```
pip install -r requirements.txt
python app.py
python tests/test_analysis.py
```

- `highlighter/analysis.py`: motion and sound scoring, and moment detection
- `highlighter/export.py`: cuts and joins the reel with ffmpeg
- `app.py`: Flask server and API
- `static/`: the web interface (plain HTML, CSS and JS)
