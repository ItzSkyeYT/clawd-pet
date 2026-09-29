// Behaviour test for the GNOME extension, with GNOME mocked out.
// Run: node gnome/test.mjs  (Node 22.15+ for module.registerHooks)
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import {pathToFileURL} from 'node:url';

const EXT = process.env.EXT ?? new URL('./clawd-pet@itzskyeyt.github.io/extension.js', import.meta.url).pathname;

// ── mocks ─────────────────────────────────────────────────────────────
const timers = new Map();
let nextTimer = 100;
const removed = [];
const calls = [];                 // D-Bus calls made
let watch = null;                 // {type, name, flags, appeared, vanished, id}
let unwatched = [];

class Variant {
    constructor(sig, value) {
        this.sig = sig;
        this.value = value;
    }
}

const GLib = {
    PRIORITY_DEFAULT: 0,
    SOURCE_CONTINUE: true,
    SOURCE_REMOVE: false,
    Variant,
    timeout_add(prio, ms, fn) {
        const id = nextTimer++;
        timers.set(id, {prio, ms, fn});
        return id;
    },
    source_remove(id) {
        assert.ok(timers.has(id), `source_remove of unknown source ${id}`);
        timers.delete(id);
        removed.push(id);
        return true;
    },
};

const bus = {
    call(...args) {
        assert.equal(args.length, 10, 'call() takes 10 arguments');
        calls.push(args);
    },
    call_finish(res) {
        if (res.error)
            throw new Error(res.error);
        return new Variant('()', []);
    },
};

const Gio = {
    BusType: {SESSION: 2, SYSTEM: 1},
    BusNameWatcherFlags: {NONE: 0, AUTO_START: 1},
    DBusCallFlags: {NONE: 0, NO_AUTO_START: 1},
    bus_watch_name(type, name, flags, appeared, vanished) {
        watch = {type, name, flags, appeared, vanished, id: 42};
        return 42;
    },
    bus_unwatch_name(id) {
        unwatched.push(id);
    },
};

const WT = {
    NORMAL: 0, DESKTOP: 1, DOCK: 2, DIALOG: 3, MODAL_DIALOG: 4, TOOLBAR: 5, MENU: 6,
    UTILITY: 7, SPLASHSCREEN: 8, DROPDOWN_MENU: 9, POPUP_MENU: 10, TOOLTIP: 11,
    NOTIFICATION: 12, COMBO: 13, DND: 14, OVERRIDE_OTHER: 15,
};
const Meta = {WindowType: WT};

const wsA = {name: 'A'};
const wsB = {name: 'B'};

function win(o) {
    const w = {
        id: 1, x: 0, y: 0, w: 100, h: 100, type: WT.NORMAL, cls: 'app', or: false,
        showing: true, ws: wsA, all: false, fs: false, focus: false, skip: false, stack: 0, ...o,
    };
    return {
        _m: w,
        get_wm_class: () => w.cls,
        get_wm_class_instance: () => w.cls,
        is_override_redirect: () => w.or,
        get_window_type: () => w.type,
        showing_on_its_workspace: () => w.showing,
        get minimized() {
            return !w.showing;
        },
        located_on_workspace: ws => w.all || w.ws === ws,
        is_on_all_workspaces: () => w.all,
        get_workspace: () => (w.all ? state.active : w.ws),
        is_skip_taskbar: () => w.skip,
        get_frame_rect: () => ({x: w.x, y: w.y, width: w.w, height: w.h}),
        is_fullscreen: () => w.fs,
        has_focus: () => w.focus,
        get_id: () => w.id,
    };
}

const state = {pointer: [0, 0], windows: [], active: wsA};
const mockGlobal = {
    get_pointer: () => [state.pointer[0], state.pointer[1], 0],
    display: {
        list_all_windows: () => [...state.windows].reverse(),   // "no particular order"
        sort_windows_by_stacking: list => [...list].sort((a, b) => a._m.stack - b._m.stack),
    },
    workspace_manager: {get_active_workspace: () => state.active},
};

class Extension {
    constructor(metadata) {
        this.metadata = metadata;
    }
}

globalThis.__mock = {Gio, GLib, Meta, Extension};

const MODULES = {
    'gi://Gio': 'export default globalThis.__mock.Gio;',
    'gi://GLib': 'export default globalThis.__mock.GLib;',
    'gi://Meta': 'export default globalThis.__mock.Meta;',
    'resource:///org/gnome/shell/extensions/extension.js':
        'export const Extension = globalThis.__mock.Extension;',
};
registerHooks({
    resolve(specifier, context, next) {
        if (specifier in MODULES)
            return {url: `data:text/javascript,${encodeURIComponent(MODULES[specifier])}`, shortCircuit: true};
        assert.ok(!specifier.startsWith('gi://') && !specifier.startsWith('resource://'),
            `unexpected import ${specifier}`);
        return next(specifier, context);
    },
});

// the extension uses GNOME Shell's `global`; Node's own alias is replaced for the test
const nodeGlobal = globalThis;
nodeGlobal.global = mockGlobal;

const {default: Ext} = await import(pathToFileURL(EXT).href);

// ── helpers ───────────────────────────────────────────────────────────
function timerBy(ms) {
    const found = [...timers.entries()].filter(([, t]) => t.ms === ms);
    assert.equal(found.length, 1, `exactly one ${ms} ms timer`);
    return found[0][1];
}
const tickCursor = () => assert.equal(timerBy(40).fn(), true, 'cursor timer keeps going');
const tickWindows = () => assert.equal(timerBy(150).fn(), true, 'windows timer keeps going');
const sent = method => calls.filter(c => c[3] === method);
const lastArg = method => {
    const s = sent(method);
    return s.length ? s[s.length - 1][4].value[0] : undefined;
};
function reply(method, error) {
    const c = sent(method);
    const call = c[c.length - 1];
    call[9](bus, {error});
}

// ── tests ─────────────────────────────────────────────────────────────
const ext = new Ext({uuid: 'clawd-pet@itzskyeyt.github.io'});
ext.enable();
assert.equal(watch.type, Gio.BusType.SESSION);
assert.equal(watch.name, 'org.clawdpet.Pet');
assert.equal(watch.flags, Gio.BusNameWatcherFlags.NONE);
assert.equal(timers.size, 0, 'no timers before he shows up');

watch.vanished(bus, 'org.clawdpet.Pet');         // GIO calls this first when nobody owns it
assert.equal(timers.size, 0, 'still no timers without him');

watch.appeared(bus, 'org.clawdpet.Pet', ':1.99');
assert.equal(timers.size, 2, 'two timers while he runs');
assert.equal(timerBy(40).prio, GLib.PRIORITY_DEFAULT);
timerBy(150);

// the first pointer report goes out at once, in the KWin format
state.pointer = [100, 200];
tickCursor();
assert.equal(sent('Cursor').length, 1);
{
    const c = sent('Cursor')[0];
    assert.deepEqual(c.slice(0, 4), ['org.clawdpet.Pet', '/Pet', 'org.clawdpet.Pet', 'Cursor']);
    assert.ok(c[4] instanceof Variant);
    assert.equal(c[4].sig, '(s)');
    assert.deepEqual(c[4].value, ['100,200']);
    assert.equal(c[5], null);
    assert.equal(c[6], Gio.DBusCallFlags.NO_AUTO_START);
    assert.equal(c[7], 500);
    assert.equal(c[8], null);
    assert.equal(typeof c[9], 'function');
}

// no backlog: nothing new while a call is out, the newest once it's back
state.pointer = [150, 200];
tickCursor();
assert.equal(sent('Cursor').length, 1, 'waits for the reply');
reply('Cursor');
tickCursor();
assert.equal(lastArg('Cursor'), '150,200');
reply('Cursor');

// no window of his known: every 4 px
state.pointer = [153, 200];
tickCursor();
assert.equal(sent('Cursor').length, 2, '3 px is under the 4 px step');
state.pointer = [152, 202];                     // 2 + 2 = 4 px (Manhattan) from 150,200
tickCursor();
assert.equal(lastArg('Cursor'), '152,202');
reply('Cursor');

// his windows come from the window scan: him (override-redirect), plus a
// dialog of his (managed) that must not stand in for him while he's out
const pet = win({id: 900, cls: 'clawd-pet', or: true, type: WT.OVERRIDE_OTHER, x: 1000, y: 1000, w: 50, h: 40});
const petDialog = win({id: 901, cls: 'clawd-pet', or: false, type: WT.NORMAL, x: 0, y: 0, w: 10, h: 10});
state.windows = [pet, petDialog];
tickWindows();
assert.equal(lastArg('Windows'), '[]', 'his own windows are never listed');
reply('Windows');

// far from him (> 400 px): every 24 px
state.pointer = [152 + 23, 202];
tickCursor();
assert.equal(lastArg('Cursor'), '152,202', '23 px far off is not enough');
state.pointer = [152 + 24, 202];
tickCursor();
assert.equal(lastArg('Cursor'), '176,202');
reply('Cursor');

// near him (<= 400 px off his frame): every 2 px
state.pointer = [1050 + 390, 1020];             // 390 px right of his right edge: near
tickCursor();
assert.equal(lastArg('Cursor'), '1440,1020');
reply('Cursor');
state.pointer = [1441, 1020];
tickCursor();
assert.equal(lastArg('Cursor'), '1440,1020', '1 px is under the 2 px step');
state.pointer = [1442, 1020];
tickCursor();
assert.equal(lastArg('Cursor'), '1442,1020');
reply('Cursor');
// the edge, as in the KWin script (off > NEAR is far): 400 px off is near, 402 far
state.pointer = [1050 + 400, 1020];
tickCursor();
assert.equal(lastArg('Cursor'), '1450,1020');
reply('Cursor');
state.pointer = [1050 + 402, 1020];             // 2 px moved, but far now: under 24
tickCursor();
assert.equal(lastArg('Cursor'), '1450,1020', 'over 400 px off: the 24 px step');
// diagonal distance adds up both axes, as in the KWin script: 300 + 101 = 401 -> far
state.pointer = [1050 + 300, 1040 + 101];
tickCursor();                                   // moved a lot anyway
reply('Cursor');
const before = sent('Cursor').length;
state.pointer = [1050 + 300 + 3, 1040 + 101];   // 3 px: < 24 far, so no send
tickCursor();
assert.equal(sent('Cursor').length, before, 'axis distances add up (Manhattan) like KWin');

// his ladder is out as well: the nearest of his windows counts
const ladder = win({id: 902, cls: 'clawd-pet', or: true, type: WT.OVERRIDE_OTHER, x: 5000, y: 0, w: 20, h: 800});
state.windows = [pet, petDialog, ladder];
tickWindows();                                  // same (empty) list: not resent
assert.equal(sent('Windows').length, 1, 'unchanged list is not resent');
state.pointer = [5030, 400];                    // 10 px right of the ladder, far from him
tickCursor();
reply('Cursor');
state.pointer = [5032, 400];
tickCursor();
assert.equal(lastArg('Cursor'), '5032,400', 'near his ladder: 2 px step');
reply('Cursor');

// only his managed dialog left (he's hidden): it stands in for him
state.windows = [petDialog];
tickWindows();
state.pointer = [20, 20];                       // 10 px off the dialog
tickCursor();
reply('Cursor');
state.pointer = [22, 20];
tickCursor();
assert.equal(lastArg('Cursor'), '22,20', 'a lone managed window of his is used');
reply('Cursor');

// nothing of his at all: back to every 4 px
state.windows = [];
tickWindows();
state.pointer = [25, 20];
tickCursor();
assert.equal(lastArg('Cursor'), '22,20', '3 px < 4 px with no window of his');

// the window list: filtering, stacking, format
const w = {
    normal: win({id: 11, x: 0, y: 0, w: 800, h: 600, stack: 5}),
    dialog: win({id: 12, type: WT.DIALOG, x: 10.4, y: 20.6, w: 300, h: 200, stack: 7, focus: true, skip: true}),
    modal: win({id: 13, type: WT.MODAL_DIALOG, x: -5, y: 3, w: 50, h: 60, stack: 6, skip: true}),
    fs: win({id: 14, x: 1920, y: 0, w: 1920, h: 1080, stack: 1, fs: true}),
    sticky: win({id: 15, ws: wsB, all: true, x: 1, y: 2, w: 3, h: 4, stack: 3}),
    // all of these must be left out
    otherWs: win({id: 20, ws: wsB, stack: 2}),
    minimized: win({id: 21, showing: false, stack: 4}),
    or: win({id: 22, or: true, type: WT.POPUP_MENU, stack: 9}),
    desktop: win({id: 23, type: WT.DESKTOP, stack: 0}),
    dock: win({id: 24, type: WT.DOCK, stack: 10}),
    utility: win({id: 25, type: WT.UTILITY, stack: 11}),
    ding: win({id: 26, type: WT.NORMAL, skip: true, x: 0, y: 0, w: 1920, h: 1080, stack: -1}),
    him: pet,
};
state.windows = Object.values(w);
tickWindows();
const expected = JSON.stringify([
    [1920, 0, 1920, 1080, 0, 1, 0, '', '14'],
    [1, 2, 3, 4, 1, 0, 0, '', '15'],
    [0, 0, 800, 600, 2, 0, 0, '', '11'],
    [-5, 3, 50, 60, 3, 0, 0, '', '13'],
    [10, 21, 300, 200, 4, 0, 1, '', '12'],
]);
assert.equal(lastArg('Windows'), expected);
assert.equal(expected,
    '[[1920,0,1920,1080,0,1,0,"","14"],[1,2,3,4,1,0,0,"","15"],[0,0,800,600,2,0,0,"","11"],' +
    '[-5,3,50,60,3,0,0,"","13"],[10,21,300,200,4,0,1,"","12"]]');
const nWindows = sent('Windows').length;

// in flight: a change waits for the reply, then goes out
w.normal._m.x = 50;
tickWindows();
assert.equal(sent('Windows').length, nWindows, 'waits while a Windows call is out');
reply('Windows');
tickWindows();
assert.equal(sent('Windows').length, nWindows + 1);
assert.ok(lastArg('Windows').includes('[50,0,800,600,2,0,0,"","11"]'));

// a failed call is sent again even if nothing changed (windows can sit still)
reply('Windows', 'org.freedesktop.DBus.Error.UnknownObject');
tickWindows();
assert.equal(sent('Windows').length, nWindows + 2, 'resent after an error');
reply('Windows');
tickWindows();
assert.equal(sent('Windows').length, nWindows + 2, 'not resent after success');

// switching workspace changes the list
state.active = wsB;
tickWindows();
assert.equal(lastArg('Windows'), '[[0,0,100,100,0,0,0,"","20"],[1,2,3,4,1,0,0,"","15"]]');
reply('Windows');
state.active = wsA;

// he goes away: both timers stop, nothing more is sent
const idsBefore = [...timers.keys()];
watch.vanished(bus, 'org.clawdpet.Pet');
assert.equal(timers.size, 0, 'no timers once he is gone');
assert.deepEqual(removed.slice(-2).sort(), idsBefore.sort());
const callsAfterVanish = calls.length;

// he comes back (a restart): everything is sent afresh, even unchanged
state.pointer = [25, 20];
state.windows = [w.normal];
watch.appeared(bus, 'org.clawdpet.Pet', ':1.100');
assert.equal(timers.size, 2);
tickCursor();
assert.equal(lastArg('Cursor'), '25,20', 'pointer resent after a restart');
tickWindows();
assert.equal(lastArg('Windows'), '[[50,0,800,600,0,0,0,"","11"]]');
assert.equal(calls.length, callsAfterVanish + 2);

// a late reply from before the restart does no harm
reply('Cursor');
reply('Windows');

// a new owner without a vanish in between: still exactly two timers
watch.appeared(bus, 'org.clawdpet.Pet', ':1.101');
assert.equal(timers.size, 2, 'appeared twice: timers replaced, not doubled');

// disable: stops watching and removes every timer
ext.disable();
assert.deepEqual(unwatched, [42]);
assert.equal(timers.size, 0, 'no timers after disable');

// enable again (e.g. after the lock screen)
ext.enable();
assert.equal(timers.size, 0);
watch.appeared(bus, 'org.clawdpet.Pet', ':1.102');
assert.equal(timers.size, 2);
ext.disable();
assert.equal(timers.size, 0);
assert.deepEqual(unwatched, [42, 42]);

console.log('all extension tests passed');
