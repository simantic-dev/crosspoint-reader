"""Wiring for the simulated-hardware tests.

There is none left to do: the sim-backed tests host the engine through the
`simantic` pip package (`pip install simantic`), so they need no plugin, no
fixture tree on PYTHONPATH and no `--sim` option. They skip themselves when
the package is absent, and the pure tests (test_hid_descriptor.py) run either
way, so a bare checkout stays green.

    pip install simantic
    SIMANTIC_SIM=<publish-dir>/sim pytest test/sim -v

SIMANTIC_SIM is only needed to point at a specific build; without it the
package uses the engine it installed.
"""
