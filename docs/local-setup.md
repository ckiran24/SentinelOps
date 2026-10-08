# Run SentinelOps on your own computer

The source project was built in a remote Codex cloud workspace. A browser address beginning with `localhost` points to the computer running that browser. Containers running in the cloud workspace therefore cannot be opened through your laptop's `localhost` address.

To use `http://localhost:3000`, put the source project and run Docker on the same computer as your browser. This guide uses the repository's default ports. No model key or cloud account is needed for the fixture demo.

## 1. Get and extract the source

Open [the SentinelOps repository](https://github.com/ckiran24/SentinelOps), select **Code → Download ZIP**, and extract `SentinelOps-main.zip`. Open a terminal in the extracted `SentinelOps-main` folder containing `compose.yaml`, `.env.example`, `apps`, `docs`, and `infra`.

If using the standalone `SentinelOps-source.zip` archive instead, extract it and open a terminal in its `SentinelOps` folder. With Git installed, you can also download the project using:

```bash
git clone https://github.com/ckiran24/SentinelOps.git
cd SentinelOps
```

Why: Compose reads `compose.yaml` from the current directory. Running commands in an unrelated folder will produce a configuration-file error rather than start this project.

## 2. Install and start Docker

Install Docker Desktop for Windows or macOS using Docker's instructions for your operating system. Start Docker Desktop and wait until the engine is running. Linux users can use Docker Engine with the Compose v2 plugin.

Verify in the terminal:

```text
docker version
docker compose version
```

`docker version` should show both a Client and a Server. A missing Server or daemon connection error means the Docker engine is not running or is inaccessible. Windows installations may require WSL 2 and a restart; follow Docker Desktop's installation prompts.

Why: Docker runs the web app, API, worker, and PostgreSQL together. You do not need to install Node.js, Python, or PostgreSQL separately for this route.

## 3. Copy the local demo configuration

In Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

In macOS/Linux Terminal:

```bash
cp .env.example .env
```

If `.env` already exists, inspect it before copying over it. The example contains public demo credentials and local loopback bindings. Keep the default values for this first run:

```dotenv
WEB_PORT=3000
API_PORT=8000
NEXT_PUBLIC_API_URL=http://localhost:8000
CORS_ORIGINS=http://localhost:3000
```

Why: These settings must agree about where the browser can find the API and which web origin the API accepts. `NEXT_PUBLIC_API_URL` is included in the frontend at build time.

## 4. Build and start the app

Run this from the folder containing `compose.yaml`:

```text
docker compose up --build -d --wait
docker compose ps -a
```

The initial build downloads dependencies and takes longer than later starts. Wait for it to finish successfully before opening the browser. PostgreSQL, API, worker, and web should be running and healthy. The one-time `migrate` service should show `Exited (0)`; that is a successful migration, not a broken server.

Why: Compose builds the application images, starts PostgreSQL, creates its schema, seeds runbooks, and then starts dependent services. The `--wait` option waits for service readiness; `-d` leaves them running in the background.

## 5. Open and use the app

Open these addresses in a browser on the computer running Docker:

- Console: `http://localhost:3000`
- API documentation: `http://localhost:8000/docs`
- API readiness: `http://localhost:8000/health/ready`

For the demo, sign in as `operator` with password `sentinel-demo`. Create and investigate an incident. Sign in as `approver` with the same password to review a proposal and approve it. Execution is a separate action after approval.

The default fixture mode simulates infrastructure observations and remediation. It does not change real infrastructure.

## If the browser says "This site can't be reached"

1. Confirm the source is on this computer and Docker Desktop is running here. Cloud containers do not supply your computer's localhost server.
2. Run `docker compose ps -a` from the project folder. The `web` service should publish `127.0.0.1:3000->3000/tcp`; the API should publish `127.0.0.1:8000->8000/tcp` with default settings.
3. If services are missing, stopped, or unhealthy, inspect the errors:

   ```text
   docker compose logs --tail=100 web api migrate postgres
   ```

4. If `.env` uses another `WEB_PORT`, open that port instead. To resolve a port conflict, change all corresponding values together. For example:

   ```dotenv
   WEB_PORT=3100
   API_PORT=8100
   NEXT_PUBLIC_API_URL=http://localhost:8100
   CORS_ORIGINS=http://localhost:3100
   ```

   Then run `docker compose up --build -d --wait` again and open `http://localhost:3100`. These are an alternative pair of ports, not the defaults.

5. Use `http://`, since this local demo does not configure HTTPS. If localhost name resolution is suspect, compare the page at `http://127.0.0.1:3000`; use the configured localhost console origin for the full authenticated demo.

If the console loads but shows API connection errors, open the readiness URL and check the API logs. Confirm `NEXT_PUBLIC_API_URL` and `CORS_ORIGINS` match your chosen ports, then rebuild after correcting them.

## Stop and restart

```text
docker compose down
docker compose up -d --wait
```

Ordinary `down` keeps the named PostgreSQL volume, so incident history persists. Removing volumes discards that history; it is not needed for connection troubleshooting.
