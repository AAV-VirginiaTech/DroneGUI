# DroneGUI

Simple Python GUI for running Linux commands on the drone over SSH.

## Setup
```
pip install -r requirements.txt
python drone_gui.py
```

- Each button runs its command on the drone (`aav@192.168.144.30`). Output shows in the box below.
- The status in the top-left shows the SSH connection. If it drops, the app reconnects automatically every few seconds.
- **+ Add Command** creates a new button with a title and command. Right-click a button to delete it.
- Buttons are saved in `buttons.json` next to the script.
- Connection settings (host, user, password) are at the top of `drone_gui.py`.
