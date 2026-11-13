# Security policy

## Reporting a vulnerability

Please **don't open a public issue** for a security problem. Report it privately through GitHub:
on the repository's **Security** tab, choose **Report a vulnerability**. Include what you found,
how to reproduce it, and what an attacker could do with it.

You'll get an answer as soon as possible. Once a fix is ready, it is released and the report is
credited unless you prefer otherwise.

## Supported versions

Forge is developed on `main`; fixes go there. There are no separately maintained release branches.

## How Forge is meant to run

Forge is a **local, single-user tool**. Keep these in mind when you deploy it:

- **No authentication.** The API and web app trust whoever can reach them. Forge listens on
  `127.0.0.1` by default; don't bind it (`host`) to a public or shared network.
- **It runs commands.** Through `assetgen.toml`, Forge starts Blender, UniRig, HY-Motion and the
  `[restart.<tool>]` commands. Treat that file like a script: only use one you wrote.
- **It writes into your game project.** Exports go to the folder registered for each project.
- **Third-party tools and models** (ComfyUI and its custom nodes, Hunyuan3D, UniRig, model
  weights, LoRAs) run with your user's rights. Get them from sources you trust.

Reports about these by-design properties are welcome as hardening ideas, but they aren't treated
as vulnerabilities.
