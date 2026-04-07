# justfile for cchat

# Install the project with all optional dependencies and dev tools
install:
    uv sync --all-extras --all-groups

# Run tests
test *args:
    uv run python -m pytest tests/ {{args}}

# Lint with ruff
lint *args:
    uvx ruff check {{args}} .

# Type check with ty
typecheck *args:
    uv run --with ty ty check {{args}}

# Run all checks (test + lint + typecheck)
check: test lint typecheck

# Install a git pre-commit hook that runs check
pre-commit-hook:
    @echo '#!/bin/sh' > .git/hooks/pre-commit
    @echo 'just check' >> .git/hooks/pre-commit
    @chmod +x .git/hooks/pre-commit
    @echo "pre-commit hook installed"
