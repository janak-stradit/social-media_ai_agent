# Deploying AVIR AI to AWS (Docker + ECR + EC2 + RDS) with GitHub Actions

```
GitHub (push to main / "Run workflow" with a selected branch)
   │  GitHub Actions: build Docker image → push to ECR → SSH to EC2 → pull + start → health check
   ▼
Amazon ECR (private image registry)
   │  image pulled by the server
   ▼
EC2 (Ubuntu 24.04, Docker)                                   RDS PostgreSQL 16 (private)
 ├─ nginx :80/:443 ──► container "web" (gunicorn) :8000             ▲
 └─ container "scheduler" (publishes scheduled posts) ──────────────┘  port 5432, EC2 SG only
                                              S3 bucket ◄── generated content + uploads
```

| Piece | Where it lives |
|---|---|
| Workflow | `.github/workflows/deploy.yml` |
| Image definition | `Dockerfile`, `.dockerignore` |
| Containers on the server | `deploy/docker-compose.prod.yml` (web + scheduler from the same image, plus a small Redis for signup captcha) |
| Server bootstrap (Docker, nginx, swap, data folders) | `deploy/setup_server.sh`, runs automatically on the first deploy |
| Per-deploy script on the server | `deploy/remote_deploy.sh` |
| nginx site | `deploy/nginx/socialmedia.conf` |
| AWS policy templates | `deploy/aws/*.json` |
| `.env` template for the `ENV_FILE` secret | `deploy/env.production.example` |

**How a deploy works:**
1. GitHub Actions logs in to AWS through **OIDC**, a temporary role, so no AWS keys are stored in GitHub.
2. It builds the image and pushes it to ECR, tagged `sha-<commit>`. Layer caching means only
   changed layers are rebuilt.
3. It connects to EC2 over SSH, passes a 12-hour ECR login and the `.env` through stdin, then
   pulls the image and restarts both containers.
4. If `/login` doesn't answer within 3 minutes, the **previous image is started again
   automatically** and the run fails with the logs.

**On the server:**
- `.env` (mode 600) is in `/opt/socialmedia/.env`.
- Data lives in `/opt/socialmedia/data/`: `static/uploads`, `brand`, `chroma_db`. The folders
  are mounted into the containers and survive every deploy.
- The server needs **no AWS credentials** for ECR.

---

## Step 1 — Security groups (AWS Console → EC2 → Security Groups)

Create two groups in the same VPC:

**`socialmedia-ec2-sg`**
| Type | Port | Source |
|---|---|---|
| HTTP | 80 | 0.0.0.0/0 |
| HTTPS | 443 | 0.0.0.0/0 |
| SSH | 22 | 0.0.0.0/0 *(see note)* |

**`socialmedia-rds-sg`**
| Type | Port | Source |
|---|---|---|
| PostgreSQL | 5432 | `socialmedia-ec2-sg` (select the security group, not an IP) |

> SSH note: GitHub-hosted runners use changing IPs, so port 22 must be reachable from the
> internet for the pipeline. Security comes from key-only login (password auth is off on
> Ubuntu AMIs) and pinning the host key (`EC2_KNOWN_HOSTS`). If you want to lock 22 down,
> use a self-hosted runner or restrict 22 to GitHub's published Actions IP ranges.

## Step 2 — RDS PostgreSQL (small)

RDS → Create database:

- **Engine:** PostgreSQL 16, template **Free tier** or **Dev/Test**
- **Availability:** **Single-AZ** (Multi-AZ doubles the price; see [Cost](#cost))
- **Identifier:** `socialmedia-db`; master username e.g. `smadmin`; set a strong password
- **Instance class:** `db.t4g.micro` (cheapest, enough to start) or `db.t4g.small` (more traffic)
- **Storage:** gp3, 20 GB, storage autoscaling on with **maximum 50 GB** (caps the bill)
- **Connectivity:** same VPC as EC2, **Public access: No**, security group `socialmedia-rds-sg`,
  **Availability Zone:** pick one (e.g. `us-east-1a`) and launch EC2 in the same AZ, because
  traffic between AZs is billed
- **Monitoring:** keep Performance Insights on the free 7-day retention; Enhanced Monitoring off
- **Additional configuration → Initial database name:** `socialmedia`
- **Backups:** 7 days retention; Deletion protection: on (production)

When it's ready, copy the **Endpoint**. Your connection string is:

```
DATABASE_URL=postgresql+psycopg2://smadmin:<password>@<endpoint>:5432/socialmedia?sslmode=require
```

URL-encode special characters in the password (`@` → `%40`, `:` → `%3A`, `/` → `%2F`, `#` → `%23`).
The app creates its schema (`social_media_agent`) and tables itself on first start.

## Step 3 — EC2 instance

EC2 → Launch instance:

- **AMI:** Ubuntu Server 24.04 LTS (x86_64). The image is built for `linux/amd64`, so don't pick
  an ARM (`t4g`) instance.
- **Type:** `t3.medium` (4 GB), recommended. Each gunicorn worker uses about 0.6–0.7 GB (torch +
  the embedding model), so 2 workers + scheduler + Chromium fit. For the cheapest setup,
  `t3.small` (2 GB) with 1 worker works; see [Cost](#cost). Use `t3.large` if traffic grows.
  (A 4 GB swap file is added automatically.)
- **Key pair:** create `socialmedia-deploy` (ED25519, .pem) and download it
- **Network:** same VPC as RDS, a **public subnet in the same AZ as RDS**, auto-assign public IP,
  security group `socialmedia-ec2-sg`
- **Storage:** **30 GB** gp3. Docker images are ~3–4 GB each, and the current and previous
  versions are kept.

Then **Elastic IPs → Allocate → Associate** with the instance so the address never changes.
Point your domain's A record at the Elastic IP.

Get the host key for `EC2_KNOWN_HOSTS` (from your own machine, after the instance is running):

```bash
ssh-keyscan -H <elastic-ip>
```

Verify it once by comparing with the fingerprint from
`ssh -i socialmedia-deploy.pem ubuntu@<elastic-ip>` on first connect.

> The containers run as UID 1000, which is the default `ubuntu` user on EC2 Ubuntu AMIs.
> Use `ubuntu` as the deploy user.

## Step 4 — S3 bucket for generated content (+ EC2 role)

Every generated image/video and user upload is copied to S3 in the background. The server's
disk works as a cache: when a file is missing there (new or rebuilt instance), the app restores
it from S3 before serving or using it. URLs stay `/static/uploads/<file>`, and the bucket stays
private. Implementation: `services/storage_service.py`.

1. **S3 → Create bucket**, e.g. `avir-content-<account-id>`, in the same region as EC2.
   Keep **Block all public access** on and default encryption (SSE-S3). Versioning is optional.
2. **IAM → Roles → Create role** → trusted entity *AWS service / EC2*, name `avir-ec2`.
   Add an inline policy from `deploy/aws/ec2-app-policy.json`, replacing `S3_MEDIA_BUCKET` and
   `AWS_REGION`. It covers S3 storage and Bedrock image analysis (`VISION_PROVIDER=bedrock`).
3. **EC2 → the instance → Actions → Security → Modify IAM role** → `avir-ec2`.
   Then **Actions → Instance settings → Modify instance metadata options** → set
   **Metadata response hop limit = 2**. Containers are one network hop away from the instance;
   with the default hop limit of 1 they can't read the role's credentials, and S3/Bedrock calls
   fail with "Unable to locate credentials".
4. In the `ENV_FILE` secret, set `S3_MEDIA_BUCKET=<bucket>` and `AWS_REGION=<region>`. Leave
   `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` empty so the role is used.
5. After the first deploy, once, to copy content that existed before S3 was switched on:
   ```bash
   sudo docker exec socialmedia-web-1 python scripts/sync_uploads_to_s3.py
   ```

With `S3_MEDIA_BUCKET` empty, S3 storage is off. Locally, your existing AWS keys or profile
are used.

## Step 5 — ECR repository + GitHub → AWS access (OIDC)

**5a. ECR repository**
1. **ECR → Private registry → Repositories → Create repository**
   - Name: `avir-ai`
   - Tag immutability: **Mutable**. The `buildcache` tag is overwritten on every build.
   - Scan on push: on (free basic vulnerability scan)
2. Open the repository → **Lifecycle policy → Edit JSON** → paste
   `deploy/aws/ecr-lifecycle-policy.json`. It keeps the 10 newest app images and removes
   leftovers after a day, which caps storage cost.

**5b. Let GitHub Actions log in to AWS without stored keys**
1. **IAM → Identity providers → Add provider → OpenID Connect**
   - Provider URL: `https://token.actions.githubusercontent.com`
   - Audience: `sts.amazonaws.com`
2. **IAM → Roles → Create role → Web identity** → that provider, audience `sts.amazonaws.com`.
   Name it `avir-github-deploy`.
3. Open the role → **Trust relationships → Edit** → **replace everything** with
   `deploy/aws/github-oidc-trust-policy.json`, replacing `ACCOUNT_ID`, `GITHUB_OWNER` and
   `GITHUB_REPO` (exact spelling, e.g. `repo:janak-stradit/social-media_ai_agent:environment:*`).
   Only workflow jobs of this repository that run in a GitHub Environment can use the role.
   > The console wizard asks for a GitHub *branch* and writes a `...:ref:refs/heads/<branch>`
   > condition. That never matches here, because the deploy job runs in an Environment, where
   > GitHub sends `repo:<owner>/<repo>:environment:<name>`. Keeping the wizard's condition fails
   > with *"Not authorized to perform sts:AssumeRoleWithWebIdentity"*.
4. **Permissions → Add permissions → Create inline policy → JSON** → paste
   `deploy/aws/github-deploy-policy.json`, replacing `AWS_REGION`, `ACCOUNT_ID` and
   `ECR_REPOSITORY`. It grants push/pull to this one repository only.
5. Copy the role **ARN** for the next step.

## Step 6 — GitHub Environments and Secrets

Repository → **Settings → Environments → New environment** → `production`
(and `staging` if you have a second server).

For `production`, recommended protection:
- **Deployment branches and tags:** "Selected branches" → add the branches allowed to go to
  production (e.g. `main`, `release/*`). A run with any other branch is rejected by GitHub.
- **Required reviewers** (optional): someone must approve each production deploy.

Add these **Environment secrets** (not repository secrets, so each environment has its own):

| Secret | Value |
|---|---|
| `AWS_REGION` | e.g. `us-east-1` (region of ECR and EC2) |
| `AWS_DEPLOY_ROLE_ARN` | ARN of `avir-github-deploy` from Step 5b |
| `ECR_REPOSITORY` | `avir-ai` |
| `EC2_HOST` | Elastic IP or DNS name of the instance |
| `EC2_USER` | `ubuntu` |
| `EC2_SSH_KEY` | Full contents of `socialmedia-deploy.pem` (including the `BEGIN`/`END` lines) |
| `EC2_KNOWN_HOSTS` | Output of `ssh-keyscan -H <elastic-ip>` (optional but recommended) |
| `ENV_FILE` | The complete `.env` for this environment — start from `deploy/env.production.example` |

`ENV_FILE` is the single place every app setting lives (API keys, `DATABASE_URL`, SMTP, …).
To change a setting, edit the secret and re-run the workflow. Unchanged images aren't rebuilt;
the containers just restart with the new `.env`. Important for production:
- Generate a new `SECRET_KEY` (don't reuse the local one).
- Do **not** include `AWS_PROFILE` (no AWS profiles exist on the server).
- `SCHEDULER_ENABLED`, `WEB_CONCURRENCY` and `REDIS_URL` are not needed; the compose file sets
  them, and its values win over `ENV_FILE`.
- Line by line, what to put in `ENV_FILE` (required vs optional) is in
  `deploy/env.production.example`.

## Step 7 — First deploy

Commit and push the deployment files, then: **Actions → Deploy to EC2 → Run workflow**:

- **Use workflow from:** the branch to deploy (dropdown of all branches)
- **environment:** `production`
- **image_tag:** leave empty

The branch must contain `.github/workflows/deploy.yml`, so it must be created from a branch
that has it (or have `main` merged into it). GitHub checks the selected branch against the
Environment's "Deployment branches" rule before the job starts.

**How long it takes:**
- **First run:** about 15–25 minutes. Most of it is building the image (torch, Playwright
  Chromium) and installing Docker + nginx on the server.
- **Code-only changes later:** about 3–5 minutes, because the dependency layers come from the
  ECR cache.
- **When `requirements.txt` changes:** the dependency layers are rebuilt.

The run fails (red) if `/login` doesn't answer HTTP 200 within 3 minutes; the previous version
is started again and the logs are printed in the job output.

Pushes to `main` deploy to `production` automatically. To turn that off, delete the `push:`
block at the top of the workflow.

Open `http://<elastic-ip>/` to check.

## Step 8 — HTTPS (once, after DNS points at the server)

```bash
ssh -i socialmedia-deploy.pem ubuntu@<elastic-ip>
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your.domain.com --redirect -m you@example.com --agree-tos
```

Certbot renews automatically. Later deploys leave the certbot-managed nginx file untouched.
Set `APP_BASE_URL=https://your.domain.com` in `ENV_FILE` so email links are correct.

---

## Cost

Approximate **on-demand prices in us-east-1** (730 hours/month). Mumbai (`ap-south-1`) and UAE
(`me-central-1`) are usually a bit higher. Prices change, so confirm in the
[AWS Pricing Calculator](https://calculator.aws/) for your region before launching.

### Monthly estimate

| Item | Budget | Recommended | Growth |
|---|---|---|---|
| EC2 | `t3.small` 2 GB, 1 worker — $15.2 | `t3.medium` 4 GB, 2 workers — $30.4 | `t3.large` 8 GB — $60.7 |
| EBS disk (gp3 30 GB, $0.08/GB) | $2.4 | $2.4 | $2.4 |
| Public IPv4 / Elastic IP ($0.005/h) | $3.7 | $3.7 | $3.7 |
| RDS PostgreSQL, Single-AZ | `db.t4g.micro` — $11.7 | `db.t4g.micro` — $11.7 | `db.t4g.small` — $23.4 |
| RDS storage (gp3 20 GB) + 7-day backups | $2.3 | $2.3 | $2.3 |
| ECR ($0.10/GB; ~2–4 GB kept by the lifecycle policy) | < $0.5 | < $0.5 | < $0.5 |
| S3 (Standard $0.023/GB + requests) | < $1 | < $1 | ~$1–3 |
| Data transfer out (first 100 GB/month free; ECR → EC2 in the same region is free) | $0 | $0 | $0.09/GB above 100 GB |
| GitHub Actions | $0 | $0 | $0 |
| **Total** | **≈ $36 / month** | **≈ $52 / month** | **≈ $96 / month** |

- **GitHub Actions:** free for public repos. Private repos get 2,000 free minutes a month on the
  Free plan. A deploy takes ~3–5 minutes (the first one ~20), so that's roughly 400+ deploys
  a month.
- **AI APIs (HeyRoute etc.) are billed separately** and usually cost more than the servers
  once people use the product. Measured: one LinkedIn text post ≈ 42,500 tokens ≈ $0.025.
  Images and videos cost extra per generation. `HEYROUTE_REASONING_EFFORT=low` cuts tokens and
  time.

### Cheapest setup (≈ $36/month)

For a beta with few users:
1. Launch `t3.small` instead of `t3.medium`.
2. In `deploy/docker-compose.prod.yml` set `WEB_CONCURRENCY: "1"` (1 gunicorn worker; its 4
   threads still handle several requests at once). Commit and deploy.
3. `db.t4g.micro`, Single-AZ, 20 GB.

Move up to `t3.medium` when `free -m` on the server shows swap regularly in use or pages get slow.
Changing the type is a stop → *Change instance type* → start, with a few minutes of downtime.

### Save more

| Option | Saving | Notes |
|---|---|---|
| 1-year **Compute Savings Plan** (EC2) + **RDS Reserved Instance**, no upfront | ~30–40% | Only after the size has proved right for a month or two |
| Check the AWS Free Tier for your account | Up to the full RDS micro / credits | Accounts created after 15 July 2025 get credits instead of the old 12-month free tier. `t3.micro` (1 GB) is too small for this app |
| Stop **staging** EC2 + RDS when not in use | Up to 100% of compute | You still pay for disk and the IPv4 address. A stopped RDS instance **restarts automatically after 7 days** |
| S3 lifecycle rule: move `uploads/` to **Standard-IA** after 30 days | ~45% on storage | Old generated media is rarely opened |
| PostgreSQL on the EC2 itself instead of RDS | ~$14 | No managed backups, patching or restore. Not recommended for production |
| **Amazon Lightsail** instead of EC2 (e.g. 4 GB plan, ~$24 incl. IPv4 + transfer) | ~$10 | Same Ubuntu + SSH + Docker, so this pipeline works with small changes (Lightsail can't use EC2 IAM roles, so S3 needs access keys). Check current prices |

### Cost traps to avoid

- **NAT Gateway (~$33/month + data):** the VPC wizard ("VPC and more") can add one. Choose
  **NAT gateways: None**; this setup doesn't need one.
- **Application Load Balancer (~$16+/month):** not needed, since nginx + certbot handle HTTPS
  for free.
- **RDS Multi-AZ** doubles the database cost. Keep Single-AZ until you need automatic failover.
- **ECR without a lifecycle policy** keeps every image forever (~1–2 GB each). Step 5a sets one.
- **Elastic IPs that aren't attached** to a running instance are still billed. Release unused ones.
- **Cross-AZ traffic:** keep EC2 and RDS in the same Availability Zone.
- **t3 "Unlimited" CPU:** if CPU stays above the baseline for long periods, AWS bills extra
  CPU credits (~$0.05 per vCPU-hour). This app mostly waits on AI APIs, so it's normally $0.
  Check *CPU credit balance* in CloudWatch if the bill looks odd.
- **Set a budget alert:** *Billing → Budgets → Create budget → Monthly cost budget* (e.g. $60)
  with an email alert, so a surprise shows up in days, not at month end.

---

## Day-to-day

| Task | How |
|---|---|
| Deploy a branch | Actions → Deploy to EC2 → Run workflow → pick branch ("Use workflow from") + environment |
| **Roll back** | Run workflow with **image_tag** = an earlier tag (from the job summary, `DEPLOY_HISTORY`, or ECR) — nothing is rebuilt |
| What's deployed? | `cat /opt/socialmedia/CURRENT_IMAGE`; history in `/opt/socialmedia/DEPLOY_HISTORY` |
| Status | `sudo docker ps` |
| Web logs | `sudo docker logs -f --tail 100 socialmedia-web-1` |
| Scheduler logs | `sudo docker logs -f --tail 100 socialmedia-scheduler-1` |
| nginx logs | `sudo tail -f /var/log/nginx/error.log` |
| Restart | `sudo docker restart socialmedia-web-1 socialmedia-scheduler-1` |
| Shell inside the app | `sudo docker exec -it socialmedia-web-1 bash` |
| Disk usage | `df -h /` and `sudo docker system df` |
| Connect to RDS from EC2 | `sudo apt-get install -y postgresql-client && psql "<DATABASE_URL without +psycopg2>"` |

**Run the production image locally** (Docker Desktop running):

```bash
docker build -t avir-ai .
docker run --rm -p 5000:5000 -e SCHEDULER_ENABLED=false -v "$PWD/.env:/app/.env:ro" avir-ai
```

## Notes

- **Only one scheduler runs.** The web container runs with `SCHEDULER_ENABLED=false`, and the
  `scheduler` container is the only process that publishes scheduled posts. Running the
  scheduler in each gunicorn worker would publish every post multiple times. Locally
  (`python app.py`) nothing changes; the scheduler still runs inside the app.
- **Timeouts:** gunicorn `--timeout 900` and nginx `proxy_read_timeout 900s`, because one
  generation (LLM + image/video) can take several minutes.
- **Secrets are never in the image.** `.dockerignore` excludes `.env`; the server mounts
  `/opt/socialmedia/.env` read-only into the containers.
- **Generated content and uploads** are backed up to S3 when `S3_MEDIA_BUCKET` is set (Step 4).
  Brand assets uploaded in the app (`/opt/socialmedia/data/brand`) are not in S3, so use EBS
  snapshots (Data Lifecycle Manager) to back them up. The image's default brand assets are
  copied there only when missing, so a user's replacement is never overwritten.
- `docker-compose.yml` in the repo root is an older local-development file (Redis, Chroma,
  Celery) and is not used by this deployment.
- **Troubleshooting:**
  - *Health check failed*: read the log lines printed in the job. The usual causes are a wrong
    `DATABASE_URL` or an RDS security group that doesn't allow the EC2 security group.
  - *`Not authorized to perform sts:AssumeRoleWithWebIdentity`*: the role's trust policy doesn't
    match what GitHub sends. The usual cause is the wizard's branch condition (`:ref:refs/heads/...`)
    instead of `:environment:*` (see Step 5b.3). Also check the repo spelling, the account ID in
    `Federated`, the OIDC provider (audience `sts.amazonaws.com`), and the
    `AWS_DEPLOY_ROLE_ARN` secret. CloudTrail → *AssumeRoleWithWebIdentity* shows the `sub`
    GitHub actually sent.
  - *`denied` / `AccessDenied` when pushing to ECR*: `ECR_REPOSITORY`, `AWS_REGION` or the
    ARN in `github-deploy-policy.json` doesn't match the repository.
  - *`Permission denied (publickey)`*: `EC2_SSH_KEY` or `EC2_USER` is wrong.
  - *`Host key verification failed`*: the instance was replaced; update `EC2_KNOWN_HOSTS`.
  - *`no space left on device`*: `sudo docker system prune -af` (images stay in ECR), or grow
    the EBS volume.
