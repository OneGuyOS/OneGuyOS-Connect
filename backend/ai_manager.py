"""
OneGuyOS Connect - Native AI Provider Manager
Supports standard Local (Ollama, LM Studio, LocalAI) and Cloud (OpenRouter, OpenAI, Groq, Anthropic, Gemini).
No web wraps, no Snap, no Flatpak - 100% native Python/GTK4.
"""

import os
import json
import urllib.request
import urllib.error
from typing import Dict, List, Optional

CONFIG_DIR = os.path.expanduser("~/.config/oneguyos-connect")
CONFIG_FILE = os.path.join(CONFIG_DIR, "ai_providers.json")

DEFAULT_PROVIDERS = [
    {
        "id": "local_ollama",
        "name": "Local Qwen 2.5 3B",
        "type": "local",
        "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
        "model": "qwen2.5:3b",
        "api_key": "",
        "active": False
    },
    {
        "id": "local_lmstudio",
        "name": "Local LM Studio",
        "type": "local",
        "endpoint": "http://127.0.0.1:1234/v1/chat/completions",
        "model": "default",
        "api_key": "",
        "active": False
    },
    {
        "id": "cloud_openrouter",
        "name": "Cloud OpenRouter",
        "type": "cloud",
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "api_key": "",
        "active": False
    },
    {
        "id": "cloud_custom",
        "name": "Custom OpenAI-Compatible",
        "type": "custom",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-4o-mini",
        "api_key": "",
        "active": False
    }
]


class AIProviderManager:
    def __init__(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.providers = self.load_providers()

    def load_providers(self) -> List[dict]:
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list) and len(data) > 0:
                        return data
            except Exception as e:
                print(f"[AIProviderManager] Error loading config: {e}")
        self.save_providers(DEFAULT_PROVIDERS)
        return DEFAULT_PROVIDERS

    def save_providers(self, providers: List[dict]):
        self.providers = providers
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(providers, f, indent=2)
        except Exception as e:
            print(f"[AIProviderManager] Error saving config: {e}")

    def get_active_provider(self) -> Optional[dict]:
        for p in self.providers:
            if p.get("active"):
                return p
        return None

    def set_active_provider(self, provider_id: str):
        for p in self.providers:
            p["active"] = (p.get("id") == provider_id)
        self.save_providers(self.providers)

    def add_or_update_provider(self, provider_dict: dict):
        found = False
        for i, p in enumerate(self.providers):
            if p.get("id") == provider_dict.get("id"):
                self.providers[i] = provider_dict
                found = True
                break
        if not found:
            self.providers.append(provider_dict)
        self.save_providers(self.providers)

    def delete_provider(self, provider_id: str):
        self.providers = [p for p in self.providers if p.get("id") != provider_id]
        if self.providers and not any(p.get("active") for p in self.providers):
            self.providers[0]["active"] = True
        self.save_providers(self.providers)

    def query_provider(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        provider_id: Optional[str] = None,
        messages: Optional[list] = None,
        allow_local_fallback: bool = True,
    ) -> str:
        active = next(
            (p for p in self.providers if p.get("id") == provider_id), None
        ) if provider_id else self.get_active_provider()
        if not active:
            return "No AI provider is active. Click 'Configure AI' to select or add a Local/Cloud endpoint."

        endpoint = active.get("endpoint", "").strip()
        if endpoint and not endpoint.endswith("/chat/completions"):
            endpoint = endpoint.rstrip("/") + "/chat/completions"
        model = active.get("model", "default").strip()
        api_key = active.get("api_key", "").strip()

        if not endpoint:
            return f"Error: Endpoint URL missing for {active.get('name')}."

        sys_msg = {"role": "system", "content": system_prompt or "You are a concise AI assistant integrated natively into OneGuyOS."}
        if messages:
            chat_messages = [sys_msg] + [m for m in messages if m.get("role") in ("user", "assistant")]
        else:
            chat_messages = [sys_msg, {"role": "user", "content": prompt}]

        payload = {
            "model": model,
            "messages": chat_messages,
            "temperature": 0.7
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "OneGuyOS-Connect/1.0"
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            if provider_id == "local_ollama" or ":11434" in endpoint:
                # Use Ollama's native route for predictable local inference.
                ollama_endpoint = "http://127.0.0.1:11434/api/chat"
                payload = {
                    "model": model,
                    "messages": chat_messages,
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.5, "num_predict": 512},
                }
                req_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(ollama_endpoint, data=req_data, headers=headers, method="POST")
                timeout = 300
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    return result.get("message", {}).get("content", "").strip() or "Received empty response from local model."

            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(endpoint, data=req_data, headers=headers, method="POST")
            # Local CPU inference can take much longer than a cloud round trip.
            timeout = 300 if ("127.0.0.1" in endpoint or "localhost" in endpoint) else 60
            # Some free OpenRouter providers occasionally return a completed
            # response with an empty content field. Retry once before showing
            # an error so a transient provider slot does not look like silence.
            for attempt in range(2):
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    choices = result.get("choices", [])
                    if choices:
                        msg = choices[0].get("message", {}).get("content", "")
                        if isinstance(msg, str) and msg.strip():
                            return msg.strip()
                if attempt == 0:
                    continue
            return "Cloud model returned an empty response twice. Try again or choose another cloud model."
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")
                err_json = json.loads(err_body)
                err_msg = err_json.get("error", {}).get("message", err_body)
            except Exception:
                err_msg = e.reason

            # Free cloud routes can disappear or hit provider quotas without
            # warning.  Keep the chat usable by falling back to the installed
            # local model for availability errors; authentication errors still
            # remain visible so a bad key is never hidden.
            if (
                allow_local_fallback
                and e.code in (404, 408, 429, 500, 502, 503, 504)
                and active.get("type") == "cloud"
            ):
                local = next(
                    (
                        provider
                        for provider in self.providers
                        if provider.get("id") == "local_ollama"
                    ),
                    None,
                )
                if local:
                    local_reply = self.query_provider(
                        prompt,
                        system_prompt=system_prompt,
                        provider_id=local.get("id"),
                        messages=messages,
                        allow_local_fallback=False,
                    )
                    if not local_reply.startswith(("API Error", "Connection Failed", "Request Error")):
                        return "Cloud provider unavailable; answered locally.\n\n" + local_reply

            return f"API Error ({e.code}): {err_msg}"
        except urllib.error.URLError as e:
            return f"Connection Failed: Ensure {active.get('name')} endpoint is running at {endpoint}."
        except Exception as e:
            return f"Request Error: {str(e)}"
