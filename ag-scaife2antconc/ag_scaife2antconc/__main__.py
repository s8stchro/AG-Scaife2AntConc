import sys

if any(arg.startswith('-') for arg in sys.argv[1:]):
    from .cli import main
else:
    from .gui import main
main()
