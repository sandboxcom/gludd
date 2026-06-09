---
{
  "name": "medium-google-workspace",
  "description": "Google Workspace automation via unified CLI. 50+ APIs: Gmail, Drive, Calendar, Docs, Sheets, Slides, Chat, Admin. Built-in MCP server for agent access. Source: unicodeveloper/medium-2026.",
  "tags": [
    "google",
    "workspace",
    "gmail",
    "drive",
    "calendar",
    "automation",
    "medium-2026"
  ],
  "category": "automation"
}
---

# Google Workspace (GWS)

Unified interface to 50+ Google Workspace APIs through dynamic discovery.

## Capabilities

- **Gmail**: read, draft, send, search, label management
- **Drive**: upload, download, share, organize files and folders
- **Calendar**: create events, check availability, manage invites
- **Docs**: create, edit, read documents programmatically
- **Sheets**: read/write cell data, create formulas, manage sheets
- **Slides**: create presentations, add slides, insert content
- **Chat**: send messages, manage spaces
- **Admin**: user management, permissions, audit logs

## Setup

```bash
npm install -g @googleworkspace/cli
gws mcp -s drive,gmail,calendar,sheets
```

## Workflow Patterns

**Executive assistant**: email drafting, calendar management, meeting notes to Docs
**Project manager**: task tracking in Sheets, status updates to Chat
**IT admin**: user management, permissions, audit logs
**Sales team**: CRM updates, proposal generation

## Key Principle

Any workflow that involves copying between Google apps can become fully
automated. Agents read Gmail, draft responses, update Sheets, create Calendar
events, and generate Docs from a single prompt.

## Install reference

Original: `npx skills add https://github.com/googleworkspace/cli`
4,900 GitHub stars in first 3 days (March 2026).
