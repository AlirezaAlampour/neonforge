#!/usr/bin/env python3
import subprocess
import re
import sys
import time
import os

def generate_constraints():
    """Silently locks down the NVIDIA GPU packages."""
    subprocess.run("pip show torch | grep Version | awk '{print \"torch==\"$2}' > /tmp/dgx_constraints.txt", shell=True, stderr=subprocess.DEVNULL)
    subprocess.run("pip show torchvision | grep Version | awk '{print \"torchvision==\"$2}' >> /tmp/dgx_constraints.txt", shell=True, stderr=subprocess.DEVNULL)

def run_app(target_script):
    if not os.path.exists(target_script):
        print(f"❌ Error: Could not find {target_script}")
        sys.exit(1)

    generate_constraints()
    
    while True:
        print(f"\n🚀 Booting {target_script}...")
        
        process = subprocess.Popen(
            [sys.executable, target_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Stream the output so you can see what the app is doing
        while True:
            output = process.stdout.readline()
            if output == '' and process.poll() is not None:
                break
            if output:
                print(output.strip())
                
        retcode = process.poll()
        _, stderr = process.communicate()
        
        # If it crashed, intercept the error
        if retcode != 0:
            match = re.search(r"ModuleNotFoundError: No module named '([^']+)'", stderr)
            if match:
                missing_module = match.group(1)
                print(f"⚠️ Missing: {missing_module}. Installing safely...")
                subprocess.run([sys.executable, "-m", "pip", "install", missing_module, "-c", "/tmp/dgx_constraints.txt"])
            else:
                print("❌ App crashed (Not a dependency issue):")
                print(stderr)
                break
        else:
            print("✅ App exited cleanly.")
            break

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: dgx-run <your_script.py>")
        sys.exit(1)
    run_app(sys.argv[1])