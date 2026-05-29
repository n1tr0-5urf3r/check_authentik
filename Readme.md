<p align="center"><img alt="Ihlecloud" src="https://me.ihlecloud.de/logo.png" height="76"></p></img>

# Icinga2 Scripts

### check_authentik

A plugin to use in Icinga / Nagios that monitors an Authentik instance through the Authentik API.

### [Download Latest](./check_authentik.py) ・ [Usage](#usage) ・ [Examples](#examples) ・ [Donate](#donation)

---

### Features

- Checks Authentik API reachability and system information.
- Warns about Authentik version updates and outdated outposts.
- Reports failed or warning task states.
- Checks connected workers and worker version mismatches.
- Checks outpost health, stale outposts, and outpost version mismatches.
- Supports self-signed certificates with `--insecure`.
- Supports token auth from a CLI flag, token file, or `AUTHENTIK_TOKEN`.

### Installation

```bash
python3 -m pip install -r requirements.txt
chmod +x check_authentik.py
```

### Authentik API Token

Create a token in the Authentik Admin Interface:

1. Open Authentik as an administrator.
2. Go to **Directory** -> **Tokens and App passwords**.
3. Create an API token for a service user.
4. Give that service user read permissions for the monitored areas:
   admin/system, version, tasks, workers, and outposts.
5. Store the token securely, preferably in a root-readable file used with `--token-file`.

Avoid putting tokens directly in Icinga/Nagios command definitions when possible.

### Usage

```text
./check_authentik.py --url URL [--token TOKEN] [--token-file PATH]
                     [--check all|system|version|tasks|workers|outposts]
                     [--insecure] [--timeout SECONDS]
                     [--min-workers N]
                     [--task-max-age SECONDS]
                     [--max-clock-skew SECONDS]
                     [--outpost-stale-seconds SECONDS]
```

Options:

```text
--url URL                  Authentik base URL
--token TOKEN              Authentik bearer token
--token-file PATH          File containing the bearer token
--user USER, --run-as USER Require this local OS user
--insecure                 Disable TLS certificate verification
--timeout SECONDS          HTTP timeout, default 10
--check CHECK              all, system, version, tasks, workers, outposts
--min-workers N            Minimum worker count, default 1
--task-max-age SECONDS     Only alert on tasks modified in this window, default 3600
--max-clock-skew SECONDS   Warning threshold, default 300
--outpost-stale-seconds N  Critical threshold, default 300
```

### Checks

- `all`: Runs all checks. This is the default.
- `system`: Calls `/api/v3/admin/system/`, verifies the response, runtime version, and server clock skew.
- `version`: Calls `/api/v3/admin/version/`, warns when Authentik or outposts are outdated.
- `tasks`: Calls `/api/v3/tasks/tasks/status/` for metrics and `/api/v3/tasks/tasks/` for recent task failures. Returns critical for recent `error` tasks and warning for recent `rejected` or `warning` tasks.
- `workers`: Calls `/api/v3/tasks/workers/`, checks minimum worker count and worker version matching.
- `outposts`: Calls `/api/v3/outposts/instances/` and each outpost health endpoint.

### Examples

```bash
./check_authentik.py --url https://auth.example.com --token-file /etc/icinga2/secrets/authentik.token
./check_authentik.py --url https://auth.example.com --token "$AUTHENTIK_TOKEN"
./check_authentik.py --url https://auth.example.com --check version --token-file ./token
./check_authentik.py --url https://auth.example.com --check workers --min-workers 2 --token-file ./token
./check_authentik.py --url https://auth.example.com --check tasks --task-max-age 7200 --token-file ./token
./check_authentik.py --url https://auth.example.com --insecure --token-file ./token
```

You can also pass the token through the environment:

```bash
export AUTHENTIK_TOKEN="..."
./check_authentik.py --url https://auth.example.com
```

### Example Output

```text
OK - authentik checks passed: system, version, tasks, workers, outposts; metrics: authentik_outdated=0, clock_skew=0.012, outpost_outdated=0, outpost_stale=0, outpost_version_outdated=0, outposts=2, workers=1 | authentik_outdated=0 clock_skew=0.012 outpost_outdated=0 outpost_stale=0 outpost_version_outdated=0 outposts=2 workers=1
```

### Icinga Example

```icinga2
object CheckCommand "check_authentik" {
  command = [ PluginDir + "/check_authentik.py" ]

  arguments = {
    "--url" = "$authentik_url$"
    "--token-file" = "$authentik_token_file$"
    "--check" = "$authentik_check$"
    "--min-workers" = "$authentik_min_workers$"
    "--insecure" = {
      set_if = "$authentik_insecure$"
    }
  }

  vars.authentik_check = "all"
  vars.authentik_min_workers = 1
}
```

```icinga2
apply Service "authentik" {
  check_command = "check_authentik"
  vars.authentik_url = "https://auth.example.com"
  vars.authentik_token_file = "/etc/icinga2/secrets/authentik.token"
  assign where host.name == "authentik.example.com"
}
```

### Nagios Example

```nagios
define command {
  command_name check_authentik
  command_line $USER1$/check_authentik.py --url "$ARG1$" --token-file "$ARG2$" --check "$ARG3$"
}
```

```nagios
define service {
  host_name            authentik.example.com
  service_description Authentik
  check_command        check_authentik!https://auth.example.com!/etc/nagios/secrets/authentik.token!all
}
```

---

## Donation

If you like this work, a donation is very welcome :)

[![Donate](https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif)](https://www.paypal.com/donate/?hosted_button_id=KXMYX49C6MLLN)
