# Update Tasks

View and edit ClickUp tasks linked to the active Fusion 360 document. Changes to task name, due date, and priority are saved directly to ClickUp when you click **OK**.

**Location:** Design workspace › PowerTools panel › Update Tasks

---

## Prerequisites

- `cache/auth.json` must exist with a valid `clickup_api_token`. Run **Set ClickUp Tokens** if it does not.
- `cache/projects.json` must exist with the active project mapped and a `clickup_list_id` set. Run **Map Project to ClickUp** if it does not.
- A saved Fusion 360 document must be open.
- The ClickUp list must have a text custom field named **Fusion Document URN**. Tasks must have been created with **Add ClickUp Task** (which populates this field) for them to appear in the dialog.

If any prerequisite is missing, the command displays a setup prompt and exits without opening the dialog.

---

## Dialog

The dialog shows all ClickUp tasks whose **Fusion Document URN** custom field exactly matches the active document's URN. Tasks are sorted by priority (Urgent → High → Normal → Low).

| Column | Editable | Description |
|---|---|---|
| Task Name | ✅ Yes | The ClickUp task title |
| Due Date | ✅ Yes | Date in `YYYY-MM-DD` format; leave blank to clear |
| Priority | ✅ Yes | Urgent, High, Normal, or Low |
| Status | ❌ No | Current task status (read-only — use ClickUp to change) |

A link to the mapped ClickUp list appears at the top of the dialog.

---

## Saving Changes

Click **OK** to apply edits. The command compares each field against its original value fetched from ClickUp and only sends a `PATCH` request for tasks where at least one field changed. Unchanged tasks are skipped — no unnecessary API calls are made.

A summary message reports how many tasks were updated successfully and flags any failures. Check the Fusion add-in log for per-task detail if errors occur.

Click **Cancel** to close the dialog without saving any changes.

---

## Due Date Validation

The **Due Date** field is validated before the dialog allows **OK** to be clicked:

- Blank — allowed; clears the existing due date on the task.
- `YYYY-MM-DD` — required format if a value is entered.
- Any other format — the **OK** button is disabled until corrected.

---

## Status Field

Task status values are workspace-specific in ClickUp and cannot be reliably enumerated without additional API calls. The **Status** column is shown for reference only. To change a task's status, open it directly in ClickUp using the task name link (visible in the task's tooltip).

---

## API Reference

The command uses:

- `GET /api/v2/list/{list_id}/task` — fetch tasks filtered by the `Fusion Document URN` custom field.
- `GET /api/v2/list/{list_id}/field` — locate the `Fusion Document URN` field ID.
- `PUT /api/v2/task/{task_id}` — update changed fields on individual tasks.

See [ClickUp API Docs — Update Task](https://developer.clickup.com/reference/updatetask) for full payload details.

---

## Related Commands

| Command | Purpose |
|---|---|
| [Add ClickUp Task](add-task.md) | Create a new task linked to the active document |
| [List Tasks](list-tasks.md) | Read-only view of document tasks and full project list |
| [Open ClickUp](open-clickup.md) | Open the mapped ClickUp list in your browser |
