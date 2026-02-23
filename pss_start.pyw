"""PSS persistent launcher - handles pythonw's null stdio."""
import sys, os, traceback

# Resolve paths relative to this file
PSS_ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(PSS_ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# pythonw sets stdout/stderr to None - uvicorn needs real streams
if sys.stdout is None:
    sys.stdout = open(os.path.join(LOG_DIR, "stdout.log"), "w")
if sys.stderr is None:
    sys.stderr = open(os.path.join(LOG_DIR, "stderr.log"), "w")

os.chdir(PSS_ROOT)
sys.path.insert(0, PSS_ROOT)

try:
    from pss.server import main
    main()
except Exception:
    with open(os.path.join(LOG_DIR, "crash.log"), "w") as f:
        traceback.print_exc(file=f)
