# Security

Found a security problem? Please don't open a public issue. Use GitHub's private reporting
instead: the **Security** tab → **Report a vulnerability**. We'll get back to you as soon as we
can.

Things that count: a way to make the bot leak its token or database URI, run code, ping people
it shouldn't, let a non-admin change a server's settings, or hammer a data source; secrets
committed to the repo; a dependency with a known vulnerability that affects the bot.

Running your own copy? Keep `DISCORD_TOKEN` and `MONGODB_URI` in `.env` (it's git-ignored),
give the bot only the Discord permissions listed in the README, and don't expose MongoDB to the
internet without authentication.
