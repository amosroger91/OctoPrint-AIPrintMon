// Knockout viewmodel for AI Print Monitor settings
$(function () {
    function ViewModel(parameters) {
        var self = this;

        // injected parameters
        self.settings = parameters[0];

        // UI state
        self.activeTab = ko.observable('connection');
        self.provider_preset = ko.observable('OpenAI');
        self.providerPresets = ['Ollama', 'OpenAI', 'Google Gemini', 'Custom'];
        self.api_endpoint = ko.observable('');
        self.api_key = ko.observable('');
        self.model_name = ko.observable('');

        self.monitor_enabled = ko.observable(true);
        self.snapshot_url = ko.observable('');
        self.interval_minutes = ko.observable(5);
        self.rounds = ko.observable(3);
        self.round_delay = ko.observable(3);
        self.cooldown_minutes = ko.observable(15);

        self.failure_rules_json = ko.observable('');
        self.system_prompt = ko.observable('');

        self.testing = ko.observable(false);

        // Called when the settings dialog is shown - populate fields
        self.onSettingsShown = function () {
            var s = self.settings.settings.plugins.ai_printmon || {};
            self.provider_preset(s.provider_preset || 'OpenAI');
            self.api_endpoint(s.api_endpoint || 'https://api.openai.com/v1/chat/completions');
            self.api_key(s.api_key || '');
            self.model_name(s.model || 'gpt-4o');

            self.monitor_enabled(typeof s.monitor_enabled === 'undefined' ? true : s.monitor_enabled);
            self.snapshot_url(s.snapshot_url || 'http://localhost:8080/?action=snapshot');
            self.interval_minutes(s.interval_minutes || 5);
            self.rounds(s.rounds || 3);
            self.round_delay(s.round_delay || 3);
            self.cooldown_minutes(s.cooldown_minutes || 15);

            try {
                self.failure_rules_json(JSON.stringify(s.failure_rules || [], null, 2));
            } catch (e) {
                self.failure_rules_json('[]');
            }

            self.system_prompt(s.system_prompt || (
                'You are a 3D print failure detection system. You will receive an image of a 3D print in progress captured from a webcam. Analyze the image for signs of failure and respond with ONLY a JSON object: {"status": "ok"} or {"status": "fail", "reason": "..."}.'
            ));
        };

        // Provide settings payload to OctoPrint when saving
        self.onSettingsBeforeSave = function () {
            // Basic validation
            if (!self.api_endpoint() || self.api_endpoint().trim() === "") {
                new PNotify({ title: "Validation error", text: "API endpoint cannot be empty", type: "error" });
                return false;
            }
            if (Number(self.interval_minutes()) < 1 || Number(self.interval_minutes()) > 30) {
                new PNotify({ title: "Validation error", text: "Interval must be between 1 and 30 minutes", type: "error" });
                return false;
            }
            var rules = [];
            try {
                rules = JSON.parse(self.failure_rules_json() || '[]');
            } catch (e) {
                rules = [];
            }

            return {
                "ai_printmon": {
                    provider_preset: self.provider_preset(),
                    api_endpoint: self.api_endpoint(),
                    api_key: self.api_key(),
                    model: self.model_name(),
                    monitor_enabled: self.monitor_enabled(),
                    snapshot_url: self.snapshot_url(),
                    interval_minutes: Number(self.interval_minutes()),
                    rounds: Number(self.rounds()),
                    round_delay: Number(self.round_delay()),
                    cooldown_minutes: Number(self.cooldown_minutes()),
                    failure_rules: rules,
                    system_prompt: self.system_prompt(),
                }
            };
        };

        // When user selects a provider preset, fetch defaults from backend
        self.provider_preset.subscribe(function (newVal) {
            if (!newVal) return;
            OctoPrint.simpleApiCommand("ai_printmon", "get_preset", { preset: newVal }).done(function (response) {
                if (response && response.success && response.preset) {
                    var p = response.preset;
                    if (p.endpoint) self.api_endpoint(p.endpoint);
                    if (p.model) self.model_name(p.model);
                    if (typeof p.api_key !== 'undefined') self.api_key(p.api_key);
                } else {
                    new PNotify({ title: "Preset error", text: response && response.message ? response.message : 'Failed to load preset', type: "error" });
                }
            }).fail(function () {
                new PNotify({ title: "Preset error", text: "Failed to contact plugin to load preset", type: "error" });
            });
        });

        self.resetSystemPrompt = function () {
            self.system_prompt('You are a 3D print failure detection system. You will receive an image of a 3D print in progress captured from a webcam. Analyze the image for signs of failure and respond with ONLY a JSON object: {"status": "ok"} or {"status": "fail", "reason": "..."}.');
        };

        // Test Connection - calls a plugin API action (backend implementation is planned separately)
        self.testConnection = function () {
            self.testing(true);
            var payload = {
                endpoint: self.api_endpoint(),
                api_key: self.api_key(),
                model: self.model_name()
            };

            // Use OctoPrint's simpleApiCommand helper to call the plugin action (backend not implemented yet)
            OctoPrint.simpleApiCommand("ai_printmon", "test_connection", payload).done(function (response) {
                new PNotify({
                    title: "Connection successful",
                    text: "Test connection succeeded.",
                    type: "success"
                });
            }).fail(function () {
                new PNotify({
                    title: "Connection failed",
                    text: "Test connection failed. Check endpoint/key.",
                    type: "error"
                });
            }).always(function () {
                self.testing(false);
            });
        };

        // Apply Settings Now - validates and sends all current settings to the backend
        self.applying = ko.observable(false);
        self.applySettings = function () {
            var payload = self.onSettingsBeforeSave();
            if (!payload) return; // Validation failed in onSettingsBeforeSave

            self.applying(true);
            OctoPrint.simpleApiCommand("ai_printmon", "apply_settings", { settings: payload.ai_printmon }).done(function (response) {
                if (response && response.success) {
                    new PNotify({
                        title: "Settings applied",
                        text: "Runtime settings have been successfully applied.",
                        type: "success"
                    });
                } else {
                    new PNotify({
                        title: "Apply failed",
                        text: "Failed to apply settings: " + (response.message || "unknown error"),
                        type: "error"
                    });
                }
            }).fail(function () {
                new PNotify({
                    title: "Apply failed",
                    text: "Failed to contact plugin to apply settings.",
                    type: "error"
                });
            }).always(function () {
                self.applying(false);
            });
        };
    }

    // Register the viewmodel with OctoPrint, injecting the settingsViewModel
    OCTOPRINT_VIEWMODELS.push([ViewModel, ["settingsViewModel"], ["#ai_printmon_settings"]]);
});
