#!/usr/bin/env python3
"""
Docktor - Docker Desktop at home
A working Docker GUI in a single Python file.
"""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango

import json
import locale
import os
import shlex
import subprocess
import threading
from pathlib import Path

CONFIG_DIR  = Path.home() / ".config" / "docktor"
CONFIG_FILE = CONFIG_DIR / "config.json"
I18N_DIR    = Path(__file__).parent / "i18n"

DEFAULT_CONFIG = {"lang": "system"}

RESTART_CODES = ["no", "always", "unless-stopped", "on-failure"]
RESTART_KEYS  = {"no": "restart_no", "always": "restart_always",
                 "unless-stopped": "restart_unless_stopped",
                 "on-failure": "restart_on_failure"}

INSTALL_METHOD_CODES = ["script", "apt"]
INSTALL_METHOD_KEYS  = {"script": "method_script", "apt": "method_apt"}


# ── Config & i18n ─────────────────────────────────────────────────────────────

def load_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def detect_system_lang():
    try:
        loc = locale.getlocale()[0] or ""
    except Exception:
        loc = ""
    if not loc:
        loc = os.environ.get("LANG", "")
    return "de" if loc.lower().startswith("de") else "en"


def resolve_lang(setting):
    if setting == "system":
        return detect_system_lang()
    return setting


def load_i18n(lang):
    en = {}
    en_path = I18N_DIR / "en.json"
    if en_path.exists():
        with open(en_path) as f:
            en = json.load(f)
    if lang == "en":
        return en
    path = I18N_DIR / f"{lang}.json"
    if not path.exists():
        return en
    with open(path) as f:
        strings = json.load(f)
    for k, v in en.items():
        strings.setdefault(k, v)
    return strings


def t(strings, key, **kwargs):
    s = strings.get(key, key)
    for k, v in kwargs.items():
        s = s.replace("{" + k + "}", str(v))
    return s


# ── MenuButton helper ─────────────────────────────────────────────────────────

def make_menu_button(items, on_select, min_width=150):
    btn = Gtk.MenuButton()
    btn.set_size_request(min_width, -1)
    lbl = Gtk.Label(label=items[0] if items else "")
    btn.add(lbl)
    menu = Gtk.Menu()

    def build_menu(items, current=None):
        for child in menu.get_children():
            menu.remove(child)
        group = []
        active = current if current in items else (items[0] if items else None)
        for text in items:
            item = Gtk.RadioMenuItem.new_with_label(group, text)
            group = item.get_group()
            if text == active:
                item.set_active(True)
            def _on_activate(i, tx=text):
                if i.get_active():
                    lbl.set_text(tx)
                    on_select(tx)
            item.connect("activate", _on_activate)
            menu.append(item)
        menu.show_all()
        if active:
            lbl.set_text(active)

    build_menu(items)
    btn.set_popup(menu)

    def update(new_items, current=None):
        build_menu(new_items, current)

    return btn, lbl, update


# ── Docker helpers ─────────────────────────────────────────────────────────────

def run_docker(args, strings):
    """Runs a docker command and returns (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["docker"] + args,
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except FileNotFoundError:
        return -1, "", t(strings, "err_docker_not_found")
    except subprocess.TimeoutExpired:
        return -1, "", t(strings, "err_timeout")


def is_docker_installed():
    import shutil
    return shutil.which("docker") is not None


def get_containers(strings):
    code, out, err = run_docker([
        "ps", "-a",
        "--format", "{{.ID}}|{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}"
    ], strings)
    if code != 0:
        return None, err
    containers = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        containers.append({
            "id":     parts[0],
            "name":   parts[1],
            "image":  parts[2],
            "status": parts[3],
            "ports":  parts[4],
        })
    return containers, None


def get_logs(container_id, strings, lines=50):
    code, out, err = run_docker(["logs", "--tail", str(lines), container_id], strings)
    return out + ("\n" + err if err else "")


# ── Dialog: Install Docker ────────────────────────────────────────────────────

class InstallDockerDialog(Gtk.Dialog):
    def __init__(self, parent, strings):
        super().__init__(title=t(strings, "dlg_install_title"),
                         transient_for=parent, flags=0)
        self.strings = strings
        self.set_default_size(500, 400)
        self.set_border_width(10)

        self.add_button(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
        self._install_btn = self.add_button(t(strings, "btn_install"), Gtk.ResponseType.APPLY)
        self._install_btn.get_style_context().add_class("suggested-action")

        box = self.get_content_area()
        box.set_spacing(8)

        # Method selector
        method_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        method_box.set_margin_bottom(6)
        label = Gtk.Label(label=t(strings, "lbl_method"), xalign=0)
        method_box.pack_start(label, False, False, 0)

        self._method_current = INSTALL_METHOD_CODES[0]
        display_items = [t(strings, INSTALL_METHOD_KEYS[c]) for c in INSTALL_METHOD_CODES]
        self.method_menu_btn, self._method_label, _ = make_menu_button(
            display_items, self._on_method_selected, min_width=260)
        method_box.pack_start(self.method_menu_btn, True, True, 0)
        box.pack_start(method_box, False, False, 0)

        # Info label
        self.info_label = Gtk.Label(xalign=0, wrap=True)
        self.info_label.set_line_wrap(True)
        box.pack_start(self.info_label, False, False, 0)

        # Command preview
        cmd_frame = Gtk.Frame(label=t(strings, "frame_command"))
        self.cmd_view = Gtk.TextView()
        self.cmd_view.set_editable(False)
        self.cmd_view.set_monospace(True)
        self.cmd_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.cmd_view.set_margin_start(6)
        self.cmd_view.set_margin_end(6)
        self.cmd_view.set_margin_top(4)
        self.cmd_view.set_margin_bottom(4)
        cmd_frame.add(self.cmd_view)
        box.pack_start(cmd_frame, False, False, 0)

        # Output
        out_frame = Gtk.Frame(label=t(strings, "frame_output"))
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(150)
        self.out_view = Gtk.TextView()
        self.out_view.set_editable(False)
        self.out_view.set_monospace(True)
        self.out_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.out_buf = self.out_view.get_buffer()
        scroll.add(self.out_view)
        out_frame.add(scroll)
        box.pack_start(out_frame, True, True, 0)

        self._update_method_info()
        self.show_all()

    def _on_method_selected(self, display_text):
        idx = [t(self.strings, INSTALL_METHOD_KEYS[c]) for c in INSTALL_METHOD_CODES].index(display_text)
        self._method_current = INSTALL_METHOD_CODES[idx]
        self._update_method_info()

    def _update_method_info(self):
        s = self.strings
        if self._method_current == "script":
            self.info_label.set_text(t(s, "info_script"))
            self.cmd_view.get_buffer().set_text(
                "curl -fsSL https://get.docker.com -o /tmp/get-docker.sh\n"
                "pkexec sh /tmp/get-docker.sh"
            )
        else:
            self.info_label.set_text(t(s, "info_apt"))
            self.cmd_view.get_buffer().set_text(
                "pkexec apt-get update\n"
                "pkexec apt-get install -y ca-certificates curl gnupg\n"
                "pkexec install -m 0755 -d /etc/apt/keyrings\n"
                "curl -fsSL https://download.docker.com/linux/ubuntu/gpg | "
                "pkexec tee /etc/apt/keyrings/docker.asc\n"
                "pkexec apt-get update\n"
                "pkexec apt-get install -y docker-ce docker-ce-cli containerd.io"
            )

    def get_method(self):
        return self._method_current

    def append_output(self, text):
        end = self.out_buf.get_end_iter()
        self.out_buf.insert(end, text)
        end = self.out_buf.get_end_iter()
        self.out_view.scroll_to_iter(end, 0, False, 0, 0)

    def set_installing(self, installing):
        self._install_btn.set_sensitive(not installing)
        self.method_menu_btn.set_sensitive(not installing)


# ── Dialog: Add container ─────────────────────────────────────────────────────

class AddContainerDialog(Gtk.Dialog):
    def __init__(self, parent, strings):
        super().__init__(title=t(strings, "dlg_add_title"), transient_for=parent, flags=0)
        self.strings = strings
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            t(strings, "btn_start_action"), Gtk.ResponseType.OK,
        )
        self.set_default_size(420, -1)
        self.set_border_width(10)

        box = self.get_content_area()
        box.set_spacing(6)

        grid = Gtk.Grid()
        grid.set_row_spacing(8)
        grid.set_column_spacing(12)
        grid.set_border_width(6)

        def add_row(label_text, widget, row):
            label = Gtk.Label(label=label_text, xalign=0)
            label.set_width_chars(12)
            grid.attach(label, 0, row, 1, 1)
            widget.set_hexpand(True)
            grid.attach(widget, 1, row, 1, 1)

        self.image_entry = Gtk.Entry(placeholder_text=t(strings, "placeholder_image"))
        self.name_entry  = Gtk.Entry(placeholder_text=t(strings, "placeholder_name"))
        self.ports_entry = Gtk.Entry(placeholder_text=t(strings, "placeholder_ports"))
        self.vol_entry   = Gtk.Entry(placeholder_text=t(strings, "placeholder_volumes"))
        self.env_entry   = Gtk.Entry(placeholder_text=t(strings, "placeholder_env"))

        add_row(t(strings, "lbl_image"),   self.image_entry, 0)
        add_row(t(strings, "lbl_name"),    self.name_entry,  1)
        add_row(t(strings, "lbl_ports"),   self.ports_entry, 2)
        add_row(t(strings, "lbl_volumes"), self.vol_entry,   3)
        add_row(t(strings, "lbl_env"),     self.env_entry,   4)

        self._restart_current = "unless-stopped"
        display_items = [t(strings, RESTART_KEYS[c]) for c in RESTART_CODES]
        self.restart_menu_btn, self._restart_label, restart_update = make_menu_button(
            display_items, self._on_restart_selected, min_width=160)
        restart_update(display_items, display_items[RESTART_CODES.index(self._restart_current)])
        add_row(t(strings, "lbl_restart"), self.restart_menu_btn, 5)

        box.pack_start(grid, True, True, 0)
        self.show_all()

    def _on_restart_selected(self, display_text):
        display_items = [t(self.strings, RESTART_KEYS[c]) for c in RESTART_CODES]
        idx = display_items.index(display_text)
        self._restart_current = RESTART_CODES[idx]

    def get_values(self):
        return {
            "image":   self.image_entry.get_text().strip(),
            "name":    self.name_entry.get_text().strip(),
            "ports":   self.ports_entry.get_text().strip(),
            "volumes": self.vol_entry.get_text().strip(),
            "env":     self.env_entry.get_text().strip(),
            "restart": self._restart_current,
        }


# ── Dialog: Pull image ────────────────────────────────────────────────────────

class PullImageDialog(Gtk.Dialog):
    def __init__(self, parent, strings):
        super().__init__(title=t(strings, "dlg_pull_title"), transient_for=parent, flags=0)
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            t(strings, "btn_pull_action"), Gtk.ResponseType.OK,
        )
        self.set_default_size(380, -1)
        self.set_border_width(10)

        box = self.get_content_area()
        box.set_spacing(6)

        label = Gtk.Label(label=t(strings, "lbl_image_name"), xalign=0)
        self.entry = Gtk.Entry(placeholder_text=t(strings, "placeholder_pull_image"))
        self.entry.set_margin_top(4)

        box.pack_start(label, False, False, 0)
        box.pack_start(self.entry, False, False, 0)
        self.show_all()

    def get_image(self):
        return self.entry.get_text().strip()


# ── Main window ───────────────────────────────────────────────────────────────

class DocktorWindow(Gtk.Window):
    def __init__(self):
        super().__init__()
        self.set_default_size(800, 550)
        self.set_border_width(0)

        self.cfg = load_config()
        self.strings = load_i18n(resolve_lang(self.cfg.get("lang", "system")))
        s = self.strings

        self.set_title(t(s, "app_title"))
        self.selected_container = None
        self._build_ui()

        if not is_docker_installed():
            self.no_docker_bar.show()
            self.btn_install_docker.show()
            self.btn_add.set_sensitive(False)
            self.btn_pull.set_sensitive(False)
            self.set_status(t(s, "status_docker_not_found"))
        else:
            self.refresh_containers()

        GLib.timeout_add_seconds(5, self._auto_refresh)

    def _build_ui(self):
        s = self.strings
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(vbox)

        # HeaderBar
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title(t(s, "app_title"))
        header.set_subtitle(t(s, "app_subtitle"))
        self.set_titlebar(header)

        self.btn_add = Gtk.Button(label=t(s, "btn_add"))
        self.btn_add.connect("clicked", self.on_add_clicked)
        header.pack_start(self.btn_add)

        self.btn_pull = Gtk.Button(label=t(s, "btn_pull"))
        self.btn_pull.connect("clicked", self.on_pull_clicked)
        header.pack_start(self.btn_pull)

        self.btn_install_docker = Gtk.Button(label=t(s, "btn_install_docker"))
        self.btn_install_docker.connect("clicked", self.on_install_docker_clicked)
        self.btn_install_docker.set_no_show_all(True)
        header.pack_start(self.btn_install_docker)

        self._lang_options = [("de", "lang_de"), ("en", "lang_en"),
                               ("system", "lang_system")]
        self.lang_menu_btn = Gtk.MenuButton()
        self.lang_menu_btn.set_size_request(130, -1)
        self._lang_label = Gtk.Label()
        self.lang_menu_btn.add(self._lang_label)
        lang_menu = Gtk.Menu()
        group = []
        current_lang = self.cfg.get("lang", "system")
        for code, key in self._lang_options:
            item = Gtk.RadioMenuItem.new_with_label(group, t(s, key))
            group = item.get_group()
            if code == current_lang:
                item.set_active(True)
                self._lang_label.set_text(t(s, key))
            item.connect("activate", self._on_lang_menu_item, code)
            lang_menu.append(item)
        lang_menu.show_all()
        self.lang_menu_btn.set_popup(lang_menu)
        header.pack_end(self.lang_menu_btn)

        self.btn_refresh = Gtk.Button()
        self.btn_refresh.set_image(Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON))
        self.btn_refresh.set_tooltip_text(t(s, "tooltip_refresh"))
        self.btn_refresh.connect("clicked", lambda _: self.refresh_containers())
        header.pack_end(self.btn_refresh)

        # Info banner when Docker is missing
        self.no_docker_bar = Gtk.InfoBar()
        self.no_docker_bar.set_message_type(Gtk.MessageType.WARNING)
        self.no_docker_bar.set_no_show_all(True)
        no_docker_label = Gtk.Label(label=t(s, "no_docker_label"))
        self.no_docker_bar.get_content_area().pack_start(no_docker_label, False, False, 0)
        install_action_btn = self.no_docker_bar.add_button(t(s, "btn_install_now"), 1)
        install_action_btn.connect("clicked", self.on_install_docker_clicked)
        vbox.pack_start(self.no_docker_bar, False, False, 0)

        # Main paned
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.set_position(310)
        vbox.pack_start(paned, True, True, 0)

        # Top: container list
        top_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.set_margin_start(8)
        toolbar.set_margin_end(8)
        toolbar.set_margin_top(6)
        toolbar.set_margin_bottom(6)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(t(s, "search_placeholder"))
        self.search_entry.connect("search-changed", lambda _: self.filter_list())
        toolbar.pack_start(self.search_entry, True, True, 0)

        self.btn_start = Gtk.Button(label=t(s, "btn_start"))
        self.btn_start.connect("clicked", self.on_start_clicked)
        self.btn_start.set_sensitive(False)
        toolbar.pack_start(self.btn_start, False, False, 0)

        self.btn_stop = Gtk.Button(label=t(s, "btn_stop"))
        self.btn_stop.connect("clicked", self.on_stop_clicked)
        self.btn_stop.set_sensitive(False)
        toolbar.pack_start(self.btn_stop, False, False, 0)

        self.btn_restart = Gtk.Button(label=t(s, "btn_restart"))
        self.btn_restart.connect("clicked", self.on_restart_clicked)
        self.btn_restart.set_sensitive(False)
        toolbar.pack_start(self.btn_restart, False, False, 0)

        self.btn_remove = Gtk.Button(label=t(s, "btn_remove"))
        self.btn_remove.connect("clicked", self.on_remove_clicked)
        self.btn_remove.set_sensitive(False)
        toolbar.pack_start(self.btn_remove, False, False, 0)

        top_box.pack_start(toolbar, False, False, 0)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        top_box.pack_start(sep, False, False, 0)

        # Container list (ListStore + TreeView)
        # Columns: name, image, status, ports, id (hidden)
        self.store = Gtk.ListStore(str, str, str, str, str)
        self.filter_model = self.store.filter_new()
        self.filter_model.set_visible_func(self._row_visible)

        self.treeview = Gtk.TreeView(model=self.filter_model)
        self.treeview.set_headers_visible(True)
        self.treeview.get_selection().connect("changed", self.on_selection_changed)

        for i, (key, expand) in enumerate([
            ("col_name", True), ("col_image", True),
            ("col_status", False), ("col_ports", False)
        ]):
            renderer = Gtk.CellRendererText()
            renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
            col = Gtk.TreeViewColumn(t(s, key), renderer, text=i)
            col.set_expand(expand)
            col.set_resizable(True)
            if i == 2:
                col.set_cell_data_func(renderer, self._status_cell)
            self.treeview.append_column(col)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.treeview)
        top_box.pack_start(scroll, True, True, 0)

        paned.pack1(top_box, True, True)

        # Bottom: logs
        bottom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        log_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        log_header.set_margin_start(8)
        log_header.set_margin_end(8)
        log_header.set_margin_top(6)
        log_header.set_margin_bottom(4)

        log_label = Gtk.Label(label=t(s, "log_label"), xalign=0)
        log_label.get_style_context().add_class("dim-label")
        log_header.pack_start(log_label, True, True, 0)

        self.btn_logs = Gtk.Button(label=t(s, "btn_load"))
        self.btn_logs.connect("clicked", self.on_load_logs)
        self.btn_logs.set_sensitive(False)
        log_header.pack_end(self.btn_logs, False, False, 0)

        bottom_box.pack_start(log_header, False, False, 0)

        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_buffer = self.log_view.get_buffer()

        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        log_scroll.add(self.log_view)
        bottom_box.pack_start(log_scroll, True, True, 0)

        paned.pack2(bottom_box, True, True)

        # Statusbar
        self.statusbar = Gtk.Statusbar()
        self.status_ctx = self.statusbar.get_context_id("main")
        vbox.pack_start(self.statusbar, False, False, 0)

    def _status_cell(self, col, renderer, model, iter_, data):
        status = model.get_value(iter_, 2)
        if "Up" in status or "running" in status.lower():
            renderer.set_property("foreground", "#2d9e5f")
        elif "Exited" in status or "stopped" in status.lower():
            renderer.set_property("foreground", "#888")
        else:
            renderer.set_property("foreground", "#e63946")

    def _row_visible(self, model, iter_, data):
        query = self.search_entry.get_text().lower()
        if not query:
            return True
        name  = model.get_value(iter_, 0).lower()
        image = model.get_value(iter_, 1).lower()
        return query in name or query in image

    def filter_list(self):
        self.filter_model.refilter()

    def set_status(self, msg):
        self.statusbar.pop(self.status_ctx)
        self.statusbar.push(self.status_ctx, msg)

    def refresh_containers(self):
        s = self.strings
        self.set_status(t(s, "status_refreshing"))

        def worker():
            containers, err = get_containers(s)
            GLib.idle_add(self._update_list, containers, err)

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _auto_refresh(self):
        self.refresh_containers()
        return True

    def _update_list(self, containers, err):
        s = self.strings
        if err:
            self.set_status(t(s, "status_error", err=err))
            return

        sel_id = self.selected_container["id"] if self.selected_container else None

        self.store.clear()
        for c in containers:
            self.store.append([c["name"], c["image"], c["status"], c["ports"], c["id"]])

        if sel_id:
            for i, row in enumerate(self.filter_model):
                if row[4] == sel_id:
                    self.treeview.get_selection().select_iter(row.iter)
                    break

        running = sum(1 for c in containers if "Up" in c["status"])
        self.set_status(t(s, "status_containers", n=len(containers), running=running))

    def on_selection_changed(self, selection):
        model, iter_ = selection.get_selected()
        if iter_:
            self.selected_container = {
                "name":   model[iter_][0],
                "image":  model[iter_][1],
                "status": model[iter_][2],
                "ports":  model[iter_][3],
                "id":     model[iter_][4],
            }
            is_running = "Up" in self.selected_container["status"]
            self.btn_start.set_sensitive(not is_running)
            self.btn_stop.set_sensitive(is_running)
            self.btn_restart.set_sensitive(is_running)
            self.btn_remove.set_sensitive(True)
            self.btn_logs.set_sensitive(True)
        else:
            self.selected_container = None
            for btn in [self.btn_start, self.btn_stop, self.btn_restart, self.btn_remove, self.btn_logs]:
                btn.set_sensitive(False)

    def _run_action(self, args, success_msg):
        s = self.strings
        self.set_status(t(s, "status_please_wait"))

        def worker():
            code, out, err = run_docker(args, s)
            msg = success_msg if code == 0 else t(s, "status_error", err=err)
            GLib.idle_add(self.set_status, msg)
            GLib.idle_add(self.refresh_containers)

        threading.Thread(target=worker, daemon=True).start()

    def on_start_clicked(self, _):
        if not self.selected_container: return
        s = self.strings
        self._run_action(
            ["start", self.selected_container["id"]],
            t(s, "status_started", name=self.selected_container["name"]))

    def on_stop_clicked(self, _):
        if not self.selected_container: return
        s = self.strings
        self._run_action(
            ["stop", self.selected_container["id"]],
            t(s, "status_stopped", name=self.selected_container["name"]))

    def on_restart_clicked(self, _):
        if not self.selected_container: return
        s = self.strings
        self._run_action(
            ["restart", self.selected_container["id"]],
            t(s, "status_restarted", name=self.selected_container["name"]))

    def on_remove_clicked(self, _):
        if not self.selected_container: return
        s = self.strings
        name = self.selected_container["name"]
        dialog = Gtk.MessageDialog(
            transient_for=self, flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=t(s, "confirm_remove_title", name=name),
        )
        dialog.format_secondary_text(t(s, "confirm_remove_text"))
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            self._run_action(
                ["rm", "-f", self.selected_container["id"]],
                t(s, "status_removed", name=name))

    def on_load_logs(self, _):
        if not self.selected_container: return
        s = self.strings
        cid = self.selected_container["id"]
        self.set_status(t(s, "status_logs_loading"))

        def worker():
            logs = get_logs(cid, s)
            GLib.idle_add(self._show_logs, logs)

        threading.Thread(target=worker, daemon=True).start()

    def _show_logs(self, text):
        s = self.strings
        self.log_buffer.set_text(text)
        end = self.log_buffer.get_end_iter()
        self.log_view.scroll_to_iter(end, 0, False, 0, 0)
        self.set_status(t(s, "status_logs_loaded"))

    def on_add_clicked(self, _):
        s = self.strings
        dialog = AddContainerDialog(self, s)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            vals = dialog.get_values()
            dialog.destroy()
            if not vals["image"]:
                self.set_status(t(s, "status_no_image"))
                return
            self._create_container(vals)
        else:
            dialog.destroy()

    def _create_container(self, vals):
        s = self.strings
        args = ["run", "-d"]
        if vals["name"]:
            args += ["--name", vals["name"]]
        if vals["restart"]:
            args += ["--restart", vals["restart"]]
        if vals["ports"]:
            for p in vals["ports"].split():
                args += ["-p", p]
        if vals["volumes"]:
            for v in vals["volumes"].split():
                args += ["-v", v]
        if vals["env"]:
            for e in shlex.split(vals["env"]):
                args += ["-e", e]
        args.append(vals["image"])

        self.set_status(t(s, "status_starting_image", image=vals["image"]))

        def worker():
            code, out, err = run_docker(args, s)
            if code == 0:
                msg = t(s, "status_started", name=vals["name"] or vals["image"])
            else:
                msg = t(s, "status_error", err=err)
            GLib.idle_add(self.set_status, msg)
            GLib.idle_add(self.refresh_containers)

        threading.Thread(target=worker, daemon=True).start()

    def on_install_docker_clicked(self, _):
        s = self.strings
        dialog = InstallDockerDialog(self, s)

        def do_install():
            method = dialog.get_method()
            dialog.set_installing(True)
            GLib.idle_add(dialog.append_output, t(s, "install_starting"))

            def run_and_stream(cmd, shell=False):
                try:
                    proc = subprocess.Popen(
                        cmd, shell=shell,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True)
                    for line in proc.stdout:
                        GLib.idle_add(dialog.append_output, line)
                    proc.wait()
                    return proc.returncode
                except Exception as e:
                    GLib.idle_add(dialog.append_output, t(s, "install_error", e=e))
                    return -1

            if method == "script":
                rc = run_and_stream(
                    "curl -fsSL https://get.docker.com -o /tmp/get-docker.sh && pkexec sh /tmp/get-docker.sh",
                    shell=True)
            else:
                steps = [
                    "pkexec apt-get update",
                    "pkexec apt-get install -y ca-certificates curl gnupg",
                    "pkexec install -m 0755 -d /etc/apt/keyrings",
                    "curl -fsSL https://download.docker.com/linux/ubuntu/gpg | pkexec tee /etc/apt/keyrings/docker.asc > /dev/null",
                    "echo \"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] "
                    "https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable\" | "
                    "pkexec tee /etc/apt/sources.list.d/docker.list > /dev/null",
                    "pkexec apt-get update",
                    "pkexec apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin",
                ]
                rc = 0
                for step in steps:
                    GLib.idle_add(dialog.append_output, f"\n$ {step}\n")
                    rc = run_and_stream(step, shell=True)
                    if rc != 0:
                        break

            if rc == 0:
                GLib.idle_add(dialog.append_output, t(s, "install_success"))
                GLib.idle_add(self._on_docker_installed)
            else:
                GLib.idle_add(dialog.append_output, t(s, "install_failed"))
            GLib.idle_add(dialog.set_installing, False)

        def on_response(dlg, response):
            if response == Gtk.ResponseType.APPLY:
                threading.Thread(target=do_install, daemon=True).start()
            elif response == Gtk.ResponseType.CLOSE:
                dlg.destroy()

        dialog.connect("response", on_response)
        dialog.show()

    def _on_docker_installed(self):
        s = self.strings
        self.no_docker_bar.hide()
        self.btn_install_docker.hide()
        self.btn_add.set_sensitive(True)
        self.btn_pull.set_sensitive(True)
        self.set_status(t(s, "status_docker_installed"))
        self.refresh_containers()

    def on_pull_clicked(self, _):
        s = self.strings
        dialog = PullImageDialog(self, s)
        response = dialog.run()
        image = dialog.get_image()
        dialog.destroy()
        if response == Gtk.ResponseType.OK and image:
            self.set_status(t(s, "status_pulling", image=image))

            def worker():
                code, out, err = run_docker(["pull", image], s)
                msg = t(s, "status_image_pulled", image=image) if code == 0 else t(s, "status_error", err=err)
                GLib.idle_add(self.set_status, msg)

            threading.Thread(target=worker, daemon=True).start()

    def _on_lang_menu_item(self, item, code):
        if not item.get_active(): return
        if code == self.cfg.get("lang"): return
        self.cfg["lang"] = code
        save_config(self.cfg)
        for c, key in self._lang_options:
            if c == code:
                self._lang_label.set_text(t(self.strings, key))
                break
        new_strings = load_i18n(resolve_lang(code))
        dlg = Gtk.MessageDialog(
            transient_for=self, flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=t(new_strings, "restart_hint"),
        )
        dlg.run()
        dlg.destroy()


def main():
    win = DocktorWindow()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
