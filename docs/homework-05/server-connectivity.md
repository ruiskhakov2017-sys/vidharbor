# Read-only server connectivity diagnostic

Status: prepared locally; GitHub execution **Not Run**. This workflow does not deploy VidHarbor. The target is `38.180.15.32:63102`, account `rul1`, domain `vidharbor.axyro.tech`. Existing projects must remain untouched. The owner's PowerShell TCP check succeeded; a successful TCP check does not yet prove SSH authentication.

## Execution

After publishing `.github/workflows/server-connectivity.yml` to the default branch, select **Actions → Server connectivity (read only) → Run workflow**. GitHub requires a workflow_dispatch definition on the default branch before it can be manually dispatched. Publishing a feature branch alone is insufficient. Review and permission to publish/merge remain separate from this local preparation.

Leave `check_ssh=false` for the first run. No secrets or login are needed: the GitHub-hosted runner resolves the domain, requires its IPv4 answer to equal the agreed host, establishes one TCP connection to the agreed port, and requires an SSH-2.0 banner. No HTTP requests are sent to existing sites. A failure is a failed job, with the actual reason in the visible `server-connectivity-evidence/connectivity.json` artifact. DNS resolution, connect and banner reads are bounded by step/process timeouts. No claim is made that the runner can reach the server until this run completes.

## Optional authenticated check

Create a separate, unencrypted Ed25519 diagnostic key **outside the repository**. Keep its private half private; never paste it into chat or commit it. Install only the public half in `rul1`'s existing `~/.ssh/authorized_keys`, preserving all existing entries and ownership/permissions. This is a separate server change and is not performed by the workflow.

For least privilege, prefix this one key with the following authorized_keys options (one line, followed by the public key):

```text
restrict,command="printf 'SSH_READ_ONLY_OK\n'; id -un; uname -s; df -h /" ssh-ed25519 <PUBLIC_KEY> vidharbor-diagnostic
```

The forced command makes this key unsuitable for deployments. Do not reuse a root key or the key of another project.

Using a trusted existing SSH session or the provider console, read the server's public host key and verify its fingerprint. Prepare a known_hosts record for the exact endpoint:

```text
[38.180.15.32]:63102 ssh-ed25519 <VERIFIED_SERVER_HOST_PUBLIC_KEY>
```

Do not trust an unverified ssh-keyscan result as proof of server identity. Add repository Actions secrets through GitHub settings or `gh secret set` using private files:

- `VIDHARBOR_DIAGNOSTIC_SSH_KEY`: private diagnostic key.
- `VIDHARBOR_DIAGNOSTIC_KNOWN_HOSTS`: the independently verified known_hosts line.

No password secret is required. Once the public key is installed and both secrets exist, dispatch with `check_ssh=true`. The SSH step uses pinned host identity, batch key authentication, no agent, no forwarding and no TTY. It runs only the fixed identity/OS/disk commands. Its key files are removed by an EXIT trap and are outside the artifact directory. SSH authentication errors fail the job; a skipped authenticated check is not PASS. Inspect the individual step result, not just the transport artifact, to assess authentication.

## Acceptance and limits

PASS for the default run proves DNS and SSH transport **from that GitHub runner at that time**, not login or deployment. Optional SSH PASS additionally proves access to the pinned server using the dedicated key. None of these checks inspect project secrets, call Docker, run sudo, change Nginx/firewall/DNS, install packages or start containers. Server SSH audit logs may record the connection normally.

Before a future deployment, separately inspect existing ports/projects/resources, agree an isolated directory/Compose project/volumes and routing, and approve the exact deployment plan. No deployment credentials, automatic deployment trigger or server modifications are included here.
