# What the first merge to `main` does

Checked 2026-10-10 against `.github/workflows/*.yml`, `render.yaml`, `k8s/`
and the repository settings (`gh api`): `Awais68/support-Agent-hackathon-5-with-CRM`,
public, default branch `main`, **0 Actions variables, 0 Actions secrets, 0
environments**. Read this before merging PR #2.

## 1. Workflows that start on the push to `main`

| Workflow | Trigger | Deploys? |
|---|---|---|
| CI (`ci.yml`) | `push: [main, develop]` | No. Lint, unit tests, live compose job, front-end lint/build/unit tests |
| Secret scan (`secret-scan.yml`) | every push | No |
| Chaos (`chaos.yml`) | Monday cron / manual only | No, not on merge |
| CD (`cd.yml`) | `workflow_run` of CI on `main` | See below |

## 2. CD, step by step

CD runs only after CI **completed** on `main`, and its jobs run only if CI
concluded `success` and CI was started by a `push` (not a PR). If CI fails,
nothing below happens. Runs are serialised (`concurrency: cd-main`, no
cancel).

### Job `build-and-push` (runs)

1. Checks out exactly the commit CI tested (`workflow_run.head_sha`).
2. Image base: `ghcr.io/awais68/support-agent-hackathon-5-with-crm`
   (lower-cased; GHCR rejects upper case).
3. Logs in to GHCR with the run's `GITHUB_TOKEN` (`packages: write`). No
   repository secret is needed.
4. Builds the root `Dockerfile` and **pushes**:
   - `…/api:<commit-sha>`
   - `…/api:latest`
   This one image is the API and the worker.
5. Builds `web-form/Dockerfile` and **pushes**:
   - `…/web-form:<commit-sha>`
   - `…/web-form:latest`
   No `--build-arg` is passed, so `NEXT_PUBLIC_API_URL`,
   `NEXT_PUBLIC_COMPANY_NAME` and `NEXT_PUBLIC_SUPPORT_EMAIL` are empty in
   this image's browser bundle (the live-chat WebSocket has no API URL).
   Form submit, ticket lookup and voice are fine: they use the runtime
   `API_INTERNAL_URL`.
6. Uses the GitHub Actions build cache (`type=gha`).

Result: four tags in GHCR under the repository owner. The packages are new,
so check their visibility in GitHub → Packages after the run. Images are
built from the repo after S5, so no `.env`/credentials are in their layers
(`.dockerignore`).

### Job `deploy` (skipped)

Gated on the repository variable `K8S_DEPLOY == 'true'`. It is not set, so
the job is **skipped** and nothing is deployed to Kubernetes. If you set it:

1. Uses the `production` environment (GitHub creates it on first use; no
   protection rules exist, so no approval gate).
2. Needs the secret `KUBE_CONFIG`. It is not set, so the job fails here with
   "K8S_DEPLOY is true but the KUBE_CONFIG secret is not set".
3. With a kubeconfig: creates Job `techflow-migrate-<sha12>` in namespace
   `techflow` from the new API image, runs `scripts/render_migrate.sh`
   against secret `techflow-secrets/database-url`, waits up to 300 s. A
   failed migration stops the run before any image changes.
4. `kubectl set image` on `deployment/techflow-api` (container `api`) and
   `deployment/techflow-worker` (container `worker`) to `…/api:<sha>`.
5. `deployment/techflow-web-form` (container `web-form`) to
   `…/web-form:<sha>`, only if that deployment exists.
6. Waits for each rollout (300 s each).

Before enabling it, the cluster needs secret `techflow-secrets` with
`database-url`, `api-key`, `deepseek-api-key`, `gemini-api-key`
(`openrouter-api-key` optional), the configmap, and the manifests applied
once (`k8s/`). The manifests' own `image:` values are placeholders; CD
overrides them.

## 3. Render (outside GitHub Actions)

`render.yaml` sets `autoDeploy: true` for `techflow-api` and
`techflow-webform`; `techflow-worker` sets nothing (Render's default is
auto-deploy on). No `branch:` is set, so Render follows the branch chosen
when the Blueprint was connected.

- There is no sign that Render is connected: no GitHub deployments and no
  Render status on `main` commits. Check the Render dashboard.
- **If it is connected**, the merge also deploys there: Render builds its
  own images from the Dockerfiles (not the GHCR ones) on the push, in
  parallel with CI and **not gated on CI passing**, runs
  `preDeployCommand: bash scripts/render_migrate.sh` for the API, and
  swaps traffic when the health check passes. To gate it on CI, set
  Render's auto-deploy to "after CI checks pass" (or off) before merging.
- Render needs `DEEPSEEK_API_KEY` and `GEMINI_API_KEY` set in its dashboard
  (`sync: false`); without Gemini the API reports `degraded`.

## 4. Not triggered by the merge

- No database is touched unless Render is connected (pre-deploy migration)
  or `K8S_DEPLOY` is enabled (migration Job).
- No secrets are read except `GITHUB_TOKEN`.
- Chaos does not run.
