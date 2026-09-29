// Clawd pet helper: tells the Clawd desktop pet where the pointer and your
// windows are. He runs through XWayland (a pet has to move his own window,
// which Wayland doesn't allow), and an X11 app sees neither the pointer over
// Wayland apps nor their windows. On KDE a KWin script does this job
// (kwin_desktop_script() in claude_pet.py); this is the same feed for GNOME
// Shell, over the same D-Bus interface and in the same format.
//
// Nothing runs unless he does: we only watch for his name on the session bus,
// and poll while he holds it. Only numbers go out, never titles, app ids or pids.

import Gio from "gi://Gio";
import GLib from "gi://GLib";
import Meta from "gi://Meta";

import {Extension} from "resource:///org/gnome/shell/extensions/extension.js";

const SERVICE = "org.clawdpet.Pet";
const PET_CLASS = "clawd-pet";      // WM_CLASS of all his windows (Qt takes it from the app name)
const NEAR = 400;                   // px: closer than this to him, the pointer is reported in detail
const FAR_STEP = 24;                // further off, only every this many px of movement
const CURSOR_MS = 40;               // quick enough for him to hang off the pointer
const WINDOWS_MS = 150;              // he rides a window you drag: keep up
const TIMEOUT_MS = 500;

// Windows he can stand on: what KWin calls normal windows and dialogs.
function isAppWindow(w) {
    switch (w.get_window_type()) {
    case Meta.WindowType.NORMAL:
        // one kept off the taskbar isn't an app's window: older releases of
        // Desktop Icons draw the desktop as one, as big as the screen
        return !w.is_skip_taskbar();
    case Meta.WindowType.DIALOG:
    case Meta.WindowType.MODAL_DIALOG:
        return true;                // (they skip the taskbar whenever they have a parent)
    default:
        return false;
    }
}

export default class ClawdPetHelper extends Extension {
    enable() {
        this._bus = null;
        this._cursorTimer = 0;
        this._windowsTimer = 0;
        this._pet = [];
        // Replies are only waited for, never read.
        this._cursorDone = (bus, res) => {
            this._cursorBusy = false;
            try {
                bus.call_finish(res);
            } catch {
                // busy or gone: the pointer moves on and tells him again
            }
        };
        this._windowsDone = (bus, res) => {
            this._windowsBusy = false;
            try {
                bus.call_finish(res);
            } catch {
                this._lastWindows = null;   // windows can sit still for hours: send them again
            }
        };
        this._watch = Gio.bus_watch_name(Gio.BusType.SESSION, SERVICE, Gio.BusNameWatcherFlags.NONE,
            bus => this._start(bus), () => this._stop());
    }

    disable() {
        if (this._watch) {
            Gio.bus_unwatch_name(this._watch);
            this._watch = 0;
        }
        this._stop();
        this._cursorDone = this._windowsDone = null;
    }

    _start(bus) {
        this._stop();                       // a new owner (he restarted): everything afresh
        this._bus = bus;
        this._lastX = this._lastY = -1e6;
        this._lastWindows = null;
        this._cursorBusy = this._windowsBusy = false;
        this._cursorTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, CURSOR_MS, () => {
            this._sendCursor();
            return GLib.SOURCE_CONTINUE;
        });
        this._windowsTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, WINDOWS_MS, () => {
            this._sendWindows();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _stop() {
        if (this._cursorTimer)
            GLib.source_remove(this._cursorTimer);
        if (this._windowsTimer)
            GLib.source_remove(this._windowsTimer);
        this._cursorTimer = this._windowsTimer = 0;
        this._bus = null;
        this._pet = [];                     // don't keep his windows alive
    }

    _sendCursor() {
        if (this._cursorBusy)
            return;                         // no backlog: once he's free he gets the newest position
        const [x, y] = global.get_pointer();
        const moved = Math.abs(x - this._lastX) + Math.abs(y - this._lastY);
        if (moved < 2 || moved < this._step(x, y))
            return;                         // 2 px is the finest step, so a still pointer skips the lookup
        this._lastX = x;
        this._lastY = y;
        this._cursorBusy = true;
        this._call("Cursor", x + "," + y, this._cursorDone);
    }

    // As in the KWin script: every 2 px near him, every 24 px further off,
    // every 4 px while no window of his is found.
    _step(x, y) {
        if (this._pet.length === 0)
            return 4;
        let off = Infinity;
        for (const w of this._pet) {        // him, and his ladder while it's out
            const g = w.get_frame_rect();
            off = Math.min(off, Math.max(g.x - x, 0, x - g.x - g.width) +
                                Math.max(g.y - y, 0, y - g.y - g.height));
        }
        return off > NEAR ? FAR_STEP : 2;
    }

    _sendWindows() {
        const active = global.workspace_manager.get_active_workspace();
        const shown = [];
        const own = [];                     // his override-redirect windows: him, his ladder
        const managed = [];                 // his other windows stand in only while he's hidden
        for (const w of global.display.list_all_windows()) {
            const unmanaged = w.is_override_redirect();
            if (w.get_wm_class() === PET_CLASS)
                (unmanaged ? own : managed).push(w);
            // showing: not minimized, nor a dialog of a minimized window
            else if (!unmanaged && isAppWindow(w) && w.showing_on_its_workspace() &&
                     w.located_on_workspace(active))
                shown.push(w);
        }
        this._pet = own.length > 0 ? own : managed;
        if (this._windowsBusy)
            return;                         // the next tick sends whatever is newest by then
        const rows = [];
        const stack = global.display.sort_windows_by_stacking(shown);   // bottom to top
        for (let i = 0; i < stack.length; i++) {
            const w = stack[i];
            const g = w.get_frame_rect();
            // "": no screen name, he works out the screen from the geometry
            rows.push([Math.round(g.x), Math.round(g.y), Math.round(g.width), Math.round(g.height),
                       i, w.is_fullscreen() ? 1 : 0, w.has_focus() ? 1 : 0, "", String(w.get_id())]);
        }
        const json = JSON.stringify(rows);
        if (json === this._lastWindows)
            return;
        this._lastWindows = json;
        this._windowsBusy = true;
        this._call("Windows", json, this._windowsDone);
    }

    _call(method, text, done) {
        this._bus.call(SERVICE, "/Pet", SERVICE, method, new GLib.Variant("(s)", [text]),
            null, Gio.DBusCallFlags.NO_AUTO_START, TIMEOUT_MS, null, done);
    }
}
