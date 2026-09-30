# Deploying to AWS EC2 + RDS with GitHub Actions

```
GitHub (push to main / "Run workflow" with a selected branch)
   │  GitHub Actions: checkout branch → rsync code over SSH → write .env from secret → restart
   ▼
EC2 (Ubuntu 24.04)                                     RDS PostgreSQL 16 (private)
 ├─ nginx :80/:443  ──►  gunicorn :8000 (socialmedia-web)      ▲
 └─ socialmedia-scheduler (publishes scheduled posts) ─────────┘  port 5432, EC2 SG only
```

| Piece | Where it lives |
|---|---|
| Workflow | `.github/workflows/deploy.yml` |
| Server bootstrap (packages, swap, services, nginx) | `deploy/setup_server.sh` — runs automatically on the first deploy |
| Per-deploy script on the server | `deploy/remote_deploy.sh` |
| systemd services | `deploy/systemd/socialmedia-web.service`, `socialmedia-scheduler.service` |
| nginx site | `deploy/nginx/socialmedia.conf` |
| `.env` template for the `ENV_FILE` secret | `deploy/env.production.example` |

On the server: code in `/opt/socialmedia/app`, virtualenv in `/opt/socialmedia/venv`, the
`.env` (mode 600) in `/opt/socialmedia/app/.env`. Uploads and generated content
(`static/uploads/`), brand assets uploaded in the app (`static/img/brand/`) and `chroma_db/`
are never overwritten or deleted by a deploy.

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
- **Identifier:** `socialmedia-db`; master username e.g. `smadmin`; set a strong password
- **Instance class:** `db.t4g.micro` (cheapest) or `db.t4g.small` (recommended for real traffic)
- **Storage:** gp3, 20 GB, storage autoscaling on
- **Connectivity:** same VPC as EC2, **Public access: No**, security group `socialmedia-rds-sg`
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

- **AMI:** Ubuntu Server 24.04 LTS (x86_64)
- **Type:** `t3.medium` (4 GB) minimum — torch + sentence-transformers + 2 gunicorn workers need
  it; `t3.large` if you see memory pressure. (A 4 GB swap file is added automatically.)
- **Key pair:** create `socialmedia-deploy` (ED25519, .pem) and download it
- **Network:** same VPC as RDS, public subnet, auto-assign public IP, security group `socialmedia-ec2-sg`
- **Storage:** 30 GB gp3

Then **Elastic IPs → Allocate → Associate** with the instance so the address never changes.
Point your domain's A record at the Elastic IP.

Recommended: attach the IAM role from **Step 3b** to the instance and leave
`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` empty.

Get the host key for `EC2_KNOWN_HOSTS` (from your own machine, after the instance is running):

```bash
ssh-keyscan -H <elastic-ip>
```

Verify it once by comparing with the fingerprint from
`ssh -i socialmedia-deploy.pem ubuntu@<elastic-ip>` on first connect.

## Step 3b — S3 bucket for generated content

Every generated image/video and user upload is copied to S3 in the background. The server's
disk works as a cache: when a file is missing there (new or rebuilt instance), the app restores
it from S3 before serving or using it. URLs stay `/static/uploads/<file>`, and the bucket stays
private. Implementation: `services/storage_service.py`.

1. **S3 → Create bucket**, e.g. `avir-content-<account-id>`, in the same region as EC2.
   Keep **Block all public access** on and default encryption (SSE-S3). Versioning is optional
   (protects against overwrites or deletes).
2. **IAM → Roles → Create role** → trusted entity *AWS service / EC2*. Add this inline policy
   (replace `BUCKET`):
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject"], "Resource": "arn:aws:s3:::BUCKET/uploads/*"},
       {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": "arn:aws:s3:::BUCKET"}
     ]
   }
   ```
3. **EC2 → the instance → Actions → Security → Modify IAM role** → select the role.
4. In the `ENV_FILE` secret, set `S3_MEDIA_BUCKET=<bucket>` and `AWS_REGION=<region>`. Leave
   `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` empty so the role is used. Then redeploy.
5. Once, to copy content that existed before S3 was switched on:
   ```bash
   cd /opt/socialmedia/app && /opt/socialmedia/venv/bin/python scripts/sync_uploads_to_s3.py
   ```

To run locally with S3, set `S3_MEDIA_BUCKET` in your `.env`. Your existing AWS keys or profile
are used. With `S3_MEDIA_BUCKET` empty, S3 storage is off.

## Step 4 — GitHub Environments and Secrets

Repository → **Settings → Environments → New environment** → `production`
(and `staging` if you have a second server).

For `production`, recommended protection:
- **Deployment branches and tags:** "Selected branches" → add the branches allowed to go to
  production (e.g. `main`, `release/*`). A run with any other branch is rejected by GitHub.
- **Required reviewers** (optional): someone must approve each production deploy.

Add these **Environment secrets** (not repository secrets, so each environment has its own):

| Secret | Value |
|---|---|
| `EC2_HOST` | Elastic IP or DNS name of the instance |
| `EC2_USER` | `ubuntu` |
| `EC2_SSH_KEY` | Full contents of `socialmedia-deploy.pem` (including the `BEGIN`/`END` lines) |
| `EC2_KNOWN_HOSTS` | Output of `ssh-keyscan -H <elastic-ip>` (optional but recommended) |
| `ENV_FILE` | The complete `.env` for this environment — start from `deploy/env.production.example` |

`ENV_FILE` is the single place every app setting lives (API keys, `DATABASE_URL`, SMTP, …).
To change a setting, edit the secret and re-run the workflow. Important for production:
- Generate a new `SECRET_KEY` (don't reuse the local one).
- Do **not** include `AWS_PROFILE` (no AWS profiles exist on the server).
- `SCHEDULER_ENABLED` is not needed; the services set it correctly.

## Step 5 — First deploy

Commit and push the deployment files, then: **Actions → Deploy to EC2 → Run workflow**:

- **Use workflow from:** the branch to deploy (dropdown of all branches)
- **environment:** `production`

The branch must contain `.github/workflows/deploy.yml`, so it must be created from a branch
that has it (or have `main` merged into it). GitHub checks the selected branch against the
Environment's "Deployment branches" rule before the job starts.

The first run takes 10–20 minutes. It installs Python 3.11, nginx, ffmpeg, swap, the systemd
services, CPU-only torch, all requirements and Playwright Chromium. Later runs skip anything
unchanged and take about 1–2 minutes. The run fails (red) if `/login` doesn't answer HTTP 200
within 3 minutes of the restart; the last 80 log lines are printed in the job output.

Pushes to `main` deploy to `production` automatically. To turn that off, delete the `push:`
block at the top of the workflow.

Open `http://<elastic-ip>/` to check.

## Step 6 — HTTPS (once, after DNS points at the server)

```bash
ssh -i socialmedia-deploy.pem ubuntu@<elastic-ip>
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your.domain.com --redirect -m you@example.com --agree-tos
```

Certbot renews automatically. Later deploys leave the certbot-managed nginx file untouched.
Set `APP_BASE_URL=https://your.domain.com` in `ENV_FILE` so email links are correct.

---

## Day-to-day

| Task | How |
|---|---|
| Deploy a branch | Actions → Deploy to EC2 → Run workflow → pick branch ("Use workflow from") + environment |
| Roll back | `git revert` the bad commit and push to `main` (or run the workflow on a branch at the last good commit) |
| What's deployed? | `cat /opt/socialmedia/DEPLOYED_VERSION` on the server |
| Web logs | `journalctl -u socialmedia-web -f` |
| Scheduler logs | `journalctl -u socialmedia-scheduler -f` |
| nginx logs | `sudo tail -f /var/log/nginx/error.log` |
| Restart | `sudo systemctl restart socialmedia-web socialmedia-scheduler` |
| Status | `systemctl status socialmedia-web socialmedia-scheduler` |
| Connect to RDS from EC2 | `sudo apt-get install -y postgresql-client && psql "<DATABASE_URL without +psycopg2>"` |

## Notes

- **Only one scheduler runs.** In production the web workers start with
  `SCHEDULER_ENABLED=false`, and `socialmedia-scheduler` is the only process that publishes
  scheduled posts. Running the scheduler in each gunicorn worker would publish every post
  multiple times. Locally (`python app.py`) nothing changes; the scheduler still runs inside
  the app.
- **Timeouts:** gunicorn `--timeout 900` and nginx `proxy_read_timeout 900s`, because one
  generation (LLM + image/video) can take several minutes.
- **Generated content and uploads** are backed up to S3 when `S3_MEDIA_BUCKET` is set (Step 3b).
  Without it they exist only on the instance's disk. Brand assets uploaded in the app
  (`static/img/brand/`) are not in S3, so use EBS snapshots (Data Lifecycle Manager) to back
  them up.
- **Troubleshooting:**
  - *Health check failed*: read the log lines printed in the job. The usual causes are a wrong
    `DATABASE_URL` or an RDS security group that doesn't allow the EC2 security group.
  - *`Permission denied (publickey)`*: `EC2_SSH_KEY` or `EC2_USER` is wrong.
  - *`Host key verification failed`*: the instance was replaced; update `EC2_KNOWN_HOSTS`.
