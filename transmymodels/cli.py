"""transmymodels command-line interface."""
from __future__ import annotations

import sys
from typing import List, Optional

import httpx
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from .config import (
    ApiKey,
    Config,
    ModelMapping,
    Upstream,
    find_upstream,
    load_config,
    save_config,
)
from .normalize import dedupe, normalize_model_id
from .upstream import UpstreamError, fetch_upstream_models

console = Console()


# ---------- helpers ----------

def _normalize_base(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _collect_routed_names(existing: Config) -> set:
    names = set()
    for u in existing.upstreams:
        for m in u.models:
            names.add(m.routed_name)
    return names


# ---------- add ----------

def cmd_add() -> None:
    cfg = load_config()

    console.rule("[bold cyan]Add upstream")
    name = Prompt.ask("Upstream nickname (e.g. openai, deepseek)").strip()
    if not name:
        console.print("[red]nickname cannot be empty.[/]")
        sys.exit(1)
    if find_upstream(cfg, name) is not None:
        console.print(f"[red]upstream '{name}' already exists. Remove it first, or pick another name.[/]")
        sys.exit(1)

    base_url = _normalize_base(
        Prompt.ask("Base URL (e.g. https://api.openai.com/v1)")
    )

    # One BaseURL, many keys (comma-separated), each with a weight.
    raw_keys = Prompt.ask("API key(s) (comma-separated for multiple keys)", password=True).strip()
    if not raw_keys:
        console.print("[red]at least one API key is required.[/]")
        sys.exit(1)
    key_strings = [k.strip() for k in raw_keys.split(",") if k.strip()]
    api_keys: List[ApiKey] = []
    for i, ks in enumerate(key_strings, 1):
        if len(key_strings) == 1:
            w = 1
        else:
            w = Prompt.ask(
                f"  weight for key #{i} (higher = more traffic, default 1)",
                default="1",
            ).strip()
            try:
                w = max(1, int(w))
            except ValueError:
                w = 1
        api_keys.append(ApiKey(key=ks, weight=w))

    # Auto-fetch model list using the first key.
    console.print(f"\n[dim]Fetching models from {base_url}/models ...[/]")
    try:
        upstream_ids = fetch_upstream_models(base_url, api_keys[0].key)
        console.print(f"[green]found {len(upstream_ids)} models.[/]\n")
    except UpstreamError as e:
        console.print(f"[yellow]could not auto-fetch models: {e}[/]")
        manual = Prompt.ask(
            "Enter model ids manually, comma-separated, or press Enter to abort",
            default="",
        ).strip()
        if not manual:
            console.print("aborted.")
            return
        upstream_ids = [m.strip() for m in manual.split(",") if m.strip()]

    # Map each upstream model id to a routed name. Default = normalized lowercase
    # short name (date/build suffixes stripped). Enter = accept default.
    used = _collect_routed_names(cfg)
    mappings: List[ModelMapping] = []
    console.print("[dim]Default routed name = normalized lowercase (date suffixes stripped). Press Enter to accept, or type a custom alias.[/]\n")
    for mid in upstream_ids:
        candidate = dedupe(normalize_model_id(mid), used)
        routed = Prompt.ask(
            f"  [cyan]{mid}[/] -> routed name",
            default=candidate,
        ).strip()
        if not routed:
            routed = candidate
        while routed in used:
            routed = Prompt.ask(
                f"  [red]'{routed}' already taken, pick another[/]",
                default=candidate,
            ).strip() or candidate
        used.add(routed)
        mappings.append(ModelMapping(upstream_model=mid, routed_name=routed))

    upstream = Upstream(name=name, base_url=base_url, api_keys=api_keys, models=mappings)
    cfg.upstreams.append(upstream)
    save_config(cfg)

    console.print(f"\n[green]added upstream '{name}' with {len(mappings)} routed models.[/]")
    _print_local_info(cfg)


# ---------- list ----------

def cmd_list() -> None:
    cfg = load_config()
    if not cfg.upstreams:
        console.print("[yellow]no upstreams configured yet. Run `transmymodels add`.[/]")
        return

    t = Table(title="Upstreams", show_lines=True)
    t.add_column("Upstream", style="cyan")
    t.add_column("Base URL")
    t.add_column("Keys", justify="right", style="yellow")
    t.add_column("Models", justify="right", style="magenta")
    t.add_column("Mapping (routed <= upstream)")
    for u in cfg.upstreams:
        mapping_str = "\n".join(f"{m.routed_name}  <=  {m.upstream_model}" for m in u.models)
        keys_str = ", ".join(f"{k.weight}" for k in u.api_keys)
        t.add_row(u.name, u.base_url, keys_str, str(len(u.models)), mapping_str)
    console.print(t)
    _print_local_info(cfg)


# ---------- remove ----------

def cmd_remove(name: str) -> None:
    cfg = load_config()
    u = find_upstream(cfg, name)
    if u is None:
        console.print(f"[red]no upstream named '{name}'.[/]")
        sys.exit(1)
    if not Confirm.ask(f"Remove upstream '{name}' and its {len(u.models)} routed models?", default=False):
        console.print("aborted.")
        return
    cfg.upstreams = [x for x in cfg.upstreams if x.name != name]
    save_config(cfg)
    console.print(f"[green]removed '{name}'.[/]")


# ---------- refresh ----------

def cmd_refresh(name: Optional[str]) -> None:
    cfg = load_config()
    targets = [u for u in cfg.upstreams if (name is None or u.name == name)]
    if not targets:
        console.print(f"[red]no upstream{'s' if name is None else ''} matched.[/]")
        sys.exit(1)

    used = _collect_routed_names(cfg)
    for u in targets:
        console.print(f"[dim]refreshing {u.name} ...[/]")
        try:
            ids = fetch_upstream_models(u.base_url, u.pick_key())
        except UpstreamError as e:
            console.print(f"[red]{u.name}: {e}[/]")
            continue
        # Preserve existing routed names for models that still exist; default new ones.
        existing = {m.upstream_model: m.routed_name for m in u.models}
        new_mappings: List[ModelMapping] = []
        for mid in ids:
            if mid in existing:
                routed = existing[mid]
            else:
                routed = dedupe(normalize_model_id(mid), used)
            used.add(routed)
            new_mappings.append(ModelMapping(upstream_model=mid, routed_name=routed))
        u.models = new_mappings
        console.print(f"[green]{u.name}: {len(new_mappings)} models.[/]")
    save_config(cfg)


# ---------- serve ----------

def cmd_serve() -> None:
    cfg = load_config()
    if not cfg.upstreams:
        console.print("[yellow]warning: no upstreams configured. Run `transmymodels add` first.[/]")
    _print_local_info(cfg)
    console.rule(f"[bold green]serving on http://{cfg.local.host}:{cfg.local.port}[/]")
    from .server import serve
    serve(cfg.local.host, cfg.local.port)


# ---------- info ----------

def _print_local_info(cfg: Config) -> None:
    t = Table(title="Local endpoint", show_header=False, box=None)
    t.add_column(style="dim")
    t.add_column()
    t.add_row("Base URL", f"http://{cfg.local.host}:{cfg.local.port}/v1")
    t.add_row("API key", cfg.local.api_key)
    t.add_row("Models", "GET /v1/models")
    console.print(t)


def cmd_info() -> None:
    cfg = load_config()
    _print_local_info(cfg)


# ---------- entry ----------

def main(argv: Optional[List[str]] = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        console.print(__usage__)
        return

    cmd, rest = argv[0], argv[1:]
    if cmd == "add":
        cmd_add()
    elif cmd == "list":
        cmd_list()
    elif cmd == "remove":
        if not rest:
            console.print("usage: transmymodels remove <name>")
            sys.exit(1)
        cmd_remove(rest[0])
    elif cmd == "refresh":
        cmd_refresh(rest[0] if rest else None)
    elif cmd == "serve":
        cmd_serve()
    elif cmd in ("info", "key", "keys"):
        cmd_info()
    elif cmd in ("-h", "--help", "help"):
        console.print(__usage__)
    else:
        console.print(f"[red]unknown command: {cmd}[/]\n")
        console.print(__usage__)
        sys.exit(1)


__usage__ = """\
[bold cyan]transmymodels[/] — one local OpenAI endpoint, many upstreams.

  transmymodels add        interactively import a new upstream (ModelName + BaseURL + APIKey)
  transmymodels list       show upstreams and routed model mappings
  transmymodels refresh    re-fetch model lists (optionally: refresh <name>)
  transmymodels remove X   delete upstream X
  transmymodels serve      start the local OpenAI-compatible proxy
  transmymodels info      show local Base URL + API key

On `add`, the tool auto-fetches /v1/models from your upstream. For every
returned model you may press Enter to accept the default routed name
(<nick>/<model_id>) or type a custom alias.
"""


if __name__ == "__main__":
    main()
