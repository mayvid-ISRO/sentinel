# Contributing to Sentinel

Thanks for taking the time to contribute! This project is open-source and we welcome improvements — especially around tool additions, documentation, and cross-platform reliability.

---

## Getting Started

```bash
git clone https://github.com/mayvid-ISRO/sentinel.git
cd sentinel
./setup.sh          # or setup.bat on Windows
```

Run the test suite before making changes:

```bash
./setup.sh test     # 174 tests, no network or LLM needed
```

All tests pass offline — you don't need an LLM or browser installed to verify your changes.

---

## Pull Request Guidelines

1. **Describe the problem** — What issue does this PR solve? Link to any relevant issues.
2. **Keep changes scoped** — One feature or fix per PR. Makes review faster.
3. **Add tests** — If you're changing logic, add or update a test in `tests/`.
4. **No secrets in code** — Credentials belong in `tools/credentials.py` or environment variables, never committed.
5. **Follow existing style** — Run `python -m pytest` before submitting; all 174 tests must pass.

---

## Adding a New Tool

1. Create a new file in `tools/<category>/my_tool.py`
2. Import the `Tool` base class from `tools/base.py`
3. Define `name`, `description`, `args_schema`, and the `func`
4. Register it in `tools/__init__.py`
5. Add a test in `tests/test_<category>.py`
6. Document the args in the tool's docstring (the agent uses this for prompting)

---

## Code of Conduct

Be respectful, constructive, and patient. This project serves critical infrastructure operators — clarity and correctness matter more than speed.
