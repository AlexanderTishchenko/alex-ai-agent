# Changelog

## [Unreleased] Scheduler Feature

- Major UI/UX overhaul: schedule one-off (date+time) or recurring (daily, weekday, weekly, monthly, custom) prompts with a friendly interface.
- Date/time pickers, recurrence dropdown, preview pill, and validation for non-technical users.
- One-off tasks use ISO-8601; recurring use cron. No backend changes needed.
- Requires vendor JS libs: rrule.js, cronstrue, cron-converter (via CDN in scheduler.html).
- All requirements and docs updated; full test coverage recommended via pytest/playwright.
