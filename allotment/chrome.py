"""Launch ordinary Chrome. Desktop automation lives in roboclick.desktop."""
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlparse


def browser_mode(worker):
    return "roboclick"


def chrome_executable():
    found = shutil.which("chrome.exe") or shutil.which("google-chrome")
    if found:
        return found
    for folder in [os.environ.get("LOCALAPPDATA"), os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")]:
        if folder:
            candidate = Path(folder) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if candidate.is_file():
                return str(candidate)
    raise ValueError("Google Chrome was not found. Install Chrome .")


def launch_chrome(url="about:blank"):
    if urlparse(url).scheme not in {"http", "https", "about"}:
        raise ValueError("Chrome can open only a webpage, a blank test tab .")
    # Omit --user-data-dir, --profile-directory, incognito and debug flags deliberately:
    # Chrome chooses its normal profile exactly as it does when launched by the user.
    # A URL without --new-window opens a tab in an existing Chrome window.
    return subprocess.Popen([chrome_executable(), url],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)

