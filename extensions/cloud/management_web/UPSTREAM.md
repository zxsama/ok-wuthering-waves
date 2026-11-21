# Cloud management UI provenance

These files preserve the cloud management UI removed from ok-script 3.0.7.
The application continues to run on ok-script 3.0.7; no older core is loaded.

Source: https://pypi.org/project/ok-script/2.0.15b2/
Repository: https://github.com/ok-oldking/ok-script
Artifact: `ok_script-2.0.15b2-py3-none-any.whl`
SHA-256: `f58e60d2fd6825ccf833c2ce7d290c0f93464c41224411ec9b9794ac6c03762c`

Original paths: `ok/ui/web/app.py`, `ok/ui/web/requirements.py`,
`ok/ui/web/static/*`, and `ok/task/web.py` (now `task_tabs.py`).
The bundled upstream license is retained in `LICENSE.upstream.txt`.

The cloud workspace maps the ok-script 3.0.7 Qt run bar, navigation and task
pages to browser controls. `static/workspace.js` owns routing, runtime events,
theme and navigation. `static/app.js` exports the retained React panels for
schedules, settings, scripts, templates, about and task-owned tabs. Its schedule
forms persist the cloud email-report option. See `static/workspace_panels.js`
for their integration. The package only uses the installed 3.0.7 runtime.
