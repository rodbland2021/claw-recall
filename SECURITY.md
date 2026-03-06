# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Claw Recall, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, email: **rod@rodbland.com**

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

You will receive a response within 48 hours. We will work with you to understand and address the issue before any public disclosure.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 2.x     | ✅ Yes    |
| 1.x     | ❌ No     |

## Security Best Practices for Users

- **Never expose the web UI or MCP SSE server to the public internet** without authentication
- **Use environment variables** for API keys, never hardcode them
- **Bind to `127.0.0.1`** unless you specifically need remote access
- **Keep dependencies updated**: `pip install --upgrade -r requirements.txt`
