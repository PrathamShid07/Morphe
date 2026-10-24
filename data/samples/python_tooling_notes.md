# Python tooling notes

## Virtual environments

python -m venv .venv creates an isolated environment so project dependencies do not collide. Activate it, install packages with pip install -r requirements.txt and never commit the environment folder itself.

## Testing with pytest

pytest discovers files named test_*.py and plain assert statements. Fixtures provide reusable setup, parametrize runs one test with many inputs, and the monkeypatch fixture replaces attributes safely.

## Packaging

Describe a project in pyproject.toml, build wheels with python -m build and publish them to PyPI with twine. Pin exact dependency versions in applications and use compatible ranges in libraries.

## Linting and typing

Ruff checks style and common bugs very quickly, black formats code, and mypy verifies type hints. Run them in a pre-commit hook so problems are caught before review.

## Debugging

Insert breakpoint() to open the pdb debugger. Commands n, s and c step over, step into and continue, and p prints a value. The logging module with levels is better than print for long running services.

## Dependency management

pip freeze lists installed versions, pip-tools compiles a locked requirements file, and uv resolves and installs packages much faster. Review dependency updates with a changelog before upgrading.
