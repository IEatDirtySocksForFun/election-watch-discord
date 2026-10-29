# Security

How to report a security problem in election-watch-discord, and what counts as one.

## 🔒 Reporting a vulnerability

Do not open a public issue. Use GitHub's private reporting instead: the **Security** tab →
**Report a vulnerability**. Reports get an answer as soon as possible.

## 🎯 What counts

- A way to make the bot leak its token or database URI, or run code.
- A way to make the bot ping people it should not, or let a non-admin change a server's
  settings.
- A way to make the bot hammer a data source past its request budget.
- Secrets committed to the repository.
- A dependency with a known vulnerability that affects the bot.

## 🛡️ Running a copy safely

- Keep `DISCORD_TOKEN` and `MONGODB_URI` in `.env` (git-ignored).
- Give the bot only the Discord permissions listed in the README.
- Never expose MongoDB to the internet without authentication.
