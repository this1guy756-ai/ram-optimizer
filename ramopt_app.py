#!/usr/bin/env python3
"""RAM Optimizer - Desktop Application"""

import os, sys, time, threading, ctypes
from datetime import datetime

# Auto-install missing packages on first run
def _ensure_deps():
    needed = []
    for mod in ["customtkinter", "psutil"]:
        try:
            __import__(mod)
        except ImportError:
            needed.append(mod)
    if needed:
        import subprocess
        print(f"Installing {', '.join(needed)} ...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install"] + needed,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

_ensure_deps()

import customtkinter as ctk
import psutil

# ── Windows API ──────────────────────────────────────────────────────────────

_k32   = ctypes.windll.kernel32
_psapi = ctypes.windll.psapi
_PFLAGS = 0x0400 | 0x0100   # PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA

def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False

def trim_pid(pid):
    """Move a process's pages to standby (frees physical RAM, process can reclaim instantly)."""
    h = _k32.OpenProcess(_PFLAGS, False, pid)
    if not h:
        return False
    try:
        return bool(_psapi.EmptyWorkingSet(h))
    finally:
        _k32.CloseHandle(h)

# ── Data helpers ─────────────────────────────────────────────────────────────

def fmt(b):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"

def proc_mb(pid):
    try:
        return psutil.Process(pid).memory_info().rss / 1_048_576
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0

def get_procs(min_mb=5.0):
    out = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            mb = p.info["memory_info"].rss / 1_048_576
            if mb >= min_mb:
                out.append({"pid": p.info["pid"], "name": p.info["name"], "mb": mb})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return sorted(out, key=lambda x: x["mb"], reverse=True)

def get_sys_mem():
    v = psutil.virtual_memory()
    s = psutil.swap_memory()
    return {
        "total": v.total, "used": v.used, "avail": v.available, "pct": v.percent,
        "sw_total": s.total, "sw_used": s.used,
    }

# ── App ───────────────────────────────────────────────────────────────────────

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BLUE   = "#1f6aa5"
GREEN  = "#1a7a1a"
RED    = "#7a1a1a"
ORANGE = "#e09020"
DRED   = "#e05050"

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("RAM Optimizer")
        self.geometry("960x680")
        self.minsize(820, 580)
        self.iconbitmap(default="")   # no icon file needed

        self._watch_rules  = []
        self._log_lines    = []
        self._proc_widgets = []

        self._build_header()
        self._build_sidebar()
        self._build_overview()
        self._build_processes()
        self._build_gamemode()
        self._build_watch()

        self.show_view("overview")
        self._tick()   # start live refresh

    # ── Header ────────────────────────────────────────────────────────────────

    def _build_header(self):
        hdr = ctk.CTkFrame(self, height=86, corner_radius=0, fg_color=("gray85", "gray17"))
        hdr.pack(side="top", fill="x")
        hdr.pack_propagate(False)

        ctk.CTkLabel(hdr, text="RAM Optimizer",
                     font=ctk.CTkFont(size=20, weight="bold")).place(x=16, y=8)

        self._ram_text = ctk.CTkLabel(hdr, text="", font=ctk.CTkFont(size=12))
        self._ram_text.place(x=16, y=36)

        self._ram_bar = ctk.CTkProgressBar(hdr, width=520, height=16, corner_radius=6)
        self._ram_bar.place(x=16, y=62)
        self._ram_bar.set(0)

        color = "#1a8a1a" if is_admin() else "#9a4a00"
        badge = "  Admin  " if is_admin() else "  Limited (run as admin for best results)  "
        ctk.CTkLabel(hdr, text=badge, fg_color=color, corner_radius=6,
                     font=ctk.CTkFont(size=11)).place(relx=1.0, x=-16, y=12, anchor="ne")

    # ── Sidebar ───────────────────────────────────────────────────────────────

    def _build_sidebar(self):
        self._sidebar = ctk.CTkFrame(self, width=168, corner_radius=0, fg_color=("gray80","gray15"))
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        self._nav_btns = {}
        items = [
            ("overview",  "  Overview"),
            ("processes", "  Processes"),
            ("gamemode",  "  Game Mode"),
            ("watch",     "  Auto Watch"),
        ]
        ctk.CTkLabel(self._sidebar, text="", height=8).pack()
        for key, label in items:
            btn = ctk.CTkButton(
                self._sidebar, text=label, anchor="w",
                width=152, height=42, corner_radius=8,
                fg_color="transparent", hover_color=("gray70", "gray28"),
                font=ctk.CTkFont(size=14),
                command=lambda k=key: self.show_view(k),
            )
            btn.pack(padx=8, pady=3)
            self._nav_btns[key] = btn

        ctk.CTkLabel(self._sidebar, text="v1.0", font=ctk.CTkFont(size=10),
                     text_color="gray50").pack(side="bottom", pady=10)

    # ── Overview ──────────────────────────────────────────────────────────────

    def _build_overview(self):
        self._v_overview = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")

        # Stat cards
        cards = ctk.CTkFrame(self._v_overview, fg_color="transparent")
        cards.pack(fill="x", padx=20, pady=(18, 10))

        self._c_used  = self._card(cards, "Used RAM")
        self._c_free  = self._card(cards, "Free RAM")
        self._c_total = self._card(cards, "Total RAM")
        self._c_pf    = self._card(cards, "Pagefile")
        for c in (self._c_used, self._c_free, self._c_total, self._c_pf):
            c.pack(side="left", expand=True, fill="x", padx=5)

        # Trim panel
        panel = ctk.CTkFrame(self._v_overview)
        panel.pack(fill="x", padx=20, pady=6)

        self._trim_btn = ctk.CTkButton(
            panel, text="  TRIM ALL PROCESSES",
            height=52, font=ctk.CTkFont(size=17, weight="bold"),
            fg_color=BLUE, hover_color="#144870",
            command=self._do_trim_all,
        )
        self._trim_btn.pack(fill="x", padx=16, pady=(16, 8))

        self._trim_msg = ctk.CTkLabel(panel, text="Click to free unused RAM from all running processes.",
                                      font=ctk.CTkFont(size=12), text_color="gray60")
        self._trim_msg.pack(pady=(0, 14))

        # Log
        log = ctk.CTkFrame(self._v_overview)
        log.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        row = ctk.CTkFrame(log, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(10, 4))
        ctk.CTkLabel(row, text="Activity Log", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        ctk.CTkButton(row, text="Clear", width=64, height=26,
                      fg_color="transparent", border_width=1,
                      command=self._clear_log).pack(side="right")

        self._log_box = ctk.CTkTextbox(log, state="disabled",
                                        font=ctk.CTkFont(family="Consolas", size=12))
        self._log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _card(self, parent, label):
        f = ctk.CTkFrame(parent)
        ctk.CTkLabel(f, text=label, font=ctk.CTkFont(size=11),
                     text_color="gray60").pack(pady=(12, 2))
        val = ctk.CTkLabel(f, text="—", font=ctk.CTkFont(size=16, weight="bold"))
        val.pack(pady=(0, 12))
        f._val = val
        return f

    # ── Processes ─────────────────────────────────────────────────────────────

    def _build_processes(self):
        self._v_processes = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")

        # Toolbar
        tb = ctk.CTkFrame(self._v_processes, fg_color="transparent")
        tb.pack(fill="x", padx=20, pady=(14, 6))

        self._proc_search = ctk.CTkEntry(tb, placeholder_text="Filter by name...", width=220, height=34)
        self._proc_search.pack(side="left")
        self._proc_search.bind("<KeyRelease>", lambda _: self._refresh_procs())

        ctk.CTkButton(tb, text="Refresh", width=84, height=34,
                      command=self._refresh_procs).pack(side="left", padx=8)

        ctk.CTkButton(tb, text="Trim Visible", width=110, height=34,
                      command=self._trim_visible).pack(side="left")

        self._proc_msg = ctk.CTkLabel(tb, text="", font=ctk.CTkFont(size=12))
        self._proc_msg.pack(side="left", padx=12)

        # Column headers
        hdr = ctk.CTkFrame(self._v_processes, height=30, fg_color=("gray75","gray20"))
        hdr.pack(fill="x", padx=20)
        hdr.pack_propagate(False)
        for text, w in [("PID", 72), ("Process Name", 300), ("RAM Usage", 110), ("Action", 80)]:
            ctk.CTkLabel(hdr, text=text, width=w,
                         font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(10, 0))

        # Scrollable rows
        self._proc_scroll = ctk.CTkScrollableFrame(self._v_processes, label_text="")
        self._proc_scroll.pack(fill="both", expand=True, padx=20, pady=(2, 20))

    # ── Game Mode ─────────────────────────────────────────────────────────────

    def _build_gamemode(self):
        self._v_gamemode = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")

        ctk.CTkLabel(self._v_gamemode, text="Game Mode",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(anchor="w", padx=24, pady=(22, 2))
        ctk.CTkLabel(self._v_gamemode,
                     text="Trims all background apps to hand your game the maximum amount of free RAM.",
                     font=ctk.CTkFont(size=13), text_color="gray60").pack(anchor="w", padx=24)

        box = ctk.CTkFrame(self._v_gamemode)
        box.pack(fill="x", padx=24, pady=18)

        ctk.CTkLabel(box, text="Enter your game's process name (partial is fine):",
                     font=ctk.CTkFont(size=13)).pack(anchor="w", padx=16, pady=(16, 6))
        ctk.CTkLabel(box, text='Examples:  "elden"  "cs2"  "minecraft"  "gta5"',
                     font=ctk.CTkFont(size=12), text_color="gray55").pack(anchor="w", padx=16)

        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=12)

        self._game_entry = ctk.CTkEntry(row, placeholder_text="e.g.  elden", width=260, height=44,
                                         font=ctk.CTkFont(size=14))
        self._game_entry.pack(side="left")
        self._game_entry.bind("<Return>", lambda _: self._do_gamemode())

        ctk.CTkButton(row, text="  Boost for Game  ", height=44,
                      font=ctk.CTkFont(size=14, weight="bold"),
                      fg_color=GREEN, hover_color="#125012",
                      command=self._do_gamemode).pack(side="left", padx=12)

        self._gm_msg = ctk.CTkLabel(box, text="", font=ctk.CTkFont(size=13))
        self._gm_msg.pack(anchor="w", padx=16, pady=(0, 16))

        # Tips
        tips = ctk.CTkFrame(self._v_gamemode)
        tips.pack(fill="x", padx=24)
        ctk.CTkLabel(tips, text="Tips", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=14, pady=(12, 4))
        for tip in [
            "Run the app as Administrator for the biggest improvement.",
            "Click Boost right before launching your game — not after.",
            "Background apps will gradually reclaim RAM over time. That's normal.",
            "Works great with Chrome, Discord, Spotify running in the background.",
        ]:
            ctk.CTkLabel(tips, text=f"    •  {tip}", font=ctk.CTkFont(size=12),
                         text_color="gray65", anchor="w").pack(anchor="w", padx=10, pady=1)
        ctk.CTkLabel(tips, text="").pack(pady=4)

    # ── Auto Watch ────────────────────────────────────────────────────────────

    def _build_watch(self):
        self._v_watch = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")

        ctk.CTkLabel(self._v_watch, text="Auto Watch",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(anchor="w", padx=24, pady=(22, 2))
        ctk.CTkLabel(self._v_watch,
                     text="Automatically trims a process whenever its RAM exceeds your limit.",
                     font=ctk.CTkFont(size=13), text_color="gray60").pack(anchor="w", padx=24)

        # Add rule form
        form = ctk.CTkFrame(self._v_watch)
        form.pack(fill="x", padx=24, pady=16)
        ctk.CTkLabel(form, text="New Watch Rule",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=14, pady=(12, 8))

        row = ctk.CTkFrame(form, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 4))

        def lbl(t): ctk.CTkLabel(row, text=t, font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 4))

        lbl("Process:")
        self._w_name = ctk.CTkEntry(row, placeholder_text="e.g. chrome", width=160, height=34)
        self._w_name.pack(side="left", padx=(0, 12))

        lbl("Max RAM (MB):")
        self._w_mb = ctk.CTkEntry(row, placeholder_text="e.g. 1500", width=100, height=34)
        self._w_mb.pack(side="left", padx=(0, 12))

        lbl("Check every:")
        self._w_int = ctk.CTkEntry(row, placeholder_text="30s", width=72, height=34)
        self._w_int.pack(side="left", padx=(0, 12))
        lbl("seconds")

        ctk.CTkButton(row, text="Add Rule", height=34, width=96,
                      command=self._add_rule).pack(side="left", padx=(8, 0))

        self._w_err = ctk.CTkLabel(form, text="", font=ctk.CTkFont(size=12), text_color=DRED)
        self._w_err.pack(anchor="w", padx=14, pady=(2, 10))

        # Rules list
        rules_frame = ctk.CTkFrame(self._v_watch)
        rules_frame.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        ctk.CTkLabel(rules_frame, text="Active Rules",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=14, pady=(12, 4))
        self._rules_area = ctk.CTkScrollableFrame(rules_frame)
        self._rules_area.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._rule_widgets = []

    # ── Actions ───────────────────────────────────────────────────────────────

    def _do_trim_all(self):
        self._trim_btn.configure(state="disabled", text="  Trimming…")

        def work():
            before = psutil.virtual_memory().available
            ok = fail = 0
            for p in get_procs(40):
                if p["pid"] == os.getpid():
                    continue
                if trim_pid(p["pid"]):
                    ok += 1
                else:
                    fail += 1
            time.sleep(0.7)
            freed = (psutil.virtual_memory().available - before) / 1_048_576
            self.after(0, lambda: self._trim_all_done(ok, fail, freed))

        threading.Thread(target=work, daemon=True).start()

    def _trim_all_done(self, ok, fail, freed_mb):
        self._trim_btn.configure(state="normal", text="  TRIM ALL PROCESSES")
        freed_str = f"+{freed_mb:.0f} MB freed" if freed_mb > 0 else "pages moved to standby"
        msg = f"Trimmed {ok} processes  —  {freed_str}"
        self._trim_msg.configure(text=msg, text_color="gray90")
        self._log(f"Trim all: {ok} processes, {freed_str}")

    def _trim_visible(self):
        q = self._proc_search.get().lower()
        procs = [p for p in get_procs(5) if not q or q in p["name"].lower()]
        ok = 0
        for p in procs:
            if p["pid"] != os.getpid() and trim_pid(p["pid"]):
                ok += 1
        self._proc_msg.configure(text=f"Trimmed {ok} processes")
        threading.Timer(0.5, lambda: self.after(0, self._refresh_procs)).start()

    def _trim_one(self, pid, name, lbl):
        before = proc_mb(pid)
        if trim_pid(pid):
            time.sleep(0.3)
            after = proc_mb(pid)
            self.after(0, lambda: lbl.configure(text=f"{after:.0f} MB"))
            self._log(f"Trimmed {name} ({pid}):  {before:.0f} → {after:.0f} MB")
        else:
            self._log(f"Could not trim {name} ({pid})  (access denied)")

    def _do_gamemode(self):
        name = self._game_entry.get().strip()
        if not name:
            self._gm_msg.configure(text="Please enter a game name first.", text_color=DRED)
            return
        self._gm_msg.configure(text="Trimming background processes…", text_color="gray60")

        def work():
            keep = {os.getpid()}
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    if name.lower() in p.info["name"].lower():
                        keep.add(p.info["pid"])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            before = psutil.virtual_memory().available
            count = 0
            for p in get_procs(25):
                if p["pid"] not in keep and trim_pid(p["pid"]):
                    count += 1
            time.sleep(0.7)
            freed = (psutil.virtual_memory().available - before) / 1_048_576
            self.after(0, lambda: self._gm_done(name, count, freed))

        threading.Thread(target=work, daemon=True).start()

    def _gm_done(self, name, count, freed):
        msg = f"Done!  Trimmed {count} background processes  —  freed approx. {freed:.0f} MB"
        self._gm_msg.configure(text=msg, text_color="#50dd50")
        self._log(f"Game mode ({name}): {count} trimmed, ~{freed:.0f} MB freed")

    def _add_rule(self):
        name   = self._w_name.get().strip()
        mb_str = self._w_mb.get().strip()
        int_str = self._w_int.get().strip() or "30"

        if not name:
            self._w_err.configure(text="Process name is required.")
            return
        try:
            max_mb = float(mb_str)
        except ValueError:
            self._w_err.configure(text="Max RAM must be a number (e.g. 1500).")
            return
        try:
            interval = max(5, int(int_str))
        except ValueError:
            self._w_err.configure(text="Interval must be a whole number.")
            return

        self._w_err.configure(text="")
        stop = threading.Event()
        rule = {"name": name, "max_mb": max_mb, "interval": interval,
                "stop": stop, "hits": 0}
        self._watch_rules.append(rule)

        def watcher():
            while not stop.is_set():
                for p in psutil.process_iter(["pid", "name", "memory_info"]):
                    try:
                        if name.lower() not in p.info["name"].lower():
                            continue
                        mb = p.info["memory_info"].rss / 1_048_576
                        if mb > max_mb and trim_pid(p.info["pid"]):
                            rule["hits"] += 1
                            after = proc_mb(p.info["pid"])
                            n, b, a = p.info["name"], mb, after
                            self.after(0, lambda n=n, b=b, a=a:
                                self._log(f"Watch: {n}  {b:.0f} → {a:.0f} MB"))
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                stop.wait(interval)

        threading.Thread(target=watcher, daemon=True).start()
        self._render_rules()
        for e in (self._w_name, self._w_mb, self._w_int):
            e.delete(0, "end")

    def _remove_rule(self, rule):
        rule["stop"].set()
        self._watch_rules.remove(rule)
        self._render_rules()

    def _render_rules(self):
        for w in self._rule_widgets:
            w.destroy()
        self._rule_widgets.clear()

        if not self._watch_rules:
            lbl = ctk.CTkLabel(self._rules_area, text="No rules yet. Add one above.",
                               font=ctk.CTkFont(size=12), text_color="gray55")
            lbl.pack(pady=20)
            self._rule_widgets.append(lbl)
            return

        for rule in self._watch_rules:
            row = ctk.CTkFrame(self._rules_area)
            row.pack(fill="x", pady=3, padx=2)
            ctk.CTkLabel(row, text=rule["name"],
                         width=200, font=ctk.CTkFont(size=13)).pack(side="left", padx=12)
            ctk.CTkLabel(row, text=f"Max {rule['max_mb']:.0f} MB",
                         width=120).pack(side="left")
            ctk.CTkLabel(row, text=f"Every {rule['interval']}s",
                         width=90).pack(side="left")
            ctk.CTkLabel(row, text=f"Trimmed {rule['hits']}x",
                         width=100, text_color="#50dd50").pack(side="left")
            ctk.CTkButton(row, text="Remove", width=80, height=28,
                          fg_color=RED, hover_color="#5a0f0f",
                          command=lambda r=rule: self._remove_rule(r)).pack(side="right", padx=10, pady=5)
            self._rule_widgets.append(row)

    # ── Process list ──────────────────────────────────────────────────────────

    def _refresh_procs(self):
        for w in self._proc_widgets:
            w.destroy()
        self._proc_widgets.clear()

        q = self._proc_search.get().lower()
        procs = get_procs(5)
        if q:
            procs = [p for p in procs if q in p["name"].lower()]
        procs = procs[:80]

        for p in procs:
            row = ctk.CTkFrame(self._proc_scroll, height=34)
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)

            ctk.CTkLabel(row, text=str(p["pid"]),
                         width=72, font=ctk.CTkFont(size=12)).pack(side="left", padx=(8, 0))
            ctk.CTkLabel(row, text=p["name"],
                         width=300, font=ctk.CTkFont(size=12), anchor="w").pack(side="left")
            mb_lbl = ctk.CTkLabel(row, text=f"{p['mb']:.0f} MB",
                                   width=110, font=ctk.CTkFont(size=12))
            mb_lbl.pack(side="left")
            ctk.CTkButton(
                row, text="Trim", width=70, height=26,
                command=lambda pid=p["pid"], name=p["name"], lbl=mb_lbl:
                    threading.Thread(target=self._trim_one, args=(pid, name, lbl), daemon=True).start()
            ).pack(side="left", padx=6)
            self._proc_widgets.append(row)

    # ── Live refresh ──────────────────────────────────────────────────────────

    def _tick(self):
        m = get_sys_mem()
        pct = m["pct"] / 100
        self._ram_bar.set(pct)
        color = DRED if m["pct"] > 85 else ORANGE if m["pct"] > 65 else BLUE
        self._ram_bar.configure(progress_color=color)
        self._ram_text.configure(
            text=f"  {fmt(m['used'])} used  /  {fmt(m['total'])} total"
                 f"   ({m['pct']:.1f}%)      Free: {fmt(m['avail'])}"
        )
        self._c_used._val.configure(text=fmt(m["used"]))
        self._c_free._val.configure(text=fmt(m["avail"]))
        self._c_total._val.configure(text=fmt(m["total"]))
        sw = fmt(m["sw_used"]) if m["sw_total"] else "N/A"
        self._c_pf._val.configure(text=sw)
        self.after(2000, self._tick)

    # ── Navigation ────────────────────────────────────────────────────────────

    def show_view(self, key):
        views = {
            "overview":  self._v_overview,
            "processes": self._v_processes,
            "gamemode":  self._v_gamemode,
            "watch":     self._v_watch,
        }
        for k, v in views.items():
            v.pack_forget()
        views[key].pack(side="right", fill="both", expand=True)

        for k, btn in self._nav_btns.items():
            btn.configure(fg_color=BLUE if k == key else "transparent")

        if key == "processes":
            self._refresh_procs()

    # ── Log ───────────────────────────────────────────────────────────────────

    def _log(self, msg):
        ts  = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}]  {msg}"
        self._log_lines.insert(0, line)
        if len(self._log_lines) > 300:
            self._log_lines.pop()
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.insert("end", "\n".join(self._log_lines))
        self._log_box.configure(state="disabled")

    def _clear_log(self):
        self._log_lines.clear()
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")


# ── Launch ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    App().mainloop()
