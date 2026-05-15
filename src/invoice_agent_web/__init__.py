"""HTTP adapter exposing :func:`invoice_agent.agent.run_intake` to a browser UI.

This package is an *adapter* layer (per ARCHITECTURE.md): it owns no
business logic. It accepts a multipart upload (Email.json + PDF), stages
the inputs in a temp case directory, calls ``run_intake``, and returns
the produced artefacts as JSON for the React dashboard in ``src/frontend/``.
"""
