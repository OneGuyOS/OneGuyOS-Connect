"""
OneGuyOS Connect - Native Settings Tab Component
Configure Local and Cloud AI endpoints, network discovery, and connection options.
100% native GTK4/Libadwaita.
"""

from datetime import datetime
import shutil
import subprocess
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from backend.ai_manager import AIProviderManager


class SettingsView(Gtk.Box):
    def __init__(
        self,
        backend,
        show_toast_fn,
        on_settings_changed_fn=None,
        ai_manager=None,
        on_back_fn=None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.backend = backend
        self.show_toast = show_toast_fn
        self.on_settings_changed = on_settings_changed_fn
        self.on_back = on_back_fn
        # Share the chat view's manager when embedded so both controls always
        # reflect the same in-memory provider list and active selection.
        self.ai_manager = ai_manager or AIProviderManager()

        self.set_margin_top(14)
        self.set_margin_bottom(14)
        self.set_margin_start(16)
        self.set_margin_end(16)

        if self.on_back:
            btn_back = Gtk.Button(label="Back to AI Chat")
            btn_back.set_icon_name("go-previous-symbolic")
            btn_back.set_halign(Gtk.Align.START)
            btn_back.add_css_class("oneguy-secondary-btn")
            btn_back.connect("clicked", lambda _button: self.on_back())
            self.append(btn_back)

        # Scroller for settings
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.append(scroller)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        scroller.set_child(content)

        # --- Section 1: AI Provider Configuration ---
        ai_pref_group = Adw.PreferencesGroup()
        ai_pref_group.set_title("Installed AI Providers")
        ai_pref_group.set_description(
            "Select, edit, add, or remove local and cloud models available to AI Chat."
        )
        content.append(ai_pref_group)

        # Active Provider Selector Row
        self.active_row = Adw.ComboRow()
        self.active_row.set_title("Installed providers")
        self.update_provider_list()
        self.active_row.connect("notify::selected", self.on_active_model_selected)
        ai_pref_group.add(self.active_row)

        # Provider Editor Card
        editor_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        editor_card.add_css_class("oneguy-card")
        editor_card.set_margin_top(6)

        lbl_edit = Gtk.Label(label="Edit Selected / Add New Provider")
        lbl_edit.set_halign(Gtk.Align.START)
        lbl_edit.add_css_class("tile-label")
        editor_card.append(lbl_edit)

        # Form Fields
        self.entry_name = Gtk.Entry(placeholder_text="Display Name (e.g. Local Ollama, LM Studio, OpenRouter)")
        editor_card.append(self.entry_name)

        self.entry_endpoint = Gtk.Entry(placeholder_text="Endpoint URL (e.g. http://127.0.0.1:11434/v1/chat/completions)")
        editor_card.append(self.entry_endpoint)

        self.entry_model = Gtk.Entry(placeholder_text="Model ID (e.g. llama3, mistral, gpt-4o, claude-3-5)")
        editor_card.append(self.entry_model)

        self.entry_key = Gtk.PasswordEntry()
        self.entry_key.set_show_peek_icon(True)
        self.entry_key.set_property("placeholder-text", "API Key (leave blank for local offline models)")
        editor_card.append(self.entry_key)

        # Buttons Row
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)

        btn_delete = Gtk.Button(label="Delete")
        btn_delete.add_css_class("oneguy-destructive-btn")
        btn_delete.connect("clicked", self.on_delete_provider)
        btn_box.append(btn_delete)

        btn_new = Gtk.Button(label="Add as New")
        btn_new.add_css_class("oneguy-secondary-btn")
        btn_new.connect("clicked", self.on_add_new_provider)
        btn_box.append(btn_new)

        btn_save = Gtk.Button(label="Save & Activate")
        btn_save.add_css_class("oneguy-primary-btn")
        btn_save.connect("clicked", self.on_save_provider)
        btn_box.append(btn_save)

        editor_card.append(btn_box)
        content.append(editor_card)

        # --- Section 2: Quick Presets ---
        preset_group = Adw.PreferencesGroup()
        preset_group.set_title("Quick AI Presets")
        preset_group.set_description("One-click templates for standard local and cloud engines.")
        content.append(preset_group)

        preset_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        
        btn_ollama = Gtk.Button(label="Ollama (Local)")
        btn_ollama.add_css_class("oneguy-secondary-btn")
        btn_ollama.connect("clicked", lambda b: self.load_preset("Local Qwen 2.5 3B", "http://127.0.0.1:11434/v1/chat/completions", "qwen2.5:3b", ""))
        preset_box.append(btn_ollama)

        btn_lmstudio = Gtk.Button(label="LM Studio (Local)")
        btn_lmstudio.add_css_class("oneguy-secondary-btn")
        btn_lmstudio.connect("clicked", lambda b: self.load_preset("Local LM Studio", "http://127.0.0.1:1234/v1/chat/completions", "default", ""))
        preset_box.append(btn_lmstudio)

        btn_openrouter = Gtk.Button(label="OpenRouter (Cloud)")
        btn_openrouter.add_css_class("oneguy-secondary-btn")
        btn_openrouter.connect("clicked", lambda b: self.load_preset("Cloud OpenRouter", "https://openrouter.ai/api/v1/chat/completions", "liquid/lfm-2.5-2.6b:free", ""))
        preset_box.append(btn_openrouter)

        preset_group.add(preset_box)

        # --- Section 3: Connection & Protocol Controls ---
        conn_group = Adw.PreferencesGroup()
        conn_group.set_title("Network &amp; Discovery")
        content.append(conn_group)

        row_direct_info = Adw.ActionRow()
        row_direct_info.set_title("Wi-Fi Direct P2P Hotspot")
        row_direct_info.set_subtitle("Manage a configured OneGuyOS-Direct helper")
        
        btn_start_direct = Gtk.Button(label="Start AP")
        btn_start_direct.add_css_class("oneguy-primary-btn")
        btn_start_direct.set_valign(Gtk.Align.CENTER)
        btn_start_direct.connect("clicked", self.on_start_direct)
        row_direct_info.add_suffix(btn_start_direct)

        btn_stop_direct = Gtk.Button(label="Stop AP")
        btn_stop_direct.add_css_class("oneguy-secondary-btn")
        btn_stop_direct.set_valign(Gtk.Align.CENTER)
        btn_stop_direct.connect("clicked", self.on_stop_direct)
        row_direct_info.add_suffix(btn_stop_direct)

        conn_group.add(row_direct_info)

        # Load currently active provider into form
        self.populate_form_from_active()

    def on_start_direct(self, btn):
        helper = shutil.which("oneguyos-direct")
        if not helper:
            self.show_toast("OneGuyOS-Direct helper is not installed")
            return
        result = subprocess.run([helper, "start"], check=False)
        self.show_toast("Direct hotspot started" if result.returncode == 0 else "Could not start direct hotspot")

    def on_stop_direct(self, btn):
        helper = shutil.which("oneguyos-direct")
        if not helper:
            self.show_toast("OneGuyOS-Direct helper is not installed")
            return
        result = subprocess.run([helper, "stop"], check=False)
        self.show_toast("Direct hotspot stopped" if result.returncode == 0 else "Could not stop direct hotspot")

    def update_provider_list(self):
        self._updating_providers = True
        try:
            providers = self.ai_manager.providers
            names = [f"[{p.get('type').upper()}] {p.get('name')} ({p.get('model')})" for p in providers]
            str_list = Gtk.StringList.new(names)
            self.active_row.set_model(str_list)

            # Set selection
            for i, p in enumerate(providers):
                if p.get("active"):
                    self.active_row.set_selected(i)
                    break
        finally:
            self._updating_providers = False

    def populate_form_from_active(self):
        active = self.ai_manager.get_active_provider()
        if active:
            self.entry_name.set_text(active.get("name", ""))
            self.entry_endpoint.set_text(active.get("endpoint", ""))
            self.entry_model.set_text(active.get("model", ""))
            self.entry_key.set_text(active.get("api_key", ""))

    def on_active_model_selected(self, row, param):
        if getattr(self, "_updating_providers", False):
            return
        idx = row.get_selected()
        if 0 <= idx < len(self.ai_manager.providers):
            p = self.ai_manager.providers[idx]
            self.ai_manager.set_active_provider(p.get("id"))
            self.populate_form_from_active()
            self.show_toast(f"Active AI: {p.get('name')}")
            if self.on_settings_changed:
                self.on_settings_changed()

    def load_preset(self, name: str, endpoint: str, model: str, key: str):
        self.entry_name.set_text(name)
        self.entry_endpoint.set_text(endpoint)
        self.entry_model.set_text(model)
        self.entry_key.set_text(key)
        self.show_toast(f"Loaded {name} preset. Click 'Save & Activate' to apply.")

    def on_save_provider(self, btn):
        name = self.entry_name.get_text().strip() or "Custom AI"
        endpoint = self.entry_endpoint.get_text().strip()
        model = self.entry_model.get_text().strip() or "default"
        api_key = self.entry_key.get_text().strip()

        if not endpoint:
            self.show_toast("Endpoint URL cannot be empty")
            return

        active = self.ai_manager.get_active_provider() or {}
        pid = active.get("id", f"prov_{int(datetime.now().timestamp())}")
        p_type = "local" if ("127.0.0.1" in endpoint or "localhost" in endpoint) else "cloud"

        item = {
            "id": pid,
            "name": name,
            "type": p_type,
            "endpoint": endpoint,
            "model": model,
            "api_key": api_key,
            "active": True
        }
        self.ai_manager.add_or_update_provider(item)
        self.ai_manager.set_active_provider(pid)
        self.update_provider_list()
        self.show_toast(f"Saved & Activated {name}")
        if self.on_settings_changed:
            self.on_settings_changed()

    def on_add_new_provider(self, btn):
        name = self.entry_name.get_text().strip() or "New AI Provider"
        endpoint = self.entry_endpoint.get_text().strip()
        model = self.entry_model.get_text().strip() or "default"
        api_key = self.entry_key.get_text().strip()

        if not endpoint:
            self.show_toast("Endpoint URL cannot be empty")
            return

        pid = f"prov_{int(datetime.now().timestamp())}"
        p_type = "local" if ("127.0.0.1" in endpoint or "localhost" in endpoint) else "cloud"

        item = {
            "id": pid,
            "name": name,
            "type": p_type,
            "endpoint": endpoint,
            "model": model,
            "api_key": api_key,
            "active": True
        }
        self.ai_manager.add_or_update_provider(item)
        self.ai_manager.set_active_provider(pid)
        self.update_provider_list()
        self.show_toast(f"Created & Activated {name}")
        if self.on_settings_changed:
            self.on_settings_changed()

    def on_delete_provider(self, btn):
        active = self.ai_manager.get_active_provider()
        if not active:
            self.show_toast("No provider selected to delete")
            return
        if len(self.ai_manager.providers) <= 1:
            self.show_toast("Cannot delete the only remaining provider")
            return

        name = active.get("name")
        self.ai_manager.delete_provider(active.get("id"))
        self.update_provider_list()
        self.populate_form_from_active()
        self.show_toast(f"Deleted {name}")
        if self.on_settings_changed:
            self.on_settings_changed()
