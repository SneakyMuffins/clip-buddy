"""Entry point of ClipBuddy.exe (the Windows download). Built by tools/build_windows.py."""
import traceback

from clipbuddy.__main__ import main

try:
    main()
except KeyboardInterrupt:
    pass
except BaseException:
    # Double-clicked exe: without this the window closes before the error can be read.
    traceback.print_exc()
    input("\nClip Buddy stopped with an error. Press Enter to close this window.")
