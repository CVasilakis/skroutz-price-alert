<h1 align="center">
  <img src="assets/banner.svg" alt="Project Banner" width="120"><br>
  Scrooge Alert
</h1>

<p align="center">An extensible price monitor for supported product pages and classified listings. Receive automated push notifications when offers reach your desired price.</p>


> [!IMPORTANT]
> Store and marketplace names are trademarks of their respective owners. This independent, unofficial project is not affiliated with, authorized, maintained, sponsored, or endorsed by any supported marketplace.

## 📑 Table of Contents

<details>
  <summary><b>Click to expand</b></summary>
  <br>

1. [Features](#-features)
2. [Supported Stores](#-supported-stores)
3. [Prerequisites](#-prerequisites)
4. [Installation](#-installation)
5. [Configuration](#%EF%B8%8F-configuration)
   - [Scraper Configuration (config/<target>.json)](#file-1-scraper-configuration-configtargetjson)
     - [Scraper Settings](#scraper-settings)
     - [Tracked Items](#tracked-items)
   - [General Configuration (config/general.json)](#file-2-general-configuration-configgeneraljson)
     - [Notification Settings](#notification-settings)
     - [General Settings](#general-settings)
6. [Usage](#-usage)
   - [Automated Systemd Execution](#automated-systemd-execution)
   - [Manual Execution](#manual-execution)
   - [Helper Scripts](#helper-scripts)
7. [Notifications & Messages](#-notifications--messages)
8. [Uninstallation](#%EF%B8%8F-uninstallation)
9. [Troubleshooting & Debugging](#-troubleshooting--debugging)
10. [Rate Limiting](#%EF%B8%8F-rate-limiting)
11. [Frequently Asked Questions (FAQ)](#-frequently-asked-questions)
12. [Future Updates (Roadmap)](#%EF%B8%8F-future-updates-roadmap)
13. [Contributing & Issues](#-contributing--issues)
14. [Support & Donations](#-support--donations)
15. [Disclaimer](#%EF%B8%8F-disclaimer)
16. [License](#-license)

</details>

## ✨ Features

* **Automated Monitoring:** Set it and forget it. Tracks configured items silently in the background.
* **Instant Notifications:** Get instant push notifications (Telegram, Discord, Slack, Email, etc.) for price drops.
* **Custom Target Prices:** Define a price-drop threshold for each monitored item.

## 🌍 Supported Stores

Supported targets are discovered from the checked-in plugin packages. After
installation, run `./scrooge-alert run --help` to see the exact target flags available
in your checkout; before installation, the command prints setup guidance instead.
Each target's accepted URLs, custom fields, settings, dependencies, and examples are
documented in `src/core/scrapers/plugins/<target>/README.md` beside its implementation.

## 📋 Prerequisites

*   Linux/Unix environment (`systemd` available for scheduling).
*   Python 3.10+ installed (`python3`, `python3-venv`).

## 🚀 Installation

1. **Install required system packages:**

    <details open>
    <summary><b>Debian / Ubuntu / Raspberry Pi OS / Linux Mint</b></summary>
    <br>

    ```sh
    sudo apt update
    sudo apt install git python3-venv
    ```
    </details>

    <details>
    <summary><b>Fedora / RHEL / Rocky Linux</b></summary>
    <br>

    ```sh
    sudo dnf install git python3
    ```
    </details>

    <details>
    <summary><b>Arch Linux / Manjaro</b></summary>
    <br>

    ```sh
    sudo pacman -S git python
    ```
    </details>

2. **Clone the repository:**

    ```sh
    git clone https://github.com/CVasilakis/scrooge-alert
    cd scrooge-alert
    ```

3. **Run the installation command:**

    ```sh
    ./scrooge-alert install
    ```

    The install command creates a project-owned Python virtual environment, installs the required dependencies, and sets up one systemd user timer per scraper using its valid configured `execution_interval`, or the plugin's built-in default when unset. If one scraper config is structurally invalid, that scraper is reported and skipped while healthy scrapers are still installed; its existing units are preserved and the command exits with status `15`. No `sudo` or elevated privileges are required for the installation.

    Scrooge Alert creates a real root `venv/` directory and regular canonical
    systemd unit files. The enablement symlinks created by systemd below
    directories such as `timers.target.wants/` are expected and are not managed
    unit destinations. A symlink placed at a managed destination is unsupported:
    install, schedule, and update reject it before changing packages, units, or
    timer state. Remove such a unit entry safely with
    `./scrooge-alert uninstall --<target>`, then reinstall it.

4. **Configure your settings:**

    Proceed to the [Configuration](#%EF%B8%8F-configuration) section for scraper,
    notification, and project-wide settings.

## ⚙️ Configuration

All user parameters live in strict, schema-versioned JSON files under `config/`.
Runtime never modifies them; update-time changes are handled by `./scripts/dev/migrate.sh`.
Machine-owned data is stored separately under `state/`.

> [!IMPORTANT]
> Migration is supported only between schema versions declared within this major
> release. Unversioned documents and documents from earlier major releases are
> unsupported: migration fails closed and leaves them untouched. Recreate target
> configs from the current plugin `config.example.json` files and recreate
> `config/general.json` from `src/core/general/config.example.json`. Delete an incompatible
> `state/<target>.json` only when you accept losing that scraper's stored checks and alert
> history. Deleting an incompatible `state/general.json` loses the stored reminder
> timestamp and scheduling history and may cause a reminder to be sent again. The
> application will recreate the deleted machine state.

### File 1: Scraper Configuration (`config/<target>.json`)

Each scraper reads a strict, versioned, read-only-at-runtime JSON file in `config/` (for example,
`config/<target>.json`) containing its settings and tracked items. Machine state is
stored separately in the ignored, schema-versioned `state/<target>.json`. Example files
live beside their plugins:

```sh
target=TARGET_NAME  # replace with a target shown by ./scrooge-alert run --help
cp "src/core/scrapers/plugins/$target/config.example.json" "config/$target.json"
nano "config/$target.json"
```

A complete file is structured like this:

```json
{
  "schema_version": 1,
  "plugin_schema_version": 1,
  "settings": {
    "execution_interval": "1h",
    "log_retention_days": 7,
    "notify_scraping_errors": true,
    "suppress_repeated_price_alerts": false
  },
  "items": [
    {
      "id": "monitor-123",
      "name": "Awesome Monitor",
      "url": "https://store.example/products/123",
      "target_price": 150
    }
  ]
}
```

#### Scraper Settings

The optional top-level `settings` holds per-scraper preferences, separate from your item list:

| Setting | Type | Description |
| :--- | :--- | :--- |
| `execution_interval` | String | How often the scraper's background timer runs. One of `15m`, `30m`, `1h`, `2h`, `4h`, `8h`, `12h`, `24h`. If omitted, the scraper's built-in default is used. |
| `log_retention_days` | Integer / String | How many days of log files each scraper keeps. It should be an integer between **1–30**, written as a number or a day string (`"7d"`, `"7 days"`). Only days are supported (no hours/weeks/months), and logging cannot be disabled. If omitted or an unsupported value is used, the default of 7 days is used. |
| `notify_scraping_errors` | Boolean | Whether to send the **Scraping Errors** notification for a scraper. Defaults to `true`. Set it to `false` to stop those alerts. The scraping errors are still logged, and the **Tracking Stale** and **Script Crash** alerts are unaffected — so a persistent problem still surfaces. If omitted or an unsupported value is used, the default (`true`, notify) applies. |
| `suppress_repeated_price_alerts` | Boolean | Whether to suppress a price alert that was already delivered successfully for the same active deal. Defaults to `false`, so below-target prices alert on every run. When `true`, single-price alerts resume only after the price is observed at or above the target and later drops again; listing alerts are deduplicated by canonical offer URL and resume if an offer leaves the below-target result set and later returns. Failed deliveries remain eligible for retry. |

> [!NOTE]
> Changing `execution_interval` does not take effect on its own. After editing it, apply it to the live timer with the [Set Execution Interval](#set-execution-interval) script: `./scrooge-alert schedule`.

#### Tracked Items

The `items` array lists the entries you want to monitor. Every row needs a unique,
stable `id`; separate IDs may intentionally use the same source input.

| Field | Type | Source | Description |
| :--- | :--- | :--- | :--- |
| `id` | String | **User-defined** | Unique stable state key for this row. |
| `name` | String | **User-defined** | A friendly naming label used inside the notifications. |
| `target_price` | Number | **User-defined** | The maximum price threshold. If the price drops below this, you get alerted; use `0` to monitor without an alert threshold. |
| `skip` | Boolean | **User-defined** | Optional. Set to `true` to skip monitoring this item. Defaults to `false`. |
Runtime price, check, and successful price-alert history is written to
`state/<target>.json`, not to user configuration. Timestamps are RFC 3339 UTC strings
ending in `Z`. Cooperative runtime and migration locks are machine-managed under
`state/locks/`: each scraper uses `<target>.lock`, alongside the framework-owned
`reminder.lock` and `migration.lock`.

> [!IMPORTANT]
> Scraper state and target configuration have independent schema sequences. Both begin
> at version 1. `./scrooge-alert update` migrates known documents before reactivating timers; use
> `./scripts/dev/migrate.sh --check` to validate and report without modifying managed JSON
> documents. Check mode still takes the cooperative locks, so `state/locks/` and its
> lock metadata may be created. Add `--debug` to expose the underlying migration output
> when diagnosing a failure.

Plugins declare every source input beyond the shared keys above. Most product-page
plugins require a `url`; other adapters may use several URLs or identifiers such as a
SKU, region, query, or category without any URL. They may also declare required,
optional, or sensitive settings. These additions belong exclusively in the
package-local guide and example config, so adding a new target does not require
changing this document.

### File 2: General Configuration (`config/general.json`)

Notification endpoints and preferences not tied to a single scraper live in
`config/general.json`. Copy the provided template and restrict access because Apprise
URLs commonly contain credentials:

```sh
cp src/core/general/config.example.json config/general.json
chmod 600 config/general.json
nano config/general.json
```

```json
{
  "schema_version": 1,
  "notifications": {
    "urls": [
      "tgram://<token>/<chat_id>",
      "discord://<webhook_id>/<webhook_token>"
    ]
  },
  "settings": {
    "reminder": "1 month",
    "reminder_day": "Saturday",
    "reminder_time": "13:00"
  }
}
```

#### Notification Settings

Scrooge Alert uses [Apprise](https://github.com/caronc/apprise) to deliver push
notifications through Discord, Telegram, Slack, email, and many other services. Consult
the [supported-services documentation](https://appriseit.com/services/) or
[URL builder](https://appriseit.com/tools/url-builder/) for the URL required by a
service. Add each endpoint as a separate string in `notifications.urls`; order is
preserved by the `ping` command.

At least one valid URL is required for background/systemd execution. An interactive run
may continue without one so configuration problems can be inspected. Invalid entries are
ignored when a valid endpoint also exists and are detailed by `./scrooge-alert ping`.
The Configuration Check panel recommends `chmod 600` when the file is accessible to
group or other users, but this permission warning never prevents a run.

#### General Settings

Every general setting falls back to its default when the file or key is absent:

| Setting | Type | Source | Description |
| :--- | :--- | :--- | :--- |
| `reminder` | String | **User-defined** | How often to send a short notification confirming the scrapers are still active in the background and whether a project update is available. One of `off`, `1 week`, `1 month` (default), `3 months`, `6 months`, `1 year`. Set it to `off` to disable these reminders. If an unsupported value is used, the default (`1 month`) applies. |
| `reminder_day` | String | **User-defined** | The weekday the reminder is sent, in your server's local time. A weekday name or common short form (e.g. `Saturday` (default), `sat`, `Monday`, `mon`). If an unsupported value is used, the default (`Saturday`) applies. |
| `reminder_time` | String | **User-defined** | The time of day the reminder is sent, in your server's local time. A 24-hour `HH:MM` (or bare hour), or a 12-hour am/pm value — e.g. `13:00` (default), `13`, `1pm`, `9:30am`. Delivery is approximate (see note below), so minutes are a hint, not an exact send time. If an unsupported value is used, the default (`13:00`) applies. |
Reminder state is stored in `state/general.json` as an RFC 3339 UTC timestamp. The
schema-versioned state file is machine-owned; `config/general.json` has its own schema
sequence and remains read-only outside explicit update/migration tooling.

> [!NOTE]
> With the defaults above you get a reminder about once a month, on a Saturday around 13:00 in your server's local time. The time is approximate: the reminder goes out on the first scraper run at or after the chosen day and time, so if your scrapers only run every few hours it might arrive a little later that day (or the next morning), but never earlier. Months are counted in weeks, so `1 month` means every 4th Saturday, `3 months` means every 13th, and so on, which keeps every reminder on the same day and time.

## 💻 Usage

There are two ways to execute the script: automatically via the scheduled systemd timer, or manually for testing.

### Automated Systemd Execution

Once `./scrooge-alert install` has run successfully, each scraper executes automatically via its own systemd timer — on its plugin-defined default cadence, or on the valid cadence set by its [execution_interval](#scraper-settings) setting. The systemd timer applies a randomized up-to-3m startup delay before launching the execution wrapper (`scripts/run.sh`) to simulate human timing and avoid exact scheduling footprints.

### Manual Execution

You can manually interact with the application using the wrapper script. You can safely interrupt the manual execution at any time by pressing `Ctrl+C`.

```
./scrooge-alert run [-h] [--quiet] [--<target> ...]
```

#### Available CLI Flags:

**Execution Flags:**
These flags modify the overall behavior of the script or trigger user assistance routines.

| Flag&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Action |
| :--- | :--- |
| `-h`, `--help` | Displays the help message with all available script arguments. |
| `--quiet` | Suppresses all console output and redirects execution logs to the `logs/` directory. This is utilized by the systemd setup to ensure silent background operation. |

**Target Scraper Flags:**
These flags allow you to isolate execution to specific platforms. If no target flags are provided, the script defaults to running all registered scrapers sequentially. They can be combined with `--quiet`.

| Flag&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Action |
| :--- | :--- |
| `--<target>` | Activates only the specified target's scraper (e.g., `--skroutz`). You can pass one or more target flags simultaneously. |

> [!NOTE]
> Only one instance of a specific scraper target is allowed to run at a time to avoid triggering anti-bot protections. These machine-managed locks live at `state/locks/<target>.lock`. If a background execution for a target (e.g., Skroutz) is currently in progress, your manual run for that target will be blocked and skipped. If you need to forcefully stop all active background executions to run the scraper manually, you can safely use the [stop script](#stop-active-runs): `./scrooge-alert stop`. This stops all the current background runs but will not break any future scheduled executions.

#### Status Check:

The `status` command verifies the integrity of
your JSON configuration, validates notification URLs, and queries systemd to display the
following background execution details:

```
./scrooge-alert status
```

- **Systemd Timer Active:** Shows whether the timer is currently active.
- **Last Execution Time:** Displays when the script was last run.
- **Last Execution Status:** Indicates last execution results and if any errors happened.
- **Next Scheduled Execution:** Displays the next scheduled run or if it's currently running.

The **Configuration Check** panel additionally reports whether systemd user
lingering is enabled (see the FAQ entry below). Lingering is what lets the timers keep
firing while you are logged out, so a `Lingering  Disabled` row warns that scheduled runs
may not happen — remedy it with `loginctl enable-linger $USER`, or ask your system
administrator if that requires elevated rights. Like the permission warning, this row is
advisory and never blocks a run or changes an exit code. A host with no `loginctl` at all
omits the row instead of warning about a feature it does not have.

Background runs expose precise exit statuses through **Last Execution Status**:

| Code | Meaning |
| :--- | :--- |
| `1` | An unexpected application failure occurred; inspect `logs/errors.txt`. |
| `15` | At least one target configuration could not be loaded. That target is skipped while other selected targets continue; management commands preserve its existing units. |
| `16` | Notification configuration in `config/general.json` is unusable. |
| `17` | The store blocked or rate-limited the scraper. |
| `18` | A parser or unexpected scraper fault exhausted all retries. |
| `19` | Machine state or its cooperative lock could not be loaded, persisted, or used safely. |
| `20` | At least one configured notification failed; shown as a yellow warning. |
| `21` | The selected scraper's private dependencies are missing. |
| `42` | Every selected scraper was already running. |
| `130` | The run was interrupted. |

When a manual multi-target run observes more than one condition, interruption and
hard application/configuration/storage failures take precedence over scraper and
rate-limit failures, notification warnings, and the all-targets-already-running status.

#### Test Notifications:

To test notification URLs without waiting for a scheduled run or price drop, use the
`ping` command:

```
./scrooge-alert ping
```

This will send a test message to each configured Apprise URL(s). It will output a report of successes and failures, helping you quickly identify and debug any misconfigured notification endpoints.

> [!TIP]
> If the script fails to run in the background or you do not receive expected notifications, please consult the [Troubleshooting & Debugging](#-troubleshooting--debugging) section. If your problem persists, feel free to [open an issue](https://github.com/CVasilakis/scrooge-alert/issues).

### Helper Scripts

The repository-local `./scrooge-alert` command is the user-facing interface for managing background scrapers and updates. Its POSIX shell owners live under `scripts/`; it does not install a launcher, modify `PATH`, edit shell profiles, or place completion files outside the checkout. Developer-only setup, validation, and plugin-contributor commands remain under `scripts/dev/` and are documented in `CONTRIBUTING.md`. Management commands support `--help` and can be applied to specific targets. They suppress underlying system-command output by default; pass `--debug` to expose it when diagnosing a failure. The `run`, `ping`, and `status` commands are deliberate exceptions because their Python entry points own their terminal UI, runtime diagnostics, and logging.

#### Install & Add Scrapers
Sets up the Python virtual environment and installs the systemd timer(s) and service(s). Run it as many times as you like to add more scrapers later:

```
./scrooge-alert install [-h] [--debug] [--<target> ...]
```

| Flag&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Action |
| :--- | :--- |
| `-h`, `--help` | Show the help message and exit. |
| `--debug` | Show the underlying system and package command output. |
| `--<target>` | Install and enable only the specified target's scraper (e.g., `--skroutz`). You can pass one or more target flags simultaneously. If no target flag is provided, every registered scraper is installed and enabled. |

#### Stop Active Runs
Stops the currently running scraper service(s), aborting any scrape in progress:

```
./scrooge-alert stop [-h] [--debug] [--<target> ...]
```

| Flag&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Action |
| :--- | :--- |
| `-h`, `--help` | Show the help message and exit. |
| `--debug` | Show the underlying system command output. |
| `--<target>` | Stop only the specified target's scraper (e.g., `--skroutz`). You can pass one or more target flags simultaneously. If no target flag is provided, every running scraper service is stopped. |

#### Pause Background Schedule
Stops and disables the background schedule (systemd timer) so the scraper(s) no longer run automatically:

```
./scrooge-alert disable [-h] [--debug] [--<target> ...]
```

| Flag&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Action |
| :--- | :--- |
| `-h`, `--help` | Show the help message and exit. |
| `--debug` | Show the underlying system command output. |
| `--<target>` | Disable only the specified target's scraper. You can pass one or more target flags simultaneously. If no flag is provided, every installed scraper's timer is disabled. |

#### Resume Background Schedule
Re-enables and starts the background schedule (systemd timer) for the installed scraper(s):

```
./scrooge-alert enable [-h] [--debug] [--<target> ...]
```

| Flag&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Action |
| :--- | :--- |
| `-h`, `--help` | Show the help message and exit. |
| `--debug` | Show the underlying system command output. |
| `--<target>` | Enable only the specified target's scraper. You can pass one or more target flags simultaneously. If no flag is provided, every installed scraper's timer is enabled. |

#### Set Execution Interval
Applies each scraper's configured `execution_interval` (from `config/<target>.json`) to the installed systemd timer. Run it whenever you change an interval:

```
./scrooge-alert schedule [-h] [--debug] [--<target> ...]
```

| Flag&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Action |
| :--- | :--- |
| `-h`, `--help` | Show the help message and exit. |
| `--debug` | Show the underlying system and schedule-resolution output. |
| `--<target>` | Apply only the specified target's interval (e.g., `--skroutz`). You can pass one or more target flags simultaneously. If no flag is provided, every installed scraper's timer is updated to match its configured interval. A scraper whose config file is missing, structurally invalid, or has an unsupported `execution_interval` is reported and left unchanged. Other targets continue, and a structural config error makes the command exit `15`. |

Eligible timer changes are staged and applied as one transaction. Successful
writes replace absent paths or regular files atomically. If writing, reloading,
or restarting any timer fails, every changed regular file and timer activation
state is restored.

#### Remove Scrapers & Uninstall
Performs a full or partial teardown of the background services:

```
./scrooge-alert uninstall [-h] [--debug] [--<target> ...]
```

| Flag&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Action |
| :--- | :--- |
| `-h`, `--help` | Show the help message and exit. |
| `--debug` | Show the underlying system command output. |
| `--<target>` | Removes only the specified scrapers' units, leaving the virtual environment and other targets intact. You can pass one or more target flags simultaneously. With no flag, removes every installed systemd timer/service and deletes the Python virtual environment. |

#### Update to Latest Version
Updates Scrooge Alert to the latest version from `origin/main`, updates dependencies
for exactly the scraper targets that already have timer or service units, and
transactionally replaces their systemd unit files:

```
./scrooge-alert update [-h|--help] [--debug]
```

Pass `--debug` to expose the Git, migration, package, and systemd command output
used by the update.

The checkout must already be on `main`, with no tracked changes, nonignored
untracked files, unpublished commits, or diverged history. The updater never
discards work or switches branches: after verifying `origin/main`, it advances the
checkout with a fast-forward-only merge. It stops and disables selected targets
before replacing source or unit files, then restores each timer's prior
enabled/active state. Unsupported unit links are rejected before any target is
quiesced. If an update is interrupted after source replacement, affected timers stay
disabled and the command prints the retained recovery path and status command.
If a selected target's config is structurally invalid after the source update,
that target's previous unit files and timer state are restored while healthy
targets are reprovisioned; the update completes its recovery work and exits `15`.
If some JSON documents migrate before a later document fails, the updater prints
the retained directory containing exact-byte recovery copies; inspect those copies
before retrying the update.
Transactional rollback restores prior regular-file bytes or prior absence and
then verifies the original timer state. Teardown remains link-aware: disable,
stop, and uninstall discover legacy links, and uninstall removes the link itself
without following or modifying its target.

## 🔔 Notifications & Messages

You might receive the following push notification alerts throughout the lifecycle of the script:

| Notification&nbsp;Title&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Trigger Condition |
| :--- | :--- |
| **Scrooge Alert - Price Drop!** | Sent when an item's observed price falls below your price limit. Listing-result plugins send one alert per matching offer, each linking directly to that offer. Successfully delivered repeats can be suppressed per scraper with [`suppress_repeated_price_alerts`](#scraper-settings). |
| **Scrooge Alert - Tracking Stale** | Sent if a specific item continuously fails the scrape. |
| **Scrooge Alert - Scraping Errors** | Sent if the application hits request limits or unhandled exceptions. Can be turned off per scraper via the [notify_scraping_errors](#scraper-settings) setting. |
| **Scrooge Alert - Script Crash** | Sent if the script completely failed to run. |
| **Scrooge Alert - Status Update** | Periodic liveness reminder confirming the scrapers still run in the background, as well as notifying you of any available updates. Can be turned off via the project-wide [reminder](#general-settings) setting. |
| **Scrooge Alert - Test Notification** | Sent when manually invoking the `ping` command. |

## 🗑️ Uninstallation

To completely remove the background service and clean up the Python virtual environment, execute the uninstallation script:

```sh
./scrooge-alert uninstall
```

The uninstallation process safely performs the following actions:
* Stops and disables the systemd scheduled timer and service.
* Removes the associated systemd configuration files.
* Deletes the Python virtual environment (`venv`).

> [!NOTE]
> **User Data:** `config/` and `state/` are preserved by uninstallation. Delete
> the project directory only if you also want to remove configuration, price history,
> check timestamps, and reminder state.
> 
> **User Lingering:** The script purposefully leaves systemd user lingering enabled, as other background services on your system may rely on it. If you are certain that no other services require this functionality, you can manually disable it by running: `loginctl disable-linger $USER`

## 🔧 Troubleshooting & Debugging

Shell management and developer tools normally suppress underlying command output
and show only their concise status interface. Rerun a failing command with
`--debug` to capture its complete subprocess diagnostics. The runtime wrappers
intentionally have no shell-level `--debug` flag: use their Python-owned terminal
output, the `status` command, and the application logs described below.

**1. Failing to Fetch Items:**

If the script cannot retrieve data for certain items, review the target's package-local guide and verify the source inputs in `config/<target>.json`. For URL-based targets, check for broken links; invalid URLs are often redirected to similar pages.
If the inputs are correct but failures persist across multiple items, your connection has likely been temporarily restricted by the website's anti-bot protection. To mitigate this, reduce your network traffic by tracking fewer active items, or decrease the script's run frequency by setting a longer [`execution_interval`](#scraper-settings) in the scraper's config and applying it with `./scrooge-alert schedule`.

> [!TIP]  
> For the best results, this script should **not** be run behind a VPN and should ideally be executed from a standard Greek residential IP address. High traffic coming from known VPS providers, data centers, or VPNs is very likely to trigger strict anti-bot mechanisms, causing the script to fail.

**2. Not Receiving Notifications:**

If you do not receive a test message, review the [Notification Settings](#notification-settings)
section and verify the Apprise URLs in `config/general.json`.
You can easily test your notification setup using the `ping` command:

```sh
./scrooge-alert ping
```

**3. Application Logs & Crash Reports:**

The application maintains comprehensive logs to help you monitor background executions and diagnose issues. You can find these files in the `logs/` directory:

*   **Background Execution Logs (`logs/<target>/output.log`):** When the script runs automatically in the background, all standard output is saved here (one subdirectory per scraper target). Log line timestamps are recorded in UTC (and labelled as such). These logs rotate daily at midnight UTC, and at each rotation the oldest files beyond your configured [`log_retention_days`](#scraper-settings) (default 7) are pruned. Because pruning happens only at rotation, lowering the value takes effect at the next midnight-UTC rotation, while raising it keeps more history going forward without deleting anything.
*   **Scraper Error Logs (`logs/<target>/errors.txt`):** When a specific scraper hits a critical exception during a run, the detailed stack trace and error information are saved to that scraper's own `errors.txt` (one per target, e.g., `logs/skroutz/errors.txt`).
*   **General Error Logs (`logs/errors.txt`):** Top-level failures that occur with no specific scraper context (e.g. a total crash before any target starts) are saved to the root `logs/errors.txt` instead. Both error logs are timestamped in UTC.

## ⚖️ Rate Limiting

The default configuration applies rate limiting to reduce traffic and increase the success rate of the web scraper:

*   A randomized startup delay (up to 3 minutes) is applied by the systemd timer before each background execution to avoid exact scheduling footprints.
*   Items are checked sequentially, not concurrently.
*   A base 20s delay, plus randomized jitter (1-5s), is enforced between requests.

> [!TIP]
> Periodically remove items from your `config/<target>.json` file once you purchase them or abandon interest. Also avoid decreasing the scraping delays. Over-frequent scraping will trigger strict anti-bot mechanisms and the script will fail to fetch source data.

## ❓ Frequently Asked Questions

<details open>
<summary><b>1. How can I tell if the script is actively running in the background?</b></summary>
<br>

To confirm the script is running in the background, use the `status` command. If the script reports no errors, you can be sure it is configured correctly and running in the background:

```sh
./scrooge-alert status
```

The systemd execution metrics reported by the `status` command only reflect background scheduled executions, not manual runs.
If the command reveals any warnings, please run `./scrooge-alert update` which re-installs the background service and ensures that you are on the latest version. If the issue persists after updating, please [open an issue](https://github.com/CVasilakis/scrooge-alert/issues) for further assistance.
</details>

<details>
<summary><b>2. Can I get notifications sent to Discord, Telegram, or other specific services?</b></summary>
<br>

Most likely, yes! The script uses the [Apprise](https://github.com/caronc/apprise) push
notification library, which supports almost every major platform. Add the service URL to
`notifications.urls` in `config/general.json` and check the
[Supported Services](https://appriseit.com/services/) page for the full list.
</details>

<details>
<summary><b>3. How do I update the script to the latest version?</b></summary>
<br>

Navigate to a clean checkout on the `main` branch and run the update script. It
fetches `origin/main`, refuses unpublished or diverged work, quiesces the installed
scrapers, updates their dependencies, and transactionally replaces their systemd
unit files:

```sh
./scrooge-alert update
```

The updater preserves selective installations and prior timer activation states.
It uses a verified fast-forward and never prompts to discard changes or switches
branches automatically.
</details>

<details>
<summary><b>4. Is it safe to edit my item list while the script is running?</b></summary>
<br>

Absolutely. Configuration is read-only at runtime, so you can add, edit, or remove
items between runs. Use unique stable IDs if you want existing state to follow a row.
</details>

<details>
<summary><b>5. How many items can I track at once?</b></summary>
<br>

Because the script intentionally pauses for about 25 seconds per active item to avoid being blocked by the website, monitoring too many items might cause an execution to overlap its next scheduled cycle. Safety locks prevent overlapping runs for the same target; as a rough example, an hourly schedule has a practical soft limit of around **100 active items** per target.
</details>

<details>
<summary><b>6. How long does a full scrape take to complete?</b></summary>
<br>

To mimic human behavior, the script spaces out its requests. It applies a base delay of 20 seconds per active item, plus an unpredictable jitter of 1–5 seconds. If you are tracking 10 active items, a full manual run will take approximately 4 minutes. *(Note: Background runs via systemd also have a randomized startup delay of up to 3 minutes, which is not applied to manual executions).*
</details>

<details>
<summary><b>8. How do I move the project to a different folder?</b></summary>
<br>

1. Run `./scrooge-alert uninstall` in the old folder to clean up the existing background processes.
2. Clone the repository into your new desired folder using Git.
3. Move your `config/` and `state/` data from the old folder to the new one.
4. Run `./scrooge-alert install` in the new location to rebuild the environment and background timers.
5. Safely delete the old project folder.
</details>

<details>
<summary><b>9. What is systemd "lingering," and why does the installer enable it?</b></summary>
<br>

By default, Linux kills all background processes associated with a user the moment they log out of their SSH session. Enabling "lingering" tells the system to keep your user's background services running continuously, even after you disconnect. It is a completely safe, standard Linux feature that allows the scraper to run automatically without requiring root (`sudo`) privileges. The installer simply checks if it's enabled for your user and turns it on if it isn't, and because other services might rely on this setting, the uninstallation script intentionally leaves it enabled.
</details>

<details>
<summary><b>10. How can I temporarily disable background executions?</b></summary>
<br>

If you want to stop the script from running automatically in the background without completely uninstalling it, you can use the disable script:

```sh
./scrooge-alert disable
```

To re-enable background scheduled executions later, run:

```sh
./scrooge-alert enable
```
</details>

## 🗺️ Future Updates (Roadmap)

- [x] **Enhanced Evasion:** Rotate TLS sessions and request fingerprints intelligently.
- [x] **Multi-Marketplace Expansion:** Support more scrapers for other marketplaces.
- [ ] **User Interface:** Introduction of a Web UI for non-CLI management.
- [ ] **Docker Support:** Add an alternative Dockerized setup via docker-compose configuration.

To see all the undergoing feature requests or to request a new feature, please check the [open issues](https://github.com/CVasilakis/scrooge-alert/issues).

## 🤝 Contributing & Issues

New stores use the additive in-repository plugin contract. Start with
`./scripts/dev/setup.sh --debug`, then run `./scripts/dev/plugin-create.sh` for the
guided Rich wizard. A strict all-argument mode is available for automation. The
generated package README and
[CONTRIBUTING.md](CONTRIBUTING.md) provide the complete workflow, public contracts,
recommended testing practices, and submission checks. Missing plugin tests produce a
focused-verifier warning rather than blocking submission. The catalog discovers a valid new
adapter automatically, so normal plugin contributions do not edit the framework,
CLI, UI, workflows, root documentation, or snapshots.

Contributions are always welcome! If you have an idea to make this project better, feel free to fork the repository and submit a pull request.
If you encounter a bug or run into any issues, please [open an issue](https://github.com/CVasilakis/scrooge-alert/issues). To help me resolve it quickly, include as much detail as possible.

## 💝 Support & Donations

Did this project save you time or help you snag a deal? Leaving a ⭐ on the repository means a lot! If you'd like to further support my work, consider buying me a coffee. Thanks!

<p align="left">
  <a href="https://www.paypal.com/donate/?hosted_button_id=EQ4BXMGA2R544">
    <img src="assets/qrcode.svg" alt="Donation QR Code" width="150">
  </a>
</p>

## ⚠️ Disclaimer

Please use this script responsibly. It is intended for personal, educational use. Users are solely responsible for complying with each monitored website's terms and applicable rules. The author is not responsible for bans, blocks, or legal issues arising from use of this software.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
