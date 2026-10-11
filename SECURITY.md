# Security

Stardeck connects to people's databases, so security reports are taken seriously.

## Reporting a problem

Please **don't open a public issue**. Report it privately through GitHub instead:
[Security → Report a vulnerability](https://github.com/Artechie0806/Stardeck/security/advisories/new).

Include what you found, how to reproduce it, and what someone could do with it. You'll get a reply
within a week. Once a fix is released, you'll be credited in the changelog unless you'd rather not be.

## What's in scope

The things Stardeck promises, and anything that breaks them:

- **Read-only:** nothing Stardeck runs can change a connected database.
- **Row rules:** a viewer limited to some rows can't see others, through charts, filters or the analyst.
- **Workspaces (hosted mode):** one workspace can never read or change another's data.
- **Share links:** people with a link can't see SQL, run their own SQL, or reach anything beyond that dashboard.
- **Stored secrets:** saved database passwords stay encrypted and are never returned by the API.
- **Hidden columns:** columns marked "Hide from AI" never reach the model.
- **Hosted network rules:** data sources can't reach private networks or the server itself.

## Supported versions

Fixes go into the latest release. Please check that the problem still happens on `main` before reporting it.
