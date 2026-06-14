"""local_proxy entry point.

CLI:  python -m local_proxy
GUI:  python -m local_proxy --gui
      python -m local_proxy.gui
"""

from __future__ import annotations

import sys


def main() -> None:
    """Dispatch to CLI or GUI depending on ``--gui``."""
    if "--gui" in sys.argv:
        sys.argv = [a for a in sys.argv if a != "--gui"]
        from local_proxy.gui import main as gui_main

        gui_main()
        return
    from local_proxy.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
