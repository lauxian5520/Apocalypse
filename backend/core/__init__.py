"""Cross-cutting infrastructure: configuration, database, security, dependencies.

Nothing in this package imports from `routers` or `services`, which keeps the
dependency graph one-directional:

    routers  ->  services  ->  models
        \\____________\\_________/  ->  core
"""
