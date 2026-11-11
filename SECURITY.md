# Security Policy

## Supported Versions

We actively maintain and provide security updates for the latest major release of the **OoT Remake Asset Explorer**.

| Version | Supported          |
| ------- | ------------------ |
| Latest  | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

We take the security of our application and users seriously. If you discover a security vulnerability (such as memory safety issues when parsing corrupted archives or binary files), please report it responsibly.

### How to Report

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report vulnerabilities by following these steps:

1. **Email or Private Disclosure:** Reach out via GitHub Private Vulnerability Reporting (available under the **Security** tab of this repository) or contact the core maintainer directly.
2. **Provide Details:** Include a detailed description of the issue, steps to reproduce, and any proof-of-concept files or code (e.g., a specially crafted dummy asset file causing a crash or heap overflow).

### What to Expect

- **Acknowledgment:** You will receive a response within 48 hours acknowledging receipt of your report.
- **Assessment:** The maintainers will investigate and verify the vulnerability within 5 business days.
- **Resolution:** If confirmed, a patch will be prepared and released in a subsequent update. You will be credited in the release notes if you wish.

## Scope & Intellectual Property Notice

This tool is strictly designed to analyze and process local game archives (`romfs`) provided by the user.

- **Data Privacy:** This tool operates entirely offline and does not collect, track, or transmit any user data or system telemetry.
- **Asset Integrity:** Users are responsible for using valid, uncorrupted, and legally obtained file dumps. Security reports regarding malicious behavior caused by modified third-party software or unofficial game patches fall outside the scope of this security policy.

Thank you for helping keep this project safe for the modding community!
