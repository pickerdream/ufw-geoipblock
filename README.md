# UFW-GeoIP block

A script to automate GeoIP filtering for Linux servers using `xtables-addons` and `UFW`. It manages country-based blocking/allowing by injecting rules into UFW's `before.rules`.

## 🛡️ Architecture

The script adds a multi-phase filtering block to UFW without overwriting existing configurations:

1.  **Phase 1: ipset Blacklist**: Drops traffic from IPs matched in the `persistent_offenders` ipset.
2.  **Phase 2: State Management**: Uses `conntrack` (`RELATED,ESTABLISHED`) to allow existing active sessions.
3.  **Phase 3: Local Network**: Automatically detects and allows traffic from the host's directly attached local subnets.
4.  **Phase 4: GeoIP Filtering**: Uses `xt_geoip` to match countries. Non-permitted TCP/UDP traffic is logged (with rate limits) and dropped.

## ✨ Features

- **Auto-Rollback (Dead Man's Switch)**: If you do not run `geoipblock-confirm` within 3 minutes of installation, the firewall changes are automatically reverted to prevent accidental SSH lockouts.
- **Daily Updates**: GeoIP databases are updated daily via a systemd timer. Updates are built in a temporary directory and swapped atomically using `mv`.
- **Download Retries**: If the GeoIP database download fails, it retries 3 times before aborting safely.
- **CSV Configuration**: Manage ports and ranges using a simple CSV file.

## 🎯 Target Use Case & Sweet Spot

This tool is designed for a specific "sweet spot" in server management:

### Ideal Environment
1.  **Debian/Ubuntu Standalone Servers**: Best for "pet" servers where you manage the OS directly (VPS providers like DigitalOcean, Linode, Vultr, or bare metal).
2.  **Bare-Metal Services**: Optimized for environments where services (sshd, nginx, postfix, etc.) run directly on the host OS rather than in containers.
3.  **Direct Internet Connection**: Best for servers that interact directly with client IP addresses at the transport layer (L4).
4.  **Solo Devs & Small Teams**: Perfect for those who need a practical, quick solution to stop overseas brute-force attacks and port scans without the complexity of enterprise-grade IaC.

### 🙅 When NOT to use this tool
- **Docker-based environments**: Docker bypasses the UFW rules used by this script.
- **Behind CDNs (Cloudflare, etc.)**: You will likely block the CDN's edge nodes, affecting legitimate users.
- **Cloud-Native Managed Infrastructure**: If you are on AWS/GCP/Azure, use Security Groups or cloud WAFs which are more efficient at the infrastructure level.

## 📋 Prerequisites

The `install.sh` script attempts to install the following dependencies via `apt-get` on Debian/Ubuntu systems:
- `xtables-addons-common`, `libtext-csv-xs-perl`, `libnet-cidr-lite-perl`, `ipset`, `pkg-config`, `ufw`, `curl`, `python3`

## 🚀 Installation

```bash
git clone https://github.com/jassdack/geoipblock.git
cd geoipblock

# Option A: Dry-Run (Print rules without applying)
sudo ./install.sh --dry-run JP ports.csv

# Option B: Apply configuration from CSV
sudo ./install.sh JP ports.csv

# Option C: Apply configuration from command line
sudo ./install.sh JP 22,80,443

# Option D: Allow multiple countries
# Separate country codes with commas
sudo ./install.sh JP,US,TW ports.csv
```

### MaxMind GeoLite2 Country

DB-IP remains the default. To use MaxMind's **GeoLite2 Country CSV** instead,
create a MaxMind account and obtain an account ID and license key with download access.
See [MaxMind's download documentation](https://dev.maxmind.com/geoip/updating-databases/).

Before installation (or to switch an existing installation), create the configuration
only if it does not already exist, then edit it:

```bash
sudo test -e /etc/geoipblock.conf || sudo install -o root -g root -m 600 geoipblock.conf.sample /etc/geoipblock.conf
sudoedit /etc/geoipblock.conf
```

```bash
GEOIP_SOURCE=maxmind
MAXMIND_ACCOUNT_ID='YOUR_NUMERIC_ACCOUNT_ID'
MAXMIND_LICENSE_KEY='YOUR_LICENSE_KEY'
```

For a new installation, run `sudo ./install.sh JP ports.csv` and follow the confirmation
workflow below. For an existing installation, follow “Upgrade from a version without MaxMind support” below.
Once these scripts are installed, configuration changes only require
`sudo /usr/local/bin/update-geoip.sh`. Daily updates read the same configuration.
Set `GEOIP_SOURCE=dbip` to switch back. The installer preserves an existing configuration.

The converter supports IPv4 and IPv6, uses the geographic country (`geoname_id`),
and skips unknown countries without substituting the ISP's registered country.
It converts the CSV ZIP into the format consumed by `xt_geoip_build`; `.mmdb` files
are not supported. A failed download, conversion or empty build keeps the existing DB.
The updater changes files on disk; already loaded firewall matches may retain old
ranges until rules are reloaded (for example with `sudo ufw reload`).

Keep `/etc/geoipblock.conf` owned by root with mode `600`: it is sourced as shell code
and contains credentials. Do not commit credentials. Uninstallation retains this file;
remove it manually if you want to delete the credentials. Use data according to your
MaxMind license. This product includes GeoLite2 data created by MaxMind, available from
[MaxMind](https://www.maxmind.com), when this source is selected.

### 🚨 Installation Workflow & Rollback
To prevent accidental lockouts, the script uses a 3-minute confirmation window:
1. Run `./install.sh`.
2. Open a **NEW** terminal window and verify you can still SSH into your server.
3. If successful, run the following command in your original terminal to keep the rules:
   ```bash
   sudo geoipblock-confirm
   ```
4. If you fail to run the confirm command within 3 minutes, the GeoIP rules are automatically removed and UFW is reloaded.

### 📝 CSV Configuration Example (`ports.csv`)
The format is `port_range,memo,status`.
```csv
22,SSH Access,block
80,HTTP Web,block
443,HTTPS Secure,block
3000:3010,Dev Web Servers,pass
```
*To disable GeoIP filtering for a rule, change its status to `pass` (or anything other than `block`) and re-run `install.sh`.*

## 🔄 Upgrade from a version without MaxMind support

For an existing installation, replace the updater and install the converter using the steps below. This preserves allowed countries, ports, trusted subnets, ipsets, and systemd configuration. Uninstallation is unnecessary. `git pull` alone does not update the installed files in `/usr/local/bin`.

These steps assume the existing `update-geoip.service` / `update-geoip.timer` units. Run commands in order in the same shell; stop and investigate if a command fails.

### 1. Obtain the updated source

Run from your existing repository clone, on a branch where the MaxMind changes have been published. Save any local changes first.

```bash
git status --short
git pull --ff-only
test -f maxmind-to-dbip.py && test -f geoipblock.conf.sample
```

### 2. Pause scheduled updates and back up the installation

```bash
sudo systemctl stop update-geoip.timer
systemctl is-active update-geoip.service
```

If the service is `active` or `activating`, wait for it to finish. Allow any manually started updater to finish too. An `inactive` result with a nonzero exit code is normal. Investigate `failed` using `journalctl -u update-geoip.service` before continuing.

```bash
GEOIP_BACKUP=$(sudo mktemp -d /var/backups/geoipblock-upgrade.XXXXXXXX)
printf 'Backup: %s\n' "$GEOIP_BACKUP"
sudo cp -a /usr/local/bin/update-geoip.sh "$GEOIP_BACKUP/update-geoip.sh"
sudo cp -a /usr/share/xt_geoip "$GEOIP_BACKUP/xt_geoip"
if sudo test -f /etc/geoipblock.conf; then
    sudo cp -a /etc/geoipblock.conf "$GEOIP_BACKUP/geoipblock.conf"
fi
```

Save the printed backup path for recovery. Versions without MaxMind support normally do not have `/etc/geoipblock.conf`.

### 3. Install dependencies and updated files

```bash
sudo apt-get update
sudo apt-get install -y xtables-addons-common libtext-csv-xs-perl libnet-cidr-lite-perl curl python3
sudo install -o root -g root -m 755 update-geoip.sh /usr/local/bin/update-geoip.sh
sudo install -o root -g root -m 644 maxmind-to-dbip.py /usr/local/bin/maxmind-to-dbip.py
if ! sudo test -e /etc/geoipblock.conf; then
    sudo install -o root -g root -m 600 geoipblock.conf.sample /etc/geoipblock.conf
fi
```

The service executable path is unchanged, so replacing unit files or running `daemon-reload` is unnecessary. These steps do not run `install.sh`: they do not regenerate UFW rules or schedule the three-minute rollback, and `geoipblock-confirm` is not required.

### 4. Select a source and verify the update

Keep `GEOIP_SOURCE=dbip` to continue using DB-IP. To switch to MaxMind, follow “MaxMind GeoLite2 Country” above to set `GEOIP_SOURCE=maxmind` and credentials in `/etc/geoipblock.conf`.

```bash
sudo chown root:root /etc/geoipblock.conf
sudo chmod 600 /etc/geoipblock.conf
sudo systemctl start update-geoip.service
sudo systemctl status update-geoip.service --no-pager
sudo journalctl -u update-geoip.service -n 50 --no-pager
```

Confirm that `systemctl start` succeeded and the logs report a completed update. This is a oneshot service: `inactive (dead)` after success is normal; look for `status=0/SUCCESS`. If updating fails, correct the configuration and retry, or use the recovery steps below.

To load the updated database into firewall rules, ensure you have a recovery path such as a server console, then run the following. Changing sources can change the country assigned to an IP address.

```bash
sudo ufw reload
```

Verify access using a new SSH connection, then resume scheduled updates:

```bash
sudo systemctl start update-geoip.timer
systemctl list-timers update-geoip.timer --all
```

### Recovery if the upgrade fails

Do not leave the update timer stopped indefinitely. Ensure all updater processes have finished, then restore using `$GEOIP_BACKUP` from the same shell (or set it to the saved backup path in a new shell). Use the server console if SSH is unavailable.

```bash
sudo systemctl stop update-geoip.timer
sudo test -f "$GEOIP_BACKUP/update-geoip.sh" && sudo test -d "$GEOIP_BACKUP/xt_geoip"
# Continue only if the check above succeeds
sudo cp -a "$GEOIP_BACKUP/update-geoip.sh" /usr/local/bin/update-geoip.sh
sudo mv /usr/share/xt_geoip "$GEOIP_BACKUP/xt_geoip.failed"
sudo cp -a "$GEOIP_BACKUP/xt_geoip" /usr/share/xt_geoip
if sudo test -f "$GEOIP_BACKUP/geoipblock.conf"; then
    sudo cp -a "$GEOIP_BACKUP/geoipblock.conf" /etc/geoipblock.conf
fi
sudo ufw reload
sudo systemctl start update-geoip.timer
```

Choose another name if `xt_geoip.failed` already exists. The old updater ignores the new configuration file and converter. Manually remove `/etc/geoipblock.conf` if you do not want to retain newly added credentials. This restores the script and database, not packages upgraded through apt.

## ⚙️ Configuration Overrides

To manually override the auto-detected local trusted networks, pass the `TRUSTED_SUBNETS` environment variable:
```bash
sudo TRUSTED_SUBNETS="10.0.0.0/8 192.168.1.0/24" ./install.sh JP ports.csv
```

### Note on Rule Priority
The GeoIP block is injected at the top of UFW's `before.rules`. Because Phase 3 (Local Network Trust) uses an `ACCEPT` rule, any local traffic matching these subnets will bypass the GeoIP block and any subsequent rules in UFW. 
- If you need to **deny** specific local IPs, you must either:
  1. Define those deny rules manually *above* the geoipblock markers in `before.rules`.
  2. Or, narrow down `TRUSTED_SUBNETS` to only include the specific management IPs you trust.
- Setting `TRUSTED_SUBNETS=""` (empty string) will disable the automatic local trust phase entirely.

## 🛠️ Maintenance & Monitoring

```bash
# Check timer status
systemctl status update-geoip.timer

# View update logs
journalctl -u update-geoip.service
```

## 🖤 Manual Blacklisting (ipset)

Phase 1 of the defense system uses a high-performance `ipset` named `persistent_offenders`. You can use this to manually block specific IPs (even those from your allowed country) for 30 days:

```bash
# Block an IP
sudo ipset add persistent_offenders 1.2.3.4

# Remove an IP from blacklist
sudo ipset del persistent_offenders 1.2.3.4

# List all blacklisted IPs
sudo ipset list persistent_offenders
```

## 🤝 Integration with Fail2Ban

You can integrate Fail2Ban with this tool to achieve multi-layered defense. By pointing Fail2Ban to the `persistent_offenders` ipset, you can block brute-force attackers at Phase 1 (the fastest layer).

### Sample Fail2Ban Action (`/etc/fail2ban/action.d/geoipblock.conf`)
```ini
[Definition]
actionban = ipset add persistent_offenders <ip> -exist
actionunban = ipset del persistent_offenders <ip> -exist
```

## 🔐 Let's Encrypt (Certbot) Compatibility
If you use HTTP-01 validation, use these hooks to temporarily bypass the GeoIP block during renewal:
```bash
--pre-hook "iptables -I ufw-before-input 1 -p tcp --dport 80 -j ACCEPT; ip6tables -I ufw6-before-input 1 -p tcp --dport 80 -j ACCEPT" \
--post-hook "iptables -D ufw-before-input -p tcp --dport 80 -j ACCEPT; ip6tables -D ufw6-before-input -p tcp --dport 80 -j ACCEPT"
```

## 🧹 Uninstallation
```bash
sudo ./uninstall.sh
```

## ⚠️ Known Limitations & Compatibility

### 1. Docker Bypass
Docker directly manipulates `iptables` and routes traffic through the `PREROUTING` and `DOCKER` chains, effectively **bypassing UFW's `INPUT` chain**. 
- Ports published via Docker (e.g., `docker run -p 80:80`) will **NOT** be protected by this GeoIP script.

### 2. CDNs and Reverse Proxies (Cloudflare, etc.)
This script operates at Layer 4 (iptables). If your server is behind a CDN like Cloudflare, `iptables` will only see Cloudflare's Edge IPs, not the real visitor's IP.
- If you use a CDN, you must set your HTTP/HTTPS ports to `pass` in `ports.csv` and rely on the CDN's own WAF for GeoIP filtering. Otherwise, legitimate traffic may be blocked if the CDN edge node is located outside your allowed country.

## ⚠️ Troubleshooting
- **Database Download Fails**: Check `GEOIP_SOURCE` in `/etc/geoipblock.conf`. For MaxMind, verify the account ID, license key, GeoLite2 download access, and HTTPS access to MaxMind and its redirect destination. For DB-IP, use a current `xtables-addons` package.
- **Rules Not Applying**: Run `lsmod | grep xt_geoip` to ensure the kernel module is loaded. Some VPS kernels (like OpenVZ) may not support custom kernel modules.
- **UFW Errors**: Check `/var/log/syslog` for iptables syntax errors.

## ⚖️ Disclaimer (免責事項)
**USE AT YOUR OWN RISK.** This tool modifies your system's firewall rules. 
- The author is **NOT responsible** for any damage, data loss, or server lockouts caused by the use of this script.
- 本ツールの使用によるいかなる損害（サーバーへのアクセス不能等）についても、作者は一切の責任を負いません。自己責任でご利用ください。

## 📜 Acknowledgments & Data Sources
This tool relies on the `xt_geoip` module provided by `xtables-addons`. 
- This product uses the DB-IP IP to City Lite database available from [https://db-ip.com](https://db-ip.com), licensed under CC-BY 4.0.
- Alternately, this product may include GeoLite2 data created by MaxMind, available from [https://www.maxmind.com](https://www.maxmind.com).

## 🔮 Future Considerations & Technical Outlook

As the Linux infrastructure landscape evolves, users should be aware of the following long-term trends:

1.  **The Sunset of iptables**: Linux is steadily migrating from `iptables` to `nftables`. Since this tool depends on `xtables-addons` (a Netfilter extension), it will reach its end-of-life when major distributions eventually drop support for the legacy `xtables` framework.
2.  **Container Orchestration (Kubernetes, etc.)**: The gap between host-level firewalling and container orchestration is widening. In environments like Kubernetes, managing firewall rules manually on the host OS can interfere with complex network policies and pod-to-pod communication.
3.  **The Shift to Edge Defense**: Modern cloud best practices favor "Edge Defense" (filtering at the CDN/WAF level like Cloudflare or AWS WAF). Dropping packets at the origin server after they have already consumed network bandwidth and CPU cycles is becoming a legacy approach compared to stopping threats at the network edge.

*This tool remains a powerful and practical solution for the "standalone VPS" era, but users should consider native cloud firewalls for new, large-scale cloud-native architectures.*
