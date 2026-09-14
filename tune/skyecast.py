import json
import time
import subprocess
import threading
import urllib.request
from typing import Optional
from .client import send_cmd

def start_broadcast(name: str, port: int) -> None:
    print(f"📡 Starting Skyecast broadcast on port {port}...")
    try:
        proc = subprocess.Popen(["npx", "--yes", "localtunnel", "--port", str(port), "--subdomain", name],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # Wait a bit for it to start
        url = ""
        if proc.stdout:
            for _ in range(30):
                line = proc.stdout.readline()
                if "your url is:" in line:
                    url = line.split("is:")[1].strip()
                    break
        if not url:
            url = f"https://{name}.loca.lt"
        print("\n" + "="*50)
        print(f"📻 SKYCAST LIVE: {url}")
        print(f"👉 Tell your friends to run: skye join {name}")
        print("="*50)
        print("\nPress Ctrl+C to stop broadcasting.")
        proc.wait()
    except FileNotFoundError:
        print("❌ 'npx' is not installed. Please install Node.js to use Skyecast.")
    except KeyboardInterrupt:
        if 'proc' in locals():
            proc.terminate()
        print("\nBroadcast ended.")

def join_broadcast(name: str) -> None:
    url = name if name.startswith("http") else f"https://{name}.loca.lt"
    events_url = f"{url}/api/events"
    print(f"🎧 Joining Skyecast: {url}...")
    
    req = urllib.request.Request(events_url, headers={"User-Agent": "SkyeClient/1.0", "Bypass-Tunnel-Reminder": "true"})
    
    last_url = ""
    try:
        with urllib.request.urlopen(req) as r:
            print("✅ Connected! Syncing playback...")
            for line in r:
                line = line.decode("utf-8").strip()
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                        cur_url = data.get("url")
                        state = data.get("state")
                        pos = data.get("pos", 0)
                        
                        if not cur_url:
                            continue
                            
                        # If track changed, play it
                        if cur_url != last_url:
                            print(f"🎵 Host played: {data.get('title')}")
                            send_cmd("play", cur_url)
                            last_url = cur_url
                            # Give daemon a second to load before seeking
                            time.sleep(1)
                            
                        # Sync state (pause/play)
                        local_status = send_cmd("status").get("data", {})
                        if local_status.get("state") != state:
                            if state == "playing" and local_status.get("state") == "paused":
                                send_cmd("resume")
                            elif state == "paused" and local_status.get("state") == "playing":
                                send_cmd("pause")
                                
                        # Sync position if drift > 4 seconds
                        local_pos = local_status.get("pos", 0)
                        if state == "playing" and abs(local_pos - pos) > 4.0:
                            send_cmd("seek", str(int(pos)))
                            
                    except json.JSONDecodeError:
                        pass
    except urllib.error.URLError as e:
        print(f"❌ Could not connect to {url}: {e}")
    except KeyboardInterrupt:
        print("\nLeft broadcast.")
