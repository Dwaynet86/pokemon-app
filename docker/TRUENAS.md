# Running on TrueNAS SCALE

The image is built by GitHub (Actions) every time you push to `main`, and published to GitHub Container Registry.
The app on TrueNAS pulls the newest image every time it starts. Your data lives in two datasets, so updating never touches it.

## One-time setup

1. **GitHub:** push this repo. Open the repo's **Actions** tab and wait for "Build and publish image" to go green.
2. **Make the image pullable.** On GitHub open your profile -> **Packages** -> the package -> **Package settings** ->
   **Change visibility -> Public**. (The image has no secrets in it: those live only in your TrueNAS app. If you must keep it private,
   TrueNAS needs a GitHub token with `read:packages` to pull it.)
3. **TrueNAS: create two datasets** (Datasets -> Add Dataset), for example under your apps pool:
   `tank/apps/pokemon-cards/db` and `tank/apps/pokemon-cards/uploads`. Plain defaults are fine.
4. **Edit `docker-compose.truenas.yml`:** fix the lines marked `CHANGE` (image name, passwords, `SECRET_KEY`, dataset paths).
5. **TrueNAS: Apps -> Discover Apps -> Custom App -> Install via YAML.** Paste the file, name it `pokemon-cards`, Save.
6. Open `http://<truenas-ip>:30080`. The first run creates the tables itself and walks you through creating the first account.

## Updating

Push to GitHub -> wait for the green check -> in TrueNAS **stop and start** the app (or Edit -> Save). `pull_policy: always`
makes it fetch the new image on start, and the schema is brought up to date automatically.

For hands-off updates, run [Watchtower](https://containrrr.dev/watchtower/) as a second custom app (it needs the Docker socket
mounted), or schedule a small TrueNAS cron job that restarts the app nightly.

To roll back, change `:latest` to an older tag shown on the package page (`sha-xxxxxxx`) and save.

## Backups

Snapshot the `pokemon-cards` datasets (Data Protection -> Periodic Snapshot Tasks). For a portable copy, use **Maintenance -> Export** in the app.

## Behind HTTPS

Put Nginx Proxy Manager, Caddy or Traefik in front, then set `TRUST_PROXY: "1"` and `SESSION_COOKIE_SECURE: "1"`.
