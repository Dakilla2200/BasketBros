# TapTask — TinyTask-style auto clicker for iPhone

Record taps, long-presses and swipes on your iPhone, then replay them on a loop at any speed. There's also a fixed-point auto-clicker mode.

## How it works

iOS sandboxing stops one app from tapping inside another. No App Store app can do this without a jailbreak. TapTask runs on your computer and controls the phone through **WebDriverAgent (WDA)**, Apple's XCUITest automation agent (the same one Appium uses):

```
Browser UI  ──►  autoclicker.py (localhost:8765)  ──►  WDA on iPhone (:8100)  ──►  real touches
```

The browser shows a live mirror of the phone. Every click or drag you make on the mirror is sent to the phone and, while recording, saved along with its timing.

## Setup (once)

You need a **Mac with Xcode** and a free Apple ID.

1. On the iPhone, turn on **Settings › Privacy & Security › Developer Mode** and restart.
2. Clone WDA and open it in Xcode:
   ```bash
   git clone https://github.com/appium/WebDriverAgent && cd WebDriverAgent
   open WebDriverAgent.xcodeproj
   ```
   Under **WebDriverAgentRunner › Signing & Capabilities**, choose your team and change the bundle ID to something unique, e.g. `com.yourname.WebDriverAgentRunner`.
3. Plug in the phone and start WDA (leave this running):
   ```bash
   xcodebuild -project WebDriverAgent.xcodeproj -scheme WebDriverAgentRunner \
     -destination 'platform=iOS,name=<your iPhone name>' test
   ```
   The first time, trust the developer profile on the phone under **Settings › General › VPN & Device Management**.
4. Forward the ports over USB:
   ```bash
   brew install libimobiledevice
   iproxy 8100 8100 &   # WDA control
   iproxy 9100 9100 &   # MJPEG screen stream (smooth mirror)
   ```
   Over Wi-Fi, skip iproxy and use the phone's IP: `--wda http://<phone-ip>:8100 --mjpeg http://<phone-ip>:9100`.

> **Windows or Linux:** you can't build WDA there, but you can install a pre-signed WDA `.ipa` and then run `pip install tidevice && tidevice wdaproxy -B <bundle-id> --port 8100`. On a free Apple ID, signing expires every 7 days.

## Use

```bash
python3 autoclicker.py
# open http://localhost:8765
```

| Action on the mirror | Sent to the phone |
|---|---|
| click | tap |
| hold more than 0.45 s | long press (actual duration) |
| click and drag | swipe (actual speed) |

- **● Record** (Ctrl+Shift+R): do your sequence on the mirror. The gaps between gestures are saved.
- **▶ Play** (Ctrl+Shift+P): replay. *Loops* 0 = forever. *Speed ×* 2 = twice as fast. *Gap* = pause between loops.
- **■ Stop** (Esc): stops playback, recording or auto-click.
- **Auto click:** click **Pick point**, click the spot on the mirror, set the interval and count (0 = until stopped), then click **Start**.
- **Save / Load** writes or reads macro `.json` files. You can remove single steps from the list.

Coordinates are stored as fractions of the screen, so a macro still works on an iPhone with a different screen size.

### Headless replay (no browser)

```bash
python3 autoclicker.py play macro.json --loops 0 --speed 1.5 --loop-gap 2
```

Ctrl+C stops it.

## Limits

- The top speed is about 5–15 taps per second. Each tap is an XCUITest round-trip, so you can't go faster.
- The phone must stay unlocked. Auto-Lock can stop WDA, so set **Settings › Display › Auto-Lock › Never** for long runs.
- It doesn't use image or pixel matching. Like TinyTask, it replays blindly.

## No-computer alternative: Switch Control "Recipes"

This is built into iOS and gives you a phone-only record/replay, but it's clunkier:

1. **Settings › Accessibility › Switch Control › Switches › Add New Switch › Screen › Full Screen** → action *Select Item*.
2. **Switch Control › Saved Gestures › Create New Gesture**: tap or swipe the pattern you want to repeat, then **Save**.
3. **Switch Control › Recipes › Create New Recipe**: name it, then **Assign Switch › Full Screen › Custom Gesture**, and choose or record the gesture.
4. **Recipes › Launch Recipe** → choose it. Add Switch Control to **Accessibility Shortcut** (triple-click the side button) to turn it on and off.
5. Turn Switch Control on, then tap anywhere and the recorded gesture plays once per tap.

You can't set loop counts or intervals, and it's awkward to exit (triple-click again).
