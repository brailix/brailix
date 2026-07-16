# Security Policy

brailix converts documents, mathematics, music, and images into braille and
tactile graphics. It runs as a library, as the `brailix` command line tool, and
can be embedded in a service that accepts files from users you do not control.
This document explains how to report a security problem and what to expect in
return.

## Supported versions

brailix is developed on the `main` branch and published as versioned releases.
Security fixes land on `main` and in the most recent release. If you are running
an older release, please upgrade to the latest one before reporting, because the
issue may already be fixed.

| Version | Supported |
| --- | --- |
| Latest release and `main` | Yes |
| Older releases | No |

## Reporting a vulnerability

Please report security issues privately, not in a public issue, pull request, or
discussion. Publicly disclosing a vulnerability before a fix exists puts every
user at risk.

Use GitHub's private vulnerability reporting: open this repository's Security tab
and choose "Report a vulnerability". That opens a private advisory visible only
to you and the maintainers.

Please include, as far as you can:

- the affected release or commit,
- a description of the issue and the impact you believe it has,
- a minimal input or the steps that reproduce it, and
- a suggested fix, if you have one.

## What to expect

- An acknowledgement that your report was received, within seven days.
- An initial assessment — accepted, needs more information, or out of scope —
  within fourteen days.
- Progress updates while a fix is developed. The time to a fix depends on the
  severity of the issue and the complexity of the change; a clear, high-severity
  issue is prioritised.
- Coordinated disclosure. Once a fix is released, a security advisory is
  published with credit to you, unless you would rather remain anonymous, and a
  CVE is requested through GitHub for issues that warrant one.

Please give the maintainers a reasonable opportunity to release a fix before you
disclose the issue publicly.

## Scope

In scope is the brailix core library and command line tool in this repository,
especially the parsing and conversion of untrusted input: text, Markdown,
MusicXML and `.mxl`, MathML, LaTeX and OMML, SVG and raster images, and the
OOXML inside a `.docx`. The library caps input size and rejects XML
entity-expansion ("billion laughs") bombs; a service that accepts untrusted
uploads should still set its own `InputLimits`, which the API documentation
describes.

Out of scope are vulnerabilities in third-party dependencies, which should be
reported to the upstream project, issues that require an already-compromised
host, and resource exhaustion driven by inputs far above the size limits you
have configured.
