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
     - [Monitored Items](#monitored-items)
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

Supported targets are discovered from the checked-in plugin packages. Run
`./scripts/run.sh --help` to see the exact target flags available in your checkout.
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

3. **Run the installation script:**

    ```sh
    chmod +x install.sh
    ./install.sh
    ```

    The `install.sh` script will automatically create a Python virtual environment, install the required dependencies, and set up one systemd user timer per scraper using its valid configured `execution_interval`, or the plugin's built-in default when unset. No `sudo` or elevated privileges are required for the installation.

4. **Configure your settings:**

    Proceed to the [Configuration](#%EF%B8%8F-configuration) section for scraper,
    notification, and project-wide settings.

## ⚙️ Configuration

All user parameters live in strict, unversioned JSON files under `config/`. Runtime
never modifies them; machine-owned data is stored separately under `state/`.

### File 1: Scraper Configuration (`config/<target>.json`)

Each scraper reads a strict, unversioned, read-only JSON file in `config/` (for example,
`config/<target>.json`) containing its settings and monitored items. Machine state is
stored separately in the ignored, schema-versioned `state/<target>.json`. Example files
live beside their plugins:

```sh
target=TARGET_NAME  # replace with a target shown by ./scripts/run.sh --help
cp "src/core/scrapers/plugins/$target/config.example.json" "config/$target.json"
nano "config/$target.json"
```

A complete file is structured like this:

```json
{
  "settings": {
    "execution_interval": "1h",
    "log_retention_days": 7,
    "notify_scraping_errors": true
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

> [!NOTE]
> Changing `execution_interval` does not take effect on its own. After editing it, apply it to the live timer with the [Set Execution Interval](#set-execution-interval) script: `./scripts/schedule.sh`.

#### Monitored Items

The `items` array lists the entries you want to monitor. Every row needs a unique,
stable `id`; separate IDs may intentionally use the same source input.

| Field | Type | Source | Description |
| :--- | :--- | :--- | :--- |
| `id` | String | **User-defined** | Unique stable state key for this row. |
| `name` | String | **User-defined** | A friendly naming label used inside the notifications. |
| `target_price` | Number | **User-defined** | The maximum price threshold. If the price drops below this, you get alerted; use `0` to monitor without an alert threshold. |
| `skip` | Boolean | **User-defined** | Optional. Set to `true` to skip monitoring this item. Defaults to `false`. |
Runtime `last_price` and `last_checked` values are written to `state/<target>.json`, not
to user configuration. Timestamps are RFC 3339 UTC strings ending in `Z`.

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
preserved by `--ping`.

At least one valid URL is required for background/systemd execution. An interactive run
may continue without one so configuration problems can be inspected. Invalid entries are
ignored when a valid endpoint also exists and are detailed by `./scripts/run.sh --ping`.
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
schema-versioned state file is machine-owned; `config/general.json` remains unversioned
and read-only.

> [!NOTE]
> With the defaults above you get a reminder about once a month, on a Saturday around 13:00 in your server's local time. The time is approximate: the reminder goes out on the first scraper run at or after the chosen day and time, so if your scrapers only run every few hours it might arrive a little later that day (or the next morning), but never earlier. Months are counted in weeks, so `1 month` means every 4th Saturday, `3 months` means every 13th, and so on, which keeps every reminder on the same day and time.

## 💻 Usage

There are two ways to execute the script: automatically via the scheduled systemd timer, or manually for testing.

### Automated Systemd Execution

Once `install.sh` has run successfully, each scraper executes automatically via its own systemd timer — on its plugin-defined default cadence, or on the valid cadence set by its [execution_interval](#scraper-settings) setting. The systemd timer applies a randomized up-to-3m startup delay before launching the execution wrapper (`scripts/run.sh`) to simulate human timing and avoid exact scheduling footprints.

### Manual Execution

You can manually interact with the application using the wrapper script. You can safely interrupt the manual execution at any time by pressing `Ctrl+C`.

```
./scripts/run.sh [-h] [--quiet] [--status] [--ping] [--<target> ...]
```

#### Available CLI Flags:

**Execution Flags:**
These flags modify the overall behavior of the script or trigger user assistance routines.

| Flag&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Action |
| :--- | :--- |
| `-h`, `--help` | Displays the help message with all available script arguments. |
| `--quiet` | Suppresses all console output and redirects execution logs to the `logs/` directory. This is utilized by the systemd setup to ensure silent background operation. |
| `--status` | Performs a comprehensive health check. It validates the configuration, and verifies the background systemd service and timer status. |
| `--ping` | Sends a test notification directly to the Apprise URLs in `config/general.json`, then immediately exits. |

**Target Scraper Flags:**
These flags allow you to isolate execution to specific platforms. If no target flags are provided, the script defaults to running all registered scrapers sequentially. They can be combined with `--quiet`.

| Flag&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Action |
| :--- | :--- |
| `--<target>` | Activates only the specified target's scraper (e.g., `--skroutz`). You can pass one or more target flags simultaneously. |

> [!NOTE]
> Only one instance of a specific scraper target is allowed to run at a time to avoid triggering anti-bot protections. If a background execution for a target (e.g., Skroutz) is currently in progress, your manual run for that target will be blocked and skipped. If you need to forcefully stop all active background executions to run the scraper manually, you can safely use the [stop script](#stop-active-runs): `./scripts/stop.sh`. This stops all the current background runs but will not break any future scheduled executions.

#### Status Check:

If you run the script using the `--status` flag, the script verifies the integrity of
your JSON configuration, validates notification URLs, and queries systemd to display the
following background execution details:

```
./scripts/run.sh --status
```

- **Systemd Timer Active:** Shows whether the timer is currently active.
- **Last Execution Time:** Displays when the script was last run.
- **Last Execution Status:** Indicates last execution results and if any errors happened.
- **Next Scheduled Execution:** Displays the next scheduled run or if it's currently running.

Background runs expose precise exit statuses through **Last Execution Status**:

| Code | Meaning |
| :--- | :--- |
| `15` | A scraper products config could not be loaded. |
| `16` | Notification configuration in `config/general.json` is unusable. |
| `17` | The store blocked or rate-limited the scraper. |
| `18` | A parser or unexpected scraper fault exhausted all retries. |
| `19` | Scraper state could not be loaded or persisted atomically. |
| `20` | At least one configured notification failed; shown as a yellow warning. |
| `21` | The selected scraper's private dependencies are missing. |
| `42` | Every selected scraper was already running. |
| `130` | The run was interrupted. |

#### Test Notifications:

To test notification URLs without waiting for a scheduled run or price drop, use
`--ping`:

```
./scripts/run.sh --ping
```

This will send a test message to each configured Apprise URL(s). It will output a report of successes and failures, helping you quickly identify and debug any misconfigured notification endpoints.

> [!TIP]
> If the script fails to run in the background or you do not receive expected notifications, please consult the [Troubleshooting & Debugging](#-troubleshooting--debugging) section. If your problem persists, feel free to [open an issue](https://github.com/CVasilakis/scrooge-alert/issues).

### Helper Scripts

The project includes several helper scripts to manage your background scraper services and update the application. Most are located in the `scripts/` directory, while the install and update scripts are in the root directory. They support a `--help` flag and can be applied to specific targets.

#### Install & Add Scrapers
Sets up the Python virtual environment and installs the systemd timer(s) and service(s). Run it as many times as you like to add more scrapers later:

```
./install.sh [-h] [--<target> ...]
```

| Flag&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Action |
| :--- | :--- |
| `-h`, `--help` | Show the help message and exit. |
| `--<target>` | Install and enable only the specified target's scraper (e.g., `--skroutz`). You can pass one or more target flags simultaneously. If no target flag is provided, every registered scraper is installed and enabled. |

#### Stop Active Runs
Stops the currently running scraper service(s), aborting any scrape in progress:

```
./scripts/stop.sh [-h] [--<target> ...]
```

| Flag&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Action |
| :--- | :--- |
| `-h`, `--help` | Show the help message and exit. |
| `--<target>` | Stop only the specified target's scraper (e.g., `--skroutz`). You can pass one or more target flags simultaneously. If no target flag is provided, every running scraper service is stopped. |

#### Pause Background Schedule
Stops and disables the background schedule (systemd timer) so the scraper(s) no longer run automatically:

```
./scripts/disable.sh [-h] [--<target> ...]
```

| Flag&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Action |
| :--- | :--- |
| `-h`, `--help` | Show the help message and exit. |
| `--<target>` | Disable only the specified target's scraper. You can pass one or more target flags simultaneously. If no flag is provided, every installed scraper's timer is disabled. |

#### Resume Background Schedule
Re-enables and starts the background schedule (systemd timer) for the installed scraper(s):

```
./scripts/enable.sh [-h] [--<target> ...]
```

| Flag&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Action |
| :--- | :--- |
| `-h`, `--help` | Show the help message and exit. |
| `--<target>` | Enable only the specified target's scraper. You can pass one or more target flags simultaneously. If no flag is provided, every installed scraper's timer is enabled. |

#### Set Execution Interval
Applies each scraper's configured `execution_interval` (from `config/<target>.json`) to the installed systemd timer. Run it whenever you change an interval:

```
./scripts/schedule.sh [-h] [--<target> ...]
```

| Flag&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Action |
| :--- | :--- |
| `-h`, `--help` | Show the help message and exit. |
| `--<target>` | Apply only the specified target's interval (e.g., `--skroutz`). You can pass one or more target flags simultaneously. If no flag is provided, every installed scraper's timer is updated to match its configured interval. A scraper whose config file is missing, or whose `execution_interval` is unsupported, is reported and left unchanged. |

#### Remove Scrapers & Uninstall
Performs a full or partial teardown of the background services:

```
./scripts/uninstall.sh [-h] [--<target> ...]
```

| Flag&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Action |
| :--- | :--- |
| `-h`, `--help` | Show the help message and exit. |
| `--<target>` | Removes only the specified scrapers' units, leaving the virtual environment and other targets intact. You can pass one or more target flags simultaneously. With no flag, removes every installed systemd timer/service and deletes the Python virtual environment. |

#### Update to Latest Version
Updates Scrooge Alert to the latest version by pulling from the repository and reinstalling the scraper(s) you previously installed:

```
./update.sh
```

## 🔔 Notifications & Messages

You might receive the following push notification alerts throughout the lifecycle of the script:

| Notification&nbsp;Title&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Trigger Condition |
| :--- | :--- |
| **Scrooge Alert - Price Drop!** | Sent when an item's observed price falls below your price limit. Listing-result plugins send one alert per matching offer, each linking directly to that offer. |
| **Scrooge Alert - Tracking Stale** | Sent if a specific item continuously fails the scrape. |
| **Scrooge Alert - Scraping Errors** | Sent if the application hits request limits or unhandled exceptions. Can be turned off per scraper via the [notify_scraping_errors](#scraper-settings) setting. |
| **Scrooge Alert - Script Crash** | Sent if the script completely failed to run. |
| **Scrooge Alert - Status Update** | Periodic liveness reminder confirming the scrapers still run in the background, as well as notifying you of any available updates. Can be turned off via the project-wide [reminder](#general-settings) setting. |
| **Scrooge Alert - Test Notification** | Sent when manually invoking the script with the `--ping` flag. |

## 🗑️ Uninstallation

To completely remove the background service and clean up the Python virtual environment, execute the uninstallation script:

```sh
./scripts/uninstall.sh
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

**1. Failing to Fetch Items:**

If the script cannot retrieve data for certain items, review the target's package-local guide and verify the source inputs in `config/<target>.json`. For URL-based targets, check for broken links; invalid URLs are often redirected to similar pages.
If the inputs are correct but failures persist across multiple items, your connection has likely been temporarily restricted by the website's anti-bot protection. To mitigate this, reduce your network traffic by tracking fewer active items, or decrease the script's run frequency by setting a longer [`execution_interval`](#scraper-settings) in the scraper's config and applying it with `./scripts/schedule.sh`.

> [!TIP]  
> For the best results, this script should **not** be run behind a VPN and should ideally be executed from a standard Greek residential IP address. High traffic coming from known VPS providers, data centers, or VPNs is very likely to trigger strict anti-bot mechanisms, causing the script to fail.

**2. Not Receiving Notifications:**

If you do not receive a test message, review the [Notification Settings](#notification-settings)
section and verify the Apprise URLs in `config/general.json`.
You can easily test your notification setup using the `--ping` flag:

```sh
./scripts/run.sh --ping
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

To confirm the script is running in the background, use the `--status` flag. If the script reports no errors, you can be sure it is configured correctly and running in the background:

```sh
./scripts/run.sh --status
```

The systemd execution metrics reported by the `--status` flag only reflect background scheduled executions, not manual runs.
If the command reveals any warnings, please run `./update.sh` which re-installs the background service and ensures that you are on the latest version. If the issue persists after updating, please [open an issue](https://github.com/CVasilakis/scrooge-alert/issues) for further assistance.
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

Navigate to the project directory and run the update script. This will pull the latest changes using Git and automatically run the installation script again to ensure any new dependencies are installed and your environment is properly updated:

```sh
./update.sh
```

When run manually, the script automatically checks the online repository for updates. If a newer version is found, a message is displayed in the terminal.
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

1. Run `./scripts/uninstall.sh` in the old folder to clean up the existing background processes.
2. Clone the repository into your new desired folder using Git.
3. Move your `config/` and `state/` data from the old folder to the new one.
4. Run `./install.sh` in the new location to rebuild the environment and background timers.
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
./scripts/disable.sh
```

To re-enable background scheduled executions later, run:

```sh
./scripts/enable.sh
```
</details>

## 🗺️ Future Updates (Roadmap)

- [x] **Enhanced Evasion:** Rotate TLS sessions and request fingerprints intelligently.
- [x] **Multi-Marketplace Expansion:** Support more scrapers for other marketplaces.
- [ ] **User Interface:** Introduction of a Web UI for non-CLI management.
- [ ] **Docker Support:** Add an alternative Dockerized setup via docker-compose configuration.

To see all the undergoing feature requests or to request a new feature, please check the [open issues](https://github.com/CVasilakis/scrooge-alert/issues).

## 🤝 Contributing & Issues

New stores use the in-repository plugin contract: run
`./scripts/plugin-create.sh`, keep `plugin.py` import-light, export `Client` from
`client.py`, add mocked target-owned tests, and run
`./scripts/plugin-check.sh --<target>`. The immutable plugin
catalog and shell manifest discover the new adapter automatically, so framework,
CLI, UI, and management-script edits are unnecessary. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the complete input, URL, field, setting, result,
exception, dependency, and testing contracts.

Contributions are always welcome! If you have an idea to make this project better, feel free to fork the repository and submit a pull request.
To add a marketplace, follow [CONTRIBUTING.md](CONTRIBUTING.md): a scraper is one
self-contained package discovered by the immutable catalog, with no application,
shell, or UI edits required.
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
