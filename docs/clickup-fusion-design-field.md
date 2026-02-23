# Creating the Fusion Design Custom Field in ClickUp

The **Add ClickUp Task** command can attach a shortened "Open in Fusion" link directly to a newly created task. For this to work, the target ClickUp list must have a URL-type custom field named exactly **Fusion Design**.

This field needs to be created once per ClickUp list you intend to use with the add-in.

---

## Step 1 — Open the List's Custom Fields

1. In ClickUp, navigate to the **List** mapped to your Fusion project.
2. Click the **+** icon in the task table header (or right-click any column header) to open the custom fields menu.

<!-- ![ClickUp task table header with the + column button highlighted](images/clickup-custom-field-add-button.png) -->

---

## Step 2 — Add a New Field

1. Select **+ Add Field** or **Create New** from the menu.

<!-- ![Custom fields menu showing Create New option](images/clickup-custom-field-menu.png) -->

---

## Step 3 — Choose the URL Field Type

1. In the field-type picker, select **URL**.

<!-- ![Field type picker with URL selected](images/clickup-custom-field-type-url.png) -->

---

## Step 4 — Name the Field

1. Enter the name **Fusion Design** exactly — spelling and capitalisation must match.
2. Leave all other options at their defaults.
3. Click **Create**.

<!-- ![New URL field named Fusion Design](images/clickup-custom-field-name.png) -->

> **Important:** The field name must be `Fusion Design` (capital F, capital D). The add-in searches for this exact string when looking up the field ID. A mismatch will cause the document link to be silently skipped.

---

## Step 5 — Confirm the Field Appears

After saving, the **Fusion Design** column should appear in your task list. You can drag it to a more convenient position in the table if needed.

![Task list with the Fusion Design URL column visible](images/clickup-custom-field-confirmed.png)

---

## Repeat for Each List

The custom field exists at the **List** level in ClickUp. If you map multiple Fusion projects to different ClickUp lists, you must add the **Fusion Design** field to each list individually.

---

## How the Field Is Used

When **Link Document to Task** is checked in the Add Task dialog:

1. The add-in builds a `fusion360://` deep-link for the active document.
2. The link is shortened via TinyURL using the token in `cache/auth.json`.
3. The add-in calls the ClickUp API to find the field whose name matches `Fusion Design` on the target list.
4. If found, the short URL is written to that field on the newly created task.
5. Clicking the field value in ClickUp opens the document directly in Fusion 360.

If the field is missing or the TinyURL token is not configured, the task is still created — only the document link is omitted.

---

## Related

- [Add ClickUp Task](add-task.md)
- [Set ClickUp Tokens](set-tokens.md)
