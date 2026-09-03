---
type: dashboard
status: evergreen
created: {{DATE}}
updated: {{DATE}}
memory_level: l2
memory_provenance: "work-zone PARA folders and frontmatter status fields"
tags: [dashboard]
---
# Work — Control Panel

> Live dashboard (auto-updates). Requires the **Dataview** community plugin.
> Scope: WORK zone only (projects / areas / resources). Other zones
> have their own dashboards in their zones.

## 🔴 Stale — active but untouched 45+ days (review, finish, or archive)
```dataview
TABLE status, updated, priority
FROM "wiki/workspace/projects" or "wiki/areas" or "wiki/resources"
WHERE (status = "active" or status = "current" or status = "developing" or status = "build-ready" or status = "dev")
  AND updated AND updated <= date(today) - dur(45 days)
SORT updated ASC
```

## 🟡 Drafts — unfinished, awaiting a decision
```dataview
TABLE status, type, updated, priority
FROM "wiki/workspace/projects" or "wiki/areas" or "wiki/resources"
WHERE status = "draft"
SORT updated DESC
```

## 🟢 Active work — most recently touched
```dataview
TABLE status, type, updated
FROM "wiki/workspace/projects" or "wiki/areas" or "wiki/resources"
WHERE status = "active" or status = "current" or status = "dev" or status = "developing" or status = "build-ready" or status = "ready-to-run"
SORT updated DESC
LIMIT 25
```

## 📥 Inbox backlog — route these into a project/resource
```dataview
LIST
FROM "wiki/workspace/projects/inbox" or "wiki/resources/incoming"
SORT file.mtime DESC
```
