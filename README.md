# <img src="/UPS-Logo-Round.png" alt="The Ultimate Plex Stack" width="400px"></img>

Welcome to my Plex stack repository! This repository showcases my Docker Compose setup for managing various media-related services using Docker containers. The compose file is meant to be changed to each users liking as I know not everyone has the same requirements. Hope you enjoy!

Currently you can choose from the **Basic** or the **Advanced** compose using Docker Compose profiles.

## Overview

**Basic Compose** Includes:

- **Plex:** Media server for streaming movies and TV shows.
- **Radarr:** Movie management and automation.
- **Sonarr:** TV show management and automation.
- **Prowlarr:** Indexer manager for Radarr and Sonarr.
- **Overseerr:** Request management and monitoring for Plex.
- **Qbittorrent:** BitTorrent client with VPN support.

**Advanced Compose** Includes:

- **Plex:** Media server for streaming movies and TV shows.
- **Radarr:** Movie management and automation.
- **Sonarr:** TV show management and automation.
- **Prowlarr:** Indexer manager for Radarr and Sonarr.
- **Overseerr:** Request management and monitoring for Plex.
- **Qbittorrent:** BitTorrent client with VPN support.
- **Unpackerr:** Automatically extracts downloads for Sonarr/Radarr.
- **Tautulli:** Analytics and monitoring for Plex.
- **Bazarr:** Subtitle management for movies and TV shows.
- **Autobrr:** Used to grab torrents immediately as they are released.
- **Cross-seed:** Used to find and add cross-seeds for existing torrents.
- **Flaresolverr:** Used as a proxy server to bypass Cloudflare and DDoS-GUARD protection.
- **Dozzle:** Used to view the logs of any container.
- **Watchtower:** Automatically updates containers when new images are available.
- **Uptime Kuma:** Monitors service uptime and sends alerts on failures.

## Dependencies

1. Linux
2. Docker / Docker Compose
3. OPTIONAL: Portainer - Docker GUI

## How to Use

1. Clone this repository / Copy the docker-compose.yml file:

   ```bash
   git clone https://github.com/DonMcD/ultimate-plex-stack.git
   ```

2. Copy `.env.example` to `.env` and fill in the required details
3. (Recommended) Pin images to immutable digests:

   ```bash
   ./scripts/pin-images.sh .env
   ```

4. Start the stack:

   ```bash
   # Basic profile (default services)
   docker compose up -d

   # Advanced profile (adds extra services)
   docker compose --profile advanced up -d
   ```

5. OPTIONAL: Setup a reverse proxy so you can use radarr.my-domain.com instead of 192.168.1.10 to access each of your apps

Docker Compose reads `.env` automatically. The pinning script keeps your stack stable by locking to digests.

Cross-seed config lives at `cross-seed/config/config.js`. Update `linkDirs` to match your downloads path and set `QBITTORRENT_USER` / `QBITTORRENT_PASS` and `PROWLARR_API_KEY` in `.env` (Prowlarr is used for the torznab feed).

## Example of Environment variables in Portainer

Keep in mind some variable names have changed since this screenshot was taken
<img width="657" alt="image" src="https://github.com/DonMcD/ultimate-plex-stack/assets/90471623/9a614eb0-8ff7-4eb9-b154-61c08cd595e9">

File location examples:

- {MEDIA_SHARE} = /share
- {BASE_PATH} = /home/username/docker

To allow hardlinking to work (which you will definitely want!) you will have to use the same root folder in all of your container path. In this example we use "/share", so in the container it will look like "/share/downloads/tv"

An example of my folder structure:  
![image](https://github.com/DonMcD/ultimate-plex-stack/assets/90471623/2003ac26-a929-4ff6-ad67-e35fc51fb51a)

- Feel free to expand your folders to also include "books" or "music" as you need for your setup

1. In Radarr you will want to set your category to "movies", this will create the movies folder
2. In Sonarr you will want to set your category to "tv", this will create the tv folder

Anytime you reference your media folder in a container you want the path to look like /share/media/tv instead of /tv like a lot of the default guides say, if you do end up mapping the path as /tv hardlinking will not work

## Notifications & Monitoring

This stack includes **Watchtower** for automated container updates and **Uptime Kuma** (advanced profile) for monitoring container health. Both can send notifications via Discord webhooks and email.

### Watchtower (Auto-Updates)

Watchtower automatically monitors and updates your containers when new images are available. It's configured to:

- Check for updates daily (configurable)
- Clean up old images after updates
- Send notifications via Discord and/or email when updates occur

**Configuration:**

1. Set up `WATCHTOWER_NOTIFICATION_URL` in your environment file (e.g. `.env`)

2. **Discord Notifications:**

   - Create a Discord webhook in your server (Server Settings → Integrations → Webhooks)
   - Use format: `discord://<webhook-id>/<webhook-token>`
   - Example: `discord://123456789012345678/abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ12`

3. **Email Notifications:**

   - Use Shoutrrr email format: `email://smtp.example.com:587/?from=from@example.com&to=to@example.com&auth=plain&user=user&pass=pass`
   - For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833) instead of your regular password
   - Example: `email://smtp.gmail.com:587/?from=you@gmail.com&to=you@gmail.com&auth=plain&user=you@gmail.com&pass=yourapppassword`

4. **Multiple Notifications:**
   - Combine Discord and email by comma-separating URLs:
   - `WATCHTOWER_NOTIFICATION_URL=discord://id/token,email://smtp.example.com:587/?from=from@example.com&to=to@example.com&auth=plain&user=user&pass=pass`

**Excluding Containers from Auto-Updates:**

To prevent specific containers from being automatically updated, add this label to the service in `docker-compose.yml`:

```yaml
labels:
  - "com.centurylinklabs.watchtower.enable=false"
```

**Risks & Considerations:**

- **Breaking Changes:** Auto-updates may introduce incompatible changes. Consider excluding critical containers initially.
- **Testing:** Test updates in a staging environment when possible.
- **Rollback:** Keep backups of your configuration volumes in case an update causes issues.

### Uptime Kuma (Health Monitoring)

Uptime Kuma monitors your services and sends alerts when they go down or fail health checks. It's available in the "advanced" profile.

**Setup:**

1. Start the stack with the advanced profile:

   ```bash
   docker compose --profile advanced up -d
   ```

2. Access the dashboard at `http://<your-pi-ip>:3001`

3. **Initial Configuration:**

   - Create an admin account on first access
   - Add monitors for each service (Plex, Radarr, Sonarr, etc.)
   - Configure notification channels (Discord, Email, etc.)

4. **Configure Notifications:**

   - Go to Settings → Notifications
   - Add Discord webhook (uses standard Discord webhook URL format)
   - Add Email notification (SMTP configuration)
   - Test notifications to ensure they work

5. **Add Monitors:**
   - Click "Add New Monitor"
   - Select monitor type (HTTP(s), TCP, etc.)
   - Enter service URL (e.g., `http://localhost:7878` for Radarr)
   - Set check interval (default: 60 seconds)
   - Assign notification channels

**Example Monitor URLs:**

- Radarr: `http://localhost:7878`
- Sonarr: `http://localhost:8989`
- Plex: `http://localhost:32400`
- Overseerr: `http://localhost:5055`
- Uptime Kuma: `http://localhost:3001`

**Note:** Use `localhost` for services on the same host, or use the container network names (e.g., `http://radarr:7878`) if monitoring from within the Docker network.

**Risks & Considerations:**

- **False Positives:** Network issues or slow responses may trigger false alerts. Fine-tune check intervals and retry settings.
- **Resource Usage:** Uptime Kuma uses minimal resources (~50-100MB RAM) but adds overhead.
- **Notification Fatigue:** Configure appropriate alert thresholds to avoid being overwhelmed.

### Notification Channel Setup

**Discord Webhook:**

1. Go to your Discord server → Server Settings → Integrations → Webhooks
2. Click "New Webhook" and copy the webhook URL
3. For Watchtower: Extract the ID and token from the URL (format: `https://discord.com/api/webhooks/<ID>/<TOKEN>`) and use `discord://<ID>/<TOKEN>`
4. For Uptime Kuma: Use the full webhook URL directly

**Email (SMTP):**

- **Gmail:** Use an App Password (not your regular password). Enable 2FA first, then generate an App Password at https://myaccount.google.com/apppasswords
- **Other Providers:** Use your SMTP server details (host, port, username, password)

## External Access with Cloudflare Tunnel

Cloudflare Tunnel (cloudflared) enables secure external access to your services without port forwarding, firewall configuration, or exposing ports directly to the internet. All traffic is encrypted and routed through Cloudflare's network.

### Prerequisites

1. A Cloudflare account (free tier works)
2. A domain name added to your Cloudflare account
3. DNS management through Cloudflare (change nameservers if needed)

### Setup Instructions

#### 1. Create a Cloudflare Tunnel

1. Log in to your [Cloudflare Dashboard](https://dash.cloudflare.com/)
2. Select your domain
3. Go to **Zero Trust** (or **Access** → **Tunnels**)
4. Click **Networks** → **Tunnels**
5. Click **Create a tunnel**
6. Select **Cloudflared** as the connector type
7. Give your tunnel a name (e.g., "plex-stack")
8. Copy the **Tunnel Token** (you'll need this for the `.env` file)

#### 2. Configure Environment Variable

Add the tunnel token to your `.env` file:

```bash
CLOUDFLARE_TUNNEL_TOKEN=your-tunnel-token-here
```

#### 3. Configure Routes in Cloudflare Dashboard

For each service you want to expose, add a public hostname:

1. In the tunnel configuration, click **Public Hostnames**
2. Click **Add a public hostname**
3. Configure each service:

   **Example for Plex:**
   - Subdomain: `plex`
   - Domain: `yourdomain.com`
   - Service: `http://localhost:32400`
   - Path: (leave empty)

   **Example for Overseerr:**
   - Subdomain: `overseerr`
   - Domain: `yourdomain.com`
   - Service: `http://localhost:5055`
   - Path: (leave empty)

   **Example for Radarr:**
   - Subdomain: `radarr`
   - Domain: `yourdomain.com`
   - Service: `http://localhost:7878`
   - Path: (leave empty)

   **Example for Sonarr:**
   - Subdomain: `sonarr`
   - Domain: `yourdomain.com`
   - Service: `http://localhost:8989`
   - Path: (leave empty)

   Repeat for any other services you want to expose (Prowlarr, Tautulli, Bazarr, etc.)

   **Note:** Since Plex uses `network_mode: host`, use `localhost:32400`. For other services, use `localhost` with their respective ports.

#### 4. Start the Cloudflared Service

Start the stack with the cloudflared service:

```bash
docker compose up -d cloudflared
```

Or start the entire stack:

```bash
docker compose up -d
```

The cloudflared container will connect to Cloudflare's network and route traffic to your services.

#### 5. Configure Authentication (Recommended)

For enhanced security, set up Cloudflare Access authentication:

1. In Cloudflare Dashboard, go to **Zero Trust** → **Access** → **Applications**
2. Click **Add an application**
3. Select **Self-hosted**
4. Configure the application:
   - Application name: e.g., "Plex Stack - Overseerr"
   - Session duration: Your preference
   - Application domain: Select the subdomain (e.g., `overseerr.yourdomain.com`)
5. Set up access policies:
   - Policy name: e.g., "Allow My Email"
   - Action: Allow
   - Include: Email addresses (add your email addresses)
   - Or use other criteria like country, IP ranges, etc.
6. Click **Add application**

Repeat for each service you want to protect with Access authentication.

**Benefits of Cloudflare Access:**
- Additional authentication layer before accessing services
- Users must log in with their email (or other configured methods)
- Free tier supports unlimited users
- Can configure per-service access policies
- Works alongside service-level authentication

#### 6. Verify Access

1. Wait a few minutes for DNS propagation
2. Access your services via their subdomains (e.g., `https://plex.yourdomain.com`)
3. If using Cloudflare Access, you'll be prompted to authenticate first
4. Then access the service using its built-in authentication

### Service URLs Summary

After setup, your services will be accessible at:

- Plex: `https://plex.yourdomain.com`
- Overseerr: `https://overseerr.yourdomain.com`
- Radarr: `https://radarr.yourdomain.com`
- Sonarr: `https://sonarr.yourdomain.com`
- Prowlarr: `https://prowlarr.yourdomain.com`
- Tautulli: `https://tautulli.yourdomain.com` (advanced profile)
- Bazarr: `https://bazarr.yourdomain.com` (advanced profile)
- (Add others as needed)

### Troubleshooting

- **Tunnel not connecting:** Verify the `CLOUDFLARE_TUNNEL_TOKEN` in your `.env` file is correct
- **Services not accessible:** Check that routes are configured correctly in Cloudflare Dashboard (Service should point to `http://localhost:<port>`)
- **SSL errors:** Cloudflare provides SSL automatically - ensure your domain DNS is pointing to Cloudflare nameservers
- **Container logs:** Check logs with `docker compose logs cloudflared`

### Security Considerations

- **Cloudflare Access is recommended** for an additional authentication layer
- Services are still protected by their built-in authentication
- All traffic is encrypted via HTTPS through Cloudflare
- No ports need to be opened on your router/firewall
- Services are not directly exposed to the public internet

## Possible Additions

1. Organizr - Creates a lovely dashboard to help navigate to all of your apps
2. Portainer - Docker GUI
