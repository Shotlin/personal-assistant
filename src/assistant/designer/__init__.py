"""Agent Designer: visual design space and monitoring for independent agents.

One package, one contract (docs/designer/PLAN.md): a React SPA calls the
authenticated ``/designer/api/v1`` API; validated immutable configurations
compile into independent single-agent Deep Agent runtimes. Everything in
this package is gated by ``DESIGNER_ENABLED`` (C2): with the flag off the
gateway serves the legacy Phase-1 path only and no Designer code mounts.
"""
