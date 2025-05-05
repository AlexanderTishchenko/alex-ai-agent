# Alex AI Agent

Alex AI Agent is a pet project CLI and web-based client for interacting with Large Language Models using the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). It enables users to send one-off or recurring prompts, manage scheduled tasks, and integrate with various MCP-compatible servers.

## Main Features

- **Terminal-based CLI**: Send prompts, pipe inputs, use prompt templates, analyze images, and trigger external tools.
- **Scheduler UI**: Schedule one-off or recurring prompts with a sleek calendar interface and live preview (ISO-8601 and cron).
- **Authentication**: Optional Google OAuth support for secure, session-based usage.
- **Task Management API**: Create, list, update, and delete scheduled tasks via REST endpoints.
- **Tool Integrations**: Web search, content fetching, YouTube summarization, and more through MCP servers.

## Technologies Used

- **Python & Flask**: Backend API, scheduling logic, and server framework.
- **Flask-Login**: Session management and optional OAuth authentication.
- **JavaScript, HTML & CSS**: Interactive frontend components and responsive design.
- **rrule.js, cronstrue & cron-converter**: Client-side scheduling logic and human-readable output.
- **OpenAI API & llama.cpp**: Integration with cloud and local LLM providers.
- **Model Context Protocol (MCP)**: Standardized client-server communication across tools.

## Usage

### Scheduled Prompts (Task Scheduler)

- Click the **Scheduler** button in the chat UI (top right).
- Add a task: enter a prompt, pick a date and time, and choose recurrence (Doesn’t repeat, daily, weekday, weekly, monthly, or custom).
- One-off tasks are sent as ISO-8601 timestamps; recurring as 5-field cron strings (all handled automatically by the UI).
- Live preview shows the schedule in plain English (uses cronstrue for cron, local time display for ISO).
- Validation ensures you can’t schedule in the past or with missing fields.
- Vendor JS libraries required (via CDN in scheduler.html): `rrule.js`, `cronstrue`, `cron-converter`.
- Tasks will POST to `/send_message` as if from you, and responses stream back as usual.
- All scheduled tasks persist across restarts and can be managed in the Scheduler UI.
- API: `/api/tasks` (CRUD, per-user session). See `src/tasks_routes.py`.

## Contributing

Feel free to submit issues and pull requests for improvements or bug fixes.
