"""
OneGuyOS Connect - Native AI Chat Tab Component
Pure native GTK4/Libadwaita interface with Local/Cloud AI selector and configuration.
No web wraps, no Snap, no Flatpak.
"""

import json
import os
import subprocess
import threading
from datetime import datetime
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Pango

from backend.ai_manager import AIProviderManager

CHAT_HISTORY_DIR = os.path.expanduser("~/Documents/OneGuyOS-Chat-History")
SHARED_ROOM_STATE = os.path.join(CHAT_HISTORY_DIR, "shared-ai-room.json")
SHARED_ROOM_TRANSCRIPT = os.path.join(CHAT_HISTORY_DIR, "Shared-AI-Room.md")
CHAT_MODE_PREFS = os.path.expanduser("~/.config/oneguyos-connect/chat_mode.json")
SHARED_ROOM_SYSTEM_PROMPT = """You are participating in the OneGuyOS Shared AI Room.
The user selected multiple AI models that may answer in the same visible thread.
Prior assistant messages are labeled with the model that wrote them. Read those
messages as peer contributions, build on useful work, and clearly correct a peer
when needed. Answer the user's latest request directly and do not pretend you
wrote another model's reply. Reply only as the exact model identity assigned to
you. Never simulate, quote, or invent a reply for another room member; every
other checked model has its own worker. Keep ordinary replies under 100 words
unless the user asks for detail."""
INDIVIDUAL_CHAT_SYSTEM_PROMPT = (
    "You are a concise AI assistant integrated into OneGuyOS Connect. "
    "This is an individual conversation between you and the user."
)


class AIChatView(Gtk.Box):
    def __init__(
        self,
        backend,
        get_current_device_fn,
        show_toast_fn,
        open_provider_manager_fn=None,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.backend = backend
        self.get_current_device = get_current_device_fn
        self.show_toast = show_toast_fn
        self.open_provider_manager = open_provider_manager_fn
        self.ai_manager = AIProviderManager()
        self.conversation_log = []
        self._pending_replies = 0
        self._pending_rows = {}
        self._updating_room_members = False
        self._updating_chat_mode = False
        self.chat_mode = self._load_chat_mode()
        os.makedirs(CHAT_HISTORY_DIR, exist_ok=True)

        self.set_margin_top(10)
        self.set_margin_bottom(10)
        self.set_margin_start(14)
        self.set_margin_end(14)

        # 1. Native AI Provider Selector & History Controls Bar
        top_card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top_card.add_css_class("oneguy-card")
        top_card.set_margin_bottom(2)

        ai_icon = Gtk.Image.new_from_icon_name("avatar-default-symbolic")
        ai_icon.set_pixel_size(24)
        top_card.append(ai_icon)

        lbl_prov = Gtk.Label(label="Provider:")
        lbl_prov.add_css_class("tile-label")
        top_card.append(lbl_prov)

        # Dropdown provider selector
        self.provider_dropdown = Gtk.DropDown()
        self.update_dropdown_items()
        self.provider_dropdown.connect("notify::selected", self.on_provider_selected)
        self.provider_dropdown.set_hexpand(True)
        top_card.append(self.provider_dropdown)

        # Open Past Chats Folder Button
        btn_history = Gtk.Button(icon_name="folder-open-symbolic")
        btn_history.set_tooltip_text("Open Past Chats Folder (~/Documents/OneGuyOS-Chat-History)")
        btn_history.add_css_class("oneguy-secondary-btn")
        btn_history.connect("clicked", self.on_open_history_folder)
        top_card.append(btn_history)

        # Save Current Chat Button
        btn_save = Gtk.Button(icon_name="document-save-symbolic")
        btn_save.set_tooltip_text("Save Current Chat to History Folder")
        btn_save.add_css_class("oneguy-secondary-btn")
        btn_save.connect("clicked", self.on_save_chat)
        top_card.append(btn_save)

        # Config / Add Provider Button
        btn_config = Gtk.Button(icon_name="preferences-system-symbolic")
        btn_config.set_tooltip_text("Manage all installed AI providers")
        btn_config.add_css_class("oneguy-secondary-btn")
        btn_config.connect("clicked", self.on_configure_provider)
        top_card.append(btn_config)

        self.append(top_card)

        # Chat mode shade: the same attached pull-down behavior as the device
        # controls on the dashboard.
        mode_group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.chat_mode_button = Gtk.Button()
        self.chat_mode_button.add_css_class("oneguy-device-pull")
        self.chat_mode_button.set_tooltip_text("Pull down individual or group chat options")
        self.chat_mode_button.connect("clicked", self.on_toggle_chat_mode_panel)

        mode_handle = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mode_handle.set_halign(Gtk.Align.CENTER)
        self.chat_mode_summary = Gtk.Label()
        self.chat_mode_summary.add_css_class("oneguy-ribbon-label")
        self.chat_mode_icon = Gtk.Image.new_from_icon_name("pan-down-symbolic")
        mode_handle.append(self.chat_mode_summary)
        mode_handle.append(self.chat_mode_icon)
        self.chat_mode_button.set_child(mode_handle)
        mode_group.append(self.chat_mode_button)

        self.chat_mode_revealer = Gtk.Revealer()
        self.chat_mode_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.chat_mode_revealer.set_transition_duration(220)
        self.chat_mode_revealer.set_reveal_child(False)

        mode_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        mode_panel.add_css_class("oneguy-device-shade")
        mode_choices = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.individual_mode_check = Gtk.CheckButton(label="Individual chat")
        self.group_mode_check = Gtk.CheckButton(label="Group chat")
        self.group_mode_check.set_group(self.individual_mode_check)
        self.individual_mode_check.connect("toggled", self.on_chat_mode_changed, "individual")
        self.group_mode_check.connect("toggled", self.on_chat_mode_changed, "group")
        mode_choices.append(self.individual_mode_check)
        mode_choices.append(self.group_mode_check)
        mode_panel.append(mode_choices)

        group_hint = Gtk.Label(label="Models included in group chat")
        group_hint.set_halign(Gtk.Align.START)
        group_hint.add_css_class("dim-label")
        mode_panel.append(group_hint)

        # Multi-model room membership. Checked bots receive the same visible
        # thread and add their own attributed reply to it.
        self.room_members_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        mode_panel.append(self.room_members_box)
        self.chat_mode_revealer.set_child(mode_panel)
        mode_group.append(self.chat_mode_revealer)
        self.append(mode_group)

        self._updating_chat_mode = True
        self.individual_mode_check.set_active(self.chat_mode == "individual")
        self.group_mode_check.set_active(self.chat_mode == "group")
        self._updating_chat_mode = False
        self.update_room_members()

        # 2. Chat History Scroller
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_vexpand(True)
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.append(self.scroller)

        self.vadj = self.scroller.get_vadjustment()
        self._programmatic_scroll = False
        self._scroll_source_id = 0
        self._scroll_attempts = 0
        self._scroll_stable_ticks = 0
        self._last_scroll_target = None

        self.scroll_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.msg_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.msg_list.set_hexpand(True)
        self.scroll_content.append(self.msg_list)

        # Keep a real widget at the live edge so the final scroll target exists
        # even while a long wrapped reply is still being allocated.
        self.bottom_anchor = Gtk.Box()
        self.bottom_anchor.set_size_request(-1, 1)
        self.scroll_content.append(self.bottom_anchor)
        self.scroller.set_child(self.scroll_content)

        # Restore the shared room after an app restart. The persisted transcript
        # also gives local tooling a human-readable view of the same discussion.
        if not self._load_shared_room():
            self.add_message(
                "assistant",
                "Shared AI Room ready. Check the models that should see and answer this chat.",
                provider_name="OneGuyOS Connect",
                record=False,
            )

        # 3. Input bar (anchored at bottom)
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        input_box.set_margin_top(8)
        input_box.set_margin_bottom(4)
        input_box.set_valign(Gtk.Align.END)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Ask AI or type a prompt...")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", lambda e: self.on_send())
        input_box.append(self.entry)

        self.btn_send = Gtk.Button(label="Send")
        self.btn_send.add_css_class("oneguy-primary-btn")
        self.btn_send.connect("clicked", lambda b: self.on_send())
        input_box.append(self.btn_send)

        self.append(input_box)
        self.last_ai_message = ""

    def update_dropdown_items(self):
        self._updating_dropdown = True
        try:
            providers = self.ai_manager.providers
            names = [f"[{p.get('type').upper()}] {p.get('name')}" for p in providers]
            names.append("Manage Providers…")
            str_list = Gtk.StringList.new(names)
            self.provider_dropdown.set_model(str_list)

            # Select active index
            active_idx = 0
            for i, p in enumerate(providers):
                if p.get("active"):
                    active_idx = i
                    break
            self.provider_dropdown.set_selected(active_idx)
        finally:
            self._updating_dropdown = False
        if hasattr(self, "room_members_box"):
            self.update_room_members()

    def get_room_providers(self):
        members = [provider for provider in self.ai_manager.providers if provider.get("room_member")]
        if members:
            return members
        active = self.ai_manager.get_active_provider()
        if active:
            active["room_member"] = True
            self.ai_manager.save_providers(self.ai_manager.providers)
            return [active]
        return []

    def _load_chat_mode(self):
        try:
            with open(CHAT_MODE_PREFS, "r", encoding="utf-8") as prefs:
                mode = json.load(prefs).get("mode")
                if mode in ("individual", "group"):
                    return mode
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        return "individual"

    def _save_chat_mode(self):
        os.makedirs(os.path.dirname(CHAT_MODE_PREFS), exist_ok=True)
        try:
            with open(CHAT_MODE_PREFS, "w", encoding="utf-8") as prefs:
                json.dump({"mode": self.chat_mode}, prefs)
        except OSError as error:
            print(f"[AIChatView] Could not save chat mode: {error}")

    def on_toggle_chat_mode_panel(self, _button):
        expanded = not self.chat_mode_revealer.get_reveal_child()
        self.chat_mode_revealer.set_reveal_child(expanded)
        self.chat_mode_icon.set_from_icon_name(
            "pan-up-symbolic" if expanded else "pan-down-symbolic"
        )

    def on_chat_mode_changed(self, check, mode):
        if self._updating_chat_mode or not check.get_active():
            return
        self.chat_mode = mode
        self._save_chat_mode()
        self.update_room_members()

    def update_room_members(self):
        self._updating_room_members = True
        try:
            while child := self.room_members_box.get_first_child():
                self.room_members_box.remove(child)

            members = self.get_room_providers()
            member_ids = {provider.get("id") for provider in members}
            for provider in self.ai_manager.providers:
                label = f"[{provider.get('type', 'bot').upper()}] {provider.get('name', 'Unnamed bot')}"
                check = Gtk.CheckButton(label=label)
                check.set_active(provider.get("id") in member_ids)
                check.connect("toggled", self.on_room_member_toggled, provider.get("id"))
                self.room_members_box.append(check)
            count = len(members)
            suffix = "bot" if count == 1 else "bots"
            active = self.ai_manager.get_active_provider() or {}
            if self.chat_mode == "group":
                summary = f"Group chat • {count} {suffix} seeing all"
            else:
                summary = f"Individual chat • {active.get('name', 'No bot selected')}"
            self.chat_mode_summary.set_text(summary)
            self.room_members_box.set_sensitive(self.chat_mode == "group")
        finally:
            self._updating_room_members = False

    def on_room_member_toggled(self, check, provider_id):
        if self._updating_room_members:
            return
        provider = next(
            (item for item in self.ai_manager.providers if item.get("id") == provider_id),
            None,
        )
        if not provider:
            return
        provider["room_member"] = check.get_active()
        if not any(item.get("room_member") for item in self.ai_manager.providers):
            provider["room_member"] = True
            check.set_active(True)
            self.show_toast("Keep at least one bot in the shared chat")
        self.ai_manager.save_providers(self.ai_manager.providers)
        self.update_room_members()

    def on_provider_selected(self, dropdown, param):
        if getattr(self, "_updating_dropdown", False):
            return
        idx = dropdown.get_selected()
        if idx == len(self.ai_manager.providers):
            self.update_dropdown_items()
            if self.open_provider_manager:
                self.open_provider_manager()
            return
        if 0 <= idx < len(self.ai_manager.providers):
            target = self.ai_manager.providers[idx]
            self.ai_manager.set_active_provider(target.get("id"))
            target["room_member"] = True
            self.ai_manager.save_providers(self.ai_manager.providers)
            self.update_room_members()
            self.show_toast(f"Active AI: {target.get('name')}")

    def on_configure_provider(self, btn):
        if self.open_provider_manager:
            self.open_provider_manager()
            return

        # Fallback for embedding the chat view without the full AI workspace.
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading="Configure AI Provider",
            body="Set up a Local (Ollama, LM Studio) or Cloud (OpenRouter, OpenAI) endpoint.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save Provider")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)

        # Form fields
        active = self.ai_manager.get_active_provider() or {}

        entry_name = Gtk.Entry(placeholder_text="Provider Name (e.g. Local Ollama)")
        entry_name.set_text(active.get("name", ""))
        box.append(entry_name)

        entry_endpoint = Gtk.Entry(placeholder_text="Endpoint URL (e.g. http://127.0.0.1:11434/v1/chat/completions)")
        entry_endpoint.set_text(active.get("endpoint", ""))
        box.append(entry_endpoint)

        entry_model = Gtk.Entry(placeholder_text="Model Name (e.g. llama3, mistral, gpt-4o)")
        entry_model.set_text(active.get("model", ""))
        box.append(entry_model)

        entry_key = Gtk.PasswordEntry()
        entry_key.set_show_peek_icon(True)
        entry_key.set_property("placeholder-text", "API Key (leave blank for local)")
        entry_key.set_text(active.get("api_key", ""))
        box.append(entry_key)

        dialog.set_extra_child(box)

        def on_response(dlg, response):
            if response == "save":
                name = entry_name.get_text().strip() or "Custom Provider"
                endpoint = entry_endpoint.get_text().strip()
                model = entry_model.get_text().strip() or "default"
                api_key = entry_key.get_text().strip()

                if not endpoint:
                    self.show_toast("Endpoint URL cannot be empty")
                    return

                p_type = "local" if ("127.0.0.1" in endpoint or "localhost" in endpoint) else "cloud"
                pid = active.get("id", f"prov_{int(datetime.now().timestamp())}")

                new_prov = {
                    "id": pid,
                    "name": name,
                    "type": p_type,
                    "endpoint": endpoint,
                    "model": model,
                    "api_key": api_key,
                    "active": True
                }
                self.ai_manager.add_or_update_provider(new_prov)
                self.ai_manager.set_active_provider(pid)
                self.update_dropdown_items()
                self.show_toast(f"Saved & Activated {name}")

        dialog.connect("response", on_response)
        dialog.present()

    def add_message(self, sender: str, text: str, provider_name=None, record=True, timestamp=None):
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        row.add_css_class("oneguy-card")
        row.set_margin_bottom(4)
        row.set_hexpand(True)

        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        name_label = Gtk.Label()
        if sender == "user":
            name_label.set_markup("<b>You</b>")
        else:
            name_label.set_markup(f"<b>{GLib.markup_escape_text(provider_name or 'OneGuyOS AI')}</b>")
        name_label.set_halign(Gtk.Align.START)
        header_row.append(name_label)

        message_time = timestamp or datetime.now().isoformat()
        time_lbl = Gtk.Label(label=message_time[11:16])
        time_lbl.add_css_class("oneguy-subtitle")
        time_lbl.set_halign(Gtk.Align.START)
        header_row.append(time_lbl)

        row.append(header_row)

        content = Gtk.Label(label=text)
        content.set_wrap(True)
        content.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        content.set_halign(Gtk.Align.START)
        content.set_xalign(0)
        content.set_hexpand(True)
        content.set_selectable(True)
        row.append(content)

        self.msg_list.append(row)
        if record:
            self.conversation_log.append({
                "timestamp": message_time,
                "sender": sender,
                "provider": provider_name if sender == "assistant" else None,
                "mode": self.chat_mode,
                "text": text,
            })
            self._write_shared_room()

        if sender == "assistant" and record:
            self.last_ai_message = text

        # A new message always becomes visible. Force GTK to recalculate and
        # redraw the wrapped text, then follow the changing bottom until the
        # allocation has remained stable for several frames.
        row.queue_resize()
        self.msg_list.queue_resize()
        self.scroll_content.queue_resize()
        self.scroller.queue_resize()
        self.scroller.queue_draw()
        self._schedule_scroll_follow()
        GLib.idle_add(self._schedule_scroll_follow)
        return row

    def _load_shared_room(self):
        try:
            with open(SHARED_ROOM_STATE, "r", encoding="utf-8") as saved:
                messages = json.load(saved)
            if not isinstance(messages, list):
                return False
        except (OSError, json.JSONDecodeError):
            return False

        self.conversation_log = [message for message in messages[-80:] if isinstance(message, dict)]
        for message in self.conversation_log[-30:]:
            self.add_message(
                message.get("sender", "assistant"),
                message.get("text", ""),
                provider_name=message.get("provider"),
                record=False,
                timestamp=message.get("timestamp"),
            )
        return bool(self.conversation_log)

    def _write_shared_room(self):
        state_tmp = SHARED_ROOM_STATE + ".tmp"
        transcript_tmp = SHARED_ROOM_TRANSCRIPT + ".tmp"
        try:
            with open(state_tmp, "w", encoding="utf-8") as saved:
                json.dump(self.conversation_log[-80:], saved, indent=2)
            os.replace(state_tmp, SHARED_ROOM_STATE)

            with open(transcript_tmp, "w", encoding="utf-8") as transcript:
                transcript.write("# OneGuyOS Shared AI Room\n\n")
                transcript.write("Every assistant reply is attributed to the model that wrote it.\n\n")
                for message in self.conversation_log:
                    if message.get("sender") == "user":
                        speaker = "You"
                    else:
                        speaker = message.get("provider") or "OneGuyOS AI"
                    when = message.get("timestamp", "")[11:16]
                    transcript.write(f"## {speaker} ({when})\n\n{message.get('text', '')}\n\n")
            os.replace(transcript_tmp, SHARED_ROOM_TRANSCRIPT)
        except OSError as error:
            print(f"[AIChatView] Could not save shared room: {error}")

    def on_open_history_folder(self, btn):
        os.makedirs(CHAT_HISTORY_DIR, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", CHAT_HISTORY_DIR])
            self.show_toast("Opened Past Chats Folder")
        except Exception as e:
            self.show_toast(f"Could not open folder: {e}")

    def on_save_chat(self, btn):
        if not self.conversation_log:
            self.show_toast("No messages to save yet")
            return

        os.makedirs(CHAT_HISTORY_DIR, exist_ok=True)
        filename = f"chat-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.md"
        filepath = os.path.join(CHAT_HISTORY_DIR, filename)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"# OneGuyOS AI Chat Log - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                active = self.ai_manager.get_active_provider()
                if active:
                    f.write(f"**Model / Provider:** {active.get('name')} ({active.get('model')})\n\n---\n\n")
                for m in self.conversation_log:
                    who = "### You" if m["sender"] == "user" else f"### {m.get('provider') or 'OneGuyOS AI'}"
                    f.write(f"{who} ({m['timestamp'][11:16]}):\n{m['text']}\n\n")

            self.show_toast(f"Saved chat to {filename}")
        except Exception as e:
            self.show_toast(f"Error saving chat: {e}")

    def _schedule_scroll_follow(self):
        self._scroll_attempts = 0
        self._scroll_stable_ticks = 0
        self._last_scroll_target = None
        self._scroll_bottom_once()
        if not self._scroll_source_id:
            self._scroll_source_id = GLib.timeout_add(40, self._scroll_follow_tick)
        return False

    def _scroll_follow_tick(self):
        target = self._scroll_bottom_once()
        self._scroll_attempts += 1

        if self._last_scroll_target is not None and abs(target - self._last_scroll_target) < 0.5:
            self._scroll_stable_ticks += 1
        else:
            self._scroll_stable_ticks = 0
        self._last_scroll_target = target

        # Run for at least 400 ms, then stop once the layout has settled. The
        # two-second ceiling covers unusually long wrapped replies.
        settled = self._scroll_attempts >= 10 and self._scroll_stable_ticks >= 5
        if settled or self._scroll_attempts >= 50:
            self._scroll_source_id = 0
            self.bottom_anchor.queue_draw()
            self.scroller.queue_draw()
            return False
        return True

    def _scroll_bottom_once(self):
        adj = self.scroller.get_vadjustment()
        if not adj:
            return 0.0
        target = max(adj.get_lower(), adj.get_upper() - adj.get_page_size())
        self._programmatic_scroll = True
        try:
            adj.set_value(target)
        finally:
            self._programmatic_scroll = False
        return target

    def on_send(self):
        text = self.entry.get_text().strip()
        if not text:
            return

        self.entry.set_text("")
        self.add_message("user", text)
        self.btn_send.set_sensitive(False)

        active = self.ai_manager.get_active_provider()
        active_name = active.get("name") if active else None

        # Group mode includes attributed replies from every checked bot.
        # Individual mode keeps peer replies out of the selected bot's context.
        thread_messages = []
        for m in self.conversation_log[-60:]:
            role = "user" if m.get("sender") == "user" else "assistant"
            if (
                self.chat_mode == "individual"
                and role == "assistant"
                and m.get("provider") != active_name
            ):
                continue
            content = m.get("text", "")
            if role == "assistant" and self.chat_mode == "group":
                content = f"[Reply from {m.get('provider') or 'OneGuyOS AI'}]\n{content}"
            thread_messages.append({"role": role, "content": content})

        if self.chat_mode == "group":
            targets = [dict(provider) for provider in self.get_room_providers()]
        else:
            targets = [dict(active)] if active else []
        if not targets:
            self.btn_send.set_sensitive(True)
            self.show_toast("Select at least one bot for the shared chat")
            return
        self._pending_replies = len(targets)
        local_is_in_group = any(
            provider.get("id") == "local_ollama" for provider in targets
        )
        roster = "\n".join(
            f"- [{provider.get('type', 'bot').upper()}] {provider.get('name')} ({provider.get('model')})"
            for provider in targets
        )
        for provider in targets:
            waiting_text = (
                "Thinking locally…"
                if provider.get("type") == "local"
                else "Waiting for cloud response…"
            )
            self._pending_rows[provider.get("id")] = self.add_message(
                "assistant",
                waiting_text,
                provider_name=provider.get("name"),
                record=False,
            )
            if self.chat_mode == "group":
                system_prompt = (
                    SHARED_ROOM_SYSTEM_PROMPT
                    + "\n\nExact room roster:\n"
                    + roster
                    + f"\n\nYou are [{provider.get('type', 'bot').upper()}] "
                    + f"{provider.get('name')} ({provider.get('model')})."
                )
                if provider.get("type") == "local":
                    system_prompt += (
                        " Your inference runs only through the loopback Ollama service; "
                        "you are not being sent to a cloud endpoint."
                    )
            else:
                system_prompt = INDIVIDUAL_CHAT_SYSTEM_PROMPT
            threading.Thread(
                target=self._query_worker,
                args=(
                    text,
                    thread_messages,
                    provider.get("id"),
                    provider.get("name"),
                    not (local_is_in_group and provider.get("type") == "cloud"),
                    system_prompt,
                ),
                daemon=True,
            ).start()

    def _query_worker(
        self,
        prompt,
        thread_messages,
        provider_id,
        provider_name,
        allow_local_fallback,
        system_prompt,
    ):
        if provider_id == "local_ollama":
            thread_messages = self._recent_context(thread_messages, 2400)
        resp = self.ai_manager.query_provider(
            prompt,
            system_prompt=system_prompt,
            provider_id=provider_id,
            messages=thread_messages,
            allow_local_fallback=allow_local_fallback,
        )
        if resp.startswith("Cloud provider unavailable; answered locally."):
            local = next(
                (item for item in self.ai_manager.providers if item.get("id") == "local_ollama"),
                None,
            )
            if local:
                provider_name = f"{local.get('name')} (cloud fallback)"
        elif resp.startswith("API Error") and not allow_local_fallback:
            resp = f"{provider_name} is temporarily unavailable. The local bot still received the shared chat."
        GLib.idle_add(self._post_reply, resp, provider_name, provider_id)

    @staticmethod
    def _recent_context(messages, max_chars):
        """Keep the newest complete turns within a local-model latency budget."""
        selected = []
        used = 0
        for message in reversed(messages):
            content = message.get("content", "")
            remaining = max_chars - used
            if remaining <= 0:
                break
            if len(content) > remaining:
                content = content[-remaining:]
            selected.append({"role": message.get("role"), "content": content})
            used += len(content)
        return list(reversed(selected))

    def _post_reply(self, resp, provider_name, provider_id):
        pending_row = self._pending_rows.pop(provider_id, None)
        if pending_row and pending_row.get_parent() is self.msg_list:
            self.msg_list.remove(pending_row)

        is_availability_notice = resp.endswith(
            "is temporarily unavailable. The local bot still received the shared chat."
        )
        self.add_message(
            "assistant",
            resp,
            provider_name=provider_name,
            record=not is_availability_notice,
        )
        self._pending_replies = max(0, self._pending_replies - 1)
        if self._pending_replies == 0:
            self.btn_send.set_sensitive(True)

    def on_push_latest_to_phone(self, btn):
        dev = self.get_current_device()
        if not dev:
            self.show_toast("No phone connected to sync with")
            return

        if not self.last_ai_message:
            self.show_toast("No message to sync")
            return

        self.backend.share_text(dev.id, self.last_ai_message)
        self.backend.send_clipboard(dev.id, self.last_ai_message)
        self.show_toast(f"Synced AI response to {dev.name}")
