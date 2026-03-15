# PowerTools – Plus Project

PowerTools Plus Project is an Autodesk Fusion add-in that connects your design projects to [ClickUp](https://clickup.com), a cloud-based project management platform. Map any Fusion project to a ClickUp task list, open that list from within Fusion with a single click, and create or update ClickUp tasks without leaving the design environment.

![PowerTools Plus Project toolbar panel](_assets/toolbar-panel.png)

---

## Commands

| Command | Location | Purpose |
|---|---|---|
| [Set ClickUp Tokens](set-tokens.md) | QAT › PowerTools Settings | Store your ClickUp and TinyURL API credentials |
| [Map Project to ClickUp](map-project.md) | QAT › PowerTools Settings | Link the active Fusion project to a ClickUp list |
| [Open ClickUp](open-clickup.md) | Design workspace › PowerTools panel | Open the mapped ClickUp list in your browser |
| [Add ClickUp Task](add-task.md) | Design workspace › PowerTools panel | Create a new ClickUp task from within Fusion |
| [List Tasks](list-tasks.md) | Design workspace › PowerTools panel | View tasks linked to the active document and the full project list |
| [Update Tasks](update-tasks.md) | Design workspace › PowerTools panel | Edit task name, due date, and priority for tasks linked to the active document |

---

## First-time setup

Complete these two steps once before using the toolbar commands.

### 1. Set API tokens

Run **Set ClickUp Tokens** from **QAT › PowerTools Settings** and enter your credentials:

- **ClickUp API Token** — Required for all commands that read or write ClickUp tasks. See [Getting Started with the ClickUp API](https://help.clickup.com/hc/en-us/articles/6303426241687-Getting-Started-with-the-ClickUp-API).
- **TinyURL API Token** — Required only when you use the **Link Document to Task** option in **Add ClickUp Task**. See [TinyURL Developer API](https://tinyurl.com/app/dev).

Tokens are saved locally to `cache/auth.json` inside the add-in folder.

### 2. Map each Fusion project

For each Fusion project you want to connect to ClickUp:

1. Open any saved document that belongs to the project.
2. Run **Map Project to ClickUp** from **QAT › PowerTools Settings**.
3. Enter the ClickUp list URL and List ID for that project.

Project mappings are saved locally to `cache/projects.json`.

---

## Typical workflow

1. Create a corresponding ClickUp list for each Fusion project you want to track.
2. Run **Map Project to ClickUp** once per project to store the list URL and List ID.
3. While working in Fusion, use **Open ClickUp** to jump directly to the task list in your browser.
4. Use **Add ClickUp Task** to log new tasks. Optionally link the active Fusion document so teammates can open it directly from ClickUp.
5. Use **List Tasks** to review all tasks linked to the active document or the full project list.
6. Use **Update Tasks** to edit task name, due date, or priority without leaving Fusion.

---

## System architecture

The following diagram shows the high-level relationships between the add-in, Autodesk Fusion, the local cache, and the external services it depends on.

```mermaid
C4Context
    title PowerTools Plus Project — System Context Diagram

    Person(user, "Designer", "Autodesk Fusion user who manages design-related tasks in ClickUp")

    System_Boundary(addin_boundary, "PowerTools Plus Project Add-in") {
        System(addin, "PowerTools Plus Project", "Fusion add-in that bridges the design environment and the ClickUp project management platform")
    }

    System(fusion, "Autodesk Fusion", "CAD host application; provides document and project context to the add-in")
    SystemDb(cache, "Local Cache", "auth.json + projects.json — stores API credentials and project-to-list mappings on the local file system")
    System_Ext(clickup, "ClickUp API v2", "Cloud-based project management platform; receives and returns task data")
    System_Ext(tinyurl, "TinyURL API", "URL shortening service; used when attaching Fusion document deep links to tasks")
    System_Ext(browser, "Web Browser", "Default system browser; opened by the Open ClickUp command")

    Rel(user, addin, "Uses commands in the Fusion toolbar and QAT")
    Rel(addin, fusion, "Reads active document identity and project URN")
    Rel(addin, cache, "Reads and writes API tokens and project mappings")
    Rel(addin, clickup, "Creates, reads, and updates tasks via REST API")
    Rel(addin, tinyurl, "Shortens fusion360:// deep-link URLs (optional)")
    Rel(addin, browser, "Opens ClickUp list URLs on demand")
```

---

## Requirements

- Autodesk Fusion — any current subscription tier
- A ClickUp account with API access
- A TinyURL account with API access *(optional — required only for document linking)*

---

## Cache files

The add-in stores runtime data in the `cache/` folder at the add-in root. These files are not included in source control.

| File | Contents |
|---|---|
| `cache/auth.json` | ClickUp and TinyURL API tokens |
| `cache/projects.json` | Fusion project URN → ClickUp list mappings |

> [!WARNING]
> `cache/auth.json` contains API tokens stored in plain text. Do not share this file or commit it to a repository.

---

*Copyright © 2026 IMA LLC. All rights reserved.*
