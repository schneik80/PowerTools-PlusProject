# PowerTools – Plus Project

A Fusion 360 add-in that connects your design projects to [ClickUp](https://clickup.com). Map any Fusion project to a ClickUp task list, open that list with a single click, and create new tasks without leaving the design environment.

![PowerTools Plus Project toolbar panel](docs/_assets/toolbar-panel.png)

---

## Commands

| Command | Location | Purpose |
|---|---|---|
| [Set ClickUp Tokens](docs/set-tokens.md) | QAT › PowerTools Settings | Store your ClickUp and TinyURL API keys |
| [Map Project to ClickUp](docs/map-project.md) | QAT › PowerTools Settings | Link the active Fusion project to a ClickUp list |
| [Open ClickUp](docs/open-clickup.md) | Design toolbar | Open the mapped ClickUp list in your browser |
| [Add ClickUp Task](docs/add-task.md) | Design toolbar | Create a new ClickUp task from within Fusion |

---

## Installation

1. Download or clone this repository.
2. In Fusion 360, open the **Scripts and Add-Ins** dialog (`Shift+S`).
3. On the **Add-Ins** tab, click the **+** icon and select the repository folder.
4. Select **PowerTools-PlusProject** and click **Run**.

The add-in loads the PowerTools panel into the Design workspace toolbar and adds a **PowerTools Settings** flyout to the Quick Access Toolbar (QAT).

---

## First-Time Setup

Complete these two steps before using the toolbar commands.

### 1. Set API Tokens

Run **Set ClickUp Tokens** from **QAT › PowerTools Settings** and enter your credentials:

- **ClickUp API Token** — required for all commands. See [Getting Started with the ClickUp API](https://help.clickup.com/hc/en-us/articles/6303426241687-Getting-Started-with-the-ClickUp-API).
- **TinyURL API Token** — required only when attaching a document link to tasks. See [TinyURL Developer API](https://tinyurl.com/app/dev).

### 2. Map Each Fusion Project

Open any saved document in a project, run **Map Project to ClickUp**, and enter the ClickUp list URL and List ID. Repeat for each Fusion project you want to connect.

---

## Typical Workflow

1. Create a peer project in ClickUp for each Fusion project, with a task list to track work.
2. Run **Map Project to ClickUp** once per project to store the list URL and ID.
3. While working in Fusion, use **Open ClickUp** to jump directly to the task list.
4. Use **Add ClickUp Task** to log new tasks — optionally linking the active Fusion document.

---

## Requirements

- Autodesk Fusion — any current subscription tier
- A ClickUp account with API access
- A TinyURL account with API access *(optional — required only for document linking)*

---

## Documentation

Full command reference is in the [`docs/`](docs/) folder.

- [Set ClickUp Tokens](docs/set-tokens.md)
- [Map Project to ClickUp](docs/map-project.md)
- [Open ClickUp](docs/open-clickup.md)
- [Add ClickUp Task](docs/add-task.md)
- [Creating the Fusion Design Custom Field](docs/clickup-fusion-design-field.md)

---

## Cache Files

The add-in stores runtime data locally in `cache/` at the add-in root. These files are excluded from source control and must not be shared.

| File | Contents |
|---|---|
| `cache/auth.json` | ClickUp and TinyURL API tokens |
| `cache/projects.json` | Fusion project URN → ClickUp list mappings |
