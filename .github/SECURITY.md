# Security Policy

## Reporting a vulnerability

Report suspected vulnerabilities privately through GitHub's
[private vulnerability reporting](https://github.com/TilmannF/teamspeak-prometheus/security/advisories/new)
rather than a public issue.

Please include what you observed, how to reproduce it, and the version or commit
you tested.

## Scope

This exporter holds a TeamSpeak ServerQuery credential and exposes an unauthenticated
HTTP endpoint. The things worth reporting:

- The ServerQuery password appearing in output, an error message, an exception
  trace, a metric label, or the container image
- Anything served on the metrics port beyond Prometheus metrics
- Dependency vulnerabilities that are actually reachable from this code

## Known and accepted

- **The metrics endpoint is unauthenticated and unencrypted.** That is how
  Prometheus exporters work. Do not expose it to the public internet; bind it to
  a private network or put it behind your own proxy.
- **`--ts3password` exposes the password through the process list.** Use the
  `TEAMSPEAK_PASSWORD` environment variable instead. The flag is kept for
  backwards compatibility and documented as risky.
- **Raw ServerQuery is unencrypted.** The password travels in cleartext
  between exporter and TeamSpeak, as with every raw ServerQuery client. Run the
  exporter on the same host or a private network.
- **The TeamSpeak server is trusted with the password**, necessarily: it is
  sent there to log in. What a hostile server answers is treated as untrusted
  — the password is censored from every log line and every metric label, and
  control characters in log lines are escaped, so server text can neither
  leak the password into logs or `/metrics` nor forge log lines.

## Not in scope

Vulnerabilities in TeamSpeak itself — report those to TeamSpeak Systems.
