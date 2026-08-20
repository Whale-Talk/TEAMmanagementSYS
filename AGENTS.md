# Repository Guidelines

## Project Structure & Module Organization

The application lives in `pm-webapp-20260817/webapp/`. `app.py` contains the Flask routes, authentication, request handling, and startup logic. Database models and relationships are defined in `models.py`; AI integration belongs in `ai.py`. Jinja templates are under `templates/`, shared styling is in `static/style.css`, and SQLite data is stored in `database.db`. Schema maintenance scripts (`migrate.py` and `fix_task_schema.py`) should remain focused, reviewable utilities rather than application entry points.

## Build, Test, and Development Commands

Run commands from the web application directory:

```bash
cd pm-webapp-20260817/webapp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

The server starts at `http://localhost:5000` and creates missing tables on launch. Run `python migrate.py` only after reviewing the script and backing up `database.db`. There is no separate build step; templates and CSS are served directly by Flask.

## Coding Style & Naming Conventions

Use four-space indentation and follow PEP 8 for Python. Prefer `snake_case` for functions and variables, `PascalCase` for SQLAlchemy models, and descriptive Flask endpoint names such as `project_edit`. Keep route handlers small where practical and place reusable data behavior on models or focused helpers. Template filenames use lowercase snake case, for example `change_password.html`; partials begin with `_`, such as `_task_card.html`. No formatter or linter is configured, so preserve the surrounding style and remove unused imports.

## Testing Guidelines

No automated test framework or coverage threshold is currently configured. Before submitting changes, exercise login, CSRF-protected form submissions, role permissions, project/task CRUD, and database migrations against a disposable database copy. If adding tests, use `pytest`, place them in `webapp/tests/`, and name files `test_<feature>.py`.

## Commit & Pull Request Guidelines

Git history is unavailable in this workspace, so no repository-specific commit convention can be inferred. Use short, imperative subjects such as `Fix project priority ordering`, and keep unrelated changes separate. Pull requests should explain the user-visible effect, database or configuration impact, and validation performed. Link relevant issues and include screenshots for template or CSS changes.

## Security & Configuration

Store local secrets in `webapp/.env`; never commit credentials or production data. Replace the development `SECRET_KEY` for deployments. Treat `database.db` as sensitive state and back it up before running schema utilities.
