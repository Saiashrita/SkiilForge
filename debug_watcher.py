"""
Run this to test if watchdog detects changes on your system.
python debug_watcher.py
Then edit any .py file and save it.
"""
import time
import sys
from watchdog.observers.polling import PollingObserver
from watchdog.events import FileSystemEventHandler

class TestHandler(FileSystemEventHandler):
    def on_any_event(self, event):
        print(f"EVENT: {event.event_type} -> {event.src_path}")

print("Starting watchdog test on current directory...")
print("Edit and save any .py file now. You should see EVENT lines appear.")
print("Press Ctrl+C to stop.\n")

observer = PollingObserver(timeout=1)
observer.schedule(TestHandler(), ".", recursive=True)
observer.start()

try:
    while True:
        time.sleep(1)
        sys.stdout.flush()
except KeyboardInterrupt:
    observer.stop()
observer.join()