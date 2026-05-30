# VPS Redeploy Guide

This guide is for rebuilding on a new VPS with minimum manual steps.

## 1) Upload code to VPS

Put this repo at:

`/home/ubuntu/video-shopping`

## 2) Bootstrap once

Run on VPS:

```bash
cd /home/ubuntu/video-shopping
chmod +x deploy/bootstrap_vps.sh
./deploy/bootstrap_vps.sh
```

If first run created `/etc/video-shopping/workflow-ui.env`, edit it:

```bash
sudo nano /etc/video-shopping/workflow-ui.env
```

Set a valid `OPENAI_API_KEY` and save.

## 3) Restart and verify

```bash
cd /home/ubuntu/video-shopping
chmod +x deploy/restart_and_check.sh
./deploy/restart_and_check.sh
```

External check:

`http://<your-vps-ip>:8600`

## 4) Runtime directories (do not commit)

- `workflow_ui/runs/`
- `workflow_ui/content_runs/`
- `out/`
- logs

## 5) Notes

- This project expects CPA at `http://127.0.0.1:8318/v1`.
- Keep secrets only in `/etc/video-shopping/workflow-ui.env`.
- Do not write keys into git or service template files.

