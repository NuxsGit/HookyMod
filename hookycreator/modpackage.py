"""Compatibility entry point for the separated, source-only .hooky packager.

HookyCreator does not use this module; desktop builds exclude it and modmaker.
Importing the old name returns the implementation itself, preserving callers
that patch constants or import private helpers. The historical CLI still works.
"""
if __name__ == "__main__":
    from modmaker.modpackage import main

    raise SystemExit(main())
else:
    import sys

    from modmaker import modpackage as _implementation

    sys.modules[__name__] = _implementation
