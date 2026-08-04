
# DeliverableQA Agent — WSL Debian + gh + Wrangler setup

One-time environment setup for deploying DeliverableQA Agent to Cloudflare from WSL Debian.

## 1. Prep WSL Debian

```bash
sudo apt update && sudo apt upgrade -y

# Bun
curl -fsSL https://bun.sh/install | bash
source ~/.bashrc

# GitHub CLI
sudo mkdir -p -m 755 /etc/apt/keyrings
wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null
sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
sudo apt update && sudo apt install gh -y

# Wrangler (via Bun)
bun install -g wrangler
```

## 2. Authenticate

```bash
gh auth login       # HTTPS, login with browser
wrangler login       # opens a browser window, WSL forwards it to Windows

gh auth status       # verify
wrangler whoami      # verify
```

## 3. Create the repo

```bash
mkdir deliverableqa-agent && cd deliverableqa-agent
gh repo create deliverableqa-agent --private --source=. --remote=origin
```

## 4. Provision Cloudflare resources (one-time)

```bash
wrangler r2 bucket create deliverableqa-uploads
wrangler d1 create deliverableqa-findings
# copy the returned database_id into wrangler.toml

wrangler pages project create deliverableqa-dashboard
```

## 5. Deploy loop

```bash
# Worker (orchestrator + agents API)
wrangler deploy

# Pages (dashboard)
wrangler pages deploy dashboard/dist --project-name=deliverableqa-dashboard
```

Normal cycle: PI agent edits code → `wrangler deploy` to check → `git push` → `gh pr create` for teammate review.

