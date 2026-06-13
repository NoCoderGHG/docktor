# Docktor

A simple Docker GUI ("Docker Desktop at home") in a single GTK3 Python file — manage containers, view logs, pull images, and even install Docker itself.

![Status: Linux-only](https://img.shields.io/badge/platform-Linux-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

## Features

- **Container management** — start, stop, restart, remove containers
- **Live container list** — auto-refreshing, with status color-coding and search
- **Logs viewer** — load and display the last 50 lines of a container's logs
- **Run new containers** — image, name, ports, volumes, env vars, restart policy
- **Pull images** — pull any image by name
- **Docker installer** — install Docker via the official script or apt, with live output
- **Multi-language** — English and German, switchable in the app with system language auto-detection

## Requirements

System packages (Debian/Ubuntu/Mint):

```
sudo apt install python3-gi gir1.2-gtk-3.0
```

Fedora:
```
sudo dnf install python3-gobject gtk3
```

Arch:
```
sudo pacman -S python-gobject gtk3
```

Docker itself is not required to launch the app — if missing, Docktor offers to install it.

## Installation

```
git clone https://github.com/NoCoderGHG/docktor.git
cd docktor
python3 docktor.py
```

No pip dependencies. No virtual environment needed.

## Configuration

Language preference is stored in `~/.config/docktor/config.json`.

## License

MIT — see [LICENSE](LICENSE).
