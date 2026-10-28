from conduit.core import completion as c


def test_parse_bash_prompt() -> None:
    ctx = c.parse_input("root@web-01:/var/www$ tail -f acc")
    assert ctx.has_prompt
    assert ctx.input == "tail -f acc"
    assert ctx.cwd == "/var/www"
    assert ctx.token == "acc"
    assert ctx.command == "tail"
    assert not ctx.at_command_position


def test_parse_prompt_with_home_and_title_fallback() -> None:
    ctx = c.parse_input("paul@box:~$ ls")
    assert ctx.cwd == "~" and ctx.input == "ls"
    ctx = c.parse_input("$ git st", title="paul@box: /srv/app")
    assert ctx.cwd == "/srv/app" and ctx.input == "git st"
    ctx = c.parse_input("no prompt here")
    assert not ctx.has_prompt and ctx.input == "no prompt here"


def test_command_position_after_pipe_and_sudo() -> None:
    assert c.parse_input("$ ps aux | gr").at_command_position
    assert c.parse_input("$ sudo sys").at_command_position
    assert c.parse_input("$ sudo systemctl rest").command == "systemctl"
    assert not c.parse_input("$ cat fi").at_command_position


def test_resolve_directory() -> None:
    assert c.resolve_directory("", "/var/www", "/home/p") == "/var/www"
    assert c.resolve_directory("logs/", "/var/www", "/home/p") == "/var/www/logs"
    assert c.resolve_directory("../", "/var/www", "/home/p") == "/var"
    assert c.resolve_directory("/etc/", "/var/www", None) == "/etc"
    assert c.resolve_directory("~/", "/x", "/home/p") == "/home/p"
    assert c.resolve_directory("~/a/", "/x", "/home/p") == "/home/p/a"
    assert c.resolve_directory("", "~", "/home/p") == "/home/p"
    assert c.resolve_directory("", "~/proj", "/home/p") == "/home/p/proj"
    assert c.resolve_directory("x/", None, None) is None
    assert c.resolve_directory("~/", None, None) is None


def test_split_path_token() -> None:
    assert c.split_path_token("/var/lo") == ("/var/", "lo")
    assert c.split_path_token("docs") == ("", "docs")
    assert c.split_path_token("./a/b") == ("./a/", "b")


def test_command_suggestions_prefix_first() -> None:
    ctx = c.parse_input("$ sys")
    items = c.suggest(ctx)
    assert items and items[0].kind == "command"
    assert all(s.text.startswith("sys") for s in items if s.kind == "command")
    assert items[0].insert == items[0].text[3:] + "" and items[0].replace == 0
    # exact command already typed is not suggested as itself
    assert "ls" not in [s.text for s in c.suggest(c.parse_input("$ ls"))]


def test_history_outranks_commands() -> None:
    ctx = c.parse_input("$ doc")
    items = c.suggest(ctx, history=["docker compose up -d --build", "ls"])
    assert items[0].kind == "history"
    assert items[0].insert == "ker compose up -d --build"


def test_snippets_replace_token() -> None:
    ctx = c.parse_input("$ por")
    items = c.suggest(ctx, snippets=[("ports", "listening ports", "sudo ss -tulpn", True)])
    snippet = next(s for s in items if s.kind == "snippet")
    assert snippet.replace == 3
    assert snippet.insert == "sudo ss -tulpn" and snippet.run is True


def test_path_suggestions_for_file_commands() -> None:
    entries = [("logs", True), ("app.py", False), ("README.md", False), (".env", False)]
    ctx = c.parse_input("root@h:/srv$ cat ")
    items = c.suggest(ctx, remote_entries=entries, min_chars=2)
    texts = [s.text for s in items]
    assert "logs/" in texts and "app.py" in texts
    assert ".env" not in texts  # hidden files only when a dot was typed
    ctx = c.parse_input("root@h:/srv$ cat .e")
    assert [s.text for s in c.suggest(ctx, remote_entries=entries)] == [".env"]
    ctx = c.parse_input("root@h:/srv$ cd lo")
    items = c.suggest(ctx, remote_entries=entries)
    assert items[0].text == "logs/" and items[0].insert == "gs/"


def test_no_suggestions_for_short_input() -> None:
    assert c.suggest(c.parse_input("$ l"), min_chars=2) == []
