# List Tasks

Displays ClickUp tasks associated with the active Fusion 360 document and all tasks in the mapped project list. This is a read-only view — use **Update Tasks** to make edits.

**Location:** Design workspace › PowerTools panel › List Tasks

---

## Prerequisites

- `cache/auth.json` must exist with a valid `clickup_api_token`. Run **Set ClickUp Tokens** if it does not.
- `cache/projects.json` must exist with the active project mapped and a `clickup_list_id` set. Run **Map Project to ClickUp** if it does not.
- A saved Fusion 360 document must be open.
- The ClickUp list must have a text custom field named **Fusion Document URN** for document-linked tasks to appear. Tasks must have been created with **Add ClickUp Task** to populate this field.

---

## Dialog

The dialog is read-only and contains two sections.

### Tasks Linked to This Document

A table showing only tasks whose **Fusion Document URN** custom field exactly matches the active document's URN. Tasks are sorted by priority.

### Project Tasks

A table showing all tasks in the mapped ClickUp list, regardless of document linkage. Tasks are sorted by priority.

Both tables share the same columns:

| Column | Description |
|---|---|
| Task Name | Clickable link that opens the task in ClickUp |
| Priority | 🔴 Urgent / 🟠 High / 🔵 Normal / ⚪ Low |
| Status | Current ClickUp task status |

A link to the mapped ClickUp list appears at the top of the dialog.

---

## Related Commands

| Command | Purpose |
|---|---|
| [Add ClickUp Task](add-task.md) | Create a new task linked to the active document |
| [Update Tasks](update-tasks.md) | Edit task name, due date, and priority from within Fusion |
| [Open ClickUp](open-clickup.md) | Open the mapped ClickUp list in your browser |
