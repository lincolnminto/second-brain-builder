# Security policy

## Report a vulnerability privately

Use [GitHub private vulnerability reporting](https://github.com/lincolnminto/second-brain-builder/security/advisories/new), or open the repository's **Security** tab and select **Report a vulnerability**. Do not disclose vulnerabilities in public issues, pull requests, or discussions.

Include:

- The affected version or commit and the relevant component.
- Reproduction steps with synthetic data and placeholder credentials.
- The expected security boundary and how the issue crosses it.
- The potential impact and any proposed mitigation.

Do not send live tokens, encryption keys, real document content, or private repository details. Redact logs before attaching them. Maintainers will use the private GitHub advisory to discuss the report and coordinate a fix and disclosure.

Security fixes focus on the current `main` branch. Report the version you used; maintainers may ask you to check a newer revision. There is no guaranteed response or fix deadline.

## Deployment boundaries

The default Compose configuration exposes the app only on `127.0.0.1:8484`. UI login is optional; MCP always needs an API token. Review [security and privacy](docs/architecture.md#security-and-privacy) before changing network exposure.

GitHub and Provider credentials are encrypted at rest. The local data volume also contains the encryption key, and the app does not encrypt Sources or Wiki Pages. Protect the volumes and backups. Each Brain requires a GitHub repository; cloud Providers receive content used for their configured tasks.

Treat Sources and generated pages as untrusted content. An agent that reads a Brain must treat document text as evidence, not as instructions.
