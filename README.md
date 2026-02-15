# OctoPrint-AIPrintMon

AI-powered print failure detection for OctoPrint. Captures webcam snapshots during active prints and sends them to a vision-capable LLM for automated failure analysis. Uses a configurable voting system to reduce false positives, with escalating actions from notifications to canceling the print and stopping your [Continuous Print](https://plugins.octoprint.org/plugins/continuousprint/) queue.

Works with any OpenAI-compatible vision API including Ollama (local), OpenAI, and Google Gemini.

## Setup

Install via the bundled [Plugin Manager](https://docs.octoprint.org/en/main/bundledplugins/pluginmanager.html) or manually using this URL:

    https://github.com/rogerh/OctoPrint-AIPrintMon/archive/main.zip

### Requirements

- A webcam configured in OctoPrint with a working snapshot URL
- Access to a vision-capable LLM:
  - **Local (free):** [Ollama](https://ollama.ai/) with a vision model like `llava`, `llava-llama3`, or `moondream`
  - **Cloud:** OpenAI API key with access to `gpt-4o`, or Google Gemini API key with access to `gemini-2.0-flash`
  - **Any other** OpenAI-compatible vision API endpoint

## Configuration

After installing, go to **Settings > AI Print Monitor**. The settings are organized into four tabs.

### LLM Connection

Select a provider preset (Ollama, OpenAI, Gemini, or Custom) to prefill the endpoint URL. Enter your API key if using a cloud provider and specify the model name. Use the **Test Connection** button to verify your setup before starting a print.

### Monitoring

- **Check interval:** How often to capture and analyze a snapshot (default: 5 minutes)
- **Delay between rounds:** Seconds to wait between voting rounds so each gets a fresh snapshot (default: 3 seconds)
- **Cooldown after alert:** Minutes to wait after an alert before checking again (default: 15 minutes)

### Failure Response

Configure how many voting rounds to run (1-3) and what action to take at each failure threshold.

**Default configuration (3 rounds):**

| Threshold | Action |
|-----------|--------|
| 1 of 3 fail | Do nothing |
| 2 of 3 fail | Warn only |
| 3 of 3 fail | Cancel print + stop queue |

Actions escalate: Do nothing > Warn > Pause print > Cancel print > Cancel print + stop Continuous Print queue.

You can set this to be as aggressive or conservative as your setup requires. If you have a high-quality camera and a strong vision model, a single-round check that stops everything on one failure detection may be appropriate.

### System Prompt

The system prompt sent to the LLM is fully editable. The default prompt instructs the model to look for common failure modes (spaghetti, layer shifting, bed adhesion failure, warping, etc.) and respond with a JSON object. You can customize this to focus on specific failure types relevant to your printer and materials.

## How It Works

1. When a print starts, a timer begins capturing webcam snapshots at your configured interval.
2. Each snapshot is base64-encoded and sent to the vision LLM with the system prompt.
3. If the LLM detects a failure, additional rounds capture fresh snapshots for confirmation (based on your configured round count).
4. The vote tally is evaluated against your action rules. The highest triggered action is executed.
5. If the action includes stopping the Continuous Print queue, the plugin deactivates the queue first, then cancels the active print.

The plugin includes short-circuit logic to skip unnecessary API calls. For example, with 3 rounds configured and default rules, if the first two rounds both pass, the third round is skipped since even a failure can't reach an actionable threshold.

## Continuous Print Integration

If the [Continuous Print](https://plugins.octoprint.org/plugins/continuousprint/) plugin is installed, AI Print Monitor can stop its queue when a failure is confirmed. This prevents the queue from advancing to the next job after a failed print. The integration is auto-detected on startup and can be toggled in settings.

## Error Handling

The plugin is designed to never interfere with your print due to its own errors:

- If the snapshot URL is unreachable, the check is skipped and retried next interval.
- If the LLM endpoint is unreachable or times out, the check is skipped. Your print continues normally.
- If the LLM returns an unparseable response, it is treated as inconclusive (not counted as a fail).
- If the Continuous Print API call fails, the print is still canceled via OctoPrint directly.
- After 3 consecutive API errors, monitoring is disabled and the user is notified to check settings.

## Events

The plugin fires custom OctoPrint events that other plugins (Telegram, Discord, email, etc.) can subscribe to:

- `plugin_ai_printmon_check_passed` — routine check was OK
- `plugin_ai_printmon_warning` — warning threshold reached
- `plugin_ai_printmon_critical` — cancel/stop threshold reached, print stopped
- `plugin_ai_printmon_error` — LLM unreachable or repeated API errors

## Privacy

When using Ollama or another local LLM, no data leaves your network. When using a cloud provider (OpenAI, Gemini, etc.), webcam snapshots are sent to that provider's API for analysis. No data is sent to any service other than the one you configure.

## License

Licensed under [AGPLv3](LICENSE).