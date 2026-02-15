import logging
import threading
import time
import requests
import base64
import json
import os
import re

import octoprint.plugin

logger = logging.getLogger("octoprint.plugins.ai_printmon")


class AIPrintMonPlugin(
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.EventHandlerPlugin,
    octoprint.plugin.SimpleApiPlugin,
    octoprint.plugin.StartupPlugin,
):
    """Core implementation for the OctoPrint AI Print Monitor plugin."""

    def __init__(self):
        self._monitoring = False
        self._timer = None
        self._timer_interval = 300  # seconds default (5 minutes)
        self._rounds = 3
        self._round_delay = 3
        self._cooldown = 15 * 60
        self._last_alert = 0
        self._snapshot_url = None
        self._consecutive_errors = 0

    # --- Settings / lifecycle ------------------------------------------------
    def get_settings_defaults(self):
        return {
            "provider_preset": "OpenAI",
            "api_endpoint": "https://api.openai.com/v1/chat/completions",
            "api_key": "",
            "model": "gpt-4o",
            "monitor_enabled": True,
            "snapshot_url": "http://localhost:8080/?action=snapshot",
            "interval_minutes": 5,
            "rounds": 3,
            "round_delay": 3,
            "cooldown_minutes": 15,
            "failure_rules": [
                {"threshold": "1/3", "action": "nothing"},
                {"threshold": "2/3", "action": "warn"},
                {"threshold": "3/3", "action": "cancel_stop_queue"},
            ],
            "system_prompt": (
                'You are a 3D print failure detection system. You will receive an image'
                ' of a 3D print in progress captured from a webcam. Analyze the image'
                ' for signs of failure and respond with ONLY a JSON object: '
                '{"status": "ok"} or {"status": "fail", "reason": "..."}.'
            ),
        }

    def on_after_startup(self):
        logger.info("AI Print Monitor plugin started")
        # In the real plugin, read settings and attach event hooks here.

    def on_after_startup(self):
        logger.info("AI Print Monitor plugin started")
        # Load settings into runtime state
        self.apply_settings(self._settings.get_all_hierarchy())

    def apply_settings(self, s):
        """Apply validated settings to runtime state (update timer, rounds, etc.)."""
        # map settings to internal state
        self._timer_interval = int(s.get("interval_minutes", 5)) * 60
        self._rounds = int(s.get("rounds", 3))
        self._round_delay = int(s.get("round_delay", 3))
        self._cooldown = int(s.get("cooldown_minutes", 15)) * 60
        self._snapshot_url = s.get("snapshot_url")

        enabled = bool(s.get("monitor_enabled", True))
        if enabled and not self._monitoring:
            logger.info("Settings applied: starting monitoring per saved settings")
            self.start_monitoring()
        elif not enabled and self._monitoring:
            logger.info("Settings applied: stopping monitoring per saved settings")
            self.stop_monitoring()

    # --- Settings plugin hooks ---------------------------------------------
    def on_settings_save(self, data):
        # Called when settings are saved in OctoPrint
        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
        # Update runtime state immediately
        self.apply_settings(self._settings.get_all_hierarchy())

    def get_template_configs(self):
        return [
            dict(type="settings", custom_bindings=True)
        ]

    def get_assets(self):
        return dict(
            js=["js/ai_printmon.js"],
            css=["css/ai_printmon.css"]
        )

    # --- API commands exposed to OctoPrint frontend ------------------------
    def get_api_commands(self):
        # Expose a simple API used by the settings UI
        return {"test_connection": [], "get_preset": ["preset"], "apply_settings": ["settings"]}

    def on_api_command(self, command, data):
        # Handle simple API commands from the frontend
        try:
            logger.info("API command received: %s data=%s", command, data)
            if command == "test_connection":
                endpoint = data.get("endpoint") if isinstance(data, dict) else None
                api_key = data.get("api_key") if isinstance(data, dict) else None
                model = data.get("model") if isinstance(data, dict) else None

                # Server-side validation
                if not endpoint or not isinstance(endpoint, str) or not re.match(r"^https?://", endpoint):
                    return {"success": False, "message": "Invalid endpoint URL"}

                ok, info = self.send_text_test_to_llm(endpoint=endpoint, api_key=api_key, model=model)
                if ok:
                    logger.info("Test connection OK: %s", info)
                    return {"success": True, "message": info}
                else:
                    logger.warning("Test connection failed: %s", info)
                    return {"success": False, "message": info}
            elif command == "get_preset":
                preset = None
                if isinstance(data, dict):
                    preset = data.get("preset")
                if not preset:
                    return {"success": False, "message": "missing preset"}
                p = self.apply_provider_preset(preset)
                if not p:
                    return {"success": False, "message": "unknown preset"}
                # Return a copy and log which preset was requested
                logger.info("Providing preset '%s' to frontend", preset)
                return {"success": True, "preset": p}

            elif command == "apply_settings":
                settings = data.get("settings") if isinstance(data, dict) else None
                if not settings:
                    return {"success": False, "message": "missing settings"}
                # OctoPrint's SettingsPlugin handles persistence if we call save()
                # But here we want to apply to runtime immediately
                self.apply_settings(settings)
                # Persist those specific values into OctoPrint settings
                for k, v in settings.items():
                    self._settings.set([k], v)
                self._settings.save()
                return {"success": True, "message": "settings applied and saved"}

            return {"unknown": True}
        except Exception:
            logger.exception("Error in on_api_command")
            return {"success": False, "message": "internal error"}

    def apply_provider_preset(self, preset_name):
        presets = {
            "Ollama": {"endpoint": "http://localhost:11434/v1/chat/completions", "api_key": "", "model": "llava:latest"},
            "OpenAI": {"endpoint": "https://api.openai.com/v1/chat/completions", "api_key": "", "model": "gpt-4o"},
            "Google Gemini": {"endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "api_key": "", "model": "gemini-2.0"},
        }
        return presets.get(preset_name)

    def send_text_test_to_llm(self, endpoint=None, api_key=None, model=None, system_prompt=None, timeout=15):
        """Send a short test text prompt to the configured endpoint to validate connectivity.

        Returns (ok: bool, info: str)
        """
        endpoint = endpoint or self.get_settings_defaults().get("api_endpoint")
        model = model or self.get_settings_defaults().get("model")
        api_key = api_key or self.get_settings_defaults().get("api_key")
        system_prompt = system_prompt or self.get_settings_defaults().get("system_prompt")

        if not endpoint:
            return False, "No endpoint configured"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Test connectivity from AI Print Monitor plugin. Reply with a short JSON object like {\"status\": \"ok\"}."},
            ],
            "n": 1,
        }

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            t0 = time.time()
            resp = requests.post(endpoint, headers=headers, data=json.dumps(payload), timeout=timeout)
            latency = time.time() - t0
            resp.raise_for_status()
            # Best-effort parse
            try:
                j = resp.json()
                parsed = self.parse_llm_response(j)
                if parsed and parsed.get("status") == "ok":
                    return True, f"ok (latency {latency:.2f}s)"
                # If parsed but not ok, still consider connection successful but note response
                return True, f"connected, LLM responded: {parsed} (latency {latency:.2f}s)"
            except Exception:
                return True, f"connected (non-JSON response) latency {latency:.2f}s"
        except Exception as e:
            logger.exception("Test connection failed")
            return False, str(e)

    # --- Event handling hooks ---------------------------------------------
    def on_event(self, event, payload):
        # Minimal event dispatcher matching OctoPrint event names from the plan
        try:
            logger.debug("Received event %s payload=%s", event, payload)
            if event == "PrintStarted":
                self._on_print_started(payload)
            elif event in ("PrintDone", "PrintFailed", "PrintCancelled"):
                self._on_print_ended(payload)
            elif event == "PrintPaused":
                self._on_print_paused(payload)
            elif event == "PrintResumed":
                self._on_print_resumed(payload)
        except Exception:
            logger.exception("Error handling event %s", event)

    def _on_print_started(self, payload):
        logger.info("Print started — starting monitoring")
        self.start_monitoring()

    def _on_print_ended(self, payload):
        logger.info("Print ended — stopping monitoring and resetting state")
        self.stop_monitoring()

    def _on_print_paused(self, payload):
        logger.info("Print paused — pausing monitoring timer")
        # In a full plugin we'd pause the internal timer; for now stop it.
        self.stop_monitoring()

    def _on_print_resumed(self, payload):
        logger.info("Print resumed — resuming monitoring timer")
        self.start_monitoring()

    # --- Monitoring timer management ----------------------------------------
    def start_monitoring(self):
        if self._monitoring:
            return
        self._monitoring = True
        self._schedule_timer()
        logger.info("Monitoring started")

    def stop_monitoring(self):
        self._monitoring = False
        if self._timer:
            self._timer.cancel()
            self._timer = None
        logger.info("Monitoring stopped")

    def _schedule_timer(self):
        if not self._monitoring:
            return
        interval = max(60, int(self._timer_interval))
        self._timer = threading.Timer(interval, self._timer_tick)
        self._timer.daemon = True
        self._timer.start()

    def _timer_tick(self):
        try:
            self.run_voting_sequence()
            self._consecutive_errors = 0  # Reset on success
        except Exception:
            self._consecutive_errors += 1
            logger.exception("Error during voting sequence (consecutive: %d)", self._consecutive_errors)
            
            if self._consecutive_errors >= 3:
                logger.error("Too many consecutive errors. Disabling monitoring.")
                self.stop_monitoring()
                self._event_bus.fire("plugin_ai_printmon_error", {"message": "Monitoring disabled after 3 consecutive errors"})
        finally:
            # Reschedule
            if self._monitoring:
                self._schedule_timer()

    # --- Snapshot capture ---------------------------------------------------
    def capture_snapshot(self, snapshot_url):
        try:
            resp = requests.get(snapshot_url, timeout=10)
            resp.raise_for_status()
            return resp.content
        except Exception:
            logger.exception("Failed to capture snapshot from %s", snapshot_url)
            return None

    # --- LLM client ---------------------------------------------------------
    def send_image_to_llm(self, img_bytes, system_prompt=None):
        if img_bytes is None:
            return None
        endpoint = self.get_settings_defaults()["api_endpoint"]
        model = self.get_settings_defaults()["model"]
        api_key = self.get_settings_defaults()["api_key"]

        b64 = base64.b64encode(img_bytes).decode("ascii")
        data_url = f"data:image/jpeg;base64,{b64}"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt or self.get_settings_defaults()["system_prompt"]},
                {"role": "user", "content": "<image>"},
            ],
            "n": 1,
        }

        files = None
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Many providers accept images inline in the message content. We'll append
        # the data URL to the user content to keep this simple and provider-agnostic.
        payload["messages"][1]["content"] = data_url

        try:
            resp = requests.post(endpoint, headers=headers, data=json.dumps(payload), timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            logger.exception("LLM request failed")
            return None

    def parse_llm_response(self, resp_json):
        """Extract the JSON object the system prompt required.

        The LLM is instructed to return ONLY a JSON object. We defensively
        attempt to parse JSON from the text fields.
        """
        try:
            if not resp_json:
                return None
            # Attempt to locate content in standard OpenAI-like response shape
            choices = resp_json.get("choices") if isinstance(resp_json, dict) else None
            if choices:
                for c in choices:
                    txt = c.get("message", {}).get("content") or c.get("text")
                    if not txt:
                        continue
                    txt = txt.strip()
                    try:
                        return json.loads(txt)
                    except Exception:
                        # Try to extract a JSON substring
                        start = txt.find("{")
                        end = txt.rfind("}")
                        if start != -1 and end != -1:
                            try:
                                return json.loads(txt[start : end + 1])
                            except Exception:
                                continue
            # Fallback: if the response is itself a JSON-like dict with 'status'
            if isinstance(resp_json, dict) and "status" in resp_json:
                return resp_json
        except Exception:
            logger.exception("Error parsing LLM response")
        return None

    # --- Voting & actions --------------------------------------------------
    def run_voting_sequence(self):
        settings = self.get_settings_defaults()
        rounds = int(settings.get("rounds", 3))
        round_delay = int(settings.get("round_delay", 3))
        snapshot_url = settings.get("snapshot_url")

        votes = []
        for r in range(rounds):
            img = self.capture_snapshot(snapshot_url)
            resp = self.send_image_to_llm(img)
            parsed = self.parse_llm_response(resp)
            if parsed is None:
                # Treat as inconclusive (do not count as fail)
                votes.append("inconclusive")
            else:
                status = parsed.get("status")
                votes.append("fail" if status == "fail" else "ok")

            # Short-circuit: determine if further rounds can change outcome
            fails = votes.count("fail")
            remaining = rounds - (r + 1)
            max_possible_fails = fails + remaining
            # Evaluate against configured rules (simple default behaviour)
            action = self.evaluate_rules(fails, rounds)
            if action != "none":
                self.execute_action(action, votes=votes, last_response=parsed)
                return

            # If no future action is possible, stop early
            # (Example: if even max_possible_fails can't reach lowest threshold)
            # For simplicity here, if no fails so far and remaining cannot reach 1, stop.
            if max_possible_fails == 0:
                return

            time.sleep(round_delay)

    def evaluate_rules(self, fail_count, rounds):
        # Minimal rule evaluation: escalate at 1/3, 2/3, 3/3 per defaults
        if rounds <= 0:
            return "none"
        if fail_count == 0:
            return "none"
        if fail_count >= rounds:
            return "cancel_stop_queue"
        if fail_count * 2 >= rounds:
            return "warn"
        return "none"

    def execute_action(self, action, votes=None, last_response=None):
        logger.info("Executing action %s (votes=%s)", action, votes)
        
        payload = {
            "action": action,
            "votes": votes,
            "reason": last_response.get("reason") if last_response else "Unknown",
            "timestamp": time.time()
        }

        if action == "warn":
            self._event_bus.fire("plugin_ai_printmon_warning", payload)
            self.fire_warning(votes, last_response)
        elif action == "pause":
            self._event_bus.fire("plugin_ai_printmon_critical", payload)
            self.fire_warning(votes, last_response)
            self.pause_print()
        elif action == "cancel" or action == "cancel_stop_queue":
            self._event_bus.fire("plugin_ai_printmon_critical", payload)
            self.fire_warning(votes, last_response)
            if action == "cancel_stop_queue":
                self.stop_continuous_queue()
            self.cancel_print()
        elif action == "none" and votes and "fail" in votes:
             self._event_bus.fire("plugin_ai_printmon_check_passed", payload)

    # --- Action helpers (stubs) --------------------------------------------
    def fire_warning(self, votes, last_response):
        logger.warning("AI Print Monitor warning: votes=%s response=%s", votes, last_response)

    def pause_print(self):
        logger.info("Pausing print")
        self._printer.pause_print()

    def cancel_print(self):
        logger.info("Cancelling print")
        self._printer.cancel_print()

    def stop_continuous_queue(self):
        logger.info("Attempting to stop Continuous Print queue")
        # Check if the plugin is available
        if "continuousprint" in self._plugin_manager.get_plugins("octoprint.plugin.types.comm_error"): # Not the best way to check type, usually plugin manager list
            pass # We'll just try the API call; most plugins expose via API anyway
        
        url = "http://localhost:5000/plugin/continuousprint/set_active"
        # In OctoPrint, we can often skip auth for local requests or use the API key from settings
        api_key = self._settings.global_get(["api", "key"])
        headers = {}
        if api_key:
            headers["X-Api-Key"] = api_key
            
        try:
            resp = requests.post(url, headers=headers, json={"active": False}, timeout=5)
            resp.raise_for_status()
            logger.info("Continuous Print queue stopped successfully")
        except Exception:
            logger.exception("Failed to stop Continuous Print queue via API")


__plugin_name__ = "AI Print Monitor"
__plugin_pythoncompat__ = ">=3.7,<4"
__plugin_implementation__ = AIPrintMonPlugin()


# If the plugin is loaded by OctoPrint it will normally register entry points.
if __name__ == "__main__":
    print("This module is an OctoPrint plugin component and is not intended to be run directly.")
