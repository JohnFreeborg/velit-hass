# Contributing to velit-hass

Home Assistant integration for Velit Camping heater and AC control via Bluetooth.
Community project — all skill levels welcome.

## Branching & Pull Requests

- Do not develop directly on `main` or `dev`
- Create a feature branch from `dev` for each piece of work: `feature/short-description`
- Bug fixes branch as: `fix/short-description`
- Open PRs targeting `dev`; `main` is reserved for releases
- Keep PRs small and focused — one concern per PR makes review faster
- Write a clear PR description explaining what changed and why

## Hardware Validation

Some work can be fully verified with unit tests alone (packet builders, config flow logic,
coordinator error handling). Work that touches live BLE communication requires validation
against real hardware before it can be merged.

If your PR includes hardware-dependent code, note in the PR description which device you
tested against. If you don't have the hardware, mark the PR clearly so another contributor
can pick up validation.

## Commit Style

- Use the imperative mood: "Add temperature conversion" not "Added" or "Adding"
- Keep the subject line concise — under 72 characters
- If more context is needed, add it in the body after a blank line
- No filler phrases, no commentary on how the code was written

Examples of good commit messages:
```
Add heater packet builder and LRC2 checksum
Fix coordinator UpdateFailed on BLE timeout
Remove unused import in climate entity
```

## Code Standards

- Python 3.12+, full type hints throughout
- Format with `black`, lint with `ruff`
- No bare `except:` — catch specific exception types
- All I/O must be async — no blocking calls on the event loop
- Non-trivial logic must be commented; comments explain *why*, not *what*
- No emojis in code, comments, commits, or documentation

## Home Assistant Conventions

- Follow HA naming conventions: `async_setup_entry`, `async_unload_entry`, etc.
- Store runtime data on `config_entry.runtime_data`, not as globals
- Use `homeassistant.components.bluetooth` wrappers for BLE — do not manage scanners directly
- Raise `UpdateFailed` for transient errors, `ConfigEntryAuthFailed` for auth failures
- Entity unique IDs must be derived from the device BLE address for stability across restarts

## Testing

See [TESTING.md](TESTING.md) for unit test setup, hardware validation checklists,
and debug logging guidance.

## Questions & Discussion

Open a GitHub issue or start a discussion on the repository. PRs are also a good place
to discuss approach before writing code — a draft PR with a description is a valid way
to propose a plan.
