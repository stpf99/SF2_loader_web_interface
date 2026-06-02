"""
SF2 Player – backend v4
FluidSynth 2.4+ / PipeWire / rtmidi
"""

import os, re, json, threading, subprocess, time, queue
from pathlib import Path
from flask import Flask, render_template, jsonify, request, Response

try:
    import fluidsynth
    FS_AVAILABLE = True
except ImportError:
    FS_AVAILABLE = False
    print("WARNING: fluidsynth not available")

try:
    import rtmidi
    RTMIDI_AVAILABLE = True
except ImportError:
    RTMIDI_AVAILABLE = False
    print("WARNING: python-rtmidi not available")

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
SF2_DIR  = BASE_DIR / "sf2"
DB_PATH  = BASE_DIR / "device_db.json"
SF2_DIR.mkdir(exist_ok=True)

# ─── Device DB ────────────────────────────────────────────────────────────────
def load_db():
    if DB_PATH.exists():
        with open(DB_PATH) as f:
            return json.load(f)
    return {"devices": {}}

def save_db(db):
    with open(DB_PATH, "w") as f:
        json.dump(db, f, indent=2)

# ─── Synth Engine ─────────────────────────────────────────────────────────────
class SynthEngine:
    def __init__(self):
        self.fs             = None
        self.sfid           = None
        self.channel        = 0
        self.current_sf2    = None
        self.current_bank   = 0
        self.current_preset = 0

        # Only params that FluidSynth actually supports
        self.params = {
            # GM CC
            "volume":      100,
            "pan":          64,
            "amp_a":         0,   # CC 73 attack time
            "amp_r":        32,   # CC 72 release time
            "filter_cut":   80,   # CC 74 brightness
            "filter_res":    0,   # CC 71 resonance
            "porta_time":    0,   # CC 5
            "porta_sw":      0,   # CC 65 on/off
            "mono":          0,   # CC 126
            "expression":  127,   # CC 11
            "modulation":    0,   # CC 1
            "sustain":       0,   # CC 64
            "bend":         64,   # pitch bend (0-127, 64=center)
            "bend_range":    2,   # semitones
            # Reverb (native FluidSynth API)
            "reverb_on":     1,
            "rev_level":    20,   # 0-127
            "rev_room":     40,   # 0-127
            "rev_damp":     50,   # 0-127
            "rev_width":    64,   # 0-127
            # Chorus (native FluidSynth API)
            "chorus_on":     0,
            "cho_level":    64,   # 0-127
            "cho_nr":        3,   # 0-99 (voice count)
            "cho_speed":    30,   # 0-127
            "cho_depth":    30,   # 0-127
            "cho_type":      0,   # 0=sine 1=triangle
        }

        if FS_AVAILABLE:
            self._init_synth()

    def _init_synth(self):
        try:
            self.fs = fluidsynth.Synth(samplerate=48000.0)
            for driver in ["pipewire", "jack", "pulseaudio", "alsa"]:
                try:
                    self.fs.start(driver=driver)
                    print(f"FluidSynth started: {driver}")
                    break
                except Exception:
                    continue
            # Default reverb/chorus state
            self.fs.setting("synth.reverb.active", 1)
            self.fs.setting("synth.chorus.active", 0)
        except Exception as e:
            print(f"FluidSynth init error: {e}")
            self.fs = None

    # ── SF2 loading ──────────────────────────────────────────────────────────
    def load_sf2(self, path, bank=0, preset=0):
        if not self.fs:
            return False
        try:
            if self.sfid is not None:
                self.fs.sfunload(self.sfid)
            self.sfid = self.fs.sfload(path)
            self.fs.program_select(self.channel, self.sfid, bank, preset)
            self.current_sf2    = path
            self.current_bank   = bank
            self.current_preset = preset
            self._apply_all()
            return True
        except Exception as e:
            print(f"SF2 load error: {e}")
            return False

    def select_preset(self, bank, preset):
        if self.fs and self.sfid is not None:
            self.fs.program_select(self.channel, self.sfid, bank, preset)
            self.current_bank   = bank
            self.current_preset = preset

    # ── Parameters ───────────────────────────────────────────────────────────
    def set_param(self, name, value):
        self.params[name] = value
        self._apply_one(name, value)

    # GM CC map (params that go via fs.cc)
    _CC = {
        "volume":     7,
        "pan":       10,
        "expression": 11,
        "modulation":  1,
        "sustain":    64,
        "porta_sw":   65,
        "porta_time":  5,
        "amp_a":      73,
        "amp_r":      72,
        "filter_cut": 74,
        "filter_res": 71,
        "mono":      126,
    }

    def _apply_one(self, name, value):
        if not self.fs:
            return
        ch = self.channel
        v  = int(value)

        if name in self._CC:
            self.fs.cc(ch, self._CC[name], v)

        elif name == "bend":
            # 0-127 → 0-16383, 64=center
            self.fs.pitch_bend(ch, int((v / 127.0) * 16383))

        elif name == "bend_range":
            # RPN 0 – pitch bend range
            self.fs.cc(ch, 101, 0)   # RPN MSB
            self.fs.cc(ch, 100, 0)   # RPN LSB
            self.fs.cc(ch, 6,   v)   # data entry
            self.fs.cc(ch, 38,  0)

        # Reverb – use native API, ignore bare cc(91)
        elif name == "reverb_on":
            self.fs.setting("synth.reverb.active", v)
        elif name == "rev_level":
            lvl = v / 127.0
            p = self.params
            self.fs.set_reverb(
                p["rev_room"] / 127.0,
                p["rev_damp"] / 127.0,
                p["rev_width"] / 127.0 * 100.0,
                lvl
            )
        elif name in ("rev_room", "rev_damp", "rev_width"):
            p = self.params
            self.fs.set_reverb(
                p["rev_room"]  / 127.0,
                p["rev_damp"]  / 127.0,
                p["rev_width"] / 127.0 * 100.0,
                p["rev_level"] / 127.0
            )

        # Chorus – native API
        elif name == "chorus_on":
            self.fs.setting("synth.chorus.active", v)
        elif name in ("cho_level", "cho_nr", "cho_speed", "cho_depth", "cho_type"):
            p = self.params
            self.fs.set_chorus(
                int(p["cho_nr"]),
                p["cho_level"] / 127.0,
                p["cho_speed"] / 127.0 * 5.0 + 0.1,   # 0.1–5.1 Hz
                p["cho_depth"] / 127.0 * 21.0,          # 0–21 ms
                int(p["cho_type"])                       # 0=sine 1=tri
            )

    def _apply_all(self):
        for name, value in self.params.items():
            self._apply_one(name, value)

    # ── Notes ─────────────────────────────────────────────────────────────────
    def note_on(self, note, vel=100):
        if self.fs:
            self.fs.noteon(self.channel, note, vel)

    def note_off(self, note):
        if self.fs:
            self.fs.noteoff(self.channel, note)

    def voices(self):
        if self.fs:
            try:
                return self.fs.get_active_voice_count()
            except Exception:
                return 0
        return 0

    def cleanup(self):
        if self.fs:
            self.fs.delete()


# ─── MIDI Manager ─────────────────────────────────────────────────────────────
class MIDIManager:
    KNOWN_CC = {
        1:"Mod", 2:"Breath", 5:"Porta Time", 7:"Volume", 10:"Pan",
        11:"Expression", 64:"Sustain", 65:"Portamento", 71:"Filter Res",
        72:"Release", 73:"Attack", 74:"Brightness", 91:"Reverb", 93:"Chorus",
    }

    def __init__(self, synth):
        self.synth        = synth
        self.midi_in      = None
        self.active_port  = None
        self.learn_param  = None
        self.cc_map       = {}
        self.detected_ccs = {}
        self.sse_queue    = queue.Queue(maxsize=200)
        self.db           = load_db()
        self._lock        = threading.Lock()
        if RTMIDI_AVAILABLE:
            self._scan_and_open()

    def list_ports(self):
        if not RTMIDI_AVAILABLE:
            return []
        try:
            m = rtmidi.MidiIn()
            ports = [{"index": i, "name": m.get_port_name(i)}
                     for i in range(m.get_port_count())]
            del m
            return ports
        except Exception:
            return []

    def _scan_and_open(self):
        ports = self.list_ports()
        if ports:
            self.open_port(ports[0]["index"])

    def open_port(self, index):
        if not RTMIDI_AVAILABLE:
            return False
        try:
            if self.midi_in:
                self.midi_in.close_port()
            self.midi_in = rtmidi.MidiIn()
            self.midi_in.open_port(index)
            self.midi_in.set_callback(self._midi_cb)
            self.active_port = self.midi_in.get_port_name(index)
            print(f"MIDI port: {self.active_port}")
            self._load_device_map(self.active_port)
            return True
        except Exception as e:
            print(f"MIDI error: {e}")
            return False

    def _midi_cb(self, message, data=None):
        msg, _ = message
        if not msg:
            return
        status  = msg[0] & 0xF0
        channel = msg[0] & 0x0F

        if status == 0x90 and len(msg) >= 3:
            note, vel = msg[1], msg[2]
            if vel > 0:
                self.synth.note_on(note, vel)
                self._push({"type": "note_on", "note": note, "vel": vel, "ch": channel})
            else:
                self.synth.note_off(note)
                self._push({"type": "note_off", "note": note, "ch": channel})

        elif status == 0x80 and len(msg) >= 3:
            self.synth.note_off(msg[1])
            self._push({"type": "note_off", "note": msg[1], "ch": channel})

        elif status == 0xB0 and len(msg) >= 3:
            cc_num, cc_val = msg[1], msg[2]
            with self._lock:
                # Track detected CCs
                if cc_num not in self.detected_ccs:
                    self.detected_ccs[cc_num] = {
                        "name": self.KNOWN_CC.get(cc_num, f"CC{cc_num}"),
                        "last_val": cc_val, "count": 0
                    }
                self.detected_ccs[cc_num]["last_val"] = cc_val
                self.detected_ccs[cc_num]["count"]   += 1

                # MIDI Learn
                if self.learn_param:
                    self.cc_map[cc_num] = self.learn_param
                    self._push({"type": "learned", "cc": cc_num, "param": self.learn_param})
                    self.learn_param = None

            if cc_num in self.cc_map:
                param = self.cc_map[cc_num]
                self.synth.set_param(param, cc_val)
                self._push({"type": "cc", "cc": cc_num, "param": param, "val": cc_val})

        elif status == 0xE0 and len(msg) >= 3:
            bend   = ((msg[2] << 7) | msg[1])
            mapped = int((bend / 16383.0) * 127)
            self.synth.set_param("bend", mapped)
            self._push({"type": "bend", "val": mapped})

    def start_learn(self, param_name):
        with self._lock:
            self.learn_param = param_name

    def cancel_learn(self):
        with self._lock:
            self.learn_param = None

    def _push(self, data):
        try:
            self.sse_queue.put_nowait(data)
        except queue.Full:
            pass

    def save_profile(self):
        if not self.active_port:
            return None
        if "devices" not in self.db:
            self.db["devices"] = {}
        self.db["devices"][self.active_port] = {
            "cc_map": {str(k): v for k, v in self.cc_map.items()},
        }
        save_db(self.db)
        return self.db["devices"][self.active_port]

    def _load_device_map(self, port_name):
        device = self.db.get("devices", {}).get(port_name)
        if device:
            self.cc_map = {int(k): v for k, v in device.get("cc_map", {}).items()}
            print(f"Loaded CC map for {port_name}")

    def cleanup(self):
        if self.midi_in:
            self.midi_in.close_port()


# ─── SF2 preset scanner ───────────────────────────────────────────────────────
def get_presets(sf2_path):
    presets = []
    try:
        out = subprocess.check_output(
            ["sf2parse", sf2_path], stderr=subprocess.DEVNULL, timeout=10
        ).decode("utf-8", errors="replace")
        for m in re.finditer(r'Preset\[(\d+):(\d+)\]\s+(.+)', out):
            presets.append({"bank": int(m.group(1)), "preset": int(m.group(2)), "name": m.group(3).strip()})
        if presets:
            return presets
    except Exception:
        pass

    if FS_AVAILABLE:
        try:
            fs   = fluidsynth.Synth()
            sfid = fs.sfload(sf2_path)
            for bank in range(128):
                for preset in range(128):
                    name = fs.sfpreset_name(sfid, bank, preset)
                    if name:
                        presets.append({"bank": bank, "preset": preset, "name": name})
            fs.delete()
            return presets
        except Exception:
            pass

    return [{"bank": 0, "preset": i, "name": f"Preset {i}"} for i in range(128)]


# ─── Flask app ────────────────────────────────────────────────────────────────
app   = Flask(__name__, template_folder="templates", static_folder="static")
synth = SynthEngine()
midi  = MIDIManager(synth)


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/sf2_files")
def api_sf2_files():
    files = sorted(f.name for f in SF2_DIR.glob("*.sf2"))
    return jsonify(files)

@app.route("/api/presets")
def api_presets():
    sf2_file = request.args.get("sf2_file", "")
    path = SF2_DIR / sf2_file
    if not path.exists():
        return jsonify([])
    return jsonify(get_presets(str(path)))

@app.route("/api/load", methods=["POST"])
def api_load():
    d      = request.json
    path   = str(SF2_DIR / d.get("sf2_file", ""))
    bank   = int(d.get("bank", 0))
    preset = int(d.get("preset", 0))
    ok = synth.load_sf2(path, bank, preset)
    return jsonify({"status": "ok" if ok else "error"})

@app.route("/api/preset_select", methods=["POST"])
def api_preset_select():
    d = request.json
    synth.select_preset(int(d.get("bank", 0)), int(d.get("preset", 0)))
    return jsonify({"status": "ok"})

@app.route("/api/param", methods=["POST"])
def api_param():
    d = request.json
    synth.set_param(d.get("name", ""), int(d.get("value", 0)))
    return jsonify({"status": "ok"})

@app.route("/api/params")
def api_params():
    return jsonify(synth.params)

@app.route("/api/status")
def api_status():
    return jsonify({
        "synth":      FS_AVAILABLE and synth.fs is not None,
        "midi":       RTMIDI_AVAILABLE,
        "midi_port":  midi.active_port,
        "sf2_loaded": synth.current_sf2 is not None,
        "sf2":        os.path.basename(synth.current_sf2) if synth.current_sf2 else None,
        "bank":       synth.current_bank,
        "preset":     synth.current_preset,
        "voices":     synth.voices(),
    })

@app.route("/api/midi/ports")
def api_midi_ports():
    return jsonify({"ports": midi.list_ports(), "active": midi.active_port})

@app.route("/api/midi/open", methods=["POST"])
def api_midi_open():
    ok = midi.open_port(int(request.json.get("index", 0)))
    return jsonify({"status": "ok" if ok else "error"})

@app.route("/api/midi/learn", methods=["POST"])
def api_midi_learn():
    param = request.json.get("param", "")
    midi.start_learn(param)
    return jsonify({"status": "waiting", "param": param})

@app.route("/api/midi/learn/cancel", methods=["POST"])
def api_midi_learn_cancel():
    midi.cancel_learn()
    return jsonify({"status": "cancelled"})

@app.route("/api/midi/cc_map", methods=["GET"])
def api_cc_map():
    return jsonify({str(k): v for k, v in midi.cc_map.items()})

@app.route("/api/midi/cc_map", methods=["POST"])
def api_cc_map_set():
    d = request.json
    cc, param = int(d.get("cc", 0)), d.get("param", "")
    if param:
        midi.cc_map[cc] = param
    elif cc in midi.cc_map:
        del midi.cc_map[cc]
    return jsonify({"status": "ok"})

@app.route("/api/midi/detected_ccs")
def api_detected_ccs():
    with midi._lock:
        return jsonify({str(k): v for k, v in midi.detected_ccs.items()})

@app.route("/api/midi/save_profile", methods=["POST"])
def api_midi_save_profile():
    result = midi.save_profile()
    return jsonify({"status": "ok" if result else "error", "profile": result})

@app.route("/api/midi/events")
def api_midi_events():
    def generate():
        yield "retry: 1000\n\n"
        while True:
            try:
                ev = midi.sse_queue.get(timeout=15)
                yield f"data: {json.dumps(ev)}\n\n"
            except queue.Empty:
                yield ": keepalive\n\n"
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/note_on", methods=["POST"])
def api_note_on():
    d = request.json
    synth.note_on(int(d.get("note", 60)), int(d.get("vel", 100)))
    return jsonify({"status": "ok"})

@app.route("/api/note_off", methods=["POST"])
def api_note_off():
    synth.note_off(int(request.json.get("note", 60)))
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    try:
        app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
    finally:
        synth.cleanup()
        midi.cleanup()
