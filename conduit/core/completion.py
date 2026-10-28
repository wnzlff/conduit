"""Command-line suggestions: prompt parsing, matching and ranking (no Qt here).

The terminal only knows what is on the screen, so the current input is reconstructed from the
cursor row: everything after the last prompt marker (``$ ``, ``# ``, ``> ``, ``% ``). The working
directory is taken from a ``user@host:/path$`` style prompt or from the window title that bash
sets by default (``user@host: /path``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

PROMPT_END = re.compile(r"[$#>%]\s")
PROMPT_CWD = re.compile(r"(?:^|[\s:(\[])((?:~|/)[^\s$#>%\]]*)\s*[$#>%]\s*$")
TITLE_CWD = re.compile(r":\s*((?:~|/)\S*)\s*$")

FILE_COMMANDS = {
    "cd", "ls", "ll", "cat", "less", "more", "tail", "head", "vim", "vi", "nano", "rm", "cp", "mv",
    "chmod", "chown", "mkdir", "rmdir", "touch", "tar", "unzip", "zip", "du", "stat", "file", "source",
    "sh", "bash", "python", "python3", "node", "grep", "find", "diff", "scp", "rsync", "ln", "tree",
    "wc", "sort", "nl", "tac", "xdg-open", "code", "git", "docker", "kubectl", "systemctl", "journalctl",
}

# (command, short description). Kept to things people type on servers every day.
COMMANDS: list[tuple[str, str]] = [
    ("ls -la", "list all files with details"),
    ("ls -lah", "list all files, human readable sizes"),
    ("ls", "list directory"),
    ("cd", "change directory"),
    ("cd ..", "go to parent directory"),
    ("cd -", "go to previous directory"),
    ("pwd", "print working directory"),
    ("cat", "print file"),
    ("less", "page through a file"),
    ("tail -f", "follow a file (logs)"),
    ("tail -n 100", "last 100 lines"),
    ("head", "first lines of a file"),
    ("grep -rn", "search recursively with line numbers"),
    ("grep -i", "search, ignore case"),
    ("find . -name", "find files by name"),
    ("find . -type f", "find regular files"),
    ("du -sh *", "size of each item here"),
    ("du -sh", "size of a directory"),
    ("df -h", "disk space, human readable"),
    ("free -m", "memory usage in MB"),
    ("top", "live process view"),
    ("htop", "interactive process viewer"),
    ("ps aux", "all processes"),
    ("ps aux | grep", "find a process"),
    ("kill", "terminate a process by PID"),
    ("kill -9", "force kill a process"),
    ("pkill", "kill processes by name"),
    ("killall", "kill all processes with a name"),
    ("uptime", "load and uptime"),
    ("whoami", "current user"),
    ("id", "user and group ids"),
    ("hostname", "machine name"),
    ("uname -a", "kernel and system info"),
    ("cat /etc/os-release", "distribution info"),
    ("lsb_release -a", "distribution info"),
    ("date", "current date and time"),
    ("history", "command history"),
    ("clear", "clear the screen"),
    ("exit", "close the shell"),
    ("sudo", "run as root"),
    ("sudo -i", "root shell"),
    ("su -", "switch to root"),
    ("chmod +x", "make executable"),
    ("chmod 644", "owner rw, others r"),
    ("chmod 755", "owner rwx, others rx"),
    ("chmod -R", "change mode recursively"),
    ("chown -R", "change owner recursively"),
    ("mkdir -p", "create directory with parents"),
    ("rm -rf", "remove recursively (careful)"),
    ("rm -i", "remove with confirmation"),
    ("cp -r", "copy recursively"),
    ("mv", "move or rename"),
    ("touch", "create empty file / update mtime"),
    ("ln -s", "create symbolic link"),
    ("tar -xzf", "extract .tar.gz"),
    ("tar -czf", "create .tar.gz"),
    ("tar -xvf", "extract tar, verbose"),
    ("unzip", "extract zip"),
    ("zip -r", "create zip recursively"),
    ("wget", "download a file"),
    ("curl -O", "download with original name"),
    ("curl -I", "show response headers"),
    ("curl -s", "silent request"),
    ("scp", "copy over ssh"),
    ("rsync -avz", "sync files, compressed"),
    ("rsync -avz --delete", "mirror directory"),
    ("ssh", "open ssh connection"),
    ("ssh-keygen -t ed25519", "generate ed25519 key"),
    ("ping -c 4", "ping four times"),
    ("ip a", "network interfaces and addresses"),
    ("ip r", "routing table"),
    ("ss -tulpn", "listening sockets with processes"),
    ("netstat -tulpn", "listening sockets (legacy)"),
    ("nslookup", "dns lookup"),
    ("dig", "dns query"),
    ("traceroute", "trace network route"),
    ("nc -zv", "check if a port is open"),
    ("iptables -L -n", "list firewall rules"),
    ("ufw status", "firewall status"),
    ("systemctl status", "service status"),
    ("systemctl restart", "restart a service"),
    ("systemctl start", "start a service"),
    ("systemctl stop", "stop a service"),
    ("systemctl enable", "enable at boot"),
    ("systemctl disable", "disable at boot"),
    ("systemctl list-units --type=service", "list services"),
    ("systemctl daemon-reload", "reload unit files"),
    ("journalctl -u", "logs of a service"),
    ("journalctl -f", "follow the system log"),
    ("journalctl -xe", "recent log with context"),
    ("journalctl --since today", "today's log"),
    ("service", "control a service (sysvinit)"),
    ("crontab -e", "edit cron jobs"),
    ("crontab -l", "list cron jobs"),
    ("apt update", "refresh package lists"),
    ("apt upgrade", "upgrade packages"),
    ("apt install", "install a package"),
    ("apt remove", "remove a package"),
    ("apt search", "search packages"),
    ("apt list --upgradable", "packages with updates"),
    ("dnf install", "install a package (Fedora/RHEL)"),
    ("yum install", "install a package (RHEL)"),
    ("pacman -S", "install a package (Arch)"),
    ("snap list", "installed snaps"),
    ("pip install", "install python package"),
    ("python3 -m venv", "create virtualenv"),
    ("python3", "python interpreter"),
    ("node -v", "node version"),
    ("npm install", "install node packages"),
    ("npm run", "run npm script"),
    ("git status", "working tree status"),
    ("git pull", "fetch and merge"),
    ("git push", "push commits"),
    ("git log --oneline", "compact history"),
    ("git diff", "unstaged changes"),
    ("git checkout", "switch branch / restore"),
    ("git switch", "switch branch"),
    ("git branch -a", "all branches"),
    ("git stash", "stash changes"),
    ("git clone", "clone a repository"),
    ("git fetch --all", "fetch all remotes"),
    ("git reset --hard", "discard changes (careful)"),
    ("docker ps", "running containers"),
    ("docker ps -a", "all containers"),
    ("docker images", "local images"),
    ("docker logs -f", "follow container logs"),
    ("docker exec -it", "shell into a container"),
    ("docker restart", "restart container"),
    ("docker stop", "stop container"),
    ("docker rm", "remove container"),
    ("docker system prune", "remove unused data"),
    ("docker compose up -d", "start compose stack"),
    ("docker compose down", "stop compose stack"),
    ("docker compose logs -f", "follow compose logs"),
    ("docker compose ps", "compose services"),
    ("docker compose pull", "pull compose images"),
    ("kubectl get pods", "list pods"),
    ("kubectl logs -f", "follow pod logs"),
    ("kubectl describe", "describe a resource"),
    ("nginx -t", "test nginx config"),
    ("systemctl reload nginx", "reload nginx"),
    ("apachectl configtest", "test apache config"),
    ("php -v", "php version"),
    ("php artisan", "laravel console"),
    ("composer install", "install php dependencies"),
    ("mysql -u root -p", "mysql shell"),
    ("mysqldump -u root -p", "dump a database"),
    ("psql -U postgres", "postgres shell"),
    ("redis-cli", "redis shell"),
    ("vim", "edit with vim"),
    ("nano", "edit with nano"),
    ("echo", "print text"),
    ("export", "set environment variable"),
    ("env", "show environment"),
    ("which", "locate a command"),
    ("man", "manual page"),
    ("alias", "list aliases"),
    ("watch -n 1", "repeat a command every second"),
    ("screen -r", "reattach screen session"),
    ("tmux a", "attach tmux session"),
    ("tmux new -s", "new named tmux session"),
    ("nohup", "run immune to hangups"),
    ("jobs", "background jobs"),
    ("fg", "bring job to foreground"),
    ("bg", "continue job in background"),
    ("xargs", "build commands from input"),
    ("awk", "pattern processing"),
    ("sed -i", "edit file in place"),
    ("sort | uniq -c", "count unique lines"),
    ("wc -l", "count lines"),
    ("tree -L 2", "directory tree, two levels"),
    ("lsblk", "block devices"),
    ("mount", "mounted filesystems"),
    ("fdisk -l", "disks and partitions"),
    ("dmesg | tail", "recent kernel messages"),
    ("last", "recent logins"),
    ("w", "who is logged in"),
    ("reboot", "reboot the machine"),
    ("shutdown -h now", "power off"),
    ("certbot renew", "renew letsencrypt certificates"),
    ("openssl x509 -in", "inspect a certificate"),
    ("timedatectl", "time and timezone"),
    ("hostnamectl", "host name and system info"),
]

COMMAND_NAMES = {c.split()[0] for c, _ in COMMANDS}


@dataclass
class Suggestion:
    kind: str  # command | history | snippet | path
    text: str  # what gets completed (full command / path / snippet name)
    detail: str = ""
    replace: int = 0  # number of characters to delete before inserting (snippets replace the token)
    insert: str = ""  # characters to send
    run: bool = False  # press Enter after inserting
    score: float = 0.0


@dataclass
class InputContext:
    line: str  # full cursor row text up to the cursor
    input: str  # text after the prompt
    cwd: str | None
    has_prompt: bool

    @property
    def token(self) -> str:
        """The word being typed (after the last unquoted space)."""
        if not self.input or self.input.endswith(" "):
            return ""
        return self.input.rsplit(" ", 1)[-1]

    @property
    def words(self) -> list[str]:
        return self.input.split()

    @property
    def at_command_position(self) -> bool:
        before = self.input[: len(self.input) - len(self.token)]
        if not before.strip():
            return True
        stripped = before.rstrip()
        return stripped.endswith(("|", "&&", "||", ";", "sudo", "$(", "(")) or stripped.split()[-1] in ("sudo", "nohup", "time", "watch")

    @property
    def command(self) -> str:
        words = self.words
        if not words:
            return ""
        first = words[0]
        if first == "sudo" and len(words) > 1:
            return words[1]
        return first


def parse_input(cursor_row_text: str, title: str = "") -> InputContext:
    """Splits the cursor row into prompt and user input and extracts the working directory."""
    line = cursor_row_text
    matches = list(PROMPT_END.finditer(line))
    if matches:
        m = matches[-1]
        prompt, user_input = line[: m.end()], line[m.end():]
        has_prompt = True
    else:
        prompt, user_input, has_prompt = "", line, False
    cwd: str | None = None
    pm = PROMPT_CWD.search(prompt)
    if pm:
        cwd = pm.group(1)
    elif title:
        tm = TITLE_CWD.search(title)
        if tm:
            cwd = tm.group(1)
    return InputContext(line=line, input=user_input, cwd=cwd, has_prompt=has_prompt)


def looks_like_path(token: str) -> bool:
    return token.startswith(("/", "./", "../", "~")) or "/" in token


def split_path_token(token: str) -> tuple[str, str]:
    """``"/var/lo"`` -> ``("/var/", "lo")``, ``"docs"`` -> ``("", "docs")``."""
    if "/" in token:
        directory, _, partial = token.rpartition("/")
        return directory + "/", partial
    return "", token


def resolve_directory(prefix: str, cwd: str | None, home: str | None) -> str | None:
    """Turns the directory part of a token into an absolute remote path (None if unknown)."""
    if prefix.startswith("/"):
        return prefix.rstrip("/") or "/"
    if prefix.startswith("~"):
        if home is None:
            return None
        rest = prefix[1:].strip("/")
        return f"{home}/{rest}".rstrip("/") if rest else home
    base = cwd
    if base is not None and base.startswith("~"):
        if home is None:
            return None
        base = home + base[1:]
    if base is None:
        return None
    rel = prefix.strip("/")
    path = base.rstrip("/") or "/"
    if rel:
        path = f"{path}/{rel}".replace("//", "/")
    # collapse ./ and ../
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/" + "/".join(parts)


def _fuzzy_score(candidate: str, needle: str) -> float:
    """Prefix match scores best, then word-start match, then substring; 0 means no match."""
    if not needle:
        return 1.0
    cl, nl = candidate.lower(), needle.lower()
    if cl.startswith(nl):
        return 100.0 - len(candidate) * 0.1
    for word in cl.split()[1:]:
        if word.startswith(nl):
            return 60.0 - len(candidate) * 0.1
    if nl in cl:
        return 30.0 - cl.index(nl)
    return 0.0


def suggest(
    ctx: InputContext,
    *,
    history: Sequence[str] = (),
    snippets: Iterable[tuple[str, str, str, bool]] = (),  # (name, description, body, run)
    remote_entries: Sequence[tuple[str, bool]] | None = None,  # (name, is_dir) of the token's directory
    limit: int = 8,
    min_chars: int = 1,
) -> list[Suggestion]:
    token = ctx.token
    text = ctx.input
    results: list[Suggestion] = []
    if len(token) < min_chars and not (text and text.endswith(" ") and remote_entries is not None):
        if len(text.strip()) < min_chars:
            return []

    # Paths: when the token looks like a path, or the command takes files and we have a listing.
    if remote_entries is not None and (looks_like_path(token) or ctx.command in FILE_COMMANDS or not ctx.at_command_position):
        _directory, partial = split_path_token(token)
        for name, is_dir in remote_entries:
            if partial and not name.lower().startswith(partial.lower()):
                continue
            if not partial and name.startswith(".") and not token.endswith("."):
                continue
            completed = name + ("/" if is_dir else "")
            if " " in name:
                completed = completed.replace(" ", "\\ ")
            results.append(
                Suggestion(
                    kind="path",
                    text=completed,
                    detail="directory" if is_dir else "file",
                    insert=completed[len(partial):],
                    score=(80.0 if is_dir else 75.0) - len(name) * 0.05,
                )
            )

    if token:
        # History: whole-line prefix matches rank highest, they are what the user did before.
        for i, entry in enumerate(history):
            if entry == text:
                continue
            score = _fuzzy_score(entry, text)
            if score > 0 and entry.lower().startswith(text.lower()):
                results.append(Suggestion("history", entry, "history", insert=entry[len(text):], score=score + 20 - i * 0.01))

        if ctx.at_command_position:
            for command, description in COMMANDS:
                score = _fuzzy_score(command, token)
                if score > 0 and command != token:
                    results.append(Suggestion("command", command, description, insert=command[len(token):] if command.lower().startswith(token.lower()) else "", replace=0 if command.lower().startswith(token.lower()) else len(token), score=score))
            # A non-prefix command match must replace the typed token.
            for s in results:
                if s.kind == "command" and not s.insert:
                    s.insert = s.text

        for name, description, body, run in snippets:
            score = _fuzzy_score(name, token)
            if score > 0:
                results.append(Suggestion("snippet", name, description or body.splitlines()[0][:60], replace=len(token), insert=body, run=run, score=score + 5))

    # Deduplicate by (kind, text), keep best score, then sort.
    best: dict[tuple[str, str], Suggestion] = {}
    for s in results:
        key = (s.kind, s.text)
        if key not in best or s.score > best[key].score:
            best[key] = s
    ordered = sorted(best.values(), key=lambda s: -s.score)
    return ordered[:limit]
