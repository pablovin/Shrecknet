# AGENTS

This repository houses Shrecknet, a collaborative world building platform.

## Coding Standards
- **Python backend**
  - Format with `black` using default settings.
  - Follow PEP8 and include type hints.
  - Run `pytest` under `backend` before committing.
- **Node/React frontend**
  - Format with `prettier` and `eslint` using the repository configuration.
  - Use functional components and React hooks.
  - Run `npm test` under `frontend` before committing.
- **General**
  - Use descriptive names and add docstrings or comments where helpful.
  - Keep functions small and focused.
  - Follow Conventional Commit messages in commit logs.

## Design Philosophy
- Emphasize modularity and clear separation of concerns between backend, frontend and AI agents.
- Prefer explicit over implicit; make intent clear in code and documentation.
- Optimize for readability and maintainability over premature optimization.
- Include tests and documentation with new features when practical.
